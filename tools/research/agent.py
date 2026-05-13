from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import requests

from .analysis import detect_gaps, extract_insights_from_pages, filter_sources, remove_redundant_pages, score_page_against_query, summarize_page
from .config import build_config
from .fetch import extract_page_content, fetch_url
from .llm import maybe_refine_query, synthesize_report_markdown
from .memory import ResearchMemory
from .models import PageContent, ResearchRun
from .report import export_model_report, export_report
from .search import build_search_queries, search_web
from .utils import now_utc_iso, slugify


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Autonomous web research agent")
    parser.add_argument("--query", required=False, default="", help="Research query")
    parser.add_argument("--depth", default="standard", choices=["quick", "standard", "deep", "exhaustive"], help="Research depth")
    parser.add_argument("--output", default="markdown,pdf", help="Comma-separated output formats")
    parser.add_argument("--output-dir", default="", help="Override output directory")
    parser.add_argument("--memory-db", default="", help="Override SQLite memory database path")
    parser.add_argument("--allowed-domains", default="", help="Comma-separated allowlist domains")
    parser.add_argument("--blocked-domains", default="", help="Comma-separated blocklist domains")
    parser.add_argument("--freshness-days", type=int, default=None, help="Optional freshness filter")
    parser.add_argument("--max-sources", type=int, default=0, help="Maximum sources to open")
    parser.add_argument("--max-iterations", type=int, default=0, help="Maximum research iterations")
    parser.add_argument("--max-concurrency", type=int, default=0, help="Maximum concurrent fetches")
    parser.add_argument("--request-timeout", type=int, default=0, help="Request timeout in seconds")
    parser.add_argument("--search-results-per-query", type=int, default=0, help="Search results per query")
    parser.add_argument("--user-agent", default="", help="Custom user agent")
    parser.add_argument("--run-label", default="", help="Custom run label")
    parser.add_argument("--self-test", action="store_true", help="Run without network access")
    parser.add_argument("--dry-run", action="store_true", help="Do not export files")
    parser.add_argument("--json", action="store_true", help="Emit run summary as JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.query and not args.self_test:
        parser.error("--query is required unless --self-test is set")

    config = build_config(args)
    memory = ResearchMemory(config.memory_db_path)
    try:
        run = execute_research(config, memory)
        if not config.dry_run:
            if run.final_report_markdown:
                artifacts = export_model_report(run, config.output_dir, config.output_formats, run.final_report_markdown)
            else:
                artifacts = export_report(run, config.output_dir, config.output_formats)
            run.artifacts.update(artifacts)
            memory.save_run(run)
        else:
            memory.save_run(run)
        emit_run_logs(run)
        summary = build_summary(run)
        if args.json:
            print(json.dumps(summary, indent=2, ensure_ascii=False))
        else:
            print(summary["report_path"])
            print(summary["pdf_path"])
            print(summary["markdown_path"])
        return 0
    finally:
        memory.close()


def execute_research(config, memory: ResearchMemory) -> ResearchRun:
    from dataclasses import asdict

    run_id = slugify(f"{config.query}-{now_utc_iso()}")
    started_at = now_utc_iso()
    run = ResearchRun(run_id=run_id, query=config.query, depth=config.depth, started_at=started_at, output_dir=config.output_dir)

    context_hits = memory.search_contexts(config.query, limit=5)
    run.context_hits = context_hits

    refined_query, research_plan, plan_source = maybe_refine_query(config, context_hits, allow_model=config.chat_enabled and not config.self_test)
    run.query = refined_query or config.query
    run.research_plan = {
        "source": plan_source,
        "refined_query": research_plan.refined_query,
        "search_queries": research_plan.search_queries,
        "focus_areas": research_plan.focus_areas,
        "preferred_source_types": research_plan.preferred_source_types,
        "avoid_terms": research_plan.avoid_terms,
        "stop_conditions": research_plan.stop_conditions,
        "report_angle": research_plan.report_angle,
    }
    search_queries = research_plan.search_queries or build_search_queries(run.query, config.depth)
    run.search_queries = search_queries

    if config.self_test:
        run.pages = build_self_test_pages(config.query)
        run.insights = extract_insights_from_pages(run.pages, config.query)
        run.gaps = detect_gaps(run.insights, run.query, run.pages)
        run.finished_at = now_utc_iso()
        run.status = "completed"
        memory.save_run(run)
        memory.save_context(config.query, {"run_id": run.run_id, "summary": "self-test", "pages": [asdict(page) for page in run.pages]})
        return run

    session = requests.Session()
    session.headers.update({"User-Agent": config.user_agent})

    search_limit = max(config.max_sources, config.search_results_per_query)
    results = search_web(session, run.query, config.depth, search_limit, timeout=config.request_timeout_seconds, backend=config.search_backend)
    run.results = results

    selected = filter_sources(results, config.allowed_domains, config.blocked_domains, config.max_sources)
    if config.freshness_days is not None:
        selected = apply_freshness_filter(selected, config.freshness_days)

    pages = fetch_selected_pages(session, selected, config.max_concurrency, config.request_timeout_seconds)
    pages = [page for page in pages if page.text and page.word_count >= 80]
    for page in pages:
        page.summary = summarize_page(page, config.query)

    pages = remove_redundant_pages(pages)
    ranked_pages = sorted(pages, key=lambda page: score_page_against_query(page, config.query), reverse=True)
    run.pages = ranked_pages

    for page in run.pages:
        memory.save_source(run.run_id, page)
        memory.save_note(run.run_id, "page_summary", page.summary, source_url=page.final_url, payload=asdict(page))

    insights = extract_insights_from_pages(run.pages, config.query)
    run.insights = insights[:30]
    for insight in run.insights:
        memory.save_insight(run.run_id, insight)

    run.gaps = detect_gaps(run.insights, run.query, run.pages)
    if should_iterate_more(run, config):
        extra_results = iterate_more(session, config, run)
        if extra_results:
            run.results.extend(extra_results)
            extra_selected = filter_sources(extra_results, config.allowed_domains, config.blocked_domains, config.max_sources)
            extra_pages = fetch_selected_pages(session, extra_selected, config.max_concurrency, config.request_timeout_seconds)
            run.pages = remove_redundant_pages(run.pages + extra_pages)
            run.insights = extract_insights_from_pages(run.pages, run.query)[:30]
            run.gaps = detect_gaps(run.insights, run.query, run.pages)

    run.finished_at = now_utc_iso()
    run.status = "completed"
    model_markdown, model_source = synthesize_report_markdown(config, run, allow_model=config.chat_enabled and not config.self_test)
    if model_markdown:
        run.final_report_markdown = model_markdown
        run.report_source = model_source
    else:
        run.final_report_markdown = ""
        run.report_source = "deterministic"
    memory.save_context(config.query, {"run_id": run.run_id, "summary": run.insights[:5], "artifacts": run.artifacts, "plan": run.research_plan, "report_source": run.report_source})
    memory.save_run(run)
    return run


def fetch_selected_pages(session: requests.Session, selected, max_concurrency: int, timeout: int) -> list[PageContent]:
    pages: list[PageContent] = []
    if not selected:
        return pages
    with ThreadPoolExecutor(max_workers=max(1, max_concurrency)) as executor:
        future_map = {executor.submit(fetch_url, session, result.url, timeout): result for result in selected}
        for future in as_completed(future_map):
            result = future_map[future]
            fetch_result = future.result()
            if fetch_result.error:
                continue
            page = extract_page_content(fetch_result)
            if not page.title:
                page.title = result.title
            page.credibility_score = max(page.credibility_score, result.credibility)
            page.summary = summarize_page(page, result.query)
            pages.append(page)
    return pages


def apply_freshness_filter(results, freshness_days: int):
    # We only have page dates after fetch, so this acts as a soft no-op prefetch filter.
    return list(results)


def should_iterate_more(run: ResearchRun, config) -> bool:
    if len(run.pages) < 3:
        return True
    if len(run.insights) < 5:
        return True
    if run.gaps and len(run.gaps) > 1 and config.max_iterations > 1:
        return True
    return False


def iterate_more(session: requests.Session, config, run: ResearchRun):
    extra_query = build_followup_query(run.query, run.insights, run.gaps)
    if not extra_query:
        return []
    return search_web(session, extra_query, config.depth, max(3, config.search_results_per_query // 2), timeout=config.request_timeout_seconds, backend=config.search_backend)


def build_followup_query(query: str, insights, gaps) -> str:
    if gaps:
        return f"{query} {gaps[0]}"
    if insights:
        top = insights[0].text.split(".")[0]
        return f"{query} {top}"
    return query


def build_self_test_pages(query: str) -> list[PageContent]:
    text1 = f"{query} is widely discussed in public sources. Analysts often compare official statements, independent coverage, and data-backed findings."
    text2 = f"A second source about {query} highlights implementation details, trade-offs, and limitations. It also provides practical context and summary conclusions."
    pages = [
        PageContent(
            url="https://example.com/source-one",
            final_url="https://example.com/source-one",
            domain="example.com",
            title=f"{query} overview",
            description=f"Overview of {query}",
            author="",
            published_at="",
            text=text1,
            headings=[f"{query} overview"],
            word_count=len(text1.split()),
            quality_score=0.7,
            credibility_score=0.4,
            summary=text1,
            key_sentences=[text1],
        ),
        PageContent(
            url="https://example.org/source-two",
            final_url="https://example.org/source-two",
            domain="example.org",
            title=f"{query} analysis",
            description=f"Analysis of {query}",
            author="",
            published_at="",
            text=text2,
            headings=[f"{query} analysis"],
            word_count=len(text2.split()),
            quality_score=0.75,
            credibility_score=0.45,
            summary=text2,
            key_sentences=[text2],
        ),
    ]
    for page in pages:
        page.summary = summarize_page(page, query)
    return pages


def build_summary(run: ResearchRun) -> dict:
    output_dir = run.output_dir or Path(".")
    report_path = run.artifacts.get("markdown") or str(output_dir / f"{run.run_id}.md")
    pdf_path = run.artifacts.get("pdf") or str(output_dir / f"{run.run_id}.pdf")
    markdown_path = run.artifacts.get("markdown") or report_path
    return {
        "run_id": run.run_id,
        "status": run.status,
        "report_path": report_path,
        "pdf_path": pdf_path,
        "markdown_path": markdown_path,
        "sources": len(run.pages),
        "insights": len(run.insights),
        "gaps": run.gaps,
        "report_source": run.report_source,
    }


def emit_run_logs(run: ResearchRun) -> None:
    events = [
        {"stage": "started", "run_id": run.run_id, "query": run.query},
        {"stage": "searched", "sources": len(run.results)},
        {"stage": "opened", "pages": len(run.pages)},
        {"stage": "insights", "count": len(run.insights)},
        {"stage": "finished", "status": run.status},
    ]
    for event in events:
        print(json.dumps(event, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())
