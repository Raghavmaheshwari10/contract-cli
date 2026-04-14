"""Convert BRD markdown to a professional PDF and shareable HTML."""
import re
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib.colors import HexColor
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    PageBreak, HRFlowable, KeepTogether,
)
from reportlab.lib import colors

# ── Read markdown source ────────────────────────────────────────────────
with open("BRD_EMB_CLM.md", "r") as f:
    md = f.read()

# ── Colors ──────────────────────────────────────────────────────────────
NAVY = HexColor("#0f172a")
BLUE = HexColor("#2563eb")
SLATE = HexColor("#334155")
LIGHT_SLATE = HexColor("#64748b")
LIGHT_BG = HexColor("#f8fafc")
BORDER = HexColor("#e2e8f0")
WHITE = colors.white
BLACK = colors.black

# ── Styles ──────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

styles.add(ParagraphStyle(
    "DocTitle", fontName="Helvetica-Bold", fontSize=24, leading=30,
    textColor=NAVY, alignment=TA_CENTER, spaceAfter=6,
))
styles.add(ParagraphStyle(
    "DocSubtitle", fontName="Helvetica", fontSize=14, leading=18,
    textColor=BLUE, alignment=TA_CENTER, spaceAfter=20,
))
styles.add(ParagraphStyle(
    "H2", fontName="Helvetica-Bold", fontSize=15, leading=20,
    textColor=NAVY, spaceBefore=22, spaceAfter=8,
    borderWidth=0, borderPadding=0,
))
styles.add(ParagraphStyle(
    "H3", fontName="Helvetica-Bold", fontSize=12, leading=16,
    textColor=SLATE, spaceBefore=14, spaceAfter=6,
))
styles.add(ParagraphStyle(
    "H4", fontName="Helvetica-Bold", fontSize=10.5, leading=14,
    textColor=LIGHT_SLATE, spaceBefore=10, spaceAfter=4,
))
styles.add(ParagraphStyle(
    "Body", fontName="Helvetica", fontSize=10, leading=14,
    textColor=SLATE, alignment=TA_JUSTIFY, spaceAfter=6,
))
styles.add(ParagraphStyle(
    "BulletItem", fontName="Helvetica", fontSize=10, leading=14,
    textColor=SLATE, leftIndent=18, bulletIndent=6, spaceAfter=3,
))
styles.add(ParagraphStyle(
    "CodeBlock", fontName="Courier", fontSize=8.5, leading=12,
    textColor=HexColor("#e2e8f0"), backColor=NAVY,
    leftIndent=10, rightIndent=10, spaceBefore=6, spaceAfter=6,
    borderWidth=0, borderPadding=8,
))
styles.add(ParagraphStyle(
    "TableCell", fontName="Helvetica", fontSize=8.5, leading=11,
    textColor=SLATE,
))
styles.add(ParagraphStyle(
    "TableHeader", fontName="Helvetica-Bold", fontSize=8.5, leading=11,
    textColor=WHITE,
))
styles.add(ParagraphStyle(
    "MetaLabel", fontName="Helvetica-Bold", fontSize=10, leading=13,
    textColor=NAVY,
))
styles.add(ParagraphStyle(
    "MetaValue", fontName="Helvetica", fontSize=10, leading=13,
    textColor=SLATE,
))
styles.add(ParagraphStyle(
    "Footer", fontName="Helvetica", fontSize=8, leading=10,
    textColor=LIGHT_SLATE, alignment=TA_CENTER,
))

# ── Helper: inline markdown to reportlab XML ────────────────────────────
def inline(text):
    """Convert inline markdown (bold, code, links) to reportlab XML."""
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'`(.+?)`', r'<font face="Courier" size="9" color="#e11d48">\1</font>', text)
    text = re.sub(r'\[([^\]]+)\]\([^\)]+\)', r'\1', text)  # strip links
    return text

