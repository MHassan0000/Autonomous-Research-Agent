from __future__ import annotations

import json
from html import escape as html_escape
import shutil
import subprocess
import re
from dataclasses import asdict
from pathlib import Path

from .analysis import build_comparison_table
from .models import ResearchRun
from .utils import clip, ensure_dir, normalize_whitespace, wrap_lines


def render_markdown(run: ResearchRun) -> str:
    lines: list[str] = []
    lines.append(f"# Research Report: {run.query}")
    lines.append("")
    lines.append(f"- Run ID: `{run.run_id}`")
    lines.append(f"- Depth: `{run.depth}`")
    lines.append(f"- Started: `{run.started_at}`")
    lines.append(f"- Finished: `{run.finished_at or 'in progress'}`")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    if run.insights:
        for insight in run.insights[:5]:
            lines.append(f"- {insight.text}")
    else:
        lines.append("- No strong findings were extracted.")
    lines.append("")
    lines.append("## Key Findings")
    lines.append("")
    for index, insight in enumerate(run.insights[:12], start=1):
        lines.append(f"{index}. {insight.text}  ")
        lines.append(f"   - Category: {insight.category}")
        lines.append(f"   - Confidence: {insight.confidence:.2f}")
        lines.append(f"   - Sources: {len(insight.source_urls)}")
    lines.append("")
    lines.append("## Source Comparison")
    lines.append("")
    table = build_comparison_table(run.pages)
    if table:
        lines.append("| Source | Domain | Credibility | Quality | Summary |")
        lines.append("| --- | --- | ---: | ---: | --- |")
        for row in table:
            lines.append(
                f"| {escape_pipe(row['source'])} | {row['domain']} | {row['credibility']} | {row['quality']} | {escape_pipe(row['summary'])} |"
            )
    else:
        lines.append("- No sources were retained.")
    lines.append("")
    lines.append("## Gaps and Limitations")
    lines.append("")
    if run.gaps:
        for gap in run.gaps:
            lines.append(f"- {gap}")
    else:
        lines.append("- No material gaps were detected.")
    lines.append("")
    lines.append("## References")
    lines.append("")
    if run.pages:
        for index, page in enumerate(run.pages, start=1):
            citation = page.final_url or page.url
            lines.append(f"[{index}] {page.title} - {citation}")
    else:
        lines.append("- No references collected.")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def export_report(run: ResearchRun, output_dir: str | Path, formats: tuple[str, ...]) -> dict[str, str]:
    output_dir = ensure_dir(output_dir)
    artifacts: dict[str, str] = {}
    markdown = render_markdown(run)
    md_path = output_dir / f"{run.run_id}.md"
    md_path.write_text(markdown, encoding="utf-8")
    artifacts["markdown"] = str(md_path)

    notes_path = output_dir / f"{run.run_id}.notes.json"
    notes_payload = {
        "run": asdict(run),
    }
    notes_path.write_text(json.dumps(notes_payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["notes"] = str(notes_path)

    run_path = output_dir / f"{run.run_id}.run.json"
    run_path.write_text(json.dumps(asdict(run), indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["run"] = str(run_path)

    sources_path = output_dir / f"{run.run_id}.sources.json"
    sources_payload = [asdict(page) for page in run.pages]
    sources_path.write_text(json.dumps(sources_payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["sources"] = str(sources_path)

    notes_md_path = output_dir / f"{run.run_id}.notes.md"
    notes_md_path.write_text(render_notes_markdown(run), encoding="utf-8")
    artifacts["notes_md"] = str(notes_md_path)

    if any(fmt.lower() == "pdf" for fmt in formats):
        pdf_path = output_dir / f"{run.run_id}.pdf"
        render_pdf(markdown, pdf_path)
        artifacts["pdf"] = str(pdf_path)
    return artifacts


def export_model_report(run: ResearchRun, output_dir: str | Path, formats: tuple[str, ...], markdown: str) -> dict[str, str]:
    output_dir = ensure_dir(output_dir)
    artifacts: dict[str, str] = {}
    md_path = output_dir / f"{run.run_id}.md"
    md_path.write_text(markdown, encoding="utf-8")
    artifacts["markdown"] = str(md_path)

    notes_path = output_dir / f"{run.run_id}.notes.json"
    notes_path.write_text(json.dumps({"run": asdict(run)}, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["notes"] = str(notes_path)

    run_path = output_dir / f"{run.run_id}.run.json"
    run_path.write_text(json.dumps(asdict(run), indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["run"] = str(run_path)

    sources_path = output_dir / f"{run.run_id}.sources.json"
    sources_payload = [asdict(page) for page in run.pages]
    sources_path.write_text(json.dumps(sources_payload, indent=2, ensure_ascii=False, default=_json_default), encoding="utf-8")
    artifacts["sources"] = str(sources_path)

    notes_md_path = output_dir / f"{run.run_id}.notes.md"
    notes_md_path.write_text(render_notes_markdown(run), encoding="utf-8")
    artifacts["notes_md"] = str(notes_md_path)

    if any(fmt.lower() == "pdf" for fmt in formats):
        pdf_path = output_dir / f"{run.run_id}.pdf"
        render_pdf(markdown, pdf_path)
        artifacts["pdf"] = str(pdf_path)
    return artifacts


def render_pdf(markdown_text: str, pdf_path: Path) -> None:
    ensure_dir(pdf_path.parent)
    pandoc = shutil.which("pandoc")
    if pandoc:
        temp_md = pdf_path.with_suffix(".source.md")
        temp_md.write_text(markdown_text, encoding="utf-8")
        try:
            subprocess.run([pandoc, str(temp_md), "-o", str(pdf_path)], check=True, capture_output=True)
            return
        except Exception:
            pass
        finally:
            if temp_md.exists():
                temp_md.unlink(missing_ok=True)
    fallback_pdf(markdown_text, pdf_path)


def fallback_pdf(markdown_text: str, pdf_path: Path) -> None:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.units import inch
        from reportlab.platypus import Paragraph, SimpleDocTemplate
    except Exception:
        pdf_path.write_text(markdown_text, encoding="utf-8")
        return

    styles = _report_styles()

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=LETTER,
        leftMargin=0.7 * inch,
        rightMargin=0.7 * inch,
        topMargin=0.8 * inch,
        bottomMargin=0.7 * inch,
        title="Research Report",
        author="Autonomous Research Agent",
    )

    story = _markdown_to_story(markdown_text, styles, doc.width)
    if not story:
        story = [Paragraph("No report content available.", styles["ReportBody"])]

    def on_page(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(colors.HexColor("#102A43"))
        canvas.rect(0, LETTER[1] - 24, LETTER[0], 24, stroke=0, fill=1)
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica-Bold", 9)
        canvas.drawString(doc.leftMargin, LETTER[1] - 16, "Autonomous Research Agent")
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(LETTER[0] - doc.rightMargin, LETTER[1] - 16, f"Page {canvas.getPageNumber()}")
        canvas.setStrokeColor(colors.HexColor("#CBD2D9"))
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, 34, LETTER[0] - doc.rightMargin, 34)
        canvas.restoreState()

    doc.build(story, onFirstPage=on_page, onLaterPages=on_page)


def _report_styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    if "ReportTitle" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportTitle",
                parent=styles["Title"],
                fontName="Helvetica-Bold",
                fontSize=22,
                leading=26,
                textColor=colors.HexColor("#102A43"),
                spaceAfter=4,
            )
        )
    if "ReportSubtitle" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportSubtitle",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=9,
                leading=12,
                textColor=colors.HexColor("#52606D"),
                spaceAfter=10,
            )
        )
    if "ReportHeading1" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportHeading1",
                parent=styles["Heading1"],
                fontName="Helvetica-Bold",
                fontSize=15,
                leading=18,
                textColor=colors.HexColor("#102A43"),
                spaceBefore=10,
                spaceAfter=6,
                keepWithNext=True,
            )
        )
    if "ReportHeading2" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportHeading2",
                parent=styles["Heading2"],
                fontName="Helvetica-Bold",
                fontSize=12,
                leading=15,
                textColor=colors.HexColor("#102A43"),
                spaceBefore=8,
                spaceAfter=4,
                keepWithNext=True,
            )
        )
    if "ReportHeading3" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportHeading3",
                parent=styles["Heading3"],
                fontName="Helvetica-Bold",
                fontSize=10,
                leading=13,
                textColor=colors.HexColor("#334E68"),
                spaceBefore=6,
                spaceAfter=3,
                keepWithNext=True,
            )
        )
    if "ReportBody" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportBody",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=10,
                leading=13,
                textColor=colors.HexColor("#243B53"),
                spaceAfter=4,
            )
        )
    if "ReportBullet" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportBullet",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=10,
                leading=13,
                leftIndent=12,
                bulletIndent=0,
                textColor=colors.HexColor("#243B53"),
                spaceAfter=2,
            )
        )
    if "ReportTableHeader" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportTableHeader",
                parent=styles["BodyText"],
                fontName="Helvetica-Bold",
                fontSize=8,
                leading=10,
                textColor=colors.white,
            )
        )
    if "ReportTableCell" not in styles:
        styles.add(
            ParagraphStyle(
                name="ReportTableCell",
                parent=styles["BodyText"],
                fontName="Helvetica",
                fontSize=8,
                leading=10,
                textColor=colors.HexColor("#243B53"),
            )
        )
    return styles


