"""Build the Ripple hackathon PDF from RIPPLE-HACKATHON-DOCS.md.

A small, purpose-built Markdown -> ReportLab renderer that supports exactly the
subset used by the document: headings, paragraphs, bullets, numbered lists,
GFM tables, fenced code, blockquotes, images, and the <!-- diagram:pipeline -->
directive. Styling is tuned for a clean technical deliverable.

Run:  python ripple/docs/build_ripple_pdf.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Flowable, Frame, HRFlowable, Image, KeepTogether,
    NextPageTemplate, PageBreak, PageTemplate, Paragraph, Preformatted,
    Spacer, Table, TableStyle,
)
from reportlab.platypus.tableofcontents import TableOfContents

DOC_DIR = Path(__file__).resolve().parent
MD_PATH = DOC_DIR / "RIPPLE-HACKATHON-DOCS.md"
OUT_PATH = DOC_DIR / "Ripple-Hackathon-Documentation.pdf"

PAGE_W, PAGE_H = A4
MARGIN = 46
CONTENT_W = PAGE_W - 2 * MARGIN

SLATE_950 = colors.HexColor("#020617")
SLATE_900 = colors.HexColor("#0f172a")
SLATE_800 = colors.HexColor("#1e293b")
SLATE_700 = colors.HexColor("#334155")
SLATE_600 = colors.HexColor("#475569")
SLATE_500 = colors.HexColor("#64748b")
SLATE_400 = colors.HexColor("#94a3b8")
SLATE_300 = colors.HexColor("#cbd5e1")
SLATE_200 = colors.HexColor("#e2e8f0")
SLATE_100 = colors.HexColor("#f1f5f9")
SLATE_50 = colors.HexColor("#f8fafc")
GREEN = colors.HexColor("#22c55e")
RED = colors.HexColor("#dc2626")

PALETTE = ["#22c55e", "#a3e635", "#facc15", "#f97316", "#dc2626"]

FONT = "SegoeUI"
FONT_B = "SegoeUI-Bold"
FONT_I = "SegoeUI-Italic"
FONT_BI = "SegoeUI-BoldItalic"
MONO = "Consolas"
MONO_B = "Consolas-Bold"


def register_fonts() -> None:
    fonts = Path(r"C:\Windows\Fonts")
    pdfmetrics.registerFont(TTFont(FONT, str(fonts / "segoeui.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_B, str(fonts / "segoeuib.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_I, str(fonts / "segoeuii.ttf")))
    pdfmetrics.registerFont(TTFont(FONT_BI, str(fonts / "segoeuiz.ttf")))
    pdfmetrics.registerFont(TTFont(MONO, str(fonts / "consola.ttf")))
    pdfmetrics.registerFont(TTFont(MONO_B, str(fonts / "consolab.ttf")))
    pdfmetrics.registerFontFamily(FONT, normal=FONT, bold=FONT_B,
                                  italic=FONT_I, boldItalic=FONT_BI)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


INLINE_CODE = re.compile(r"`([^`]+)`")
BOLD = re.compile(r"\*\*(.+?)\*\*")
ITALIC = re.compile(r"\*(.+?)\*")
LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline(text: str) -> str:
    text = esc(text)
    text = INLINE_CODE.sub(
        lambda m: f'<font face="{MONO}" size="9" color="#0f172a">{m.group(1)}</font>',
        text,
    )
    text = BOLD.sub(r"<b>\1</b>", text)
    text = ITALIC.sub(r"<i>\1</i>", text)
    text = LINK.sub(r"<u>\1</u>", text)
    return text


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

S = {}
S["body"] = ParagraphStyle(
    "body", fontName=FONT, fontSize=9.8, leading=13.9, textColor=SLATE_800,
    spaceAfter=6.5, alignment=TA_LEFT,
)
S["h2"] = ParagraphStyle(
    "h2", fontName=FONT_B, fontSize=19, leading=23, textColor=SLATE_900,
    spaceAfter=2,
)
S["h3"] = ParagraphStyle(
    "h3", fontName=FONT_B, fontSize=13, leading=16, textColor=SLATE_900,
    spaceBefore=12, spaceAfter=4.5,
)
S["h4"] = ParagraphStyle(
    "h4", fontName=FONT_B, fontSize=11, leading=14, textColor=SLATE_700,
    spaceBefore=10, spaceAfter=4,
)
S["bullet"] = ParagraphStyle(
    "bullet", parent=S["body"], leftIndent=15, bulletIndent=4, spaceAfter=4,
)
S["bullet2"] = ParagraphStyle(
    "bullet2", parent=S["body"], leftIndent=30, bulletIndent=19, spaceAfter=3.5,
    textColor=SLATE_600, fontSize=9.4, leading=13.4,
)
S["number"] = ParagraphStyle(
    "number", parent=S["body"], leftIndent=18, bulletIndent=4, spaceAfter=5,
)
S["code"] = ParagraphStyle(
    "code", fontName=MONO, fontSize=7.7, leading=10.2, textColor=SLATE_900,
    backColor=SLATE_50, borderColor=SLATE_200, borderWidth=0.6, borderPadding=7,
    spaceBefore=4, spaceAfter=9,
)
S["quote"] = ParagraphStyle(
    "quote", fontName=FONT_I, fontSize=9.5, leading=13.6, textColor=SLATE_600,
    leftIndent=10, backColor=SLATE_50, borderPadding=7, spaceAfter=8,
)
S["caption"] = ParagraphStyle(
    "caption", fontName=FONT_I, fontSize=8.2, leading=10.6, textColor=SLATE_500,
    alignment=TA_CENTER, spaceBefore=3, spaceAfter=11,
)
S["th"] = ParagraphStyle(
    "th", fontName=FONT_B, fontSize=8.3, leading=10.6, textColor=colors.white,
)
S["td"] = ParagraphStyle(
    "td", fontName=FONT, fontSize=8.3, leading=10.6, textColor=SLATE_800,
)
S["toc_title"] = ParagraphStyle(
    "toc_title", fontName=FONT_B, fontSize=19, leading=23, textColor=SLATE_900,
    spaceAfter=10,
)
S["diagram_title"] = ParagraphStyle(
    "diagram_title", fontName=FONT_B, fontSize=8.6, leading=11, textColor=SLATE_900,
)
S["diagram_body"] = ParagraphStyle(
    "diagram_body", fontName=FONT, fontSize=7.6, leading=10, textColor=SLATE_600,
)
S["diagram_arrow"] = ParagraphStyle(
    "diagram_arrow", fontName=FONT_B, fontSize=9, leading=10, textColor=GREEN,
    alignment=TA_CENTER,
)


# ---------------------------------------------------------------------------
# Diagram: the pipeline flow
# ---------------------------------------------------------------------------

PIPELINE_STAGES = [
    ("1. STAC search", "1.5° cells, 12 candidates per cell per window; each window searched separately (R6); scenes ranked by AOI coverage (R2); ~360 pure-ocean cells skipped"),
    ("2. Read + validate", "Windowed HTTP-range COG reads, decimated before warping; reflectance averaged, SCL nearest at 2×; scaling derived and asserted, scene halted on failure (R1, R3)"),
    ("3. Mask water", "SCL majority-voted per cell (≥ 25% water), reflectance indices never used (R4); Natural Earth ocean subtracted (R9); region-edge water removed"),
    ("4. Index + composite", "NDCI and NDTI at grid resolution; newest usable pixel wins per 12-day window (R5); each scene read once and folded into all overlapping windows (R7)"),
    ("5. Discover bodies", "Sieve + polygonise the mask; patches ≥ 20 km² measured in ground km² (Mercator-corrected); named from catalog within 25 km or by coordinates (R8)"),
    ("6. Forecast", "Open-Meteo rainfall → SCS curve-number runoff → loading anomaly per cell; per-body forecast from each body's own rain, with its inputs reported"),
    ("7. Emit", "Display tile (≤ 4096 px) + probe tile (1024 px), lossless WebP, per frame; schema-v2 manifest.json; stale tiles pruned"),
]


class PipelineDiagram(Flowable):
    """Vertical stage chain with arrows, drawn as a table-like flowable."""

    def __init__(self, width: float):
        super().__init__()
        self.width = width
        self._table = self._build()

    def _build(self) -> Table:
        rows = []
        for index, (title, desc) in enumerate(PIPELINE_STAGES):
            cell = Paragraph(
                f'<font color="#0f172a"><b>{esc(title)}</b></font><br/>{esc(desc)}',
                S["diagram_body"],
            )
            rows.append([cell])
            if index < len(PIPELINE_STAGES) - 1:
                rows.append([Paragraph("&#9660;", S["diagram_arrow"])])
        table = Table(rows, colWidths=[self.width])
        style = [
            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("BACKGROUND", (0, 0), (0, 0), SLATE_50),
            ("BOX", (0, 0), (0, 0), 0.7, SLATE_200),
            ("LINEBEFORE", (0, 0), (0, 0), 2.4, GREEN),
        ]
        for row in range(0, len(rows), 2):
            style.append(("TOPPADDING", (0, row), (0, row), 7))
            style.append(("BOTTOMPADDING", (0, row), (0, row), 7))
        table.setStyle(TableStyle(style))
        return table

    def wrap(self, availWidth, availHeight):
        w, h = self._table.wrap(availWidth, availHeight)
        self.height = h
        return self.width, h

    def draw(self):
        self._table.drawOn(self.canv, 0, 0)


# ---------------------------------------------------------------------------
# Markdown parsing
# ---------------------------------------------------------------------------

def is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and line.strip().endswith("|")


def split_row(line: str) -> list[str]:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return cells


def build_table(rows: list[list[str]], avail_width: float) -> Table:
    header, body = rows[0], rows[1:]
    n_cols = len(header)
    lengths = [0.0] * n_cols
    for row in rows:
        for i, cell in enumerate(row[:n_cols]):
            lengths[i] = max(lengths[i], len(re.sub(r"[`*]", "", cell)))
    weights = [max(6.0, min(42.0, value ** 0.72)) for value in lengths]
    total = sum(weights)
    col_widths = [avail_width * weight / total for weight in weights]

    data = [[Paragraph(inline(cell), S["th"]) for cell in header]]
    for row in body:
        padded = row + [""] * (n_cols - len(row))
        data.append([Paragraph(inline(cell), S["td"]) for cell in padded[:n_cols]])

    table = Table(data, colWidths=col_widths, repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), SLATE_900),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, SLATE_200),
        ("BOX", (0, 0), (-1, -1), 0.7, SLATE_300),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), SLATE_50))
    table.setStyle(TableStyle(style))
    return table


def image_flowable(path: Path, caption: str, avail_width: float) -> list:
    from PIL import Image as PILImage

    with PILImage.open(path) as im:
        iw, ih = im.size
    display_w = min(avail_width, avail_width)
    display_h = display_w * ih / iw
    max_h = 285
    if display_h > max_h:
        display_h = max_h
        display_w = display_h * iw / ih
    img = Image(str(path), width=display_w, height=display_h)
    img.hAlign = "CENTER"
    out = [Spacer(1, 4), img]
    if caption:
        out.append(Paragraph(inline(caption), S["caption"]))
    return out


class RippleDoc(BaseDocTemplate):
    def afterFlowable(self, flowable) -> None:
        if isinstance(flowable, Paragraph):
            style = flowable.style.name
            if style == "h2":
                self.notify("TOCEntry", (0, flowable.getPlainText(), self.page))
            elif style == "h3":
                self.notify("TOCEntry", (1, flowable.getPlainText(), self.page))


def parse_markdown(text: str, base_dir: Path) -> list:
    lines = text.splitlines()
    story: list = []
    i = 0
    first_chapter_seen = False

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i].rstrip("\n"))
                i += 1
            i += 1
            story.append(Preformatted("\n".join(code_lines), S["code"]))
            continue

        if stripped.startswith("<!-- diagram:pipeline"):
            story.append(Spacer(1, 4))
            story.append(PipelineDiagram(CONTENT_W))
            story.append(Spacer(1, 10))
            i += 1
            continue

        if stripped.startswith("<!--"):
            i += 1
            continue

        if is_table_row(line):
            rows = []
            while i < len(lines) and is_table_row(lines[i].strip()):
                cells = split_row(lines[i].strip())
                if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells):
                    rows.append(cells)
                i += 1
            if rows:
                story.append(Spacer(1, 3))
                story.append(build_table(rows, CONTENT_W))
                story.append(Spacer(1, 9))
            continue

        image_match = re.fullmatch(r"!\[([^\]]*)\]\(([^)]+)\)", stripped)
        if image_match:
            caption, rel = image_match.group(1), image_match.group(2)
            path = (base_dir / rel).resolve()
            if path.exists():
                story.extend(image_flowable(path, caption, CONTENT_W))
            i += 1
            continue

        if stripped.startswith("# "):
            i += 1
            continue

        if stripped.startswith("## "):
            title = stripped[3:].strip()
            if first_chapter_seen:
                story.append(PageBreak())
            first_chapter_seen = True
            story.append(Paragraph(inline(title), S["h2"]))
            story.append(Spacer(1, 2))
            story.append(HRFlowable(width="22%", thickness=2.4, color=GREEN,
                                    spaceBefore=1, spaceAfter=9, hAlign="LEFT"))
            i += 1
            continue

        if stripped.startswith("### "):
            story.append(Paragraph(inline(stripped[4:].strip()), S["h3"]))
            i += 1
            continue

        if stripped.startswith("#### "):
            story.append(Paragraph(inline(stripped[5:].strip()), S["h4"]))
            i += 1
            continue

        if stripped.startswith("> "):
            quote_lines = []
            while i < len(lines) and lines[i].strip().startswith("> "):
                quote_lines.append(lines[i].strip()[2:])
                i += 1
            story.append(Paragraph(inline(" ".join(quote_lines)), S["quote"]))
            continue

        if stripped == "---":
            story.append(Spacer(1, 4))
            story.append(HRFlowable(width="100%", thickness=0.6, color=SLATE_200,
                                    spaceBefore=2, spaceAfter=10))
            i += 1
            continue

        if re.match(r"^[-*] ", stripped):
            while i < len(lines) and re.match(r"^\s*[-*] ", lines[i]):
                indent = len(lines[i]) - len(lines[i].lstrip())
                content = re.sub(r"^\s*[-*] ", "", lines[i])
                style = S["bullet2"] if indent >= 2 else S["bullet"]
                story.append(Paragraph(inline(content), style, bulletText="\u2022"))
                i += 1
            continue

        number_match = re.match(r"^(\d+)\.\s+(.*)$", stripped)
        if number_match:
            while i < len(lines):
                nm = re.match(r"^(\d+)\.\s+(.*)$", lines[i].strip())
                if not nm:
                    break
                story.append(Paragraph(inline(nm.group(2)), S["number"],
                                       bulletText=f"{nm.group(1)}."))
                i += 1
            continue

        if stripped == "":
            i += 1
            continue

        paragraph_lines = [stripped]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (nxt == "" or nxt.startswith("#") or nxt.startswith("```")
                    or nxt.startswith("!") or nxt.startswith("> ")
                    or nxt.startswith("|") or nxt.startswith("<!--")
                    or nxt.startswith("- ") or nxt.startswith("* ")
                    or nxt == "---" or re.match(r"^\d+\.\s", nxt)):
                break
            paragraph_lines.append(nxt)
            i += 1
        story.append(Paragraph(inline(" ".join(paragraph_lines)), S["body"]))

    return story


# ---------------------------------------------------------------------------
# Cover and page frames
# ---------------------------------------------------------------------------

def draw_cover(canvas, doc) -> None:
    canvas.saveState()
    band_h = 330
    canvas.setFillColor(SLATE_950)
    canvas.rect(0, PAGE_H - band_h, PAGE_W, band_h, stroke=0, fill=1)

    canvas.setFillColor(colors.white)
    canvas.setFont(FONT_B, 46)
    canvas.drawString(MARGIN, PAGE_H - 150, "RIPPLE")
    canvas.setFont(FONT, 15)
    canvas.setFillColor(SLATE_400)
    canvas.drawString(MARGIN, PAGE_H - 178,
                      "A weather-radar-style forecast map for lake and river water quality")
    canvas.setFont(FONT, 11)
    canvas.setFillColor(SLATE_500)
    canvas.drawString(MARGIN, PAGE_H - 200,
                      "Water quality now  \u00b7  a 7-day runoff outlook  \u00b7  key-free by design")

    bar_y = PAGE_H - 246
    bar_w = PAGE_W - 2 * MARGIN
    seg = bar_w / len(PALETTE)
    for index, colour in enumerate(PALETTE):
        canvas.setFillColor(colors.HexColor(colour))
        canvas.rect(MARGIN + index * seg, bar_y, seg + 0.7, 10, stroke=0, fill=1)
    canvas.setFont(FONT, 8.4)
    canvas.setFillColor(SLATE_500)
    canvas.drawString(MARGIN, bar_y - 14, "cleaner")
    canvas.drawRightString(PAGE_W - MARGIN, bar_y - 14, "more polluted")

    canvas.setFont(FONT, 10);
    canvas.setFillColor(SLATE_400)
    canvas.drawString(MARGIN, PAGE_H - 300,
                      "Hackathon documentation \u2014 system, pipeline, science and verification")

    # Stat cards below the band.
    stats = [
        ("250", "water bodies\ndiscovered"),
        ("11", "frames: 4 observed\n+ 7 forecast"),
        ("1,800", "scenes in the\nlatest composite"),
        ("0", "API keys or\naccounts required"),
    ]
    card_w = (PAGE_W - 2 * MARGIN - 3 * 12) / 4
    card_h = 68
    card_y = PAGE_H - band_h - 74
    for index, (value, label) in enumerate(stats):
        x = MARGIN + index * (card_w + 12)
        canvas.setFillColor(SLATE_50)
        canvas.setStrokeColor(SLATE_200)
        canvas.setLineWidth(0.8)
        canvas.roundRect(x, card_y, card_w, card_h, 7, stroke=1, fill=1)
        canvas.setFillColor(GREEN if index == 3 else SLATE_900)
        canvas.setFont(FONT_B, 19)
        canvas.drawString(x + 12, card_y + card_h - 28, value)
        canvas.setFillColor(SLATE_500)
        canvas.setFont(FONT, 8)
        for line_index, part in enumerate(label.split("\n")):
            canvas.drawString(x + 12, card_y + card_h - 41 - line_index * 10.5, part)

    canvas.setFillColor(SLATE_600)
    canvas.setFont(FONT, 9.5)
    canvas.drawString(MARGIN, 96,
                      "Region: United States, southern Canada and Mexico  \u00b7  "
                      "Grid 4096 \u00d7 2777 @ ~1,848 m/px  \u00b7  EPSG:3857")
    canvas.drawString(MARGIN, 80,
                      "Built and verified end to end \u2014 169 pipeline tests, "
                      "90 app tests, 24 interaction checks, 0 console errors.")
    canvas.setFillColor(SLATE_400)
    canvas.setFont(FONT, 8.4)
    canvas.drawString(MARGIN, 60,
                      "Documentation generated from the shipped build \u00b7 September 2026")
    canvas.restoreState()


def draw_footer(canvas, doc) -> None:
    canvas.saveState()
    canvas.setStrokeColor(SLATE_200)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, 34, PAGE_W - MARGIN, 34)
    canvas.setFont(FONT, 7.8)
    canvas.setFillColor(SLATE_400)
    canvas.drawString(MARGIN, 24, "Ripple \u2014 Water-Quality Forecast Map")
    canvas.setFont(FONT_B, 8.6)
    canvas.setFillColor(SLATE_600)
    canvas.drawRightString(PAGE_W - MARGIN, 23, str(doc.page))
    canvas.restoreState()


def build() -> Path:
    register_fonts()
    markdown = MD_PATH.read_text(encoding="utf-8")

    title_para = Paragraph(
        "Ripple \u2014 Water-Quality Forecast Map",
        ParagraphStyle("cover_title", fontName=FONT_B, fontSize=1, textColor=colors.white),
    )

    toc = TableOfContents()
    toc.levelStyles = [
        ParagraphStyle("toc0", fontName=FONT_B, fontSize=10.2, leading=15,
                       textColor=SLATE_900, spaceBefore=5),
        ParagraphStyle("toc1", fontName=FONT, fontSize=9, leading=13,
                       textColor=SLATE_600, leftIndent=14, spaceBefore=1),
    ]
    toc.dotsMinLevel = 0

    content_story = parse_markdown(markdown, DOC_DIR)

    story = [
        NextPageTemplate("content"),
        PageBreak(),
        Paragraph("Contents", S["toc_title"]),
        HRFlowable(width="22%", thickness=2.4, color=GREEN, spaceAfter=10, hAlign="LEFT"),
        toc,
        PageBreak(),
    ] + content_story

    doc = RippleDoc(
        str(OUT_PATH), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=44, bottomMargin=48,
        title="Ripple — Water-Quality Forecast Map (Hackathon Documentation)",
        author="Ripple",
        subject="System, pipeline, science and verification documentation",
    )
    frame = Frame(MARGIN, 44, CONTENT_W, PAGE_H - 44 - 46, id="normal",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[frame], onPage=draw_cover),
        PageTemplate(id="content", frames=[frame], onPage=draw_footer),
    ])

    doc.multiBuild(story)
    return OUT_PATH


if __name__ == "__main__":
    path = build()
    size = path.stat().st_size
    print(f"wrote {path} ({size / 1024:.0f} KB)")
