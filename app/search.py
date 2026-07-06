import re
from typing import Any
from urllib.parse import urlparse

import httpx
from tavily import AsyncTavilyClient

from app.config import Settings

SENSITIVE_OR_INAPPROPRIATE = re.compile(
    r"\b(religion|religious|politic|political|health|medical|ethnicity|race|"
    r"caste|gender|sexual orientation|disability|disabled|family status|"
    r"lingerie|underwear|perfume|cologne|jewelry|jewellery|"
    r"ring size|shirt size|dress size)\b",
    re.IGNORECASE,
)
PRICE_PATTERN = re.compile(
    r"(?P<currency>₹|\$|€|£|INR|USD|EUR|GBP)\s*(?P<amount>[\d,]+(?:\.\d{1,2})?)",
    re.I,
)
NON_RETAIL_DOMAINS = {
    "youtube.com", "youtu.be", "reddit.com", "linkedin.com", "instagram.com",
    "facebook.com", "medium.com", "goodreads.com", "rtings.com", "dzone.com",
    "pinterest.com", "tiktok.com", "x.com", "wikipedia.org",
}
COUNTRY_TLDS = {
    "India": ".in",
    "United States": ".com",
    "USA": ".com",
    "United Kingdom": ".co.uk",
    "UK": ".co.uk",
    "Australia": ".com.au",
    "Canada": ".ca",
}
UNAVAILABLE_PATTERN = re.compile(
    r"\b(currently unavailable|out of stock|sold out|no longer available)\b", re.I
)
NON_PRODUCT_PATH = re.compile(
    r"/(?:blogs?|news|articles?|collections?|product-category|category|search|"
    r"by-price|archives?|tags?)(?:/|$)",
    re.I,
)
LISTICLE_OR_GUIDE_TITLE = re.compile(
    r"\b(\d+\s+best|best\s+.+\s+under|gift\s+ideas|guide\s+to|ultimate\s+guide|"
    r"review|top\s+\d+|how\s+to)\b",
    re.I,
)
PRODUCT_PATH_HINT = re.compile(r"/(?:product|products|dp|gp/product|listing|p)/", re.I)
HTML_PRICE_PATTERNS = (
    re.compile(
        r'(?:product:price:amount|itemprop=["\']price["\'])[^>]{0,160}'
        r'(?:content|value)=["\']([\d,]+(?:\.\d{1,2})?)',
        re.I,
    ),
    re.compile(r'"price"\s*:\s*"?([\d,]+(?:\.\d{1,2})?)"?', re.I),
)
HTML_CURRENCY_PATTERN = re.compile(
    r'(?:priceCurrency|product:price:currency)["\']?\s*(?::|content=)\s*["\']([A-Z]{3})',
    re.I,
)
CURRENCY_CODES = {"₹": "INR", "$": "USD", "€": "EUR", "£": "GBP"}


class ProductSearcher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = AsyncTavilyClient(settings.tavily_api_key) if settings.tavily_api_key else None

    async def search(self, query: str, country: str) -> list[dict[str, Any]]:
        if not self.client:
            return []
        response = await self.client.search(
            query=f"{query} buy online {country}",
            search_depth="advanced",
            max_results=10,
            include_domains=None,
        )
        products = []
        for result in response.get("results", []):
            text = f"{result.get('title', '')} {result.get('content', '')}"
            if LISTICLE_OR_GUIDE_TITLE.search(text):
                continue
            price_match = PRICE_PATTERN.search(text)
            currency = price_match.group("currency").upper() if price_match else None
            currency = CURRENCY_CODES.get(currency or "", currency)
            products.append(
                {
                    "name": result.get("title", "").strip(),
                    "url": result.get("url", ""),
                    "price": (
                        float(price_match.group("amount").replace(",", ""))
                        if price_match else None
                    ),
                    "currency": currency,
                    "store": urlparse(result.get("url", "")).netloc.removeprefix("www."),
                    "description": result.get("content", "")[:800],
                    "country": country,
                }
            )
        return products

    async def url_resolves(self, url: str) -> bool:
        return await self.inspect_product({"url": url}) is not None

    async def inspect_product(self, product: dict[str, Any]) -> dict[str, Any] | None:
        url = str(product.get("url", ""))
        parsed = urlparse(url)
        host = parsed.netloc.lower().removeprefix("www.")
        clean_path = parsed.path.strip("/").lower()
        if not clean_path or clean_path == "s" or NON_PRODUCT_PATH.search(parsed.path):
            return None
        if LISTICLE_OR_GUIDE_TITLE.search(str(product.get("name", ""))):
            return None
        if ("amazon." in host and "/dp/" not in parsed.path and "/gp/product/" not in parsed.path):
            return None
        if ("flipkart." in host and "/p/" not in parsed.path):
            return None
        if not PRODUCT_PATH_HINT.search(parsed.path):
            return None
        if parsed.query.lower().startswith(("s=", "q=", "k=")):
            return None
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=min(self.settings.url_validation_timeout, 4.0),
                headers={"User-Agent": "Mozilla/5.0 GiftRecommendationValidator/1.0"},
            ) as client:
                response = await client.get(url)
                if response.status_code >= 400 or response.url.scheme not in {"http", "https"}:
                    return None
                body = response.text[:250_000]
                if UNAVAILABLE_PATTERN.search(body):
                    return None
                enriched = dict(product)
                if enriched.get("price") is None:
                    for pattern in HTML_PRICE_PATTERNS:
                        match = pattern.search(body)
                        if match:
                            enriched["price"] = float(match.group(1).replace(",", ""))
                            break
                if not enriched.get("currency"):
                    currency = HTML_CURRENCY_PATTERN.search(body)
                    if currency:
                        enriched["currency"] = currency.group(1).upper()
                enriched["url"] = str(response.url)
                return enriched
        except httpx.HTTPError:
            return None


def is_professionally_safe(product: dict[str, Any]) -> bool:
    text = " ".join(
        str(product.get(field, "")) for field in ("name", "description", "store")
    )
    store = str(product.get("store", "")).lower().removeprefix("www.")
    is_non_retail = any(store == domain or store.endswith(f".{domain}") for domain in NON_RETAIL_DOMAINS)
    return not is_non_retail and not bool(SENSITIVE_OR_INAPPROPRIATE.search(text))


def country_fit(product: dict[str, Any], country: str) -> bool:
    """Reject links clearly targeting a different national storefront."""
    host = str(product.get("store", "")).lower()
    expected = COUNTRY_TLDS.get(country)
    explicit_country_suffixes = {".in", ".co.uk", ".com.au", ".ca"}
    present = next((suffix for suffix in explicit_country_suffixes if host.endswith(suffix)), None)
    return present is None or expected is None or present == expected
