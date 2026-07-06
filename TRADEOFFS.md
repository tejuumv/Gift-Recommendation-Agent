# Trade-offs and Known Limits

## Deliberate choices

- The system prefers fewer validated recommendations over fabricated or weakly
  grounded product links.
- Search retries accumulate the full trace so reviewers can see all queries and
  products considered.
- Product validation is conservative: product-like URL shape, country fit, HTTP
  reachability, parsed price, currency, availability text, and safety filtering are
  all checked before ranking.
- Human review remains mandatory before finalization.

## Limits

- Country delivery is inferred from storefront/domain/search targeting, not checkout.
- Inventory is checked only from page text such as “sold out” or “unavailable”; it is
  not a retailer-specific stock API check.
- Retailer pages frequently hide prices or block automated requests, which reduces
  recall.
- One search provider is wired in the prototype. More adapters would improve recall.
- The professional-safety filters are intentionally conservative and mostly English.
- Batch processing is synchronous until review; production should use a job queue.

