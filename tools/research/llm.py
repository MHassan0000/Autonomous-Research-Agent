from __future__ import annotations

import json
import re
from dataclasses import asdict
from typing import Any

import requests

from .models import ResearchConfig, ResearchPlan, ResearchRun
from .search import build_search_queries
from .utils import clip, dedupe_preserve_order, normalize_text, normalize_whitespace, tokenize

DEFAULT_OPENROUTER_MODEL = "openai/gpt-oss-20b:free"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_OPENROUTER_BASE_URL,
        http_referer: str = "",
        title: str = "Autonomous Research Agent",
        timeout: int = 60,
    ) -> None:
        self.api_key = api_key.strip()
        self.model = model.strip()
        self.base_url = base_url.strip().rstrip("/") or DEFAULT_OPENROUTER_BASE_URL
        self.http_referer = http_referer.strip()
        self.title = title.strip() or "Autonomous Research Agent"
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.api_key and self.model)

    def chat(self, messages: list[dict[str, str]], temperature: float = 0.2, max_tokens: int = 2400) -> str:
        if not self.available:
            raise RuntimeError("OpenRouter is not configured")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if self.http_referer:
            headers["HTTP-Referer"] = self.http_referer
        if self.title:
            headers["X-OpenRouter-Title"] = self.title
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


def build_research_plan(config: ResearchConfig, context_hits: list[dict[str, Any]], allow_model: bool = True) -> tuple[ResearchPlan, str]:
    client = _build_client(config)
    if allow_model and client.available:
        try:
            plan = _build_research_plan_with_model(client, config, context_hits)
            return plan, f"openrouter:{client.model}"
        except Exception:
            pass
    plan = _build_research_plan_deterministic(config.query, config.depth)
    return plan, "deterministic"


def synthesize_report_markdown(config: ResearchConfig, run: ResearchRun, allow_model: bool = True) -> tuple[str, str]:
    client = _build_client(config)
    if allow_model and client.available:
        try:
            markdown = _synthesize_report_with_model(client, config, run)
            if markdown and len(markdown.strip()) > 200:
                return markdown, f"openrouter:{client.model}"
        except Exception:
            pass
    return "", "deterministic"


def _build_client(config: ResearchConfig) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=config.model_api_key,
        model=config.model_name or DEFAULT_OPENROUTER_MODEL,
        base_url=config.model_base_url or DEFAULT_OPENROUTER_BASE_URL,
        http_referer=config.model_http_referer,
        title=config.model_title,
    )


def _build_research_plan_with_model(client: OpenRouterClient, config: ResearchConfig, context_hits: list[dict[str, Any]]) -> ResearchPlan:
    prompt = {
        "query": config.query,
        "depth": config.depth,
        "current_year": 2026,
        "context_hits": context_hits[:5],
        "search_policy": "Use only free public web sources and prefer credible technical, vendor, and independent analysis sources.",
        "output_requirements": {
            "refined_query": "A tighter and more search-friendly version of the user query",
            "search_queries": "3 to 6 search queries that will retrieve diverse credible sources",
            "focus_areas": "3 to 6 areas to investigate",
            "preferred_source_types": "Source types worth prioritizing",
            "avoid_terms": "Terms that should be excluded because they create noise",
            "stop_conditions": "When to stop researching",
            "report_angle": "One-sentence angle for the final analyst report",
        },
        "rules": [
            "Return valid JSON only.",
            "Do not include markdown fences.",
            "Do not invent sources.",
            "Make the query more specific if the original query is broad or ambiguous.",
        ],
    }
    messages = [
        {"role": "system", "content": "You are a senior research strategist. Return only valid JSON."},
        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
    ]
    raw = client.chat(messages, temperature=0.2, max_tokens=900)
    data = _extract_json_object(raw)
    refined_query = normalize_whitespace(data.get("refined_query") or config.query)
    search_queries = [normalize_whitespace(item) for item in data.get("search_queries", []) if normalize_whitespace(item)]
    if not search_queries:
        search_queries = build_search_queries(refined_query or config.query, config.depth)
    focus_areas = _as_list(data.get("focus_areas"))
    preferred_source_types = _as_list(data.get("preferred_source_types"))
    avoid_terms = _as_list(data.get("avoid_terms"))
    stop_conditions = _as_list(data.get("stop_conditions"))
    report_angle = normalize_whitespace(data.get("report_angle") or "Compare the leading options, explain why they matter, and highlight tradeoffs.")
    return ResearchPlan(
        refined_query=refined_query or config.query,
        search_queries=dedupe_preserve_order(search_queries),
        focus_areas=dedupe_preserve_order(focus_areas),
        preferred_source_types=dedupe_preserve_order(preferred_source_types),
        avoid_terms=dedupe_preserve_order(avoid_terms),
        stop_conditions=dedupe_preserve_order(stop_conditions),
        report_angle=report_angle,
    )


def maybe_refine_query(config: ResearchConfig, context_hits: list[dict[str, Any]], allow_model: bool = True) -> tuple[str, ResearchPlan, str]:
    plan, source = build_research_plan(config, context_hits, allow_model=allow_model)
    return plan.refined_query or config.query, plan, source


