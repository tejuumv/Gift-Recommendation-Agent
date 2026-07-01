import asyncio
import re
from time import perf_counter
from typing import Any

from pydantic import ValidationError

from app.llm import JSONClient, LLMError
from app.models import ContactInput, ProfileSignals, RankedGift
from app.search import ProductSearcher, country_fit, is_professionally_safe
from app.state import GiftWorkflowState

SENSITIVE_SIGNAL = re.compile(
    r"\b(religion|religious|politic|political|health|medical|ethnicity|race|"
    r"caste|gender|sexual orientation|disability|disabled|family status|"
    r"married|single|pregnan|age|minority)\b",
    re.IGNORECASE,
)


class WorkflowNodes:
    def __init__(self, llm: JSONClient, searcher: ProductSearcher) -> None:
        self.llm = llm
        self.searcher = searcher

    async def ingest_contact(self, state: GiftWorkflowState) -> dict:
        contact = ContactInput.model_validate(state["contact"])
        normalized = contact.model_dump(mode="json")
        nested = normalized.get("linkedin_profile") or {}
        for field in (
            "about", "experience", "recent_posts", "recent_comments", "engaged_topics"
        ):
            if not normalized.get(field) and nested.get(field):
                normalized[field] = nested[field]
        if not normalized.get("role") and nested.get("headline"):
            normalized["role"] = nested["headline"]
        return {
            "contact": normalized,
            "retry_count": 0,
            "confidence_flags": [],
            "review_status": "processing",
            "search_queries": [],
            "raw_products": [],
            "validated_products": [],
            "rejected_product_urls": [],
            "workflow_metrics": [],
        }

    async def extract_signals(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        system = """Extract professional gift-personalisation signals from the supplied profile.
Return only JSON with strong_signals, weak_signals, signals_to_avoid arrays.
Use only explicit evidence. Never infer or mention religion, politics, health, ethnicity,
gender, family status, or other protected/sensitive traits. Do not diagnose personality."""
        profile = {
            key: state["contact"].get(key)
            for key in (
                "role", "about", "experience", "recent_posts",
                "recent_comments", "engaged_topics",
            )
        }
        profile["relationship_context"] = state["contact"].get("relationship_context")
        flags = list(state.get("confidence_flags", []))
        result: dict[str, Any] | None = None
        for attempt in range(2):
            try:
                prompt = system + (
                    "\nPrevious response was invalid. Respond with strictly valid JSON only."
                    if attempt else ""
                )
                result = await self.llm.generate(prompt, profile)
                signals = ProfileSignals.model_validate(result)
                break
            except (LLMError, ValidationError, TypeError):
                result = None
        if result is None:
            signals = self._fallback_signals(state["contact"])
            flags.append("LLM signal extraction unavailable; conservative explicit-topic fallback used.")
        clean = {
            key: [item for item in values if not SENSITIVE_SIGNAL.search(item)]
            for key, values in signals.model_dump().items()
        }
        return {
            "profile_signals": clean,
            "confidence_flags": flags,
            "workflow_metrics": self._metric(
                state, "extract_signals", started, "llm", 1 if result is not None else 2
            ),
        }

    def _fallback_signals(self, contact: dict) -> ProfileSignals:
        topics = [
            str(topic).strip() for topic in contact.get("engaged_topics", [])
            if str(topic).strip() and not SENSITIVE_SIGNAL.search(str(topic))
        ]
        posts = [
            str(post).strip()[:160] for post in contact.get("recent_posts", [])[:2]
            if str(post).strip() and not SENSITIVE_SIGNAL.search(str(post))
        ]
        return ProfileSignals(
            strong_signals=topics[:4],
            weak_signals=posts,
            signals_to_avoid=["Sensitive traits", "Overly personal or intimate gifts"],
        )

    async def generate_search_queries(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        context = state["contact"]["gift_context"]
        retry = state.get("retry_count", 0)
        system = """Create 2-4 concrete ecommerce product-search queries for professional gifts.
Return only a JSON array of strings. Use the supplied explicit signals, budget, currency
and country. Prefer purchasable product-page language such as "product page", "gift set",
"buy online", and concrete categories. Avoid listicle/blog/category wording. Do not include
names or sensitive traits. Do not invent product names or URLs."""
        payload = {
            "signals": state["profile_signals"],
            "gift_context": context,
            "broaden_search": retry > 0,
            "feedback": state.get("review_feedback"),
            "relationship_context": state["contact"].get("relationship_context"),
        }
        try:
            queries = await self.llm.generate(system, payload)
            if not isinstance(queries, list) or not all(isinstance(q, str) for q in queries):
                raise LLMError("Search query response was not a string array")
            queries = queries[:4]
        except LLMError:
            signals = (
                state["profile_signals"].get("strong_signals")
                or state["profile_signals"].get("weak_signals")
                or ["professional desk"]
            )
            prefix = "professional gift" if retry else "premium gift"
            queries = [
                f"{prefix} {signal} under {context['currency']} {context['budget_max']}"
                for signal in signals[:3]
            ]
        queries = self._expand_product_queries(state, queries)
        all_queries = list(state.get("search_queries", []))
        all_queries.extend(query for query in queries if query not in all_queries)
        return {
            "current_search_queries": queries,
            "search_queries": all_queries,
            "workflow_metrics": self._metric(state, "generate_search_queries", started, "llm", 1),
        }

    async def search_products(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        country = state["contact"]["gift_context"]["country"]
        batches = await asyncio.gather(
            *(self.searcher.search(query, country) for query in state["current_search_queries"]),
            return_exceptions=True,
        )
        seen: set[str] = set()
        products: list[dict] = []
        for batch in batches:
            if isinstance(batch, Exception):
                continue
            for product in batch:
                if product.get("url") and product["url"] not in seen:
                    seen.add(product["url"])
                    products.append(product)
        accumulated = list(state.get("raw_products", []))
        known = {product.get("url") for product in accumulated}
        accumulated.extend(product for product in products if product.get("url") not in known)
        return {
            "current_raw_products": products,
            "raw_products": accumulated,
            "workflow_metrics": self._metric(
                state, "search_products", started, "search", len(state["current_search_queries"])
            ),
        }

    async def validate_products(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        context = state["contact"]["gift_context"]
        candidates = []
        for product in state.get("raw_products", []):
            if product.get("country") not in {None, "", context["country"]}:
                continue
            if country_fit(product, context["country"]) and is_professionally_safe(product):
                candidates.append(product)
        existing_valid = list(state.get("validated_products", []))
        rejected_urls = set(state.get("rejected_product_urls", []))
        validated_urls = {self._norm_url(item.get("url", "")) for item in existing_valid}
        deduped: list[dict] = []
        seen_candidate_urls: set[str] = set()
        for item in candidates:
            url = self._norm_url(item.get("url", ""))
            if not url or url in seen_candidate_urls or url in rejected_urls or url in validated_urls:
                continue
            seen_candidate_urls.add(url)
            deduped.append(item)
        deduped.sort(
            key=lambda item: (
                item.get("price") is None,
                not self._looks_like_product_url(item.get("url", "")),
            )
        )
        candidates = deduped[:36]
        semaphore = asyncio.Semaphore(10)

        async def inspect(item: dict) -> dict | None:
            async with semaphore:
                return await self.searcher.inspect_product(item)

        inspections = await asyncio.gather(
            *(inspect(item) for item in candidates), return_exceptions=True
        )
        valid = list(existing_valid)
        for inspected in inspections:
            if not isinstance(inspected, dict):
                continue
            price = inspected.get("price")
            if price is None or not (context["budget_min"] <= price <= context["budget_max"]):
                if inspected.get("url"):
                    rejected_urls.add(self._norm_url(inspected["url"]))
                continue
            if inspected.get("currency") and inspected["currency"] != context["currency"]:
                if inspected.get("url"):
                    rejected_urls.add(self._norm_url(inspected["url"]))
                continue
            valid.append(inspected)
        inspected_urls = {
            self._norm_url(item.get("url", "")) for item in candidates
        }
        valid_urls = {self._norm_url(item.get("url", "")) for item in valid}
        rejected_urls.update(url for url in inspected_urls if url and url not in valid_urls)
        flags = list(state.get("confidence_flags", []))
        retry = state.get("retry_count", 0)
        if len(valid) < 3 and retry >= 2:
            flags.append(
                f"Only {len(valid)} validated products found after three search passes; "
                "human input is recommended."
            )
        if not state.get("raw_products") and retry >= 2:
            flags.append("Product search returned no results; no products were fabricated.")
        return {
            "validated_products": valid,
            "rejected_product_urls": sorted(rejected_urls),
            "retry_count": retry + 1 if len(valid) < 3 else retry,
            "confidence_flags": flags,
            "workflow_metrics": self._metric(
                state, "validate_products", started, "http", len(candidates)
            ),
        }

    async def rank_gifts(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        products = state.get("validated_products", [])
        if not products:
            return {
                "ranked_gifts": [],
                "workflow_metrics": self._metric(state, "rank_gifts", started, "llm", 0),
            }
        system = """Rank up to three supplied products as professional gifts. Return only a JSON
array. Each item must have rank, gift_name, product_url, store, estimated_price,
why_this_gift, personalisation_reasoning, confidence_score (0..1), risk_level
(low/medium/high), assumptions. Product identity and URL MUST be copied exactly from
the supplied validated_products. Ground every reason in a supplied profile signal.
Never mention sensitive traits. If evidence is weak, reduce confidence and state that
as an assumption. Do not add a personalised_message field."""
        payload = {
            "validated_products": products,
            "profile_signals": state["profile_signals"],
            "review_feedback": state.get("review_feedback"),
            "confidence_flags": state.get("confidence_flags", []),
            "relationship_context": state["contact"].get("relationship_context"),
        }
        try:
            response = await self.llm.generate(system, payload)
            allowed_urls = {self._norm_url(item["url"]) for item in products}
            gifts = []
            for item in response[:3] if isinstance(response, list) else []:
                item["personalised_message"] = ""
                gift = RankedGift.model_validate(item)
                safety_text = " ".join(
                    [gift.why_this_gift, gift.personalisation_reasoning, *gift.assumptions]
                )
                if (
                    self._norm_url(str(gift.product_url)) in allowed_urls
                    and not SENSITIVE_SIGNAL.search(safety_text)
                ):
                    gifts.append(gift.model_dump(mode="json"))
            if not gifts:
                raise LLMError("No grounded ranked gifts returned")
        except (LLMError, ValidationError, TypeError):
            gifts = self._fallback_ranking(state)
        return {
            "ranked_gifts": gifts,
            "workflow_metrics": self._metric(state, "rank_gifts", started, "llm", 1),
        }

    def _fallback_ranking(self, state: GiftWorkflowState) -> list[dict]:
        signal = next(iter(state["profile_signals"].get("strong_signals", [])), None)
        gifts = []
        for rank, product in enumerate(state["validated_products"][:3], 1):
            assumptions = [] if signal else ["Profile signals are weak; human confirmation advised."]
            gifts.append(
                RankedGift(
                    rank=rank,
                    gift_name=product["name"],
                    product_url=product["url"],
                    store=product.get("store", ""),
                    estimated_price=str(product.get("price", "Price verified by retailer")),
                    why_this_gift=(
                        f"Connects to the explicit professional signal: {signal}."
                        if signal else "A broadly appropriate professional gift."
                    ),
                    personalisation_reasoning=(
                        f"Grounded in the profile signal '{signal}'."
                        if signal else "No strong personalisation claim is made."
                    ),
                    confidence_score=0.65 if signal else 0.35,
                    risk_level="low" if signal else "medium",
                    assumptions=assumptions,
                ).model_dump(mode="json")
            )
        return gifts

    async def generate_messages(self, state: GiftWorkflowState) -> dict:
        started = perf_counter()
        messages: dict[str, str] = {}
        for index, gift in enumerate(state.get("ranked_gifts", [])):
            system = """Write one short, warm, professional gift note. Return only JSON:
{"message":"..."}. Use no sensitive traits and make no unsupported claims."""
            try:
                response = await self.llm.generate(
                    system,
                    {
                        "gift": gift,
                        "occasion": state["contact"]["gift_context"]["occasion"],
                        "tone": state["contact"]["gift_context"].get(
                            "message_tone", "warm professional"
                        ),
                        "relationship_context": state["contact"].get("relationship_context"),
                    },
                )
                message = str(response["message"]).strip()
                if SENSITIVE_SIGNAL.search(message):
                    raise LLMError("Generated message referenced a sensitive trait")
            except (LLMError, KeyError, TypeError):
                message = (
                    f"Wishing you all the best for "
                    f"{state['contact']['gift_context']['occasion']}. "
                    "I hope you enjoy this small token of appreciation."
                )
            messages[str(index)] = message
            gift["personalised_message"] = message
        return {
            "personalised_messages": messages,
            "ranked_gifts": state.get("ranked_gifts", []),
            "review_status": "pending_review",
            "workflow_metrics": self._metric(
                state, "generate_messages", started, "llm", len(state.get("ranked_gifts", []))
            ),
        }

    async def human_review(self, state: GiftWorkflowState) -> dict:
        return {"review_status": state.get("review_status", "pending_review")}

    async def finalize(self, state: GiftWorkflowState) -> dict:
        output = {
            "contact_name": state["contact"]["name"],
            "profile_signals": state["profile_signals"],
            "search_trace": {
                "queries_used": state.get("search_queries", []),
                "products_considered_count": len(state.get("raw_products", [])),
            },
            "recommended_gifts": state.get("ranked_gifts", []),
            "human_review": {
                "status": state["review_status"],
                "available_actions": ["approve", "reject", "edit", "regenerate"],
            },
            "workflow_metrics": state.get("workflow_metrics", []),
        }
        return {"final_output": output}

    def _metric(
        self,
        state: GiftWorkflowState,
        node: str,
        started: float,
        tool: str,
        calls: int,
    ) -> list[dict]:
        metrics = list(state.get("workflow_metrics", []))
        metrics.append(
            {
                "node": node,
                "tool": tool,
                "calls": calls,
                "latency_ms": round((perf_counter() - started) * 1000, 1),
            }
        )
        return metrics

    def _expand_product_queries(self, state: GiftWorkflowState, queries: list[str]) -> list[str]:
        context = state["contact"]["gift_context"]
        signals_text = " ".join(
            str(item)
            for bucket in ("strong_signals", "weak_signals")
            for item in state.get("profile_signals", {}).get(bucket, [])
        ).lower()
        role_text = " ".join(
            str(state["contact"].get(key, ""))
            for key in ("role", "company", "about")
        ).lower()
        budget = f"{context['currency']} {int(context['budget_min'])} {int(context['budget_max'])}"
        country = context["country"]
        additions: list[str] = []
        if "cricket" in signals_text:
            additions.extend(
                [
                    f"cricket ball inspired card holder product page {country} {budget}",
                    f"luxury cricket themed corporate gift product {country} {budget}",
                    f"premium cricket corporate gift set product page {country} {budget}",
                    f"executive cricket gift set product page {country} {budget}",
                ]
            )
        if any(term in signals_text or term in role_text for term in ("sales", "saas", "revenue")):
            additions.extend(
                [
                    f"executive desk gift product page {country} {budget} sales leader",
                    f"premium business card holder gift product page {country} {budget}",
                    f"revenue leadership book professional gift product page {country} {budget}",
                ]
            )
        if state.get("retry_count", 0) > 0:
            additions.extend(
                [
                    f"luxury pen gift set product page {country} {budget} executive",
                    f"premium corporate gift product page {country} {budget}",
                ]
            )
        expanded: list[str] = []
        for query in [*queries, *additions]:
            query = str(query).strip()
            if not query or SENSITIVE_SIGNAL.search(query):
                continue
            if context["currency"] not in query:
                query = f"{query} {budget}"
            if country.lower() not in query.lower():
                query = f"{query} {country}"
            if query not in expanded:
                expanded.append(query)
        return expanded[:6]

    def _norm_url(self, url: str) -> str:
        return str(url).rstrip("/")

    def _looks_like_product_url(self, url: str) -> bool:
        return bool(re.search(r"/(?:product|products|dp|listing|p)/", str(url), re.I))