def _markdown_to_story(markdown_text: str, styles, available_width: float):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    lines = [line.rstrip() for line in markdown_text.splitlines()]
    story = []
    paragraph_lines: list[str] = []
    table_lines: list[str] = []
    saw_title = False
    saw_subtitle = False
    body_started = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        text = " ".join(part.strip() for part in paragraph_lines if part.strip())
        paragraph_lines = []
        if not text:
            return
        story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportBody"]))

    def flush_table() -> None:
        nonlocal table_lines
        if not table_lines:
            return
        table = _markdown_table_to_flowable(table_lines, styles, available_width)
        table_lines = []
        if table is not None:
            story.append(table)
            story.append(Spacer(1, 6))

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_table()
            story.append(Spacer(1, 2))
            continue

        if _is_markdown_horizontal_rule(stripped):
            flush_paragraph()
            flush_table()
            story.append(Spacer(1, 4))
            continue

        if not saw_title and _is_bold_only_line(stripped):
            flush_paragraph()
            flush_table()
            story.append(Paragraph(_markdown_inline_to_html(_unwrap_markdown_emphasis(stripped)), styles["ReportTitle"]))
            saw_title = True
            continue

        if saw_title and not saw_subtitle and not body_started and _is_italic_only_line(stripped):
            flush_paragraph()
            flush_table()
            story.append(Paragraph(_markdown_inline_to_html(_unwrap_markdown_emphasis(stripped)), styles["ReportSubtitle"]))
            saw_subtitle = True
            continue

        heading = re.match(r"^(#{1,6})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_table()
            level = len(heading.group(1))
            text = heading.group(2).strip()
            if level == 1 and not saw_title:
                story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportTitle"]))
                saw_title = True
            elif level <= 1:
                story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportHeading1"]))
            elif level == 2:
                story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportHeading1"]))
            elif level == 3:
                story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportHeading2"]))
            else:
                story.append(Paragraph(_markdown_inline_to_html(text), styles["ReportHeading3"]))
            body_started = True
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            flush_table()
            story.append(Paragraph(f"• {_markdown_inline_to_html(bullet.group(1).strip())}", styles["ReportBullet"]))
            body_started = True
            continue

        numbered = re.match(r"^(\d+)[.)]\s+(.+)$", stripped)
        if numbered:
            flush_paragraph()
            flush_table()
            story.append(Paragraph(f"{numbered.group(1)}. {_markdown_inline_to_html(numbered.group(2).strip())}", styles["ReportBody"]))
            body_started = True
            continue

        if _is_markdown_table_row(stripped):
            flush_paragraph()
            table_lines.append(stripped)
            body_started = True
            continue

        if table_lines:
            flush_table()

        paragraph_lines.append(stripped)
        body_started = True

    flush_paragraph()
    flush_table()
    return story


def _markdown_table_to_flowable(rows: list[str], styles, available_width: float):
    from reportlab.lib import colors
    from reportlab.platypus import Paragraph, Table, TableStyle

    parsed_rows = [_split_markdown_table_row(row) for row in rows if row.strip()]
    if len(parsed_rows) < 2:
        return None
    if not _is_markdown_table_separator_row(parsed_rows[1]):
        return None

    header = parsed_rows[0]
    data_rows = parsed_rows[2:]
    if not data_rows:
        return None

    column_count = max(len(header), max((len(row) for row in data_rows), default=0))
    if column_count <= 0:
        return None

    header = _pad_cells(header, column_count)
    normalized_rows = [_pad_cells(row, column_count) for row in data_rows]
    table_data = [[Paragraph(_markdown_inline_to_html(cell), styles["ReportTableHeader"]) for cell in header]]
    for row in normalized_rows:
        table_data.append([Paragraph(_markdown_inline_to_html(cell), styles["ReportTableCell"]) for cell in row])

    column_widths = _estimate_markdown_column_widths([header] + normalized_rows, available_width)
    table = Table(table_data, colWidths=column_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102A43")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD2D9")),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E2EC")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def _markdown_inline_to_html(text: str) -> str:
    value = html_escape(normalize_whitespace(str(text)))

    def replace_link(match: re.Match[str]) -> str:
        label = match.group(1)
        url = html_escape(match.group(2), quote=True)
        return f'<a href="{url}">{label}</a>'

    value = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, value)
    value = re.sub(r"`([^`]+)`", r"<font face='Courier'>\1</font>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"__([^_]+)__", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"<i>\1</i>", value)
    value = re.sub(r"(?<!_)_([^_\n]+)_(?!_)", r"<i>\1</i>", value)
    return value


def _unwrap_markdown_emphasis(text: str) -> str:
    stripped = normalize_whitespace(text)
    for prefix, suffix in (("**", "**"), ("__", "__"), ("*", "*"), ("_", "_")):
        if stripped.startswith(prefix) and stripped.endswith(suffix) and len(stripped) > len(prefix) + len(suffix):
            return stripped[len(prefix) : -len(suffix)]
    return stripped


def _is_bold_only_line(text: str) -> bool:
    stripped = normalize_whitespace(text)
    return bool(re.fullmatch(r"\*\*.+\*\*", stripped) or re.fullmatch(r"__.+__", stripped))


def _is_italic_only_line(text: str) -> bool:
    stripped = normalize_whitespace(text)
    return bool(re.fullmatch(r"\*[^*].*[^*]\*", stripped) or re.fullmatch(r"_[^_].*[^_]_", stripped))


def _is_markdown_horizontal_rule(text: str) -> bool:
    stripped = text.strip()
    return bool(re.fullmatch(r"(-\s*){3,}", stripped) or re.fullmatch(r"(\*\s*){3,}", stripped) or re.fullmatch(r"(_\s*){3,}", stripped))


def _is_markdown_table_row(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _split_markdown_table_row(text: str) -> list[str]:
    stripped = text.strip().strip("|")
    parts = re.split(r"(?<!\\)\|", stripped)
    return [part.replace(r"\|", "|").strip() for part in parts]


def _is_markdown_table_separator_row(cells: list[str]) -> bool:
    if not cells:
        return False
    for cell in cells:
        normalized = cell.replace(" ", "")
        if not re.fullmatch(r":?-{3,}:?", normalized):
            return False
    return True


def _pad_cells(cells: list[str], width: int) -> list[str]:
    return cells + [""] * max(0, width - len(cells))


def _estimate_markdown_column_widths(rows: list[list[str]], available_width: float) -> list[float]:
    from reportlab.lib.units import inch

    if not rows:
        return [available_width]
    column_count = max(len(row) for row in rows)
    weights: list[float] = []
    for index in range(column_count):
        lengths = [len(normalize_whitespace(row[index])) for row in rows if index < len(row)]
        weights.append(max(max(lengths, default=0), 8))
    total = sum(weights) or 1.0
    widths = [available_width * weight / total for weight in weights]
    min_width = 0.8 * inch
    max_width = 3.5 * inch
    widths = [min(max(width, min_width), max_width) for width in widths]
    scale = available_width / (sum(widths) or 1.0)
    return [width * scale for width in widths]


def escape_pipe(text: str) -> str:
    return normalize_whitespace(text).replace("|", "\\|")


def render_notes_markdown(run: ResearchRun) -> str:
    lines: list[str] = []
    lines.append(f"# Research Notes: {run.query}")
    lines.append("")
    if run.pages:
        lines.append("## Sources")
        lines.append("")
        for page in run.pages:
            lines.append(f"- {page.title}: {page.final_url or page.url}")
        lines.append("")
    if run.insights:
        lines.append("## Extracted Notes")
        lines.append("")
        for insight in run.insights:
            lines.append(f"- [{insight.category}] {insight.text}")
        lines.append("")
    if run.context_hits:
        lines.append("## Memory Hits")
        lines.append("")
        for hit in run.context_hits:
            lines.append(f"- {clip(str(hit), 220)}")
    return "\n".join(lines).strip() + "\n"


def _parse_markdown_sections(markdown_text: str) -> dict:
    lines = [line.rstrip() for line in markdown_text.splitlines()]
    result = {"meta": [], "executive_summary": [], "key_findings": [], "comparison_rows": [], "gaps": [], "references": []}
    current = None
    finding = None
    in_table = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("# Research Report:"):
            result["title"] = stripped.replace("# ", "", 1)
            continue
        if stripped == "## Executive Summary":
            current = "executive_summary"
            finding = None
            in_table = False
            continue
        if stripped == "## Key Findings":
            current = "key_findings"
            finding = None
            in_table = False
            continue
        if stripped == "## Source Comparison":
            current = "comparison"
            finding = None
            in_table = True
            continue
        if stripped == "## Gaps and Limitations":
            current = "gaps"
            finding = None
            in_table = False
            continue
        if stripped == "## References":
            current = "references"
            finding = None
            in_table = False
            continue
        if stripped.startswith("- Run ID:") or stripped.startswith("- Depth:") or stripped.startswith("- Started:") or stripped.startswith("- Finished:"):
            result["meta"].append(_clean_md_bullet(stripped))
            continue
        if current == "executive_summary" and stripped.startswith("- "):
            result["executive_summary"].append(_clean_md_bullet(stripped))
            continue
        if current == "gaps" and stripped.startswith("- "):
            result["gaps"].append(_clean_md_bullet(stripped))
            continue
        if current == "references" and stripped.startswith("["):
            result["references"].append(_clean_md_bullet(stripped))
            continue
        if current == "key_findings":
            if re.match(r"^\d+\.\s+", stripped):
                headline = re.sub(r"^\d+\.\s+", "", stripped)
                headline = headline.replace("  ", "").strip()
                finding = {"headline": headline, "details": []}
                result["key_findings"].append(finding)
                continue
            if stripped.startswith("- ") and finding is not None:
                finding["details"].append(_clean_md_bullet(stripped))
                continue
        if current == "comparison" and stripped.startswith("|"):
            if stripped.startswith("| ---") or stripped.startswith("| Source "):
                continue
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            if len(cells) >= 5:
                result["comparison_rows"].append({
                    "source": cells[0],
                    "domain": cells[1],
                    "credibility": cells[2],
                    "quality": cells[3],
                    "summary": cells[4],
                })
            continue
    return result


def _make_key_value_table(rows: list[str]):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

    data = []
    for row in rows:
        if ":" in row:
            left, right = row.split(":", 1)
            data.append([Paragraph(f"<b>{_escape_inline(left)}</b>", _styles()["SmallText"]), Paragraph(_escape_inline(right.strip()), _styles()["SmallText"])])
    if not data:
        return Spacer(1, 0)
    table = Table(data, colWidths=[1.4 * inch, 4.7 * inch])
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7FA")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD2D9")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _make_source_table(rows: list[dict]):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    styles = _styles()
    data = [["Source", "Domain", "Credibility", "Quality", "Summary"]]
    for row in rows:
        data.append([
            Paragraph(_escape_inline(row.get("source", "")), styles["SmallText"]),
            Paragraph(_escape_inline(row.get("domain", "")), styles["SmallText"]),
            Paragraph(_escape_inline(row.get("credibility", "")), styles["SmallText"]),
            Paragraph(_escape_inline(row.get("quality", "")), styles["SmallText"]),
            Paragraph(_escape_inline(row.get("summary", "")), styles["SmallText"]),
        ])
    table = Table(data, colWidths=[1.35 * inch, 1.0 * inch, 0.75 * inch, 0.65 * inch, 2.75 * inch], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#102A43")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD2D9")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D9E2EC")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def _bullets(items: list[str], styles, indent: int = 0):
    from reportlab.platypus import Paragraph, Spacer

    flows = []
    for item in items:
        flows.append(Paragraph(f"• {_escape_inline(item)}", styles["BulletText"]))
    if not flows:
        flows.append(Paragraph("• None", styles["BulletText"]))
    flows.append(Spacer(1, 4))
    return flows


def _section_heading(text: str, styles):
    from reportlab.platypus import Paragraph

    return Paragraph(_escape_inline(text), styles["SectionHeader"])


def _escape_inline(text: str) -> str:
    return normalize_whitespace(str(text)).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _clean_md_bullet(text: str) -> str:
    return normalize_whitespace(text.lstrip("- ").strip())


def _styles():
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    if "SmallText" not in styles:
        styles.add(ParagraphStyle(name="SmallText", fontName="Helvetica", fontSize=8, leading=10, textColor=colors.HexColor("#243B53"), alignment=TA_LEFT))
    return styles


def _json_default(value):
    if isinstance(value, Path):
        return str(value)
    return str(value)
