# Autonomous Research Workflow

## Objective

Produce a professional analyst-style report from a user research query using free public web sources, local extraction, local memory, and PDF export.

## Inputs

- `query`: research question or topic
- `depth`: `quick`, `standard`, `deep`, or `exhaustive`
- `output`: `markdown`, `pdf`, or both
- Optional `allowed_domains`
- Optional `blocked_domains`
- Optional `max_sources`

## Environment

Use `.env` for local defaults:

- `RESEARCH_OUTPUT_DIR`
- `RESEARCH_MEMORY_DB`
- `SEARCH_BACKEND`
- `MAX_CONCURRENCY`
- `REQUEST_TIMEOUT_SECONDS`
- `MAX_SEARCH_RESULTS`
- `MAX_RESEARCH_ITERATIONS`
- `USER_AGENT`
- Optional `MODEL_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`, `MODEL_HTTP_REFERER`, `MODEL_TITLE`
- Optional `OPENROUTER_API_KEY`

## Tooling

- `python tools/research_agent.py`
- Free Bing RSS search with DuckDuckGo fallback
- Local HTML fetch and extraction
- SQLite memory store
- Markdown and PDF report export
- Optional OpenRouter chat synthesis

## Flow

1. Refine the query into search variants.
2. Search the web and rank sources by relevance and credibility.
3. Open only the best sources and extract clean text.
4. Score page quality and discard thin or redundant pages.
5. Store notes, claims, and source metadata in local memory.
6. Retrieve prior relevant notes when present.
7. Compare sources, dedupe repeated claims, and detect gaps.
8. Continue iterating until coverage is sufficient or the depth limit is reached.
9. Optionally use the chat model to refine the research plan and synthesize the final narrative.
10. Generate an analyst report with executive summary, key findings, comparison table, limitations, and references.
11. Export markdown and PDF artifacts.

## Suggested Defaults

- `SEARCH_BACKEND=bing_rss`
- `MAX_SEARCH_RESULTS=8`
- `MAX_RESEARCH_ITERATIONS=2`
- `MAX_CONCURRENCY=4`
- `REQUEST_TIMEOUT_SECONDS=20`
- `MODEL_NAME=openai/gpt-oss-20b:free` if you want free OpenRouter synthesis

## Stop Conditions

- Enough credible sources have been analyzed
- New sources add little novelty
- Max iterations or max sources reached

## Output Files

- `report.md`
- `report.pdf`
- `notes.md`
- `sources.json`
- `run.json`
