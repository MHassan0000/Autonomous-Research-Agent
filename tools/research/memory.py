from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .models import Insight, PageContent, ResearchRun, SearchResult
from .utils import ensure_dir, hash_text, normalize_text


SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    depth TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    quality_score REAL NOT NULL,
    credibility_score REAL NOT NULL,
    word_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS notes (
    note_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_url TEXT,
    note_type TEXT NOT NULL,
    content TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS insights (
    insight_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    text TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence REAL NOT NULL,
    novelty REAL NOT NULL,
    payload_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);

CREATE TABLE IF NOT EXISTS contexts (
    context_id TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class ResearchMemory:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        ensure_dir(self.db_path.parent)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.initialize()

    def initialize(self) -> None:
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def save_run(self, run: ResearchRun) -> None:
        payload = json.dumps(_run_payload(run), ensure_ascii=False, default=_json_default)
        self.conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, query, depth, started_at, finished_at, status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run.run_id, run.query, run.depth, run.started_at, run.finished_at, run.status, payload),
        )
        self.conn.commit()

    def save_source(self, run_id: str, page: PageContent) -> None:
        source_id = hash_text(f"{run_id}:{page.final_url}")
        payload = json.dumps(asdict(page), ensure_ascii=False, default=_json_default)
        self.conn.execute(
            "INSERT OR REPLACE INTO sources(source_id, run_id, url, final_url, domain, title, quality_score, credibility_score, word_count, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (source_id, run_id, page.url, page.final_url, page.domain, page.title, page.quality_score, page.credibility_score, page.word_count, payload),
        )
        self.conn.commit()

    def save_note(self, run_id: str, note_type: str, content: str, source_url: str = "", payload: dict | None = None) -> None:
        note_id = hash_text(f"{run_id}:{note_type}:{source_url}:{content}")
        record = payload or {"content": content}
        self.conn.execute(
            "INSERT OR REPLACE INTO notes(note_id, run_id, source_url, note_type, content, payload_json) VALUES (?, ?, ?, ?, ?, ?)",
            (note_id, run_id, source_url, note_type, content, json.dumps(record, ensure_ascii=False, default=_json_default)),
        )
        self.conn.commit()

    def save_insight(self, run_id: str, insight: Insight) -> None:
        insight_id = hash_text(f"{run_id}:{insight.category}:{insight.text}")
        payload = json.dumps(asdict(insight), ensure_ascii=False, default=_json_default)
        self.conn.execute(
            "INSERT OR REPLACE INTO insights(insight_id, run_id, text, category, confidence, novelty, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (insight_id, run_id, insight.text, insight.category, insight.confidence, insight.novelty, payload),
        )
        self.conn.commit()

    def save_context(self, query: str, payload: dict) -> None:
        context_id = hash_text(query)
        self.conn.execute(
            "INSERT OR REPLACE INTO contexts(context_id, query, payload_json) VALUES (?, ?, ?)",
            (context_id, query, json.dumps(payload, ensure_ascii=False, default=_json_default)),
        )
        self.conn.commit()

    def search_contexts(self, query: str, limit: int = 5) -> list[dict]:
        rows = self.conn.execute("SELECT query, payload_json FROM contexts").fetchall()
        scored: list[tuple[float, dict]] = []
        for row in rows:
            existing_query = row["query"]
            score = _semantic_overlap(query, existing_query)
            if score <= 0:
                continue
            scored.append((score, json.loads(row["payload_json"])))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [item[1] for item in scored[:limit]]

    def list_sources_for_run(self, run_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT payload_json FROM sources WHERE run_id = ? ORDER BY quality_score DESC", (run_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]

    def list_insights_for_run(self, run_id: str) -> list[dict]:
        rows = self.conn.execute("SELECT payload_json FROM insights WHERE run_id = ? ORDER BY confidence DESC", (run_id,)).fetchall()
        return [json.loads(row["payload_json"]) for row in rows]


def _semantic_overlap(a: str, b: str) -> float:
    tokens_a = set(normalize_text(a).split())
    tokens_b = set(normalize_text(b).split())
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)


def _run_payload(run: ResearchRun) -> dict:
    return {
        "run_id": run.run_id,
        "query": run.query,
        "depth": run.depth,
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "status": run.status,
        "search_queries": run.search_queries,
        "results": [asdict(result) for result in run.results],
        "pages": [asdict(page) for page in run.pages],
        "insights": [asdict(insight) for insight in run.insights],
        "gaps": run.gaps,
        "context_hits": run.context_hits,
        "artifacts": run.artifacts,
    }


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "__dict__"):
        return value.__dict__
    return str(value)