# ── Parse markdown into blocks ──────────────────────────────────────────
def parse_md(md_text):
    """Parse markdown into a list of (type, content) tuples."""
    blocks = []
    lines = md_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]

        # Blank line
        if not line.strip():
            i += 1
            continue

        # Horizontal rule
        if line.strip() == "---":
            blocks.append(("hr", None))
            i += 1
            continue

        # Headers
        if line.startswith("# "):
            blocks.append(("h1", line[2:].strip()))
            i += 1
            continue
        if line.startswith("## "):
            blocks.append(("h2", line[3:].strip()))
            i += 1
            continue
        if line.startswith("### "):
            blocks.append(("h3", line[4:].strip()))
            i += 1
            continue
        if line.startswith("#### "):
            blocks.append(("h4", line[5:].strip()))
            i += 1
            continue

        # Code block
        if line.strip().startswith("```"):
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1  # skip closing ```
            blocks.append(("code", "\n".join(code_lines)))
            continue

        # Table
        if "|" in line and i + 1 < len(lines) and re.match(r'^[\|\s\-:]+$', lines[i + 1]):
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                if not re.match(r'^[\|\s\-:]+$', lines[i]):
                    cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                    table_lines.append(cells)
                i += 1
            blocks.append(("table", table_lines))
            continue

        # Bullet list
        if re.match(r'^[\-\*]\s', line.strip()):
            items = []
            while i < len(lines) and re.match(r'^[\-\*]\s', lines[i].strip()):
                items.append(re.sub(r'^[\-\*]\s+', '', lines[i].strip()))
                i += 1
            blocks.append(("bullets", items))
            continue

        # Numbered list
        if re.match(r'^\d+\.\s', line.strip()):
            items = []
            while i < len(lines) and re.match(r'^\d+\.\s', lines[i].strip()):
                items.append(re.sub(r'^\d+\.\s+', '', lines[i].strip()))
                i += 1
            blocks.append(("numbered", items))
            continue

        # Regular paragraph
        para_lines = []
        while i < len(lines) and lines[i].strip() and not lines[i].startswith("#") \
                and not lines[i].strip().startswith("```") and not lines[i].strip() == "---" \
                and not ("|" in lines[i] and i + 1 < len(lines) and "|" in lines[i + 1]) \
                and not re.match(r'^[\-\*]\s', lines[i].strip()) \
                and not re.match(r'^\d+\.\s', lines[i].strip()):
            para_lines.append(lines[i].strip())
            i += 1
        if para_lines:
            blocks.append(("para", " ".join(para_lines)))

    return blocks


