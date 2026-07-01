# Evaluation

Use a versioned set of synthetic and consented professional profiles, with human
raters blind to the generation method.

| Dimension | Concrete check |
|---|---|
| Relevance | Raters score each gift 1–5 and identify the exact supporting signal. |
| Link validity | Re-run HTTP resolution and verify product identity on the landing page. |
| Budget/country | Assert parsed price is in range; manually test delivery country at checkout. |
| Professional fit | Two raters flag intimate, sized, romantic, or otherwise awkward gifts. |
| Sensitive inference | Seed profiles with protected-trait references and require zero use in signals, reasons, or notes. |
| Message quality | Score warmth, professionalism, brevity, and unsupported claims. |
| Poor search | Simulate zero results, timeouts, broken links, and missing prices; require empty/short lists with flags, never invented URLs. |

Suggested release gates: 100% recommended URLs appear in the validated candidate set;
100% resolve during evaluation; 0 sensitive-trait references; 0 out-of-budget parsed
prices; at least 90% human professional-appropriateness approval. Track relevance and
message scores by signal strength so weak profiles do not hide overconfident output.

Regression cases should include malformed Claude JSON (one retry then fallback), two
failed search retries, duplicate URLs, redirects, retailer blocks, edit/approve paths,
rejection, and regeneration with feedback.

