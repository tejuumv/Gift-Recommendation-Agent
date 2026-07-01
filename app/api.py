import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.config import get_settings
from app.db import ContactRepository
from app.graph import build_graph
from app.llm import create_json_client
from app.models import BatchRequest, ReviewAction, ReviewRequest
from app.nodes import WorkflowNodes
from app.search import ProductSearcher

settings = get_settings()
repository = ContactRepository(settings.database_dir / "contacts.sqlite")
nodes = WorkflowNodes(create_json_client(settings), ProductSearcher(settings))
graph = build_graph(nodes, settings.database_dir / "checkpoints.sqlite")


def config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def public_state(thread_id: str) -> dict[str, Any]:
    snapshot = graph.get_state(config(thread_id))
    return dict(snapshot.values) if snapshot.values else {}


def status_for(state: dict[str, Any]) -> str:
    return state.get("review_status", "processing")


def run_contact(thread_id: str, contact: dict[str, Any]) -> None:
    try:
        graph.invoke({"contact": contact}, config(thread_id))
        state = public_state(thread_id)
        repository.update(thread_id, status_for(state), state)
    except Exception as exc:
        record = repository.get(thread_id)
        state = (record or {}).get("state", {})
        state["confidence_flags"] = [*state.get("confidence_flags", []), f"Workflow failed: {exc}"]
        repository.update(thread_id, "failed", state)


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.database_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Hyper-Personalised Gift Recommendation Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def dashboard() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "llm_provider": settings.llm_provider,
        "llm_configured": bool(
            settings.groq_api_key
            if settings.llm_provider == "groq"
            else settings.anthropic_api_key
        ),
        "search_configured": bool(settings.tavily_api_key),
    }


@app.post("/contacts/batch", status_code=202)
async def create_batch(request: BatchRequest) -> dict[str, Any]:
    created = []
    for contact in request.contacts:
        thread_id = str(uuid4())
        payload = contact.model_dump(mode="json")
        repository.create(thread_id, contact.name, {"contact": payload})
        created.append({"contact_id": thread_id, "contact_name": contact.name, "status": "processing"})
    await asyncio.gather(
        *(asyncio.to_thread(run_contact, item["contact_id"], contact.model_dump(mode="json"))
          for item, contact in zip(created, request.contacts, strict=True))
    )
    for item in created:
        item["status"] = repository.get(item["contact_id"])["status"]
    return {"contacts": created}


@app.get("/contacts")
async def list_contacts() -> list[dict[str, Any]]:
    return repository.list()


def require_contact(contact_id: str) -> dict[str, Any]:
    record = repository.get(contact_id)
    if not record:
        raise HTTPException(status_code=404, detail="Contact not found")
    return record


@app.get("/contacts/{contact_id}")
async def get_contact(contact_id: str) -> dict[str, Any]:
    record = require_contact(contact_id)
    state = record["state"]
    return {
        "contact_id": contact_id,
        "contact_name": record["contact_name"],
        "status": record["status"],
        "ranked_gifts": state.get("ranked_gifts", []),
        "personalised_messages": state.get("personalised_messages", {}),
        "final_output": state.get("final_output"),
        "confidence_flags": state.get("confidence_flags", []),
        "workflow_metrics": state.get("workflow_metrics", []),
    }


@app.get("/contacts/{contact_id}/trace")
async def get_trace(contact_id: str) -> dict[str, Any]:
    state = require_contact(contact_id)["state"]
    return {
        key: state.get(key, [] if key != "profile_signals" else {})
        for key in (
            "profile_signals", "search_queries", "raw_products",
            "validated_products", "confidence_flags",
        )
    } | {"workflow_metrics": state.get("workflow_metrics", [])}


@app.post("/contacts/{contact_id}/review")
async def review(contact_id: str, request: ReviewRequest) -> dict[str, Any]:
    require_contact(contact_id)
    review_status = {
        ReviewAction.approve: "approved",
        ReviewAction.reject: "rejected",
        ReviewAction.edit: "edited",
        ReviewAction.regenerate: "regenerate",
    }[request.action]
    updates: dict[str, Any] = {
        "review_status": review_status,
        "review_feedback": request.feedback,
    }
    if request.action == ReviewAction.edit:
        updates["ranked_gifts"] = [
            gift.model_dump(mode="json") for gift in request.edited_gifts or []
        ]
        updates["personalised_messages"] = {
            str(index): gift.personalised_message
            for index, gift in enumerate(request.edited_gifts or [])
        }
    await asyncio.to_thread(graph.update_state, config(contact_id), updates)
    if request.action != ReviewAction.reject:
        await asyncio.to_thread(graph.invoke, None, config(contact_id))
    state = await asyncio.to_thread(public_state, contact_id)
    repository.update(contact_id, status_for(state), state)
    return {
        "contact_id": contact_id,
        "status": status_for(state),
        "final_output": state.get("final_output"),
        "ranked_gifts": state.get("ranked_gifts", []),
    }
