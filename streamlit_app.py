from __future__ import annotations

import argparse
import json
from pathlib import Path

import streamlit as st

from tools.research.agent import execute_research
from tools.research.config import build_config
from tools.research.memory import ResearchMemory
from tools.research.report import export_model_report, export_report


st.set_page_config(page_title="Autonomous Research Agent", page_icon="📊", layout="wide", initial_sidebar_state="expanded")


BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / ".tmp" / "reports"
MEMORY_DB = BASE_DIR / ".tmp" / "research_memory" / "research.db"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _env_value(name: str, default: str = "") -> str:
    return st.session_state.get(name, default) or default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_value(name, str(default)))
    except Exception:
        return default


def _env_csv(name: str, default: str = "") -> str:
    value = _env_value(name, default)
    return value.strip()


def _build_args(query: str, depth: str, output: str, max_sources: int, max_iterations: int, max_concurrency: int, request_timeout: int, search_results_per_query: int, user_agent: str, allowed_domains: str, blocked_domains: str, freshness_days: str, run_label: str, dry_run: bool, self_test: bool) -> argparse.Namespace:
    return argparse.Namespace(
        query=query,
        depth=depth,
        output=output,
        output_dir=str(REPORT_DIR),
        memory_db=str(MEMORY_DB),
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        freshness_days=int(freshness_days) if str(freshness_days).strip() else None,
        max_sources=max_sources,
        max_iterations=max_iterations,
        max_concurrency=max_concurrency,
        request_timeout=request_timeout,
        search_results_per_query=search_results_per_query,
        user_agent=user_agent,
        run_label=run_label,
        self_test=self_test,
        dry_run=dry_run,
        json=False,
    )


def _run_research(
    query: str,
    depth: str,
    output: str,
    model_name: str,
    model_base_url: str,
    model_http_referer: str,
    model_title: str,
    search_backend: str,
    max_sources: int,
    max_iterations: int,
    max_concurrency: int,
    request_timeout: int,
    search_results_per_query: int,
    user_agent: str,
    allowed_domains: str,
    blocked_domains: str,
    freshness_days: str,
    run_label: str,
    dry_run: bool,
    self_test: bool,
) -> dict[str, object]:
    server_api_key = env.get("OPENROUTER_API_KEY", "")
    env_overrides = {
        "MODEL_NAME": model_name,
        "OPENROUTER_API_KEY": server_api_key,
        "MODEL_BASE_URL": model_base_url,
        "MODEL_HTTP_REFERER": model_http_referer,
        "MODEL_TITLE": model_title,
        "SEARCH_BACKEND": search_backend,
    }
    args = _build_args(
        query=query,
        depth=depth,
        output=output,
        max_sources=max_sources,
        max_iterations=max_iterations,
        max_concurrency=max_concurrency,
        request_timeout=request_timeout,
        search_results_per_query=search_results_per_query,
        user_agent=user_agent,
        allowed_domains=allowed_domains,
        blocked_domains=blocked_domains,
        freshness_days=freshness_days,
        run_label=run_label,
        dry_run=dry_run,
        self_test=self_test,
    )
    config = build_config(args, env_overrides=env_overrides)
    memory = ResearchMemory(config.memory_db_path)
    try:
        run = execute_research(config, memory)
        if run.final_report_markdown:
            artifacts = export_model_report(run, config.output_dir, config.output_formats, run.final_report_markdown)
        else:
            artifacts = export_report(run, config.output_dir, config.output_formats)
        run.artifacts.update(artifacts)
        memory.save_run(run)
        memory.save_context(query, {"run_id": run.run_id, "summary": run.insights[:5], "artifacts": run.artifacts, "plan": run.research_plan, "report_source": run.report_source})
        report_path = Path(run.artifacts.get("markdown", artifacts.get("markdown", "")))
        pdf_path = Path(run.artifacts.get("pdf", artifacts.get("pdf", "")))
        report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        return {
            "run_id": run.run_id,
            "query": run.query,
            "depth": config.depth,
            "output_formats": list(config.output_formats),
            "status": run.status,
            "report_source": run.report_source,
            "sources": len(run.pages),
            "insights": len(run.insights),
            "gaps": run.gaps,
            "search_queries": run.search_queries,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "artifacts": {k: str(v) for k, v in run.artifacts.items()},
            "report_text": report_text,
            "pdf_name": pdf_path.name if pdf_path.exists() else "",
            "markdown_name": report_path.name if report_path.exists() else "",
            "pdf_bytes": pdf_path.read_bytes() if pdf_path.exists() else b"",
            "markdown_bytes": report_path.read_bytes() if report_path.exists() else b"",
            "source_rows": [
                {
                    "Source": page.title,
                    "Domain": page.domain,
                    "Credibility": f"{page.credibility_score:.2f}",
                    "Quality": f"{page.quality_score:.2f}",
                    "Summary": page.summary,
                    "URL": page.final_url or page.url,
                }
                for page in run.pages[:8]
            ],
            "insight_rows": [
                {
                    "Finding": insight.text,
                    "Category": insight.category,
                    "Confidence": f"{insight.confidence:.2f}",
                    "Novelty": f"{insight.novelty:.2f}",
                }
                for insight in run.insights[:12]
            ],
        }
    finally:
        memory.close()


