from __future__ import annotations

from collections import defaultdict

from .models import Insight, PageContent, SearchResult
from .utils import clip, jaccard, normalize_text, overlap_score, sentence_split, text_fingerprint, tokenize, unique_by_key


def should_open_result(result: SearchResult, allowed_domains: tuple[str, ...], blocked_domains: tuple[str, ...]) -> bool:
    domain = result.domain.lower()
    if blocked_domains and any(blocked in domain for blocked in blocked_domains):
        return False
    if allowed_domains and not any(allowed in domain for allowed in allowed_domains):
        return False
    return result.score >= 0.2 or result.credibility >= 0.35 or result.relevance >= 0.2


def filter_sources(results: list[SearchResult], allowed_domains: tuple[str, ...], blocked_domains: tuple[str, ...], max_sources: int) -> list[SearchResult]:
    selected = [result for result in results if should_open_result(result, allowed_domains, blocked_domains)]
    return selected[:max_sources]


def score_page_against_query(page: PageContent, query: str) -> float:
    combined = f"{page.title} {page.description} {page.summary} {page.text[:4000]}"
    return overlap_score(query, combined) * 0.75 + page.quality_score * 0.25


def remove_redundant_pages(pages: list[PageContent], similarity_threshold: float = 0.78) -> list[PageContent]:
    kept: list[PageContent] = []
    seen_fingerprints: set[str] = set()
    for page in sorted(pages, key=lambda item: (item.quality_score, item.credibility_score, item.word_count), reverse=True):
        fp = text_fingerprint(page.text[:1200])
        if fp in seen_fingerprints:
            continue
        if any(_page_similarity(page, other) >= similarity_threshold for other in kept):
            continue
        seen_fingerprints.add(fp)
        kept.append(page)
    return kept


def _page_similarity(left: PageContent, right: PageContent) -> float:
    left_tokens = tokenize(f"{left.title} {left.description} {left.summary} {left.text[:2000]}")
    right_tokens = tokenize(f"{right.title} {right.description} {right.summary} {right.text[:2000]}")
    return jaccard(left_tokens, right_tokens)


def extract_insights_from_pages(pages: list[PageContent], query: str) -> list[Insight]:
    sentence_map: dict[str, list[PageContent]] = defaultdict(list)
    for page in pages:
        for sentence in page.key_sentences or sentence_split(page.text)[:8]:
            normalized = normalize_text(sentence)
            if not normalized:
                continue
            sentence_map[normalized].append(page)

    insights: list[Insight] = []
    for normalized_sentence, source_pages in sentence_map.items():
        if len(normalized_sentence.split()) < 6:
            continue
        if any(term in normalized_sentence for term in ("cookie policy", "privacy policy", "subscribe", "advertising")):
            continue
        source_urls = [page.final_url for page in source_pages]
        source_titles = [page.title for page in source_pages]
        confidence = min(1.0, 0.45 + 0.12 * len(source_pages) + 0.25 * _sentence_relevance(normalized_sentence, query))
        novelty = min(1.0, 0.3 + 0.15 * len(set(source_urls)) + 0.15 * _sentence_relevance(normalized_sentence, query))
        category = categorize_sentence(normalized_sentence)
        insights.append(
            Insight(
                text=clip(normalized_sentence, 280),
                category=category,
                confidence=round(confidence, 3),
                source_urls=source_urls,
                source_titles=source_titles,
                novelty=round(novelty, 3),
            )
        )

    ranked = sorted(insights, key=lambda item: (item.confidence, item.novelty, len(item.source_urls)), reverse=True)
    deduped: list[Insight] = []
    seen: set[str] = set()
    for insight in ranked:
        key = text_fingerprint(insight.text)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(insight)
    return deduped


def categorize_sentence(sentence: str) -> str:
    lowered = sentence.lower()
    if any(term in lowered for term in ("however", "but", "although", "conflict", "contradict", "mixed")):
        return "comparison"
    if any(term in lowered for term in ("data", "study", "analysis", "evidence", "research", "survey")):
        return "evidence"
    if any(term in lowered for term in ("increase", "decrease", "growth", "decline", "%", "rate", "trend")):
        return "trend"
    if any(term in lowered for term in ("risk", "limitation", "issue", "problem", "challenge")):
        return "risk"
    return "finding"


def _sentence_relevance(sentence: str, query: str) -> float:
    return overlap_score(query, sentence)


def build_comparison_table(pages: list[PageContent]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for page in pages:
        rows.append(
            {
                "source": page.title,
                "domain": page.domain,
                "credibility": f"{page.credibility_score:.2f}",
                "quality": f"{page.quality_score:.2f}",
                "summary": clip(page.summary, 180),
            }
        )
    return rows


def detect_gaps(insights: list[Insight], query: str, pages: list[PageContent]) -> list[str]:
    gaps: list[str] = []
    if len(pages) < 3:
        gaps.append("Too few credible sources were gathered for a strong comparison.")
    if not any(insight.category == "comparison" for insight in insights):
        gaps.append("No clear cross-source comparison emerged.")
    if not any(insight.category == "risk" for insight in insights):
        gaps.append("Potential risks or limitations were not sufficiently documented.")
    if not any(_sentence_relevance(insight.text, query) > 0.5 for insight in insights):
        gaps.append("Key findings may still be under-aligned to the original query.")
    return gaps


def summarize_page(page: PageContent, query: str) -> str:
    match_score = score_page(page, query)
    source_lines = [page.title, page.description, page.summary]
    if page.key_sentences:
        source_lines.extend(page.key_sentences[:3])
    unique = unique_by_key([{"content": line} for line in source_lines], "content")
    lines = [item["content"] for item in unique if item.get("content")]
    prefix = "High-value" if match_score > 0.5 else "Relevant"
    return f"{prefix} source: {clip(' '.join(lines), 360)}"


def score_page(page: PageContent, query: str) -> float:
    return score_page_against_query(page, query)
