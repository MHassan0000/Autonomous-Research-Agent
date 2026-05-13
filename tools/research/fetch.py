from __future__ import annotations

import re
import time
from dataclasses import asdict
from html import unescape
from html.parser import HTMLParser
from typing import Iterable

import requests

from .models import FetchResult, PageContent
from .utils import canonicalize_url, clip, domain_from_url, hash_text, normalize_whitespace, sentence_split, text_fingerprint


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.description = ""
        self.author = ""
        self.published_at = ""
        self.headings: list[str] = []
        self._chunks: list[str] = []
        self._stack: list[str] = []
        self._current_tag = ""
        self._meta: dict[str, str] = {}

    def handle_starttag(self, tag, attrs):
        attrs_dict = {k.lower(): v for k, v in attrs}
        self._stack.append(tag)
        self._current_tag = tag
        if tag in {"script", "style", "noscript"}:
            return
        if tag in {"h1", "h2", "h3"}:
            self._meta["heading"] = ""
        if tag == "meta":
            key = attrs_dict.get("name") or attrs_dict.get("property") or attrs_dict.get("itemprop")
            value = attrs_dict.get("content", "")
            if key and value:
                self._meta.setdefault(key.lower(), value.strip())
        if tag == "title":
            self._meta["title"] = ""

    def handle_data(self, data):
        text = normalize_whitespace(unescape(data))
        if not text:
            return
        if self._current_tag == "title":
            self._meta["title"] = (self._meta.get("title", "") + " " + text).strip()
            return
        if self._current_tag in {"h1", "h2", "h3"}:
            self.headings.append(text)
            self._chunks.append(f"\n\n{text}\n")
            return
        if self._current_tag in {"script", "style", "noscript"}:
            return
        self._chunks.append(text)

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        self._current_tag = self._stack[-1] if self._stack else ""

    def build_text(self) -> str:
        merged = " ".join(chunk.strip() for chunk in self._chunks if chunk.strip())
        merged = re.sub(r"\s+", " ", merged)
        return normalize_whitespace(merged)


def fetch_url(session: requests.Session, url: str, timeout: int = 20) -> FetchResult:
    start = time.perf_counter()
    final_url = url
    status_code = 0
    content_type = ""
    body = b""
    error = ""
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        response.raise_for_status()
        final_url = response.url
        status_code = response.status_code
        content_type = response.headers.get("content-type", "")
        body = response.content
    except Exception as exc:
        error = str(exc)
    elapsed_seconds = round(time.perf_counter() - start, 3)
    return FetchResult(url=url, final_url=final_url, status_code=status_code, content_type=content_type, body=body, elapsed_seconds=elapsed_seconds, error=error)


def extract_page_content(fetch_result: FetchResult) -> PageContent:
    html = fetch_result.body.decode("utf-8", errors="ignore") if fetch_result.body else ""
    extractor = _TextExtractor()
    if html:
        extractor.feed(html)
    title = normalize_whitespace(extractor._meta.get("title", ""))
    description = normalize_whitespace(extractor._meta.get("description", extractor._meta.get("og:description", "")))
    author = normalize_whitespace(extractor._meta.get("author", extractor._meta.get("article:author", "")))
    published_at = normalize_whitespace(extractor._meta.get("published_time", extractor._meta.get("article:published_time", extractor._meta.get("date", ""))))
    text = extractor.build_text()
    text = clean_text(text)
    if not title:
        title = derive_title_from_text(text)
    if not description:
        description = clip(text, 240)
    word_count = len(text.split())
    sentences = sentence_split(text)
    key_sentences = select_key_sentences(sentences)
    page = PageContent(
        url=fetch_result.url,
        final_url=canonicalize_url(fetch_result.final_url or fetch_result.url),
        domain=domain_from_url(fetch_result.final_url or fetch_result.url),
        title=title,
        description=description,
        author=author,
        published_at=published_at,
        text=text,
        headings=extract_headings(extractor.headings),
        word_count=word_count,
        quality_score=quality_score(text, title, description, fetch_result.status_code, fetch_result.content_type),
        credibility_score=0.0,
        fetched_at="",
        summary=clip(text, 320),
        key_sentences=key_sentences,
        notes=[],
    )
    return page


def clean_text(text: str) -> str:
    text = normalize_whitespace(text)
    text = re.sub(r"\s*([.,;:!?])\s*", r"\1 ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def derive_title_from_text(text: str) -> str:
    for sentence in sentence_split(text):
        words = sentence.split()
        if 3 <= len(words) <= 14:
            return sentence[:120]
    return "Untitled source"


def extract_headings(headings: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for heading in headings:
        key = hash_text(heading)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(heading)
    return deduped


def select_key_sentences(sentences: list[str], limit: int = 8) -> list[str]:
    if not sentences:
        return []
    ranked = sorted(sentences, key=lambda sentence: score_sentence(sentence), reverse=True)
    output: list[str] = []
    seen: set[str] = set()
    for sentence in ranked:
        key = text_fingerprint(sentence)
        if key in seen:
            continue
        seen.add(key)
        output.append(sentence)
        if len(output) >= limit:
            break
    return output


def score_sentence(sentence: str) -> float:
    tokens = sentence.split()
    score = 0.0
    if 8 <= len(tokens) <= 32:
        score += 1.0
    if any(term in sentence.lower() for term in ("is", "are", "was", "were", "means", "shows", "indicates", "found", "reported", "according")):
        score += 0.5
    if any(term in sentence.lower() for term in ("%", "million", "billion", "202", "figure", "data", "evidence", "study", "analysis")):
        score += 0.4
    return score


def quality_score(text: str, title: str, description: str, status_code: int, content_type: str) -> float:
    if not text:
        return 0.0
    score = 0.0
    words = len(text.split())
    if words >= 250:
        score += 0.3
    if words >= 800:
        score += 0.2
    if title:
        score += 0.1
    if description:
        score += 0.05
    if 200 <= status_code < 300:
        score += 0.2
    if "text/html" in (content_type or "").lower():
        score += 0.1
    if any(term in text.lower() for term in ("privacy policy", "cookie policy", "subscribe", "advertisement", "all rights reserved")):
        score -= 0.15
    return max(0.0, min(score, 1.0))
