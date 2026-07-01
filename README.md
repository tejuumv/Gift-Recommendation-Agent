# Hyper-Personalised Gift Recommendation Agent

A FastAPI + LangGraph prototype that turns professional profile evidence into
reviewable gift recommendations. Products are sourced through Tavily, URL-checked,
budget-filtered, and professionally screened before the LLM is allowed to rank them.
The workflow never asks the model to invent a product or URL; if fewer than three
validated products are found, it returns the shorter list with a human-review flag.

## Setup

Python 3.11 or newer is required. A project-local virtual environment is already the
intended installation model:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Groq is the default LLM provider for inexpensive testing. Set `GROQ_API_KEY` and
`TAVILY_API_KEY` in `.env`; the default Groq model is `openai/gpt-oss-20b`.

Anthropic remains available: set `LLM_PROVIDER=anthropic`,
`ANTHROPIC_API_KEY`, and optionally `ANTHROPIC_MODEL`. Never commit `.env`.

Start the service:

```bash
source .venv/bin/activate
uvicorn main:app --reload
```

Open `http://127.0.0.1:8000` for the review dashboard or `/docs` for Swagger.
SQLite files are created under `data/`: `contacts.sqlite` is the API lookup store,
and `checkpoints.sqlite` is the separate LangGraph checkpoint store.

## Submit the Aarav Mehta example

The full assignment-style fixture is at `examples/aarav_mehta.json`. It uses nested
`linkedin_profile`, `relationship_context`, and `gift_context.message_tone` fields.

```bash
curl -X POST http://127.0.0.1:8000/contacts/batch \
  -H 'Content-Type: application/json' \
  --data @examples/aarav_mehta.json
```

The batch response contains a `contact_id`. Inspect and review it with:

```bash
curl http://127.0.0.1:8000/contacts
curl http://127.0.0.1:8000/contacts/CONTACT_ID
curl http://127.0.0.1:8000/contacts/CONTACT_ID/trace

curl -X POST http://127.0.0.1:8000/contacts/CONTACT_ID/review \
  -H 'Content-Type: application/json' \
  -d '{"action":"approve"}'
```

Review actions are `approve`, `reject`, `regenerate`, or `edit`. An edit request also
supplies `edited_gifts` in the ranked-gift schema. Regeneration can include `feedback`.

The latest checked sample output is saved at `examples/sample_output_aarav.json`.
With the strict final validator, the sample found one validated purchasable product
and explicitly flagged that human input is recommended instead of fabricating the
remaining two recommendations.

## Architecture

The graph runs:

`ingest → extract signals → generate queries → search → validate → rank → messages → review → finalize`

If validation yields fewer than three products, query generation broadens and retries
twice. The graph pauses immediately before `human_review`. The preceding node marks
the state `pending_review`; the review API updates that checkpoint and resumes it.
Approval/edit proceeds to finalization, rejection ends, and regeneration loops to
ranking.

External failures degrade safely. Signal extraction retries malformed JSON once and
then uses only explicit, non-sensitive profile topics. Missing/failed search yields
no recommendations and a confidence flag after the retries. Ranking receives only
validated candidates, and code rejects any returned URL not present in that set.

Product validation rejects common non-product shapes, including search pages,
category pages, blog/listicle pages, social/media domains, cross-country storefronts,
unavailable pages, sensitive/inappropriate categories, and parsed prices outside
the requested budget.

## API

- `POST /contacts/batch` validates and processes 1–100 contacts concurrently.
- `GET /contacts` lists persisted status rows.
- `GET /contacts/{id}` returns gifts, notes, flags, and final output.
- `GET /contacts/{id}/trace` exposes search and validation intermediates.
- `POST /contacts/{id}/review` updates and resumes the checkpoint.

## Tests

```bash
source .venv/bin/activate
pytest -q
```

Current local result: `13 passed, 1 warning`.

## Submission checklist

- Working prototype: FastAPI app plus browser dashboard.
- Official-schema input: `examples/aarav_mehta.json`.
- Sample output: `examples/sample_output_aarav.json`.
- Architecture note: `ARCHITECTURE.md`.
- Evaluation plan: `EVALUATION.md`.
- Trade-offs and known limits: `TRADEOFFS.md`.
- Tests: `tests/`.
- Local repository can be initialized with `git init && git add . && git commit`.

## Current trade-offs

- Search snippets and retailer pages often omit reliable prices. Those results are
  conservatively excluded, which improves budget correctness but can reduce recall.
- URL resolution proves that a page responds, not that inventory remains available.
- Country fit uses search targeting and metadata rather than checkout simulation.
- Product images and page semantics are not independently classified.
- One search provider and one LLM provider are supported.
- Batch requests wait for all workflows to reach review; a production version should
  use a durable job queue and return immediately.
- Professional-safety filtering is intentionally conservative and English-centric.

Future versions should add retailer adapters, currency conversion, inventory checks,
image validation, a policy classifier, richer edit controls, and provider failover.
