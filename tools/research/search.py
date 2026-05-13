from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict
from html import unescape
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import quote_plus

from .models import SearchResult
from .utils import canonicalize_url, clip, domain_from_url, normalize_text, normalize_whitespace, overlap_score, unwrap_duckduckgo_url


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_result = False
        self.current: dict[str, str] = {}
        self.current_link = ""
        self.current_title = []
        self.current_snippet = []
        self.results: list[dict[str, str]] = []
        self._div_depth = 0
        self._anchor_depth = 0

    def handle_starttag(self, tag, attrs):
        attrs_dict = dict(attrs)
        class_name = attrs_dict.get("class", "")
        if tag == "div" and not self.in_result and "result" in class_name and ("result__body" in class_name or "result__title" in class_name or "result__extras" in class_name or "result__snippet" in class_name or "result__header" in class_name):
            self.in_result = True
            self._div_depth = 1
            self.current = {}
            self.current_link = ""
            self.current_title = []
            self.current_snippet = []
            self._anchor_depth = 0
            return
        if self.in_result and tag == "div":
            self._div_depth += 1
        if self.in_result and tag == "a" and attrs_dict.get("class", "").find("result__a") != -1:
            self.current_link = unwrap_duckduckgo_url(attrs_dict.get("href", ""))
            self._anchor_depth += 1
        elif self.in_result and tag == "a" and self._anchor_depth > 0:
            self._anchor_depth += 1

    def handle_data(self, data):
        if not self.in_result:
            return
        text = data.strip()
        if not text:
            return
        if self._anchor_depth > 0:
            self.current_title.append(text)
        else:
            self.current_snippet.append(text)

    def handle_endtag(self, tag):
        if self.in_result and tag == "a" and self._anchor_depth > 0:
            self._anchor_depth -= 1
        if self.in_result and tag == "div":
            self._div_depth -= 1
            if self._div_depth <= 0:
                title = " ".join(self.current_title).strip()
                snippet = " ".join(self.current_snippet).strip()
                if title or snippet or self.current_link:
                    self.results.append({"title": title, "url": self.current_link, "snippet": snippet})
                self.current = {}
                self.current_link = ""
                self.current_title = []
                self.current_snippet = []
                self._div_depth = 0
                self._anchor_depth = 0
                self.in_result = False


def _strip_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", unescape(text or ""))


def build_search_queries(query: str, depth: str) -> list[str]:
    base = query.strip()
    if not base:
        return []
    variants = [base]
    if len(base.split()) > 2:
        variants.append(base.replace("?", ""))
    depth_terms = {
        "quick": [base],
        "standard": [f"{base} analysis", f"{base} report"],
        "deep": [f"{base} evidence", f"{base} source", f"{base} review"],
        "exhaustive": [f"{base} evidence", f"{base} source", f"{base} review", f"{base} study"],
    }
    variants.extend(depth_terms.get(depth, depth_terms["standard"]))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in variants:
        normalized = normalize_text(item)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(item)
    return deduped


def fetch_duckduckgo_html(session, query: str, timeout: int = 20) -> str:
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    response = session.get(url, timeout=timeout, headers={"User-Agent": session.headers.get("User-Agent", "Mozilla/5.0")})
    response.raise_for_status()
    return response.text


def fetch_bing_rss(session, query: str, timeout: int = 20) -> str:
    url = f"https://www.bing.com/search?format=rss&q={quote_plus(query)}"
    response = session.get(url, timeout=timeout, headers={"User-Agent": session.headers.get("User-Agent", "Mozilla/5.0")})
    response.raise_for_status()
    return response.text


def parse_duckduckgo_results(html: str) -> list[dict[str, str]]:
    parser = _DuckDuckGoParser()
    parser.feed(html)
    results = []
    for item in parser.results:
        url = canonicalize_url(item.get("url", ""))
        if not url:
            continue
        results.append({
            "title": item.get("title", "").strip(),
            "url": url,
            "snippet": clip(item.get("snippet", ""), 280),
        })
    return results


