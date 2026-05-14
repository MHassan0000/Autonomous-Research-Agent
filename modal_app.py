import argparse
import os
from pathlib import Path

import modal

APP_NAME = "autonomous-research-agent"
IMAGE = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install_from_requirements("requirements.txt")
    .workdir("/root")
    .add_local_python_source("tools")
)

app = modal.App(APP_NAME, image=IMAGE)

REPORT_VOLUME = modal.Volume.from_name("research-agent-reports", create_if_missing=True)
MEMORY_VOLUME = modal.Volume.from_name("research-agent-memory", create_if_missing=True)

RESEARCH_MOUNT = "/data/research"
MEMORY_MOUNT = "/data/memory"


def _modal_secrets():
    env_keys = [
        "OPENROUTER_API_KEY",
        "MODEL_NAME",
        "MODEL_BASE_URL",
        "MODEL_HTTP_REFERER",
        "MODEL_TITLE",
        "SEARCH_BACKEND",
        "RESEARCH_OUTPUT_DIR",
        "MAX_CONCURRENCY",
        "REQUEST_TIMEOUT_SECONDS",
        "MAX_SEARCH_RESULTS",
        "MAX_RESEARCH_ITERATIONS",
        "USER_AGENT",
        "ALLOWED_DOMAINS",
        "BLOCKED_DOMAINS",
        "FRESHNESS_DAYS",
    ]
    return modal.Secret.from_dict(_load_local_env(env_keys))


def _load_local_env(keys: list[str]) -> dict[str, str]:
    env_path = Path(__file__).resolve().with_name(".env")
    values: dict[str, str] = {}
    file_values: dict[str, str] = {}
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            file_values[key] = value
    for key in keys:
        value = os.environ.get(key) or file_values.get(key)
        if value:
            values[key] = value
    return values