def _coerce_int(value: object, default: int) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _option_index(options: list[str], value: str, default: int = 0) -> int:
    try:
        return options.index(value)
    except ValueError:
        return default


def _seed_session_defaults(env: dict[str, str]) -> None:
    defaults = {
        "query": "AI agent platforms 2026",
        "depth": env.get("RESEARCH_DEPTH", "deep"),
        "output": env.get("RESEARCH_OUTPUT_FORMATS", "markdown,pdf"),
        "model_name": env.get("MODEL_NAME", "openai/gpt-oss-20b:free"),
        "model_base_url": env.get("MODEL_BASE_URL", "https://openrouter.ai/api/v1"),
        "model_http_referer": env.get("MODEL_HTTP_REFERER", ""),
        "model_title": env.get("MODEL_TITLE", "Autonomous Research Agent"),
        "search_backend": env.get("SEARCH_BACKEND", "bing_rss"),
        "allowed_domains": env.get("ALLOWED_DOMAINS", ""),
        "blocked_domains": env.get("BLOCKED_DOMAINS", ""),
        "freshness_days": env.get("FRESHNESS_DAYS", ""),
        "user_agent": env.get("USER_AGENT", "AutonomousResearchAgent/1.0"),
        "run_label": env.get("RUN_LABEL", ""),
        "max_sources": _coerce_int(env.get("MAX_SEARCH_RESULTS"), 8),
        "max_iterations": _coerce_int(env.get("MAX_RESEARCH_ITERATIONS"), 3),
        "max_concurrency": _coerce_int(env.get("MAX_CONCURRENCY"), 4),
        "request_timeout": _coerce_int(env.get("REQUEST_TIMEOUT_SECONDS"), 20),
        "search_results_per_query": _coerce_int(env.get("MAX_SEARCH_RESULTS"), 8),
        "dry_run": False,
        "self_test": False,
        "latest_run": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


env = _load_env_file(BASE_DIR / ".env")
_seed_session_defaults(env)

st.markdown(
    """
    <style>
      .block-container { padding-top: 1.6rem; padding-bottom: 2rem; max-width: 1440px; }
      [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #f8fbff 0%, #edf4fb 100%);
        border-right: 1px solid rgba(148,163,184,.18);
      }
      .sidebar-brand {
        padding: 1rem 1rem 1.1rem;
        margin-bottom: 1rem;
        border-radius: 18px;
        background: linear-gradient(135deg, rgba(22, 50, 79, .96), rgba(37, 99, 235, .88));
        color: white;
        box-shadow: 0 18px 36px rgba(15, 23, 42, .18);
      }
      .sidebar-brand h2 { margin: 0 0 .35rem 0; font-size: 1.15rem; }
      .sidebar-brand p { margin: 0; font-size: .85rem; line-height: 1.45; color: rgba(255,255,255,.82); }
      .hero {
        display: flex;
        justify-content: space-between;
        gap: 1rem;
        align-items: flex-end;
        padding: 1.35rem 1.5rem;
        border: 1px solid rgba(148,163,184,.22);
        border-radius: 24px;
        background: linear-gradient(135deg, rgba(255,255,255,.98), rgba(242,246,252,.96));
        box-shadow: 0 18px 42px rgba(15,23,42,.08);
      }
      .hero h1 { margin: 0 0 .35rem 0; font-size: clamp(2rem, 3.8vw, 3rem); letter-spacing: -0.03em; }
      .hero p { margin: 0; color: #4b6075; line-height: 1.6; max-width: 70ch; }
      .hero-meta { display: flex; gap: .6rem; flex-wrap: wrap; justify-content: flex-end; }
      .pill {
        padding: .55rem .8rem;
        border-radius: 999px;
        border: 1px solid rgba(148,163,184,.28);
        background: white;
        color: #334e68;
        font-size: .82rem;
        box-shadow: 0 8px 18px rgba(15,23,42,.05);
      }
      .panel {
        border: 1px solid rgba(148,163,184,.22);
        border-radius: 22px;
        background: rgba(255,255,255,.92);
        box-shadow: 0 12px 30px rgba(15,23,42,.06);
        padding: 1rem 1.1rem;
      }
      .panel h3 { margin: 0 0 .75rem 0; font-size: 1.02rem; letter-spacing: -0.01em; }
      .subtle { color: #627d98; font-size: .9rem; }
      .stat-grid { display: grid; grid-template-columns: repeat(4, minmax(0,1fr)); gap: .9rem; }
      .preview-box {
        border: 1px solid rgba(148,163,184,.22);
        border-radius: 20px;
        background: #f8fafc;
        color: #102a43;
        padding: 1rem 1.1rem;
        box-shadow: 0 12px 30px rgba(15,23,42,.06);
      }
      .preview-box pre {
        margin: 0;
        white-space: pre-wrap;
        word-break: break-word;
        font-size: 13px;
        line-height: 1.6;
      }
      .empty-state {
        border: 1px dashed rgba(148,163,184,.35);
        border-radius: 20px;
        background: linear-gradient(180deg, #ffffff, #f8fafc);
        padding: 1.2rem;
        color: #52606d;
      }
      .section-spacer { height: 1rem; }
      .artifact-stack { display: grid; gap: .8rem; }
      .stDataFrame, [data-testid="stDataFrame"] { border-radius: 16px; }
    </style>
    """,
    unsafe_allow_html=True,
)

with st.sidebar:
    st.markdown(
        """
        <div class="sidebar-brand">
          <h2>Control Panel</h2>
          <p>Adjust model settings, filters, and execution controls without editing the env file.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("research_controls", clear_on_submit=False):
        st.markdown("#### Research")
        st.text_area("Query", key="query", height=110, placeholder="Enter a research topic")
        st.selectbox("Depth", ["quick", "standard", "deep", "exhaustive"], index=_option_index(["quick", "standard", "deep", "exhaustive"], str(st.session_state.depth), 2), key="depth")
        st.selectbox("Output", ["markdown", "markdown,pdf", "pdf"], index=_option_index(["markdown", "markdown,pdf", "pdf"], str(st.session_state.output), 1), key="output")

        st.divider()
        st.markdown("#### Model")
        st.text_input("OpenRouter model", key="model_name")
        st.text_input("Model base URL", key="model_base_url")
        st.text_input("HTTP referer", key="model_http_referer")
        st.text_input("App title", key="model_title")
        st.selectbox("Search backend", ["bing_rss", "duckduckgo_html", "auto"], index=_option_index(["bing_rss", "duckduckgo_html", "auto"], str(st.session_state.search_backend), 0), key="search_backend")
        if env.get("OPENROUTER_API_KEY", "").strip():
            st.success("OpenRouter key loaded from local environment.")
        else:
            st.warning("OpenRouter key is not set. The dashboard will fall back to deterministic reports.")

        st.divider()
        st.markdown("#### Filters")
        st.text_area("Allowed domains", key="allowed_domains", placeholder="nytimes.com, nature.com")
        st.text_area("Blocked domains", key="blocked_domains", placeholder="pinterest.com, reddit.com")
        st.text_input("Freshness days", key="freshness_days", placeholder="Optional")
        st.text_input("User agent", key="user_agent")
        st.text_input("Run label", key="run_label")

        st.divider()
        st.markdown("#### Execution")
        st.slider("Max sources", 1, 20, key="max_sources")
        st.slider("Max iterations", 1, 6, key="max_iterations")
        st.slider("Max concurrency", 1, 12, key="max_concurrency")
        st.slider("Request timeout (sec)", 5, 60, key="request_timeout")
        st.slider("Search results / query", 3, 20, key="search_results_per_query")
        st.checkbox("Dry run", key="dry_run")
        st.checkbox("Self test", key="self_test")
        submitted = st.form_submit_button("Run workflow", use_container_width=True, type="primary")

latest_run = st.session_state.get("latest_run") or {}

if submitted:
    if not str(st.session_state.query).strip() and not st.session_state.self_test:
        st.sidebar.error("Query is required unless self test is enabled.")
    else:
        with st.spinner("Running research workflow..."):
            latest_run = _run_research(
                query=str(st.session_state.query),
                depth=str(st.session_state.depth),
                output=str(st.session_state.output),
                model_name=str(st.session_state.model_name),
                model_base_url=str(st.session_state.model_base_url),
                model_http_referer=str(st.session_state.model_http_referer),
                model_title=str(st.session_state.model_title),
                search_backend=str(st.session_state.search_backend),
                max_sources=_coerce_int(st.session_state.max_sources, 8),
                max_iterations=_coerce_int(st.session_state.max_iterations, 3),
                max_concurrency=_coerce_int(st.session_state.max_concurrency, 4),
                request_timeout=_coerce_int(st.session_state.request_timeout, 20),
                search_results_per_query=_coerce_int(st.session_state.search_results_per_query, 8),
                user_agent=str(st.session_state.user_agent),
                allowed_domains=str(st.session_state.allowed_domains),
                blocked_domains=str(st.session_state.blocked_domains),
                freshness_days=str(st.session_state.freshness_days),
                run_label=str(st.session_state.run_label),
                dry_run=bool(st.session_state.dry_run),
                self_test=bool(st.session_state.self_test),
            )
        st.session_state.latest_run = latest_run
        st.success("Research run completed.")

run_status = latest_run.get("status", "ready")
run_sources = latest_run.get("sources", 0)
run_insights = latest_run.get("insights", 0)
run_source = latest_run.get("report_source", "pending")
run_id = latest_run.get("run_id", "Not run yet")

st.markdown(
    f"""
    <div class="hero">
      <div>
        <h1>Autonomous Research Agent</h1>
        <p>Professional research dashboard with sidebar controls, local execution, report preview, and one-click PDF download.</p>
      </div>
      <div class="hero-meta">
        <div class="pill">Status: {run_status}</div>
        <div class="pill">Sources: {run_sources}</div>
        <div class="pill">Insights: {run_insights}</div>
        <div class="pill">Report: {run_source}</div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

st.markdown("#### Dashboard")
metric_cols = st.columns(4)
metric_cols[0].metric("Run status", run_status)
metric_cols[1].metric("Run ID", run_id if run_id != "Not run yet" else "Ready")
metric_cols[2].metric("Sources", run_sources)
metric_cols[3].metric("Insights", run_insights)

st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)

tab_overview, tab_preview, tab_artifacts = st.tabs(["Overview", "Report Preview", "Artifacts"])

with tab_overview:
    left, right = st.columns([1.05, 0.95], gap="large")
    with left:
        with st.container(border=True):
            st.markdown("### Run Summary")
            if latest_run:
                st.write(f"**Query:** {latest_run.get('query', '')}")
                st.write(f"**Depth:** {latest_run.get('depth', '')}")
                st.write(f"**Started:** {latest_run.get('started_at', '')}")
                st.write(f"**Finished:** {latest_run.get('finished_at', '')}")
                if latest_run.get("gaps"):
                    st.markdown("**Gaps**")
                    for gap in latest_run.get("gaps", []):
                        st.write(f"- {gap}")
            else:
                st.markdown('<div class="empty-state">Run a query from the sidebar to generate a report, inspect the sources, and download the PDF output.</div>', unsafe_allow_html=True)
    with right:
        with st.container(border=True):
            st.markdown("### Active Controls")
            control_rows = [
                ("Model", st.session_state.model_name),
                ("Backend", st.session_state.search_backend),
                ("Allowed", st.session_state.allowed_domains or "None"),
                ("Blocked", st.session_state.blocked_domains or "None"),
                ("Freshness", st.session_state.freshness_days or "None"),
                ("User agent", st.session_state.user_agent),
            ]
            st.dataframe(
                {"Setting": [row[0] for row in control_rows], "Value": [row[1] for row in control_rows]},
                hide_index=True,
                use_container_width=True,
                height=236,
            )

    st.markdown('<div class="section-spacer"></div>', unsafe_allow_html=True)
    col_sources, col_insights = st.columns([1, 1], gap="large")
    with col_sources:
        with st.container(border=True):
            st.markdown("### Source Comparison")
            if latest_run.get("source_rows"):
                st.dataframe(latest_run["source_rows"], hide_index=True, use_container_width=True, height=300)
            else:
                st.caption("No sources yet. Run the workflow to populate this table.")
    with col_insights:
        with st.container(border=True):
            st.markdown("### Key Findings")
            if latest_run.get("insight_rows"):
                st.dataframe(latest_run["insight_rows"], hide_index=True, use_container_width=True, height=300)
            else:
                st.caption("No findings yet. The dashboard will summarize the strongest evidence here.")

with tab_preview:
    if latest_run.get("report_text"):
        with st.container(border=True):
            st.markdown("### Rendered Report")
            st.markdown(latest_run["report_text"])
    else:
        st.markdown(
            '<div class="empty-state">Run the workflow to preview the rendered markdown report here.</div>',
            unsafe_allow_html=True,
        )

with tab_artifacts:
    art_col_left, art_col_right = st.columns([0.95, 1.05], gap="large")
    with art_col_left:
        with st.container(border=True):
            st.markdown("### Downloads")
            pdf_name = latest_run.get("pdf_name", "")
            pdf_bytes = latest_run.get("pdf_bytes", b"")
            markdown_bytes = latest_run.get("markdown_bytes", b"")
            if pdf_name and pdf_bytes:
                st.download_button("Download PDF", data=pdf_bytes, file_name=pdf_name, mime="application/pdf", use_container_width=True)
            else:
                st.button("Download PDF", use_container_width=True, disabled=True)
            if markdown_bytes:
                st.download_button("Download Markdown", data=markdown_bytes, file_name=latest_run.get("markdown_name", "report.md"), mime="text/markdown", use_container_width=True)
            if latest_run:
                st.download_button(
                    "Download run JSON",
                    data=json.dumps({k: v for k, v in latest_run.items() if k not in {"report_text", "source_rows", "insight_rows"}}, indent=2, default=str).encode("utf-8"),
                    file_name=f"{latest_run.get('run_id', 'run')}.json",
                    mime="application/json",
                    use_container_width=True,
                )
    with art_col_right:
        with st.container(border=True):
            st.markdown("### Run Details")
            if latest_run:
                st.code(
                    json.dumps(
                        {
                            "run_id": latest_run.get("run_id"),
                            "status": latest_run.get("status"),
                            "report_source": latest_run.get("report_source"),
                            "pdf_name": latest_run.get("pdf_name"),
                            "markdown_name": latest_run.get("markdown_name"),
                            "artifacts": latest_run.get("artifacts", {}),
                        },
                        indent=2,
                        default=str,
                    ),
                    language="json",
                )
            else:
                st.caption("No run has been executed yet.")