def _build_research_plan_deterministic(query: str, depth: str) -> ResearchPlan:
    refined_query = normalize_whitespace(query)
    focus_areas = _deterministic_focus_areas(refined_query)
    return ResearchPlan(
        refined_query=refined_query,
        search_queries=build_search_queries(refined_query, depth),
        focus_areas=focus_areas,
        preferred_source_types=["official docs", "vendor docs", "independent analysis", "news coverage"],
        avoid_terms=["pricing", "download", "login"] if "agent" in refined_query.lower() else [],
        stop_conditions=["At least 3 credible sources have been opened.", "New sources add little novelty.", "The key tradeoffs are clear."],
        report_angle="Compare the leading options, explain why they matter, and highlight tradeoffs.",
    )


def _deterministic_focus_areas(query: str) -> list[str]:
    lowered = query.lower()
    areas: list[str] = []
    if any(term in lowered for term in ("agent", "agents", "platform")):
        areas.extend([
            "Leading platforms and products",
            "Capabilities and tool use",
            "Autonomy and orchestration",
            "Pricing and availability",
            "Limits and failure modes",
        ])
    else:
        tokens = [token for token in tokenize(query) if len(token) > 3]
        for token in tokens[:5]:
            areas.append(token.replace("-", " "))
    if not areas:
        areas = ["Core definition", "Leading sources", "Tradeoffs", "Limitations"]
    return dedupe_preserve_order(areas)


def _synthesize_report_with_model(client: OpenRouterClient, config: ResearchConfig, run: ResearchRun) -> str:
    evidence = _build_evidence_bundle(run)
    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior research analyst writing a polished report. "
                "Use only the provided evidence. Do not invent facts or sources. "
                "Return markdown only."
            ),
        },
        {
            "role": "user",
            "content": json.dumps(
                _build_model_prompt(run) | {
                    "instructions": {
                        "title": f"Research Report: {run.query}",
                        "required_headings": [
                            "Executive Summary",
                            "Key Findings",
                            "Source Comparison",
                            "Gaps and Limitations",
                            "References",
                        ],
                        "style": "professional analyst report, concise but substantive, visually clean markdown",
                        "citations": "Use bracketed source numbers like [1], [2] only for sources in the evidence bundle.",
                        "table": "Include a concise source comparison table.",
                        "length": "Around 1-2 pages of markdown when rendered to PDF.",
                    },
                    "query": run.query,
                    "depth": run.depth,
                    "research_plan": run.research_plan,
                    "gaps": run.gaps,
                    "sources": evidence["sources"],
                    "insights": evidence["insights"],
                    "comparison_rows": evidence["comparison_rows"],
                    "context_hits": run.context_hits[:5],
                },
                ensure_ascii=False,
            ),
        },
    ]
    return client.chat(messages, temperature=0.25, max_tokens=2200)


def _build_evidence_bundle(run: ResearchRun) -> dict[str, Any]:
    source_ids = {page.final_url or page.url: index for index, page in enumerate(run.pages, start=1)}
    sources = []
    for index, page in enumerate(run.pages[:8], start=1):
        sources.append(
            {
                "id": index,
                "title": page.title,
                "domain": page.domain,
                "url": page.final_url or page.url,
                "credibility_score": page.credibility_score,
                "quality_score": page.quality_score,
                "summary": clip(page.summary or page.description or page.text, 360),
                "key_sentences": page.key_sentences[:3],
            }
        )
    insights = []
    for insight in run.insights[:20]:
        citation_ids = sorted({source_ids.get(url) for url in insight.source_urls if source_ids.get(url)})
        insights.append(
            {
                "text": insight.text,
                "category": insight.category,
                "confidence": insight.confidence,
                "novelty": insight.novelty,
                "citations": citation_ids,
            }
        )
    return {
        "sources": sources,
        "insights": insights,
        "comparison_rows": [
            {
                "source": page.title,
                "domain": page.domain,
                "credibility": f"{page.credibility_score:.2f}",
                "quality": f"{page.quality_score:.2f}",
                "summary": clip(page.summary, 160),
            }
            for page in run.pages[:8]
        ],
    }


def _extract_json_object(text: str) -> dict[str, Any]:
    raw = normalize_whitespace(text)
    if not raw:
        return {}
    candidates = [raw]
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1))
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidates.insert(0, raw[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
            if isinstance(data, dict):
                return data
        except Exception:
            continue
    return {}


def _build_model_prompt(run: ResearchRun) -> dict[str, Any]:
    return {
        "title": f"Research Report: {run.query}",
        "depth": run.depth,
        "research_plan": run.research_plan,
        "sources": [
            {
                "id": index,
                "title": page.title,
                "domain": page.domain,
                "url": page.final_url or page.url,
                "credibility_score": page.credibility_score,
                "quality_score": page.quality_score,
                "summary": clip(page.summary or page.description or page.text, 360),
            }
            for index, page in enumerate(run.pages[:8], start=1)
        ],
        "insights": [
            {
                "text": insight.text,
                "category": insight.category,
                "confidence": insight.confidence,
                "novelty": insight.novelty,
                "source_urls": insight.source_urls,
            }
            for insight in run.insights[:20]
        ],
        "gaps": run.gaps,
    }


def _looks_like_report(markdown: str) -> bool:
    lowered = markdown.lower()
    return "executive summary" in lowered and "references" in lowered and "source comparison" in lowered


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [normalize_whitespace(str(item)) for item in value if normalize_whitespace(str(item))]
    if isinstance(value, str):
        return [normalize_whitespace(value)] if normalize_whitespace(value) else []
    return []
