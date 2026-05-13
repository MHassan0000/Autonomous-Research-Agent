from __future__ import annotations

import json
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
        from reportlab.lib.enums import TA_LEFT, TA_CENTER
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except Exception:
        pdf_path.write_text(markdown_text, encoding="utf-8")
        return

    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="ReportTitle",
            parent=styles["Title"],
            fontName="Helvetica-Bold",
            fontSize=22,
            leading=26,
            textColor=colors.HexColor("#102A43"),
            alignment=TA_LEFT,
            spaceAfter=10,
        )
    )
    styles.add(
        ParagraphStyle(
            name="ReportSubTitle",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            textColor=colors.HexColor("#52606D"),
            spaceAfter=12,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SectionHeader",
            parent=styles["Heading1"],
            fontName="Helvetica-Bold",
            fontSize=14,
            leading=18,
            textColor=colors.HexColor("#102A43"),
            spaceBefore=10,
            spaceAfter=8,
            keepWithNext=True,
        )
    )
    styles.add(
        ParagraphStyle(
            name="SmallText",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=8,
            leading=10,
            textColor=colors.HexColor("#52606D"),
        )
    )
    styles.add(
        ParagraphStyle(
            name="BulletText",
            parent=styles["BodyText"],
            fontName="Helvetica",
            fontSize=9,
            leading=12,
            leftIndent=12,
            bulletIndent=0,
            spaceAfter=3,
        )
    )

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

    story = []
    data = _parse_markdown_sections(markdown_text)
    title = data.get("title", "Research Report")
    meta = data.get("meta", [])
    executive_summary = data.get("executive_summary", [])
    key_findings = data.get("key_findings", [])
    comparison_rows = data.get("comparison_rows", [])
    gaps = data.get("gaps", [])
    references = data.get("references", [])

    story.append(Paragraph(title, styles["ReportTitle"]))
    story.append(Paragraph("Autonomous Research Agent | analyst-style report", styles["ReportSubTitle"]))
    if meta:
        story.append(_make_key_value_table(meta))
        story.append(Spacer(1, 10))
    story.append(_section_heading("Executive Summary", styles))
    story.extend(_bullets(executive_summary or ["No summary extracted."], styles))
    story.append(_section_heading("Key Findings", styles))
    if key_findings:
        for item in key_findings:
            story.append(Paragraph(f"<b>{_escape_inline(item.get('headline', 'Finding'))}</b>", styles["BodyText"]))
            if item.get("details"):
                story.extend(_bullets(item["details"], styles, indent=10))
            story.append(Spacer(1, 4))
    else:
        story.extend(_bullets(["No findings extracted."], styles))
    story.append(_section_heading("Source Comparison", styles))
    if comparison_rows:
        story.append(_make_source_table(comparison_rows))
    else:
        story.extend(_bullets(["No sources retained."], styles))
    story.append(_section_heading("Gaps and Limitations", styles))
    story.extend(_bullets(gaps or ["No material gaps were detected."], styles))
    story.append(_section_heading("References", styles))
    if references:
        story.extend(_bullets(references, styles))
    else:
        story.extend(_bullets(["No references collected."], styles))

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
