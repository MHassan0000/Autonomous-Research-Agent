from __future__ import annotations

import argparse
import os
from pathlib import Path

from .models import ResearchConfig
from .utils import ensure_dir, load_env_file, parse_csv, parse_int, parse_optional_int, slugify

DEPTH_DEFAULTS = {
    "quick": {"max_sources": 4, "max_iterations": 1, "search_results_per_query": 5},
    "standard": {"max_sources": 8, "max_iterations": 2, "search_results_per_query": 8},
    "deep": {"max_sources": 12, "max_iterations": 3, "search_results_per_query": 10},
    "exhaustive": {"max_sources": 16, "max_iterations": 4, "search_results_per_query": 12},
}


def default_root_dir() -> Path:
    return Path(__file__).resolve().parents[2]


def build_config(args: argparse.Namespace) -> ResearchConfig:
    root_dir = default_root_dir()
    load_env_file(root_dir / ".env")

    depth = (args.depth or os.environ.get("RESEARCH_DEPTH") or "standard").strip().lower()
    if depth not in DEPTH_DEFAULTS:
        depth = "standard"

    defaults = DEPTH_DEFAULTS[depth]
    output_dir = Path(args.output_dir or os.environ.get("RESEARCH_OUTPUT_DIR") or root_dir / ".tmp" / "reports")
    memory_db_path = Path(args.memory_db or os.environ.get("RESEARCH_MEMORY_DB") or root_dir / ".tmp" / "research_memory" / "research.db")
    output_formats = tuple(item for item in (args.output or os.environ.get("RESEARCH_OUTPUT_FORMATS") or "markdown,pdf").split(",") if item.strip())

    config = ResearchConfig(
        query=args.query or "",
        depth=depth,
        output_formats=output_formats,
        max_sources=args.max_sources or parse_int(os.environ.get("MAX_SEARCH_RESULTS"), defaults["max_sources"]),
        max_iterations=args.max_iterations or parse_int(os.environ.get("MAX_RESEARCH_ITERATIONS"), defaults["max_iterations"]),
        max_concurrency=args.max_concurrency or parse_int(os.environ.get("MAX_CONCURRENCY"), 4),
        request_timeout_seconds=args.request_timeout or parse_int(os.environ.get("REQUEST_TIMEOUT_SECONDS"), 20),
        search_results_per_query=args.search_results_per_query or parse_int(os.environ.get("MAX_SEARCH_RESULTS"), defaults["search_results_per_query"]),
        output_dir=output_dir,
        memory_db_path=memory_db_path,
        allowed_domains=tuple(args.allowed_domains or parse_csv(os.environ.get("ALLOWED_DOMAINS"))),
        blocked_domains=tuple(args.blocked_domains or parse_csv(os.environ.get("BLOCKED_DOMAINS"))),
        freshness_days=args.freshness_days if args.freshness_days is not None else parse_optional_int(os.environ.get("FRESHNESS_DAYS")),
        user_agent=args.user_agent or os.environ.get("USER_AGENT", "AutonomousResearchAgent/1.0"),
        search_backend=os.environ.get("SEARCH_BACKEND", "duckduckgo_html"),
        model_base_url=os.environ.get("MODEL_BASE_URL", "").strip(),
        model_name=os.environ.get("MODEL_NAME", "").strip(),
        model_api_key=(os.environ.get("MODEL_API_KEY", "").strip() or os.environ.get("OPENROUTER_API_KEY", "").strip()),
        model_http_referer=os.environ.get("MODEL_HTTP_REFERER", "").strip(),
        model_title=os.environ.get("MODEL_TITLE", "Autonomous Research Agent").strip() or "Autonomous Research Agent",
        chat_enabled=bool(os.environ.get("OPENROUTER_API_KEY", "").strip() or os.environ.get("MODEL_API_KEY", "").strip()),
        run_label=args.run_label or slugify(args.query or "research"),
        self_test=bool(args.self_test),
        dry_run=bool(args.dry_run),
        root_dir=root_dir,
    )

    ensure_dir(config.output_dir)
    ensure_dir(config.memory_db_path.parent)
    return config