@app.function(
    image=IMAGE,
    secrets=[_modal_secrets()],
    volumes={RESEARCH_MOUNT: REPORT_VOLUME, MEMORY_MOUNT: MEMORY_VOLUME},
    timeout=60 * 60,
)
@modal.asgi_app()
def web_app():
    from fastapi import FastAPI, Request
    from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

    from tools.research.agent import execute_research
    from tools.research.config import build_config
    from tools.research.memory import ResearchMemory
    from tools.research.report import export_model_report, export_report

    app = FastAPI(title=APP_NAME)

    def _coerce_int(value, default: int) -> int:
        try:
            if value in (None, ""):
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    async def _read_payload(request: Request) -> dict[str, str]:
        payload: dict[str, str] = {}
        try:
            data = await request.json()
            if isinstance(data, dict):
                payload.update({str(k): str(v) for k, v in data.items() if v is not None})
        except Exception:
            pass
        if not payload:
            try:
                form = await request.form()
                payload.update({str(k): str(v) for k, v in form.items() if v is not None})
            except Exception:
                pass
        if not payload:
            payload.update({str(k): str(v) for k, v in request.query_params.items() if v is not None})
        return payload

    @app.get("/")
    def home():
        return HTMLResponse(
            """
            <html>
              <head>
                <title>Autonomous Research Agent</title>
                  <style>
                    :root {
                      color-scheme: light;
                      --bg: #f3f6fb;
                      --panel: rgba(255, 255, 255, 0.86);
                      --panel-border: rgba(148, 163, 184, 0.24);
                      --text: #102a43;
                      --muted: #627d98;
                      --accent: #16324f;
                      --accent-hover: #0f243a;
                      --field: #ffffff;
                      --field-border: #c7d3e0;
                      --surface: #edf2f7;
                    }
                    * { box-sizing: border-box; }
                    body {
                      margin: 0;
                      min-height: 100vh;
                      font-family: Inter, Arial, sans-serif;
                      color: var(--text);
                      background:
                        radial-gradient(circle at top left, rgba(22, 50, 79, 0.10), transparent 30%),
                        radial-gradient(circle at top right, rgba(59, 130, 246, 0.10), transparent 32%),
                        linear-gradient(180deg, #f7f9fc 0%, var(--bg) 100%);
                    }
                    .page {
                      max-width: 1080px;
                      margin: 0 auto;
                      padding: 40px 24px 56px;
                    }
                    .hero {
                      display: grid;
                      grid-template-columns: minmax(0, 1.3fr) minmax(280px, 0.7fr);
                      gap: 24px;
                      align-items: end;
                      margin-bottom: 24px;
                    }
                    .hero h1 {
                      margin: 0;
                      font-size: clamp(32px, 4vw, 48px);
                      line-height: 1.02;
                      letter-spacing: -0.03em;
                    }
                    .hero p {
                      margin: 14px 0 0;
                      color: var(--muted);
                      font-size: 16px;
                      line-height: 1.6;
                      max-width: 60ch;
                    }
                    .badges {
                      display: flex;
                      gap: 10px;
                      justify-content: flex-end;
                      flex-wrap: wrap;
                    }
                    .badge {
                      padding: 8px 12px;
                      border: 1px solid var(--panel-border);
                      background: rgba(255, 255, 255, 0.72);
                      border-radius: 999px;
                      color: var(--muted);
                      font-size: 13px;
                      backdrop-filter: blur(10px);
                    }
                    .grid {
                      display: grid;
                      grid-template-columns: minmax(0, 1.2fr) minmax(320px, 0.8fr);
                      gap: 20px;
                      align-items: start;
                    }
                    .panel {
                      background: var(--panel);
                      border: 1px solid var(--panel-border);
                      border-radius: 22px;
                      box-shadow: 0 18px 45px rgba(16, 42, 67, 0.08);
                      backdrop-filter: blur(16px);
                    }
                    .panel-inner { padding: 24px; }
                    .stack { display: grid; gap: 14px; }
                    .field { display: grid; gap: 8px; }
                    label {
                      font-size: 14px;
                      font-weight: 600;
                      color: #334e68;
                    }
                    input, select, button, .download-btn {
                      font: inherit;
                      border-radius: 14px;
                    }
                    input, select {
                      width: 100%;
                      padding: 13px 14px;
                      border: 1px solid var(--field-border);
                      background: var(--field);
                      color: var(--text);
                      box-shadow: inset 0 1px 0 rgba(255,255,255,0.6);
                    }
                    input::placeholder { color: #8fa3b8; }
                    input:focus, select:focus, button:focus, .download-btn:focus {
                      outline: 3px solid rgba(56, 132, 255, 0.18);
                      outline-offset: 2px;
                      border-color: #6aa1ff;
                    }
                    button, .download-btn {
                      display: inline-flex;
                      align-items: center;
                      justify-content: center;
                      gap: 8px;
                      min-height: 46px;
                      padding: 0 16px;
                      border: 1px solid transparent;
                      cursor: pointer;
                      text-decoration: none;
                      transition: transform 0.15s ease, background-color 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
                    }
                    button { background: var(--accent); color: white; font-weight: 600; box-shadow: 0 10px 22px rgba(22, 50, 79, 0.18); }
                    button:hover, .download-btn:hover { transform: translateY(-1px); }
                    button:hover { background: var(--accent-hover); }
                    .download-btn {
                      background: #ffffff;
                      color: var(--accent);
                      border-color: var(--field-border);
                    }
                    .download-btn:hover {
                      border-color: #9cb4cd;
                      box-shadow: 0 10px 22px rgba(16, 42, 67, 0.08);
                    }
                    .actions { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; margin-top: 6px; }
                    .hint { color: var(--muted); font-size: 14px; margin: 0; }
                    .results { padding: 20px 24px 24px; border-top: 1px solid var(--panel-border); background: rgba(237, 242, 247, 0.65); }
                    .results-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
                    .results-head h2 { margin: 0; font-size: 16px; letter-spacing: -0.01em; }
                    .results-head span { color: var(--muted); font-size: 13px; }
                    pre {
                      margin: 0;
                      padding: 16px;
                      border-radius: 16px;
                      background: #0f172a;
                      color: #d7e3f4;
                      white-space: pre-wrap;
                      word-break: break-word;
                      overflow-x: auto;
                      font-size: 13px;
                      line-height: 1.6;
                      min-height: 160px;
                    }
                    .footer-note {
                      margin-top: 16px;
                      color: var(--muted);
                      font-size: 12px;
                    }
                    </style>
              </head>
              <body>
                <div class="page">
                  <div class="hero">
                    <div>
                      <h1>Autonomous Research Agent</h1>
                      <p>Run a structured research workflow, review the findings, and download a polished PDF report without leaving the page.</p>
                    </div>
                    <div class="badges" aria-hidden="true">
                      <div class="badge">Analyst-style output</div>
                      <div class="badge">PDF export</div>
                      <div class="badge">Live download</div>
                    </div>
                  </div>

                  <div class="grid">
                    <div class="panel">
                      <div class="panel-inner">
                        <form id="f" class="stack">
                          <div class="field">
                            <label>Query</label>
                            <input name="query" placeholder="AI agent platforms 2026" />
                          </div>
                          <div class="field">
                            <label>Depth</label>
                            <select name="depth">
                              <option>quick</option>
                              <option selected>deep</option>
                              <option>standard</option>
                              <option>exhaustive</option>
                            </select>
                          </div>
                          <div class="field">
                            <label>Output</label>
                            <select name="output">
                              <option>markdown</option>
                              <option selected>markdown,pdf</option>
                              <option>pdf</option>
                            </select>
                          </div>
                          <div class="actions">
                            <button type="submit">Run research</button>
                            <a id="download" class="download-btn" href="/latest.pdf?ts=0" download="latest-report.pdf">Download latest PDF</a>
                          </div>
                        </form>
                      </div>
                    </div>

                    <div class="panel">
                      <div class="results">
                        <div class="results-head">
                          <h2>Run output</h2>
                          <span>Latest job status and report metadata</span>
                        </div>
                        <pre id="out">Ready to run.</pre>
                        <div class="footer-note">Tip: after the run completes, the download button updates to the exact report for that query.</div>
                      </div>
                    </div>
                  </div>
                  <script>
                    document.getElementById('f').addEventListener('submit', async (e) => {
                      e.preventDefault();
                     const fd = new FormData(e.target);
                     const body = Object.fromEntries(fd.entries());
                     body.max_sources = 8;
                     body.max_iterations = 3;
                     document.getElementById('out').textContent = 'Running...';
                     const r = await fetch('/research', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body) });
                     const j = await r.json();
                     if (j.pdf_url) {
                       const download = document.getElementById('download');
                       download.href = j.pdf_url;
                       download.download = j.pdf;
                       download.textContent = 'Download PDF';
                     }
                     document.getElementById('out').textContent = JSON.stringify(j, null, 2);
                   });
                 </script>
              </body>
            </html>
            """
        )

    @app.post("/research")
    async def research(request: Request):
        body = await _read_payload(request)
        args = argparse.Namespace(
            query=body.get("query", "").strip(),
            depth=(body.get("depth", "deep").strip() or "deep"),
            output=(body.get("output", "markdown,pdf").strip() or "markdown,pdf"),
            output_dir=RESEARCH_MOUNT,
            memory_db=f"{MEMORY_MOUNT}/research.db",
            allowed_domains="",
            blocked_domains="",
            freshness_days=None,
            max_sources=_coerce_int(body.get("max_sources"), 8),
            max_iterations=_coerce_int(body.get("max_iterations"), 3),
            max_concurrency=0,
            request_timeout=0,
            search_results_per_query=0,
            user_agent="",
            run_label="",
            self_test=False,
            dry_run=False,
            json=False,
        )

        if not args.query:
            return JSONResponse({"error": "query is required"}, status_code=400)

        config = build_config(args)
        memory = ResearchMemory(config.memory_db_path)
        try:
            run = execute_research(config, memory)
            if run.final_report_markdown:
                artifacts = export_model_report(run, config.output_dir, config.output_formats, run.final_report_markdown)
            else:
                artifacts = export_report(run, config.output_dir, config.output_formats)
            run.artifacts.update(artifacts)
            memory.save_run(run)
            REPORT_VOLUME.commit()
            MEMORY_VOLUME.commit()
            return JSONResponse(
                {
                    "status": "completed",
                    "run_id": run.run_id,
                    "report_source": run.report_source,
                    "pdf_url": f"/reports/{run.run_id}.pdf",
                    "pdf": Path(run.artifacts.get("pdf", "")).name if run.artifacts.get("pdf") else "",
                    "markdown": Path(run.artifacts.get("markdown", "")).name if run.artifacts.get("markdown") else "",
                }
            )
        finally:
            memory.close()

    @app.get("/latest.pdf")
    def latest_pdf():
        reports = Path(RESEARCH_MOUNT)
        pdfs = sorted(reports.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not pdfs:
            return JSONResponse({"error": "No PDF available yet"}, status_code=404)
        return FileResponse(str(pdfs[0]), media_type="application/pdf", filename=pdfs[0].name)

    @app.get("/reports/{filename}")
    def report_file(filename: str):
        reports = Path(RESEARCH_MOUNT)
        path = reports / filename
        if not path.exists() or path.suffix.lower() not in {".pdf", ".md", ".json"}:
            return JSONResponse({"error": "Report not found"}, status_code=404)
        media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
        return FileResponse(str(path), media_type=media_type, filename=path.name)

    return app