def parse_bing_rss_results(xml_text: str) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return results
    for item in root.findall(".//item"):
        title = normalize_whitespace(_element_text(item, "title"))
        url = canonicalize_url(_element_raw_text(item, "link"))
        snippet = clip(_strip_html(_element_raw_text(item, "description")), 280)
        published_at = normalize_whitespace(_element_raw_text(item, "pubDate"))
        if not url:
            continue
        results.append({"title": title, "url": url, "snippet": snippet, "published_at": published_at})
    return results


def search_web(session, query: str, depth: str, limit: int, timeout: int = 20, backend: str | None = None) -> list[SearchResult]:
    queries = build_search_queries(query, depth)
    seen_urls: set[str] = set()
    results: list[SearchResult] = []
    backend = (backend or os.environ.get("SEARCH_BACKEND", "bing_rss")).strip().lower()
    backend_chain = {
        "bing_rss": ["bing_rss", "duckduckgo_html"],
        "duckduckgo_html": ["duckduckgo_html", "bing_rss"],
        "auto": ["bing_rss", "duckduckgo_html"],
    }.get(backend, [backend, "bing_rss", "duckduckgo_html"])
    for search_query in queries:
        parsed: list[dict[str, str]] = []
        for candidate_backend in backend_chain:
            try:
                if candidate_backend == "bing_rss":
                    response_text = fetch_bing_rss(session, search_query, timeout=timeout)
                    parsed = parse_bing_rss_results(response_text)
                elif candidate_backend == "duckduckgo_html":
                    response_text = fetch_duckduckgo_html(session, search_query, timeout=timeout)
                    parsed = parse_duckduckgo_results(response_text)
                else:
                    continue
            except Exception:
                parsed = []
            if parsed:
                break
        for rank, item in enumerate(parsed, start=1):
            url = canonicalize_url(item["url"])
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            title = item.get("title") or url
            snippet = item.get("snippet", "")
            domain = domain_from_url(url)
            rel = overlap_score(query, f"{title} {snippet} {domain}")
            credibility = source_credibility(domain, title, snippet)
            score = rel * 0.7 + credibility * 0.3
            results.append(
                SearchResult(
                    query=search_query,
                    title=title,
                    url=url,
                    display_url=domain,
                    snippet=snippet,
                    rank=rank,
                    domain=domain,
                    credibility=credibility,
                    relevance=rel,
                    score=score,
                )
            )
            if len(results) >= limit:
                return rank_search_results(results)
    return rank_search_results(results)


def source_credibility(domain: str, title: str, snippet: str) -> float:
    domain = domain.lower()
    score = 0.35
    trusted_tlds = (".gov", ".edu", ".ac.")
    if any(token in domain for token in trusted_tlds):
        score += 0.3
    if domain.endswith("wikipedia.org"):
        score += 0.1
    if any(token in domain for token in ("nature.com", "science.org", "nih.gov", "who.int", "worldbank.org", "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "theguardian.com")):
        score += 0.25
    lowered = f"{title} {snippet}".lower()
    if any(term in lowered for term in ("about us", "contact", "subscribe", "newsletter", "sponsored", "advertisement")):
        score -= 0.1
    if any(term in lowered for term in ("study", "report", "data", "analysis", "official")):
        score += 0.1
    if any(term in lowered for term in ("agent", "agents", "autonomous", "workflow", "orchestr", "copilot", "assistant")):
        score += 0.05
    return max(0.0, min(score, 1.0))


def rank_search_results(results: Iterable[SearchResult]) -> list[SearchResult]:
    ranked = sorted(results, key=lambda item: (item.score, item.relevance, item.credibility, -item.rank), reverse=True)
    for index, item in enumerate(ranked, start=1):
        item.rank = index
    return ranked


def results_to_json(results: Iterable[SearchResult]) -> str:
    return json.dumps([asdict(item) for item in results], indent=2, ensure_ascii=False)


def _element_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    return normalize_whitespace(child.text if child is not None and child.text else "")


def _element_raw_text(parent: ET.Element, tag: str) -> str:
    child = parent.find(tag)
    return child.text if child is not None and child.text else ""
