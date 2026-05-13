from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SearchResult:
    query: str
    title: str
    url: str
    display_url: str
    snippet: str
    rank: int
    domain: str
    credibility: float = 0.0
    relevance: float = 0.0
    score: float = 0.0
    source_type: str = "web"


@dataclass
class ResearchPlan:
    refined_query: str
    search_queries: list[str] = field(default_factory=list)
    focus_areas: list[str] = field(default_factory=list)
    preferred_source_types: list[str] = field(default_factory=list)
    avoid_terms: list[str] = field(default_factory=list)
    stop_conditions: list[str] = field(default_factory=list)
    report_angle: str = ""


@dataclass
class PageContent:
    url: str
    final_url: str
    domain: str
    title: str
    description: str
    author: str
    published_at: str
    text: str
    headings: list[str] = field(default_factory=list)
    word_count: int = 0
    quality_score: float = 0.0
    credibility_score: float = 0.0
    source_type: str = "web"
    fetched_at: str = ""
    summary: str = ""
    key_sentences: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status_code: int
    content_type: str
    body: bytes
    elapsed_seconds: float
    error: str = ""


@dataclass
class Insight:
    text: str
    category: str
    confidence: float
    source_urls: list[str] = field(default_factory=list)
    source_titles: list[str] = field(default_factory=list)
    novelty: float = 0.0


@dataclass
class ResearchRun:
    run_id: str
    query: str
    depth: str
    started_at: str
    finished_at: str = ""
    status: str = "running"
    search_queries: list[str] = field(default_factory=list)
    results: list[SearchResult] = field(default_factory=list)
    pages: list[PageContent] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)
    gaps: list[str] = field(default_factory=list)
    context_hits: list[dict[str, Any]] = field(default_factory=list)
    research_plan: dict[str, Any] = field(default_factory=dict)
    final_report_markdown: str = ""
    report_source: str = "deterministic"
    output_dir: Path | None = None
    artifacts: dict[str, str] = field(default_factory=dict)


@dataclass
class ResearchConfig:
    query: str
    depth: str = "standard"
    output_formats: tuple[str, ...] = ("markdown", "pdf")
    max_sources: int = 8
    max_iterations: int = 3
    max_concurrency: int = 4
    request_timeout_seconds: int = 20
    search_results_per_query: int = 8
    output_dir: Path = Path(".tmp/reports")
    memory_db_path: Path = Path(".tmp/research_memory/research.db")
    allowed_domains: tuple[str, ...] = ()
    blocked_domains: tuple[str, ...] = ()
    freshness_days: int | None = None
    user_agent: str = "AutonomousResearchAgent/1.0"
    search_backend: str = "duckduckgo_html"
    model_base_url: str = ""
    model_name: str = ""
    model_api_key: str = ""
    model_http_referer: str = ""
    model_title: str = "Autonomous Research Agent"
    chat_enabled: bool = False
    run_label: str = ""
    self_test: bool = False
    dry_run: bool = False
    root_dir: Path = Path(".")
