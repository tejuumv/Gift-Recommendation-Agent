import asyncio

from app.config import Settings
from app.llm import create_json_client
from app.nodes import WorkflowNodes
from app.search import ProductSearcher, country_fit, is_professionally_safe


def test_guardrail_blocks_inappropriate_categories():
    assert not is_professionally_safe({"name": "Designer perfume gift set"})
    assert not is_professionally_safe(
        {"name": "Best keyboards under ₹5000", "store": "youtube.com"}
    )
    assert is_professionally_safe({"name": "Hardcover systems-design book"})


def test_product_url_validation_rejects_search_and_article_shapes():
    searcher = ProductSearcher(Settings())
    assert asyncio.run(
        searcher.inspect_product(
            {
                "name": "2500 - ₹5000 - Cricket Kits / Cricket Equipment",
                "url": "https://www.amazon.in/Cricket-Kits-%E2%82%B92-500-%E2%82%B95-000/s?rh=n%3A3403860031",
            }
        )
    ) is None
    assert asyncio.run(
        searcher.inspect_product(
            {
                "name": "8 Best Corporate Gifts Under ₹3000 for Employees and Clients",
                "url": "https://www.mensxp.com/technology/wearables/160700-corporate-gifts-under-3000.html",
            }
        )
    ) is None


def test_fallback_ranking_uses_only_validated_urls():
    nodes = WorkflowNodes(
        create_json_client(Settings()), ProductSearcher(Settings())
    )
    state = {
        "profile_signals": {
            "strong_signals": ["systems thinking"],
            "weak_signals": [],
            "signals_to_avoid": [],
        },
        "validated_products": [
            {
                "name": "Systems book", "url": "https://store.example/book",
                "store": "store.example", "price": 2500,
            }
        ],
    }
    gifts = nodes._fallback_ranking(state)
    assert [gift["product_url"] for gift in gifts] == ["https://store.example/book"]


def test_no_keys_produces_empty_products_without_fabrication():
    settings = Settings(anthropic_api_key=None, tavily_api_key=None)
    nodes = WorkflowNodes(create_json_client(settings), ProductSearcher(settings))
    assert asyncio.run(nodes.searcher.search("desk gift", "India")) == []


def test_country_filter_rejects_different_national_storefront():
    assert not country_fit({"store": "amazon.co.uk"}, "India")
    assert country_fit({"store": "amazon.in"}, "India")


def test_ingest_normalizes_official_linkedin_profile():
    nodes = WorkflowNodes(create_json_client(Settings()), ProductSearcher(Settings()))
    state = {
        "contact": {
            "name": "Aarav Mehta",
            "linkedin_profile": {
                "headline": "VP Sales at B2B SaaS company",
                "about": "Leads enterprise SaaS sales.",
                "recent_posts": ["Cricket strategy post"],
                "engaged_topics": ["cricket", "SaaS growth"],
            },
            "gift_context": {
                "occasion": "thanks",
                "budget_min": 3000,
                "budget_max": 5000,
                "currency": "INR",
                "country": "India",
            },
        }
    }
    normalized = asyncio.run(nodes.ingest_contact(state))["contact"]
    assert normalized["role"] == "VP Sales at B2B SaaS company"
    assert normalized["about"] == "Leads enterprise SaaS sales."
    assert normalized["recent_posts"] == ["Cricket strategy post"]
    assert normalized["engaged_topics"] == ["cricket", "SaaS growth"]


def test_search_query_expansion_uses_explicit_signals_and_budget():
    nodes = WorkflowNodes(create_json_client(Settings()), ProductSearcher(Settings()))
    state = {
        "contact": {
            "role": "VP Sales",
            "company": "SaaSCo",
            "about": "",
            "gift_context": {
                "country": "India",
                "currency": "INR",
                "budget_min": 3000,
                "budget_max": 5000,
            },
        },
        "profile_signals": {
            "strong_signals": ["cricket", "SaaS sales leadership"],
            "weak_signals": [],
        },
        "retry_count": 1,
    }
    queries = nodes._expand_product_queries(state, ["professional gift"])
    assert any("cricket" in query.lower() for query in queries)
    assert any("sales leader" in query.lower() for query in queries)
    assert all("INR" in query and "India" in query for query in queries)


def test_sensitive_ranked_reasoning_falls_back_to_safe_copy(monkeypatch):
    class UnsafeLLM:
        async def generate(self, *_args, **_kwargs):
            return [
                {
                    "rank": 1,
                    "gift_name": "Book",
                    "product_url": "https://store.example/book",
                    "store": "store.example",
                    "estimated_price": "3500",
                    "why_this_gift": "Because of a political signal.",
                    "personalisation_reasoning": "Grounded in politics.",
                    "confidence_score": 0.7,
                    "risk_level": "low",
                    "assumptions": [],
                }
            ]

    nodes = WorkflowNodes(UnsafeLLM(), ProductSearcher(Settings()))
    state = {
        "profile_signals": {
            "strong_signals": ["systems thinking"],
            "weak_signals": [],
            "signals_to_avoid": [],
        },
        "validated_products": [
            {
                "name": "Book",
                "url": "https://store.example/book",
                "store": "store.example",
                "price": 3500,
            }
        ],
        "contact": {"relationship_context": None},
        "confidence_flags": [],
        "workflow_metrics": [],
    }
    result = asyncio.run(nodes.rank_gifts(state))
    assert result["ranked_gifts"][0]["product_url"] == "https://store.example/book"
    assert "politic" not in result["ranked_gifts"][0]["why_this_gift"].lower()
