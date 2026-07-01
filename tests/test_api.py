import json

from fastapi.testclient import TestClient

from app.api import app
from app.api import nodes
from app.llm import LLMError


def test_no_key_flow_pauses_and_approves(monkeypatch):
    async def no_llm(*_args, **_kwargs):
        raise LLMError("test provider disabled")

    async def no_search(*_args, **_kwargs):
        return []

    monkeypatch.setattr(nodes.llm, "generate", no_llm)
    monkeypatch.setattr(nodes.searcher, "search", no_search)
    payload = json.loads(open("examples/aarav_mehta.json", encoding="utf-8").read())
    with TestClient(app) as client:
        response = client.post("/contacts/batch", json=payload)
        assert response.status_code == 202
        created = response.json()["contacts"][0]
        assert created["status"] == "pending_review"

        contact_id = created["contact_id"]
        detail = client.get(f"/contacts/{contact_id}").json()
        assert detail["ranked_gifts"] == []
        assert any("no products were fabricated" in flag for flag in detail["confidence_flags"])

        reviewed = client.post(
            f"/contacts/{contact_id}/review", json={"action": "approve"}
        )
        assert reviewed.status_code == 200
        assert reviewed.json()["status"] == "approved"
        assert reviewed.json()["final_output"]["recommended_gifts"] == []
