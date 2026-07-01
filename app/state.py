from typing import TypedDict


class GiftWorkflowState(TypedDict):
    contact: dict
    profile_signals: dict
    search_queries: list[str]
    raw_products: list[dict]
    validated_products: list[dict]
    rejected_product_urls: list[str]
    ranked_gifts: list[dict]
    personalised_messages: dict
    review_status: str
    review_feedback: str | None
    retry_count: int
    confidence_flags: list[str]
    final_output: dict
    current_search_queries: list[str]
    current_raw_products: list[dict]
    workflow_metrics: list[dict]
