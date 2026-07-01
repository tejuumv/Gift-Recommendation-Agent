import asyncio
import sqlite3
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from app.nodes import WorkflowNodes
from app.state import GiftWorkflowState


def after_validation(state: GiftWorkflowState) -> str:
    if len(state.get("validated_products", [])) < 3 and state.get("retry_count", 0) <= 2:
        return "retry"
    return "rank"


def after_review(state: GiftWorkflowState) -> str:
    status = state.get("review_status")
    if status in {"approved", "edited"}:
        return "finalize"
    if status == "regenerate":
        return "regenerate"
    return "end"


def build_graph(nodes: WorkflowNodes, checkpoint_path: Path):
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(checkpoint_path, check_same_thread=False)
    checkpointer = SqliteSaver(connection)
    builder = StateGraph(GiftWorkflowState)
    def sync(async_node):
        def run(state):
            return asyncio.run(async_node(state))
        return run

    builder.add_node("ingest_contact", sync(nodes.ingest_contact))
    builder.add_node("extract_signals", sync(nodes.extract_signals))
    builder.add_node("generate_search_queries", sync(nodes.generate_search_queries))
    builder.add_node("search_products", sync(nodes.search_products))
    builder.add_node("validate_products", sync(nodes.validate_products))
    builder.add_node("rank_gifts", sync(nodes.rank_gifts))
    builder.add_node("generate_messages", sync(nodes.generate_messages))
    builder.add_node("human_review", sync(nodes.human_review))
    builder.add_node("finalize", sync(nodes.finalize))
    builder.add_edge(START, "ingest_contact")
    builder.add_edge("ingest_contact", "extract_signals")
    builder.add_edge("extract_signals", "generate_search_queries")
    builder.add_edge("generate_search_queries", "search_products")
    builder.add_edge("search_products", "validate_products")
    builder.add_conditional_edges(
        "validate_products", after_validation,
        {"retry": "generate_search_queries", "rank": "rank_gifts"},
    )
    builder.add_edge("rank_gifts", "generate_messages")
    builder.add_edge("generate_messages", "human_review")
    builder.add_conditional_edges(
        "human_review", after_review,
        {"finalize": "finalize", "regenerate": "rank_gifts", "end": END},
    )
    builder.add_edge("finalize", END)
    return builder.compile(checkpointer=checkpointer, interrupt_before=["human_review"])
