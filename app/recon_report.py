from __future__ import annotations

from collections import Counter
from datetime import datetime
from pathlib import Path


def _safe_text(value) -> str:
    return str(value or "").strip()


def create_instalab_recon_report(
    *,
    output_path: str | Path,
    mode: str,
    query_value: str,
    findings: list[dict],
    generated_at: datetime | None = None,
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.utcnow()

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "instalab_title",
        parent=styles["Title"],
        fontSize=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=14,
    )
    subtitle = ParagraphStyle(
        "instalab_subtitle",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#334155"),
        spaceAfter=8,
    )
    small = ParagraphStyle(
        "instalab_small",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#475569"),
    )

    confidence = Counter(_safe_text(f.get("confidence_tier")).lower() or "low" for f in findings)
    categories = Counter(_safe_text(f.get("category")).lower() or "uncategorized" for f in findings)
    top_categories = categories.most_common(5)

    story = []
    story.append(Paragraph("InstaLab Recon Report", title))
    story.append(
        Paragraph(
            f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}<br/>"
            f"Mode: <b>{_safe_text(mode) or 'unknown'}</b><br/>"
            f"Query: <b>{_safe_text(query_value) or 'unknown'}</b>",
            subtitle,
        )
    )
    story.append(Spacer(1, 0.1 * inch))

    summary_data = [
        ["Metric", "Value"],
        ["Total findings", str(len(findings))],
        ["High confidence", str(confidence.get("high", 0))],
        ["Medium confidence", str(confidence.get("medium", 0))],
        ["Low confidence", str(confidence.get("low", 0))],
    ]
    if top_categories:
        summary_data.append(["Top categories", ", ".join(name for name, _ in top_categories)])

    summary_table = Table(summary_data, colWidths=[2.0 * inch, 4.9 * inch])
    summary_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0f172a")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
                ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
                ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    story.append(summary_table)
    story.append(Spacer(1, 0.2 * inch))

    story.append(Paragraph("Findings", styles["Heading3"]))
    if not findings:
        story.append(Paragraph("No findings were returned for this query.", small))
    else:
        finding_rows = [["Platform", "Confidence", "Status", "Category", "URL"]]
        for item in findings[:250]:
            finding_rows.append(
                [
                    _safe_text(item.get("platform")) or "-",
                    (_safe_text(item.get("confidence_tier")).lower() or "low"),
                    _safe_text(item.get("tool_status")) or "-",
                    _safe_text(item.get("category")) or "-",
                    _safe_text(item.get("url")) or "-",
                ]
            )
        finding_table = Table(
            finding_rows,
            colWidths=[1.5 * inch, 0.9 * inch, 0.9 * inch, 1.1 * inch, 2.5 * inch],
            repeatRows=1,
        )
        finding_table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e293b")),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                    ("FONTSIZE", (0, 0), (-1, -1), 8),
                    ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cbd5e1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]
            )
        )
        story.append(finding_table)
        if len(findings) > 250:
            story.append(Spacer(1, 0.1 * inch))
            story.append(Paragraph(f"Showing first 250 of {len(findings)} findings.", small))

    story.append(Spacer(1, 0.2 * inch))
    story.append(
        Paragraph(
            "Analyst note: OSINT matches can include false positives. Validate critical findings before action.",
            small,
        )
    )

    doc = SimpleDocTemplate(
        str(output),
        pagesize=letter,
        leftMargin=0.5 * inch,
        rightMargin=0.5 * inch,
        topMargin=0.55 * inch,
        bottomMargin=0.55 * inch,
    )
    doc.build(story)