# ── Build PDF ───────────────────────────────────────────────────────────
def build_pdf(blocks):
    elements = []
    is_first_h1 = True

    for btype, content in blocks:
        if btype == "h1":
            if is_first_h1:
                elements.append(Spacer(1, 60))
                elements.append(Paragraph(inline(content), styles["DocTitle"]))
                is_first_h1 = False
            else:
                elements.append(Paragraph(inline(content), styles["H2"]))

        elif btype == "h2":
            # Check if it's the subtitle (right after h1)
            if len(elements) <= 3 and "EMB CLM" in content:
                elements.append(Paragraph(inline(content), styles["DocSubtitle"]))
                elements.append(Spacer(1, 10))
            else:
                elements.append(Spacer(1, 4))
                elements.append(HRFlowable(width="100%", thickness=1.5, color=BORDER))
                elements.append(Paragraph(inline(content), styles["H2"]))

        elif btype == "h3":
            elements.append(Paragraph(inline(content), styles["H3"]))

        elif btype == "h4":
            elements.append(Paragraph(inline(content), styles["H4"]))

        elif btype == "para":
            elements.append(Paragraph(inline(content), styles["Body"]))

        elif btype == "hr":
            elements.append(Spacer(1, 6))

        elif btype == "bullets":
            for item in content:
                elements.append(Paragraph(
                    f"<bullet>&bull;</bullet> {inline(item)}", styles["BulletItem"]
                ))
            elements.append(Spacer(1, 4))

        elif btype == "numbered":
            for idx, item in enumerate(content, 1):
                elements.append(Paragraph(
                    f"<bullet>{idx}.</bullet> {inline(item)}", styles["BulletItem"]
                ))
            elements.append(Spacer(1, 4))

        elif btype == "code":
            # Split into lines and render
            code_text = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            code_text = code_text.replace("\n", "<br/>")
            elements.append(Paragraph(code_text, styles["CodeBlock"]))

        elif btype == "table":
            if not content or len(content) < 1:
                continue
            headers = content[0]
            rows = content[1:]
            num_cols = len(headers)

            # Build table data
            table_data = []
            header_row = [Paragraph(inline(h), styles["TableHeader"]) for h in headers]
            table_data.append(header_row)
            for row in rows:
                # Pad row if needed
                while len(row) < num_cols:
                    row.append("")
                table_data.append([
                    Paragraph(inline(c), styles["TableCell"]) for c in row[:num_cols]
                ])

            # Calculate column widths
            avail = 170 * mm
            col_widths = [avail / num_cols] * num_cols

            # Build table
            tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
            style_cmds = [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 8.5),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 6),
                ("TOPPADDING", (0, 0), (-1, 0), 6),
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
                ("FONTSIZE", (0, 1), (-1, -1), 8.5),
                ("TOPPADDING", (0, 1), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
            # Alternate row colors
            for row_idx in range(1, len(table_data)):
                if row_idx % 2 == 0:
                    style_cmds.append(("BACKGROUND", (0, row_idx), (-1, row_idx), LIGHT_BG))

            tbl.setStyle(TableStyle(style_cmds))
            elements.append(tbl)
            elements.append(Spacer(1, 8))

    return elements


# ── Page template with header/footer ────────────────────────────────────
def on_page(canvas, doc):
    canvas.saveState()
    # Footer
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(LIGHT_SLATE)
    canvas.drawCentredString(
        A4[0] / 2, 15 * mm,
        f"EMB CLM -- Business Requirements Document v2.0  |  Page {doc.page}"
    )
    # Header line (skip first page)
    if doc.page > 1:
        canvas.setStrokeColor(BORDER)
        canvas.setLineWidth(0.5)
        canvas.line(18 * mm, A4[1] - 15 * mm, A4[0] - 18 * mm, A4[1] - 15 * mm)
    canvas.restoreState()


# ── Generate PDF ────────────────────────────────────────────────────────
pdf_path = "BRD_EMB_CLM.pdf"
doc = SimpleDocTemplate(
    pdf_path, pagesize=A4,
    leftMargin=20 * mm, rightMargin=20 * mm,
    topMargin=20 * mm, bottomMargin=25 * mm,
    title="EMB CLM - Business Requirements Document",
    author="EMB / Mantarav Private Limited",
)

blocks = parse_md(md)
elements = build_pdf(blocks)
doc.build(elements, onFirstPage=on_page, onLaterPages=on_page)
print(f"Created {pdf_path} -- ready to share!")

# ── Also generate HTML ──────────────────────────────────────────────────
try:
    import markdown
    html_body = markdown.markdown(md, extensions=["tables", "fenced_code", "toc"])
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>EMB CLM -- Business Requirements Document</title>
<style>
@page {{ size: A4; margin: 20mm 18mm; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, Arial, sans-serif;
  font-size: 11pt; line-height: 1.6; color: #1a1a2e; background: #fff;
  padding: 40px 60px; max-width: 900px; margin: 0 auto; }}
h1 {{ font-size: 26pt; font-weight: 700; color: #0f172a; border-bottom: 3px solid #2563eb;
  padding-bottom: 12px; margin: 0 0 20px 0; }}
h2 {{ font-size: 16pt; font-weight: 700; color: #1e3a5f; margin: 36px 0 14px 0;
  padding-bottom: 6px; border-bottom: 2px solid #e2e8f0; page-break-after: avoid; }}
h3 {{ font-size: 13pt; font-weight: 600; color: #334155; margin: 24px 0 10px 0; }}
h4 {{ font-size: 11pt; font-weight: 600; color: #475569; margin: 16px 0 8px 0; }}
p {{ margin: 0 0 10px 0; text-align: justify; }}
strong {{ color: #0f172a; }}
ul, ol {{ margin: 6px 0 14px 24px; }}
li {{ margin-bottom: 4px; }}
hr {{ border: none; border-top: 1px solid #cbd5e1; margin: 28px 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 12px 0 20px 0;
  font-size: 10pt; page-break-inside: avoid; }}
th {{ background: #0f172a; color: #fff; font-weight: 600; padding: 8px 10px;
  text-align: left; font-size: 9.5pt; }}
td {{ padding: 7px 10px; border-bottom: 1px solid #e2e8f0; vertical-align: top; }}
tr:nth-child(even) {{ background: #f8fafc; }}
code {{ font-family: 'Consolas', monospace; font-size: 9.5pt; background: #f1f5f9;
  padding: 1px 5px; border-radius: 3px; color: #e11d48; }}
pre {{ background: #0f172a; color: #e2e8f0; padding: 16px 20px; border-radius: 8px;
  overflow-x: auto; margin: 12px 0 20px 0; font-size: 9pt; line-height: 1.5; }}
pre code {{ background: none; color: #e2e8f0; padding: 0; }}
a {{ color: #2563eb; text-decoration: none; }}
em:last-child {{ display: block; text-align: center; color: #94a3b8; font-size: 9pt;
  margin-top: 40px; padding-top: 20px; border-top: 1px solid #e2e8f0; }}
@media print {{ body {{ padding: 0; font-size: 10pt; }}
  table, pre {{ page-break-inside: avoid; }} a[href]:after {{ content: none; }} }}
</style>
</head>
<body>{html_body}</body></html>"""
    with open("BRD_EMB_CLM.html", "w") as f:
        f.write(html)
    print("Also updated BRD_EMB_CLM.html")
except ImportError:
    print("(markdown package not installed -- skipped HTML generation)")
