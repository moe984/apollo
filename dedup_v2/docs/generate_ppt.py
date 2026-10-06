"""
Generate the Apollo presentation: Deduplication, Named Entity Recognition,
Fingerprinting and Detection Analytics.

Carbon Design System colors, dark deck, 16:9.

Everything in this deck is drawn from the code that runs today:

  Dedup engine      cip/services/ml-service/src/services/dedup/
                      stix_parser.py | ioc_normalizer.py | fingerprinter.py
                      layer1.py | layer2.py | stix_equivalence.py
                      redis_client.py | policyforge.py | engine.py
                    cip/services/ml-service/config/dedup_defaults.yaml
  NER engine        cip/services/ml-service/src/services/ner/
                      engine.py | model_loader.py | config.py
                    cip/services/ml-service/src/api/routes/ner.py
  Apollo side       apollo/src/ingestion/dedup/   (service client, root-canonical,
                      canonical-divergence, deduplicated-event)
                    apollo/src/ingestion/ner/     (extract-entities, ner-queue,
                      ner-audit, ner-service-client)
                    apollo/src/ai-models/         (entity-fingerprint,
                      entity-threshold, entity-exclusions, entity-snapshot,
                      catalog)
                    apollo/src/alert-dispositions/layer0/  (slots, slot-hash)
                    apollo/src/routes/dedup-verdict.ts
  Analytics         apollo/src/routes/dashboard/detection-analytics.ts
                    apollo/src/routes/dashboard/dedup-analytics.ts
                    apollo/src/metrics/dedup-analytics.ts
                    apollo/dashboard/src/components/detection-analytics/

Usage:  python generate_ppt.py
Output: docs/apollo_dedup_presentation.pptx
"""

import os

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_SHAPE

# ---------------------------------------------------------------------------
# Carbon Design System Colors
# ---------------------------------------------------------------------------
CARBON_GRAY_100 = RGBColor(0x16, 0x16, 0x16)
CARBON_GRAY_90 = RGBColor(0x26, 0x26, 0x26)
CARBON_GRAY_80 = RGBColor(0x39, 0x39, 0x39)
CARBON_GRAY_70 = RGBColor(0x52, 0x52, 0x52)
CARBON_GRAY_50 = RGBColor(0x8D, 0x8D, 0x8D)
CARBON_GRAY_30 = RGBColor(0xC6, 0xC6, 0xC6)
CARBON_GRAY_10 = RGBColor(0xF4, 0xF4, 0xF4)
CARBON_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
CARBON_BLUE_60 = RGBColor(0x00, 0x43, 0xCE)
CARBON_BLUE_40 = RGBColor(0x45, 0x89, 0xFF)
CARBON_GREEN_40 = RGBColor(0x42, 0xBE, 0x65)
CARBON_RED_50 = RGBColor(0xFA, 0x4D, 0x56)
CARBON_YELLOW_30 = RGBColor(0xF1, 0xC2, 0x1B)
CARBON_PURPLE_40 = RGBColor(0xBE, 0x95, 0xFF)
CARBON_TEAL_40 = RGBColor(0x08, 0xBD, 0xBA)
CARBON_CYAN_40 = RGBColor(0x33, 0xB1, 0xFF)
CARBON_MAGENTA_40 = RGBColor(0xFF, 0x7E, 0xB6)

DECK_W = 10.0
DECK_H = 5.625
LEFT = 0.5
FULL_W = 9.0

MONO = "Consolas"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def add_bg(slide, color=CARBON_GRAY_100):
    fill = slide.background.fill
    fill.solid()
    fill.fore_color.rgb = color


def text(slide, left, top, width, height, txt, size=18, color=CARBON_GRAY_10,
         bold=False, align=PP_ALIGN.LEFT, font=None, line_spacing=None):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    lines = txt.split("\n")
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(size)
        p.font.color.rgb = color
        p.font.bold = bold
        p.alignment = align
        if font:
            p.font.name = font
        if line_spacing:
            p.line_spacing = line_spacing
    return tf


def bullets(slide, left, top, width, height, items, size=14, color=CARBON_GRAY_10,
            font=None, space=6):
    box = slide.shapes.add_textbox(Inches(left), Inches(top), Inches(width), Inches(height))
    tf = box.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = item
        p.font.size = Pt(size)
        p.font.color.rgb = color
        p.space_after = Pt(space)
        if font:
            p.font.name = font
    return tf


def new_slide(prs, title, subtitle=None, kicker=None, title_size=26):
    """A standard content slide: optional kicker, title, optional subtitle."""
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide)
    y = 0.25
    if kicker:
        text(slide, LEFT, y, FULL_W, 0.25, kicker.upper(), size=9, color=CARBON_BLUE_40, bold=True)
        y = 0.48
    text(slide, LEFT, y, FULL_W, 0.5, title, size=title_size, bold=True, color=CARBON_WHITE)
    if subtitle:
        text(slide, LEFT, y + 0.52, FULL_W, 0.32, subtitle, size=12, color=CARBON_GRAY_50)
    return slide


def section_slide(prs, number, title, items, color=CARBON_BLUE_40):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide)
    bar = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0.5), Inches(1.55),
                                 Inches(0.06), Inches(2.5))
    bar.fill.solid()
    bar.fill.fore_color.rgb = color
    bar.line.fill.background()
    text(slide, 0.85, 1.5, 8.5, 0.5, number, size=13, bold=True, color=color)
    text(slide, 0.85, 1.9, 8.5, 0.8, title, size=36, bold=True, color=CARBON_WHITE)
    text(slide, 0.85, 2.95, 8.5, 1.2, "\n".join(items), size=13, color=CARBON_GRAY_50,
         line_spacing=1.4)
    return slide


def card(slide, left, top, width, height, title, value, color=CARBON_BLUE_40,
         value_size=14, title_size=10):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top),
                                   Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = value
    p.font.size = Pt(value_size)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = title
    p2.font.size = Pt(title_size)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.alignment = PP_ALIGN.CENTER
    return shape


def panel(slide, left, top, width, height, heading, lines, color=CARBON_BLUE_40,
          size=11, mono=False):
    """A bordered panel with a coloured heading and body lines."""
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top),
                                   Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.25)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.14)
    tf.margin_right = Inches(0.12)
    tf.margin_top = Inches(0.10)
    p = tf.paragraphs[0]
    p.text = heading
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = color
    for line in lines:
        q = tf.add_paragraph()
        q.text = line
        q.font.size = Pt(size)
        q.font.color.rgb = CARBON_GRAY_10
        q.space_before = Pt(3)
        if mono:
            q.font.name = MONO
    return shape


def code_block(slide, left, top, width, height, lines, size=10, color=CARBON_CYAN_40):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(top),
                                   Inches(width), Inches(height))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = CARBON_GRAY_80
    shape.line.width = Pt(1)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.15)
    tf.margin_top = Inches(0.09)
    for i, line in enumerate(lines):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.text = line
        p.font.size = Pt(size)
        p.font.name = MONO
        p.font.color.rgb = color
    return shape


def table_slide(prs, title, headers, rows, col_widths=None, kicker=None,
                subtitle=None, size=10):
    slide = prs.slides.add_slide(prs.slide_layouts[6])
    add_bg(slide)
    y = 0.25
    if kicker:
        text(slide, LEFT, y, FULL_W, 0.25, kicker.upper(), size=9, color=CARBON_BLUE_40, bold=True)
        y = 0.48
    text(slide, LEFT, y, FULL_W, 0.5, title, size=22, bold=True, color=CARBON_WHITE)
    top = y + (0.55 if not subtitle else 0.95)
    if subtitle:
        text(slide, LEFT, y + 0.5, FULL_W, 0.42, subtitle, size=10, color=CARBON_GRAY_50)

    ncols = len(headers)
    nrows = len(rows) + 1
    avail_h = DECK_H - top - 0.35
    row_h = min(0.34, avail_h / nrows)
    table_h = row_h * nrows
    shape = slide.shapes.add_table(nrows, ncols, Inches(LEFT), Inches(top),
                                   Inches(FULL_W), Inches(table_h))
    table = shape.table
    if col_widths:
        for i, w in enumerate(col_widths):
            table.columns[i].width = Inches(w)
    for r in range(nrows):
        table.rows[r].height = Inches(row_h)

    for c, h in enumerate(headers):
        cell = table.cell(0, c)
        cell.text = h
        cell.fill.solid()
        cell.fill.fore_color.rgb = CARBON_BLUE_60
        cell.margin_left = Inches(0.06)
        cell.margin_top = Inches(0.02)
        cell.margin_bottom = Inches(0.02)
        p = cell.text_frame.paragraphs[0]
        p.font.size = Pt(size + 1)
        p.font.bold = True
        p.font.color.rgb = CARBON_WHITE

    for r, row in enumerate(rows, start=1):
        for c, val in enumerate(row):
            cell = table.cell(r, c)
            cell.text = str(val)
            cell.fill.solid()
            cell.fill.fore_color.rgb = CARBON_GRAY_90 if r % 2 else CARBON_GRAY_80
            cell.margin_left = Inches(0.06)
            cell.margin_top = Inches(0.02)
            cell.margin_bottom = Inches(0.02)
            p = cell.text_frame.paragraphs[0]
            p.font.size = Pt(size)
            p.font.color.rgb = CARBON_GRAY_10
    return slide


def flow_arrow(slide, x1, y1, x2, y2, color=CARBON_GRAY_50, width=1.25):
    conn = slide.shapes.add_connector(2, Inches(x1), Inches(y1), Inches(x2), Inches(y2))
    conn.line.color.rgb = color
    conn.line.width = Pt(width)
    return conn


def flow_box(slide, x, y, w, h, label, color, sub=None, label_size=9, sub_size=7):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.03)
    tf.margin_right = Inches(0.03)
    tf.margin_top = Inches(0.03)
    p = tf.paragraphs[0]
    p.text = label
    p.font.size = Pt(label_size)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    if sub:
        p2 = tf.add_paragraph()
        p2.text = sub
        p2.font.size = Pt(sub_size)
        p2.font.color.rgb = CARBON_GRAY_30
        p2.alignment = PP_ALIGN.CENTER
    return shape


def flow_diamond(slide, x, y, w, h, label, color, label_size=8):
    shape = slide.shapes.add_shape(MSO_SHAPE.DIAMOND, Inches(x), Inches(y),
                                   Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = label
    p.font.size = Pt(label_size)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    return shape


def kv_rows(slide, left, top, width, rows, row_h=0.42, gap=0.06, size=11, cols=(0.0,)):
    """Monospace rows in bordered strips. `rows` is a list of (text, color)."""
    for i, (line, color) in enumerate(rows):
        y = top + i * (row_h + gap)
        shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(left), Inches(y),
                                       Inches(width), Inches(row_h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = CARBON_GRAY_90
        shape.line.color.rgb = color
        shape.line.width = Pt(1.25)
        tf = shape.text_frame
        tf.margin_left = Inches(0.12)
        p = tf.paragraphs[0]
        p.text = line
        p.font.size = Pt(size)
        p.font.name = MONO
        p.font.color.rgb = color


# ---------------------------------------------------------------------------
# Build Presentation
# ---------------------------------------------------------------------------
prs = Presentation()
prs.slide_width = Inches(DECK_W)
prs.slide_height = Inches(DECK_H)

# ===== SLIDE 1: Title =====
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide)
text(slide, LEFT, 0.75, FULL_W, 1.0, "Apollo Alert Intelligence", size=40, bold=True,
     color=CARBON_WHITE)
text(slide, LEFT, 1.65, FULL_W, 0.5,
     "Deduplication  ·  Named Entity Recognition  ·  Fingerprinting  ·  Detection Analytics",
     size=17, color=CARBON_BLUE_40)
text(slide, LEFT, 2.45, FULL_W, 1.0,
     "Per-slot hashing and an inverted index  |  fuzzy cross-rule matching\n"
     "A fine-tuned SecureBERT2.0 entity extractor  |  STIX 2.1 cross-validation\n"
     "Explainable verdicts, frozen results, and a full audit trail",
     size=12, color=CARBON_GRAY_50, line_spacing=1.35)
text(slide, LEFT, 4.45, FULL_W, 0.5, "Cosmos Platform Engineering  |  STIX 2.1  |  CIP SP-015",
     size=12, color=CARBON_GRAY_50)
text(slide, LEFT, 4.78, FULL_W, 0.3, "September 2026", size=12, color=CARBON_GRAY_50)

footer = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(0), Inches(5.325), Inches(10), Inches(0.3))
footer.fill.solid()
footer.fill.fore_color.rgb = CARBON_WHITE
footer.line.fill.background()
p = footer.text_frame.paragraphs[0]
p.alignment = PP_ALIGN.CENTER
p.text = "Confidential"
p.font.size = Pt(8)
p.font.color.rgb = CARBON_GRAY_100

logo_webp = os.path.expanduser("~/Downloads/tekstream-logo-2026.webp")
logo_png = os.path.join(os.path.dirname(__file__), "tekstream-logo.png")
if os.path.exists(logo_webp):
    from PIL import Image
    Image.open(logo_webp).save(logo_png, "PNG")
if os.path.exists(logo_png):
    slide.shapes.add_picture(logo_png, Inches(0.3), Inches(5.33), height=Inches(0.22))

# ===== SLIDE 2: What this covers =====
slide = new_slide(prs, "What This Deck Covers",
                  "Four capabilities that share one input — the alert — and one goal: fewer, better-explained alerts")
areas = [
    ("Deduplication", "Two-layer engine in\nml-service. Decides\nDUPLICATE / SIMILAR / NEW\nbefore an analyst sees it.", CARBON_RED_50),
    ("Entity Extraction", "A fine-tuned transformer\nreads the alert payload\nand returns typed values\nwith no field-name rules.", CARBON_PURPLE_40),
    ("Fingerprinting", "Three independent hashes:\nIOC slots, extracted\nentities, and disposition\npatterns.", CARBON_YELLOW_30),
    ("Detection Analytics", "What is firing, for whom,\nwhat it covers in ATT&CK,\nand how dedup is actually\nperforming.", CARBON_CYAN_40),
]
for i, (name, desc, color) in enumerate(areas):
    x = 0.45 + i * 2.32
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.35),
                                   Inches(2.15), Inches(2.45))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.75)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.12)
    tf.margin_right = Inches(0.12)
    p = tf.paragraphs[0]
    p.text = f"0{i+1}"
    p.font.size = Pt(11)
    p.font.bold = True
    p.font.color.rgb = CARBON_GRAY_50
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = name
    p2.font.size = Pt(14)
    p2.font.bold = True
    p2.font.color.rgb = color
    p2.alignment = PP_ALIGN.CENTER
    p2.space_before = Pt(6)
    p3 = tf.add_paragraph()
    p3.text = desc
    p3.font.size = Pt(10)
    p3.font.color.rgb = CARBON_GRAY_10
    p3.alignment = PP_ALIGN.CENTER
    p3.space_before = Pt(10)

text(slide, LEFT, 4.05, FULL_W, 0.8,
     "The through-line: every number in this deck is produced by code that runs in production today, and every\n"
     "verdict it produces can be traced back to the exact values that produced it.",
     size=12, color=CARBON_GRAY_30, line_spacing=1.3)

# ===== SLIDE 3: Where it runs =====
slide = new_slide(prs, "Where It Runs",
                  "Apollo (TypeScript / Fastify) calls the engines; the engines live in the Cosmos ml-service (Python / FastAPI)")

flow_box(slide, 0.5, 1.35, 1.6, 1.0, "Splunk SOAR", CARBON_TEAL_40, "Containers\n+ CEF artifacts", label_size=11)
flow_box(slide, 2.45, 1.35, 2.1, 1.0, "Apollo", CARBON_BLUE_40,
         "Ingest → STIX 2.1\nPostgres + RLS", label_size=11)
flow_box(slide, 4.9, 1.35, 1.9, 1.0, "CIP Gateway", CARBON_GREEN_40,
         "/api/dedup/*\n/api/ner/*", label_size=11)
flow_box(slide, 7.15, 1.35, 2.35, 1.0, "ml-service", CARBON_PURPLE_40,
         "Dedup engine + NER engine\nRedis (tenant-namespaced)", label_size=11)

flow_arrow(slide, 2.1, 1.85, 2.45, 1.85, CARBON_GRAY_50)
flow_arrow(slide, 4.55, 1.85, 4.9, 1.85, CARBON_GRAY_50)
flow_arrow(slide, 6.8, 1.85, 7.15, 1.85, CARBON_GRAY_50)

panel(slide, 0.5, 2.65, 4.4, 1.35, "Why the gateway is in the path", [
    "Apollo and ml-service sit in separate VPCs.",
    "Spoke → hub → service is what CIP Invariant 5 asks for;",
    "a direct call over a peered link is the shape it discourages.",
    "Auth: X-API-Key / CIP_SPOKE_SECRET, enforced at the gateway.",
], color=CARBON_GREEN_40, size=10)

panel(slide, 5.1, 2.65, 4.4, 1.35, "Two calls, two very different contracts", [
    "Dedup   SYNCHRONOUS, on the hot path. 3s budget, 0 retries.",
    "        Failure = fail open; the alert is processed as NEW.",
    "NER     DETACHED. 45s budget, queued at concurrency 1.",
    "        Failure = the alert simply has no entity list.",
], color=CARBON_PURPLE_40, size=10)

text(slide, LEFT, 4.15, FULL_W, 0.6,
     "Each has its own circuit breaker, registered under its own name, so a degraded NER engine can never open dedup's circuit.",
     size=11, color=CARBON_GRAY_50)

# ===== SLIDE 4: The alert journey =====
slide = new_slide(prs, "The Alert Journey",
                  "One ingest, two independent engines, one analyst-facing result", title_size=24)

y0 = 1.35
flow_box(slide, 0.35, y0, 1.15, 0.62, "SOAR push", CARBON_TEAL_40, "container")
flow_box(slide, 1.70, y0, 1.15, 0.62, "Normalize", CARBON_CYAN_40, "STIX 2.1")
flow_box(slide, 3.05, y0, 1.15, 0.62, "Persist", CARBON_CYAN_40, "alerts row")
flow_box(slide, 4.40, y0, 1.35, 0.62, "Dedup engine", CARBON_RED_50, "synchronous")
flow_box(slide, 5.95, y0, 1.35, 0.62, "Verdict", CARBON_RED_50, "DUP / SIM / NEW")
flow_box(slide, 7.50, y0, 2.0, 0.62, "Link + suppress", CARBON_GREEN_40, "canonical cluster")
for x1, x2 in [(1.50, 1.70), (2.85, 3.05), (4.20, 4.40), (5.75, 5.95), (7.30, 7.50)]:
    flow_arrow(slide, x1, y0 + 0.31, x2, y0 + 0.31, CARBON_GRAY_50)

y1 = 2.45
flow_box(slide, 4.40, y1, 1.35, 0.62, "NER queue", CARBON_PURPLE_40, "detached")
flow_box(slide, 5.95, y1, 1.35, 0.62, "Extract", CARBON_PURPLE_40, "typed entities")
flow_box(slide, 7.50, y1, 2.0, 0.62, "Snapshot + fingerprint", CARBON_YELLOW_30, "frozen at write")
flow_arrow(slide, 3.62, y0 + 0.62, 3.62, y1 + 0.31, CARBON_PURPLE_40)
flow_arrow(slide, 3.62, y1 + 0.31, 4.40, y1 + 0.31, CARBON_PURPLE_40)
flow_arrow(slide, 5.75, y1 + 0.31, 5.95, y1 + 0.31, CARBON_GRAY_50)
flow_arrow(slide, 7.30, y1 + 0.31, 7.50, y1 + 0.31, CARBON_GRAY_50)

y2 = 3.55
flow_box(slide, 0.35, y2, 1.9, 0.62, "Audit events", CARBON_GRAY_50, "request · verdict · failed")
flow_box(slide, 2.45, y2, 1.9, 0.62, "CIP event bus", CARBON_GREEN_40, "alert.deduplicated")
flow_box(slide, 4.55, y2, 1.9, 0.62, "Overwatch metrics", CARBON_BLUE_40, "latency · verdict · slots")
flow_box(slide, 6.65, y2, 2.85, 0.62, "Dashboard & SOAR verdict API", CARBON_CYAN_40,
         "analytics · auto-close recommendation")

text(slide, LEFT, 4.35, FULL_W, 0.7,
     "Dedup and extraction do not depend on each other and neither blocks the alert. Dedup is awaited because its verdict\n"
     "decides what happens to the alert; extraction is enrichment, so it is submitted to a queue and written when it lands.",
     size=11, color=CARBON_GRAY_30, line_spacing=1.3)

# ===================================================================
# SECTION 01 — DEDUPLICATION
# ===================================================================
section_slide(prs, "SECTION 01", "Deduplication", [
    "The problem  ·  Parsing, normalization and fingerprinting",
    "Layer 1 (hash)  ·  Layer 2 (fuzzy)  ·  Cross-validation",
    "Canonical clusters  ·  Policy  ·  Failure behaviour  ·  The SOAR verdict API",
], CARBON_RED_50)

# ===== The problem =====
slide = new_slide(prs, "The Problem")
bullets(slide, LEFT, 1.15, FULL_W, 3.1, [
    "The same alert arrives repeatedly — SOAR polling cycles, replays, re-pushes of one container.",
    "Different detection rules fire on the same IOCs. Rule-scoped dedup cannot see that at all.",
    "Composite confidence formulas with tuned weights answer \"why was this suppressed?\" with a number nobody can defend.",
    "Without a per-slot answer there is no way to say WHICH indicators matched and which did not.",
    "Duplicates chained to other duplicates, so a cluster's origin was whichever alert the engine happened to compare against.",
    "A suppression that cannot be explained is a suppression an analyst will not trust — and will stop using.",
], size=12, space=11)
panel(slide, LEFT, 4.3, FULL_W, 0.8, "What the engine has to deliver", [
    "A verdict that is arithmetic, not a formula: matched slots over filled slots, with both lists on the response.",
], color=CARBON_GREEN_40, size=12)

# ===== The pipeline =====
slide = new_slide(prs, "The Dedup Pipeline",
                  "engine.py — six stages, each of which can be inspected on its own", title_size=25)
stages = [
    ("1", "Parse", "STIX 2.1 bundle →\nflat alert dict", CARBON_TEAL_40),
    ("2", "Extract", "Pull IOC lists by\nfield mapping", CARBON_CYAN_40),
    ("3", "Normalize", "Canonical form\nper IOC type", CARBON_CYAN_40),
    ("4", "Fingerprint", "SHA-256 per slot\n+ time bucket", CARBON_YELLOW_30),
    ("5", "Layer 1 / 2", "Hash match, then\nfuzzy match", CARBON_RED_50),
    ("6", "Validate", "STIX equivalence\ncross-check", CARBON_PURPLE_40),
]
for i, (num, name, desc, color) in enumerate(stages):
    x = 0.4 + i * 1.56
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.4),
                                   Inches(1.42), Inches(1.75))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = num
    p.font.size = Pt(11)
    p.font.bold = True
    p.font.color.rgb = CARBON_GRAY_50
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = name
    p2.font.size = Pt(12)
    p2.font.bold = True
    p2.font.color.rgb = color
    p2.alignment = PP_ALIGN.CENTER
    p3 = tf.add_paragraph()
    p3.text = desc
    p3.font.size = Pt(9)
    p3.font.color.rgb = CARBON_GRAY_10
    p3.alignment = PP_ALIGN.CENTER
    p3.space_before = Pt(6)
    if i < 5:
        flow_arrow(slide, x + 1.42, 2.28, x + 1.56, 2.28, CARBON_GRAY_50)

panel(slide, LEFT, 3.35, 4.4, 1.5, "Every stage fails independently", [
    "A stage that raises returns a NEW verdict carrying",
    "{ error, stage } in score_breakdown, rather than",
    "an exception. An engine that cannot parse a bundle",
    "must not stop the alert from being processed.",
], color=CARBON_GRAY_50, size=10)
panel(slide, 5.1, 3.35, 4.4, 1.5, "Zero IOCs is a data-quality answer", [
    "An alert from which no IOC could be extracted is",
    "returned as NEW with a data_quality_warning, stored,",
    "and indexed. It is never silently matched: there is",
    "nothing to match on.",
], color=CARBON_YELLOW_30, size=10)

# ===== STIX parsing =====
slide = new_slide(prs, "Stage 1 — Parsing the STIX Bundle",
                  "stix_parser.py — two bundle shapes, because two kinds of vendor send them")
panel(slide, LEFT, 1.35, 4.4, 1.85, "Rich bundles: standalone SCOs", [
    "ipv4-addr / ipv6-addr   → ip",
    "domain-name             → domain",
    "url                     → url",
    "file.hashes             → file_hash (SHA-256, SHA-1, MD5)",
    "user-account            → user (user_id and account_login)",
    "indicator.name          → alert_name",
], color=CARBON_TEAL_40, size=10, mono=True)
panel(slide, 5.1, 1.35, 4.4, 1.85, "Minimal bundles: identity + indicator only", [
    "Splunk sends the IOCs inside the vendor extension,",
    "at extensions[<key>].vendor_specific.notable_fields.",
    "",
    "The parser walks a configured field map: 11 aliases for",
    "ip, 9 for user, 5 for email, 6 for domain, 2 each for",
    "url and file_hash — all in dedup_defaults.yaml.",
], color=CARBON_MAGENTA_40, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.55, "Why both paths matter — the observable gap", [
    "A parser that reads only standalone SCOs sees nothing on a Splunk bundle, so the hash confidence and the observable",
    "cross-check were computed over different data and the observable score read higher than it should have. Both sources are",
    "parsed FIRST, and every downstream score compares the parsed values — so the cross-check has no blind spot the",
    "fingerprint does not also have. Notable values are also validated on the way in: an \"ip\" that is not an IPv4 literal is",
    "dropped, an \"email\" without an @ is dropped, and an IP that arrived in a domain field is not counted as a domain.",
], color=CARBON_GREEN_40, size=10)

# ===== Normalization =====
table_slide(prs, "Stage 2/3 — Extraction and Normalization",
    ["IOC type", "Normalization applied", "Example in → out"],
    [
        ["ip (v4)", "Strip CIDR suffix, re-render each octet as an int", "203.000.113.045/32 → 203.0.113.45"],
        ["ip (v6)", "Expand ::, zero-pad every group to 4, map ::ffff: v4", "::ffff:10.0.0.1 → 0000:...:0a00:0001"],
        ["domain", "Trim, lowercase, drop the trailing root dot", "MAIL.Acme.com. → mail.acme.com"],
        ["url", "Lowercase scheme + host, drop default ports, drop fragment", "HTTPS://A.com:443/x#f → https://a.com/x"],
        ["email", "Trim + lowercase", "JDoe@Acme.com → jdoe@acme.com"],
        ["user", "Trim + lowercase", "  JDoe → jdoe"],
        ["file_hash", "Trim + lowercase", "E3B0C442... → e3b0c442..."],
    ],
    col_widths=[1.3, 4.0, 3.7],
    kicker="Deduplication",
    subtitle="ioc_normalizer.py — normalization decides what \"the same indicator\" means, so it happens before anything is hashed",
    size=10)

# ===== Fingerprinting =====
slide = new_slide(prs, "Stage 4 — Fingerprinting",
                  "fingerprinter.py — one SHA-256 per slot. Never one hash over a concatenated blob.")
code_block(slide, LEFT, 1.3, FULL_W, 1.35, [
    "for ioc_type in layer1_slots:                       # ip, user, email, domain, url, file_hash",
    "    values = iocs.get(ioc_type)                     # a SET of normalized values",
    "    if not values: continue                         # an empty slot is omitted, never hashed empty",
    "    sorted_values = \",\".join(sorted(values))         # order in the alert must not change the hash",
    "    ioc_hashes[ioc_type] = sha256(f\"{ioc_type}:{sorted_values}\").hexdigest()",
], size=10, color=CARBON_CYAN_40)

panel(slide, LEFT, 2.85, 2.85, 2.1, "Why per slot", [
    "A blob hash can only say",
    "\"matched\" or \"did not\".",
    "Per-slot hashing is what lets",
    "the response name WHICH",
    "indicators matched — which",
    "is the whole explainability",
    "claim.",
], color=CARBON_YELLOW_30, size=10)
panel(slide, 3.55, 2.85, 2.85, 2.1, "Why the type is inside", [
    "The slot name is part of the",
    "hashed string.",
    "",
    "Without it the same address",
    "in a source field and a",
    "destination field produces",
    "one hash, and no later",
    "matcher can tell them apart.",
], color=CARBON_PURPLE_40, size=10)
panel(slide, 6.65, 2.85, 2.85, 2.1, "Why sorted, why a set", [
    "Values are de-duplicated and",
    "sorted before joining.",
    "",
    "Two alerts carrying the same",
    "three IPs in different order",
    "are the same alert, and must",
    "produce byte-identical",
    "input to the digest.",
], color=CARBON_TEAL_40, size=10)

# ===== Slots =====
slide = new_slide(prs, "The Eight Layer-1 Slots",
                  "Six IOC slots that are SCORED, and two prerequisite slots that are a GATE")
slots = [
    ("ip", "203.0.113.45", CARBON_TEAL_40),
    ("user", "jdoe", CARBON_TEAL_40),
    ("email", "jdoe@acme.com", CARBON_TEAL_40),
    ("domain", "mail.acme.com", CARBON_TEAL_40),
    ("url", "portal.acme.com/login", CARBON_TEAL_40),
    ("file_hash", "e3b0c44298fc...", CARBON_TEAL_40),
]
for i, (name, example, color) in enumerate(slots):
    col = i % 3
    row = i // 3
    x = 0.5 + col * 2.25
    y = 1.35 + row * 1.05
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                   Inches(2.05), Inches(0.9))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = name
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p.font.name = MONO
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = example
    p2.font.size = Pt(9)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.alignment = PP_ALIGN.CENTER

panel(slide, 7.25, 1.35, 2.25, 1.95, "Prerequisites", [
    "alert_name",
    "time_bucket",
    "",
    "Hashed the same way,",
    "excluded from the score.",
], color=CARBON_PURPLE_40, size=10)

panel(slide, LEFT, 3.55, 4.4, 1.4, "A gate, not a score", [
    "The two prerequisites filter CANDIDATES before any",
    "comparison happens. They never contribute to the",
    "confidence, because \"same rule, same window\" is a",
    "precondition for asking the question, not evidence.",
], color=CARBON_PURPLE_40, size=10)
panel(slide, 5.1, 3.55, 4.4, 1.4, "One vocabulary per job", [
    "These are the DEDUP slots. The FP/BP disposition",
    "engine has its own sixteen (src_ip, dest_ip, host,",
    "process, technique_id, ...). What is shared between",
    "them is the per-slot SHA-256 technique, never the names.",
], color=CARBON_YELLOW_30, size=10)

# ===== Time bucket =====
slide = new_slide(prs, "The Time Bucket",
                  "How \"recent enough to be the same alert\" is turned into something hashable")
code_block(slide, LEFT, 1.3, FULL_W, 1.15, [
    "now_ms    = int(time.time() * 1000)              # INGESTION time, not the vendor's timestamp",
    "window_ms = window_minutes * 60 * 1000           # 2880 min = 48 hours (global policy)",
    "bucket    = now_ms // window_ms                  # integer division — a fixed calendar of windows",
    "prerequisites['time_bucket'] = sha256(f'time_bucket:{bucket}').hexdigest()",
], size=10, color=CARBON_CYAN_40)
panel(slide, LEFT, 2.65, 4.4, 1.5, "Fixed windows, not a sliding one", [
    "Two alerts match only if they land in the SAME window.",
    "That makes the check one set lookup instead of a range",
    "scan, at the cost of a boundary: two alerts four minutes",
    "apart across a window edge are in different buckets.",
], color=CARBON_BLUE_40, size=10)
panel(slide, 5.1, 2.65, 4.4, 1.5, "Paired with the Redis TTL", [
    "redis.ttl_minutes is set to the same 2880. The window",
    "says what can match; the TTL says how long the record",
    "exists to be matched against. They are kept in step in",
    "one config file — drift between them is silent.",
], color=CARBON_GREEN_40, size=10)
text(slide, LEFT, 4.35, FULL_W, 0.5,
     "The bucket number, window length and both window edges are returned as ISO timestamps, so a verdict can always be re-derived.",
     size=11, color=CARBON_GRAY_50)

# ===== Layer 1 =====
slide = new_slide(prs, "Layer 1 — Hash Dedup (Same Rule)",
                  "Two stages: gate on the prerequisites, then score on the IOC slots")
bullets(slide, LEFT, 1.35, 4.6, 2.4, [
    "Stage 1 — SINTER over the prerequisite index gives every alert in this rule and this window.",
    "Stage 2 — count how many IOC slot sets the candidate appears in; that count IS the confidence.",
    "Only candidates at or above the threshold are fetched and compared in full.",
    "Threshold: 0.80 by default, per customer from PolicyForge.",
], size=12, space=10)

code_block(slide, 5.25, 1.35, 4.25, 1.0, [
    "confidence =",
    "   matched_ioc_slots",
    "   ------------------",
    "   total_filled_slots",
], size=11, color=CARBON_GREEN_40)

panel(slide, 5.25, 2.5, 4.25, 1.35, "\"Total\" counts both sides", [
    "Slots the incoming alert filled, PLUS slots only the",
    "stored alert filled. An alert that carries fewer",
    "indicators than its candidate cannot reach 100% by",
    "carrying less — the missing slot counts as a miss.",
], color=CARBON_YELLOW_30, size=10)

panel(slide, LEFT, 3.95, 4.6, 1.15, "Ties are broken, not left to Redis", [
    "Equal confidence → earliest (timestamp, alert_id) wins.",
    "Candidates arrive from an unordered set, so without the",
    "id as a second key the same three alerts could cluster",
    "differently on a replay.",
], color=CARBON_BLUE_40, size=10)
text(slide, 5.25, 4.0, 4.25, 1.0,
     "The response carries filled_slots, matched_slots and missed_slots\nverbatim. The confidence is reproducible from those three lists\nwith no knowledge of the engine.",
     size=11, color=CARBON_GRAY_30, line_spacing=1.3)

# ===== Inverted index =====
slide = new_slide(prs, "The Inverted Index",
                  "The same pattern as Elasticsearch or Lucene: index by value, look up by content")
bullets(slide, LEFT, 1.35, 4.45, 2.5, [
    "Every hash — IOC slots and prerequisites alike — is a Redis SET keyed by slot_type:hash.",
    "Prerequisites are one SINTER: the alert_name set intersected with the time_bucket set.",
    "IOC slots are a pipelined SMEMBERS; the per-candidate hit count is the confidence numerator.",
    "No candidate scan, no per-candidate Jaccard, no brute force.",
    "Every key carries the window TTL, so the index prunes itself.",
], size=10, space=8)

code_block(slide, 5.0, 1.35, 4.5, 2.2, [
    "dedup:{customer}:l1:idx:ip:a1b2...      → {a1, a5}",
    "dedup:{customer}:l1:idx:user:d4e5...    → {a1, a5}",
    "dedup:{customer}:l1:idx:email:g7h8...   → {a1}",
    "dedup:{customer}:l1:{alert_id}          → hashes + ts",
    "dedup:{customer}:l1:bundle:{alert_id}   → STIX + IOCs",
    "dedup:{customer}:result:{alert_id}      → the verdict",
    "",
    "a1 in 3/3 sets → 100%    a5 in 2/3 → 67%",
], size=9, color=CARBON_CYAN_40)

panel(slide, LEFT, 4.1, FULL_W, 1.0, "The customer id is in the key, and that is the tenant boundary", [
    "Omitting it collapses every tenant into one empty namespace, where one customer's alert can suppress another's.",
    "Apollo forwards the validated customer_id on every call, and a per-customer flush escapes the id before it reaches SCAN.",
], color=CARBON_RED_50, size=10)

# ===== Layer 2 =====
slide = new_slide(prs, "Layer 2 — Fuzzy Matching (Cross-Rule)",
                  "The case Layer 1 is structurally unable to see: two different rules describing one attack")
bullets(slide, LEFT, 1.35, 4.5, 2.4, [
    "Runs only when Layer 1 returned NEW — the two never overlap.",
    "Candidates: the same time bucket, a DIFFERENT alert_name.",
    "Token Sort Ratio (rapidfuzz) over the normalized IOC VALUES, not the hashes.",
    "Per type: each incoming value takes its best match among the stored values; the type score is the mean.",
    "Overall = mean across every type present in either alert. Threshold 80.",
], size=11, space=9)

card(slide, 5.15, 1.35, 4.35, 0.62, "Parent — Defender, High Severity Alerts", "RULE A", CARBON_TEAL_40, value_size=11)
card(slide, 5.15, 2.10, 4.35, 0.62, "Incoming — Azure, Failed Logins Outside USA", "RULE B", CARBON_CYAN_40, value_size=11)
text(slide, 5.15, 2.95, 4.35, 0.35, "Same user + same IP  →  SIMILAR", size=14, bold=True,
     color=CARBON_YELLOW_30, align=PP_ALIGN.CENTER)
text(slide, 5.15, 3.30, 4.35, 0.3, "Two detections, one attack", size=10,
     color=CARBON_GRAY_50, align=PP_ALIGN.CENTER)

panel(slide, LEFT, 3.95, FULL_W, 1.15, "A type present on one side only scores zero, not nothing", [
    "If the incoming alert carries a file_hash and the candidate does not, that type scores 0 and still counts in the mean.",
    "Dropping it would let an alert raise its own score by carrying indicators the other side never had.",
    "A SIMILAR verdict groups; it does not suppress the way a DUPLICATE does.",
], color=CARBON_GREEN_40, size=10)

# ===== Five scores =====
slide = new_slide(prs, "Cross-Validation — Five Independent Scores",
                  "Every match carries five numbers computed by different means, so no single signal decides alone")
scores = [
    ("Hash\nConfidence", "Per-slot exact\nmatch ratio", "Layer 1", CARBON_BLUE_40),
    ("Fuzzy\nScore", "Token Sort Ratio\nover IOC values", "Layer 2", CARBON_YELLOW_30),
    ("STIX\nContext", "stix2 object_similarity\nover indicator,\nidentity, attack-pattern", "Both", CARBON_PURPLE_40),
    ("Observable\nby Type", "Jaccard per IOC type,\naveraged across types", "Both", CARBON_TEAL_40),
    ("Observable\nby Value", "Jaccard over ALL\nvalues pooled", "Both", CARBON_CYAN_40),
]
for i, (name, desc, layer, color) in enumerate(scores):
    x = 0.35 + i * 1.87
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.35),
                                   Inches(1.72), Inches(2.6))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.75)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = name
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = desc
    p2.font.size = Pt(9)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.alignment = PP_ALIGN.CENTER
    p2.space_before = Pt(10)
    p3 = tf.add_paragraph()
    p3.text = layer
    p3.font.size = Pt(8)
    p3.font.color.rgb = CARBON_GRAY_50
    p3.alignment = PP_ALIGN.CENTER
    p3.space_before = Pt(10)

panel(slide, LEFT, 4.15, FULL_W, 1.0, "Disagreement between the scores IS the signal", [
    "High context with low observable overlap = the same threat and the same rule, aimed at a different target.",
    "Zero hash with a high observable score = a cross-rule match. The scores are reported side by side, never blended into one.",
], color=CARBON_GREEN_40, size=10)

# ===== Canonical =====
slide = new_slide(prs, "Canonical Clusters",
                  "A cluster is a flat star — origin plus members. Never a chain.")
panel(slide, LEFT, 1.3, 4.4, 1.75, "In the engine — O(1), no walk", [
    "Every stored record carries its origin, inherited from the",
    "candidate it matched at WRITE time. An unmatched alert is",
    "its own origin. The chain is compressed as it is built,",
    "so resolving one is a single Redis GET and nothing ever",
    "walks anything.",
], color=CARBON_PURPLE_40, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.75, "In Apollo — one recursive CTE", [
    "resolveRootCanonicalId walks parent_alert_id to the",
    "parentless row in ONE query, whatever the depth. Every hop",
    "is constrained to the customer. It refuses rather than",
    "guesses: a cycle, an unreadable parent, an unknown id, or a",
    "chain past 1,000 hops raises instead of naming an origin.",
], color=CARBON_BLUE_40, size=10)

text(slide, LEFT, 3.25, FULL_W, 0.3, "Two independent answers, compared on every duplicate",
     size=13, bold=True, color=CARBON_WHITE)
kv_rows(slide, LEFT, 3.65, FULL_W, [
    ("agree              both name the same origin, or the engine reported none", CARBON_GREEN_40),
    ("engine-shallower   the engine named its direct match; Redis aged out or predates the field. Apollo's answer stands.", CARBON_YELLOW_30),
    ("disagree           the engine named a third alert. The two stores disagree about membership — a person should look.", CARBON_RED_50),
], row_h=0.40, gap=0.06, size=9)

# ===== Policy / config =====
table_slide(prs, "Policy and Configuration",
    ["Setting", "Value", "Where it comes from", "Why it is not hardcoded"],
    [
        ["Dedup window", "2880 min (48h)", "dedup_defaults.yaml", "Operator policy, changed without a deploy"],
        ["Redis TTL", "2880 min (48h)", "dedup_defaults.yaml", "Must track the window or matching goes silent"],
        ["L1 confidence", "0.80", "PolicyForge, per customer", "CIP Invariant 2 — business rules are policy"],
        ["L2 similarity", "80", "PolicyForge, per customer", "Same; tolerance differs per estate"],
        ["STIX equivalence", "enabled, thr. 80", "dedup_defaults.yaml", "Cross-check can be turned off per environment"],
        ["Notable field map", "11 ip / 9 user / 5 email / ...", "dedup_defaults.yaml", "A new vendor field is config, not code"],
    ],
    col_widths=[1.7, 1.9, 2.3, 3.1],
    kicker="Deduplication",
    subtitle="Every threshold that decides a verdict is fetched per customer and echoed back on the response",
    size=10)

slide = new_slide(prs, "Thresholds Travel With the Verdict",
                  "A score is meaningless without the bar it was judged against — and the bar varies per customer")
code_block(slide, LEFT, 1.35, FULL_W, 1.35, [
    "\"applied_thresholds\": { \"layer1_confidence_threshold\": 0.80,",
    "                        \"layer2_similarity_threshold\": 80 },",
    "\"threshold_source\":   \"POLICYFORGE\" | \"POLICYFORGE_PENDING\" | \"STATIC\"",
], size=11, color=CARBON_CYAN_40)
panel(slide, LEFT, 2.9, 4.4, 1.6, "When PolicyForge is unreachable", [
    "The fetch failure is logged, the static config is used,",
    "and the source is reported as such on the response.",
    "A config warning event is emitted so the fallback is",
    "visible rather than inferred. The engine keeps deciding;",
    "it just tells you which rules it decided under.",
], color=CARBON_YELLOW_30, size=10)
panel(slide, 5.1, 2.9, 4.4, 1.6, "Why echo it at all", [
    "A stored verdict outlives the policy that produced it.",
    "Six months later, \"hash_confidence 0.83, DUPLICATE\" is",
    "only defensible if the response also says the bar was",
    "0.80 at the time. Without it, a threshold change silently",
    "rewrites the meaning of every historical verdict.",
], color=CARBON_GREEN_40, size=10)

# ===== Failure behaviour =====
slide = new_slide(prs, "When the Engine Is Not There",
                  "Dedup is on the hot path, so its failure mode is a design decision, not an accident")
panel(slide, LEFT, 1.3, 2.85, 2.0, "Fail open", [
    "No verdict means the alert",
    "is processed as if it were",
    "NEW.",
    "",
    "There is no local fallback",
    "engine to fall back to: the",
    "in-process dedup was removed",
    "when it moved to ml-service.",
], color=CARBON_GREEN_40, size=10)
panel(slide, 3.55, 1.3, 2.85, 2.0, "Budget and breaker", [
    "timeout      3,000 ms",
    "retries      0",
    "breaker      5 consecutive",
    "cooldown     30 s half-open",
    "",
    "A retry on a slow dedup call",
    "spends the budget twice",
    "without improving the verdict.",
], color=CARBON_BLUE_40, size=10, mono=False)
panel(slide, 6.65, 1.3, 2.85, 2.0, "Never a silent gap", [
    "Four audit event types are",
    "written on the alert itself:",
    "",
    "dedup.request",
    "dedup.verdict",
    "dedup.failed",
    "dedup.skipped",
], color=CARBON_PURPLE_40, size=10)

panel(slide, LEFT, 3.5, FULL_W, 1.55, "Two contract violations Apollo refuses to honour", [
    "A DUPLICATE or SIMILAR verdict with no matched_alert_id cannot be linked to anything. Apollo logs it as the engine",
    "contract violation it is and treats the alert as NEW, rather than marking it suppressed while attaching it to nothing.",
    "",
    "An unresolvable canonical — a cycle, an unreadable parent, a chain past the bound — makes Apollo decline to name an",
    "origin at all. Returning the row the walk happened to stop on would hand the caller an intermediate wearing the name",
    "of an origin, and the cluster's dedup_count would land on the wrong alert.",
], color=CARBON_RED_50, size=10)

# ===== Example L1 =====
slide = new_slide(prs, "Example — Exact Duplicate (Layer 1)",
                  "Same rule, same window, same indicators → DUPLICATE at 100%", title_size=24)
text(slide, LEFT, 1.3, 1.9, 0.3, "SLOT", size=10, bold=True, color=CARBON_GRAY_50)
text(slide, 2.5, 1.3, 2.6, 0.3, "INCOMING", size=10, bold=True, color=CARBON_GRAY_50)
text(slide, 5.3, 1.3, 2.6, 0.3, "STORED", size=10, bold=True, color=CARBON_GRAY_50)
text(slide, 8.1, 1.3, 1.3, 0.3, "MATCH", size=10, bold=True, color=CARBON_GRAY_50)
rows = [
    ("  ip          203.0.113.45              203.0.113.45              matched", CARBON_GREEN_40),
    ("  user        jdoe                      jdoe                      matched", CARBON_GREEN_40),
    ("  email       jdoe@acme.com             jdoe@acme.com             matched", CARBON_GREEN_40),
]
kv_rows(slide, LEFT, 1.65, FULL_W, rows, row_h=0.42, gap=0.08, size=10)

panel(slide, LEFT, 3.25, FULL_W, 0.85, "Response", [
    "verdict DUPLICATE   layer 1   hash_confidence 1.00   matched [ip, user, email]   missed []",
], color=CARBON_RED_50, size=11, mono=True)
text(slide, LEFT, 4.3, FULL_W, 0.7,
     "Context 100  |  Observable by type 100  |  Observable by value 100\n"
     "Every cross-check agrees, which is what an exact duplicate is supposed to look like.",
     size=12, color=CARBON_GRAY_30, align=PP_ALIGN.CENTER, line_spacing=1.35)

# ===== Example L2 =====
slide = new_slide(prs, "Example — Cross-Rule Correlation (Layer 2)",
                  "Different rules, one shared IP, two different accounts → SIMILAR", title_size=24)
card(slide, LEFT, 1.3, 4.4, 0.6, "Parent alert", "Defender — High Severity Alerts", CARBON_TEAL_40, value_size=11)
card(slide, 5.1, 1.3, 4.4, 0.6, "Incoming alert", "Brute Force Detection", CARBON_CYAN_40, value_size=11)
kv_rows(slide, LEFT, 2.1, FULL_W, [
    ("  ip      6.244.192.143          6.244.192.143          fuzzy 100", CARBON_GREEN_40),
    ("  user    myekrangian            jreaga3                fuzzy  44", CARBON_YELLOW_30),
    ("  email   myekrangian@lsu.edu    jreaga3@lsu.edu        fuzzy  65", CARBON_YELLOW_30),
], row_h=0.42, gap=0.08, size=10)

panel(slide, LEFT, 3.7, FULL_W, 0.75, "Response", [
    "verdict SIMILAR   layer 2   fuzzy 69.8   context 100   obs/type 44   obs/value 33",
], color=CARBON_YELLOW_30, size=11, mono=True)
text(slide, LEFT, 4.6, FULL_W, 0.5,
     "Hash confidence is 0 — Layer 1 never even compared these, because the rule names differ. The shared address and the shared\n"
     "mail domain are what the fuzzy pass sees, and the context score confirms the two alerts describe the same kind of thing.",
     size=10, color=CARBON_GRAY_50, line_spacing=1.3)

# ===== SOAR verdict API =====
slide = new_slide(prs, "The SOAR Verdict API",
                  "GET /dedup-verdict/{containerId} — SOAR asks about one container and gets an actionable answer")
code_block(slide, LEFT, 1.3, 4.4, 2.0, [
    "{ \"container_id\": \"7217\",",
    "  \"role\":         \"child\",",
    "  \"verdict\":      \"DUPLICATE\",",
    "  \"parent\": { \"container_id\": \"7201\",",
    "              \"entity_fingerprint\": \"a1b2c3d\" },",
    "  \"child\":  { \"container_id\": \"7217\",",
    "              \"entity_fingerprint\": \"a1b2c3d\" },",
    "  \"recommendation\": \"auto_close\" }",
], size=10, color=CARBON_CYAN_40)

panel(slide, 5.1, 1.3, 4.4, 2.0, "How the recommendation is decided", [
    "auto_close        both sides carry the SAME entity",
    "                  fingerprint — two independent",
    "                  signals agree.",
    "review_required   anything else, INCLUDING either",
    "                  side having no fingerprint yet.",
    "                  Absence is never agreement.",
], color=CARBON_GREEN_40, size=10)

panel(slide, LEFT, 3.5, FULL_W, 1.5, "Three decisions worth stating", [
    "The match is decided on the FULL digest; only its 7-character short form is returned. A chance prefix collision can never",
    "auto-close an alert.  ·  The parent is always the cluster's ROOT canonical, never an intermediate — with one canonical and",
    "three duplicates, every duplicate ties back to the first alert.  ·  The verdict follows the row's ROLE: a parent is always",
    "NEW, a child is DUPLICATE or SIMILAR, so an inconsistent stored value cannot leak across the role line.",
], color=CARBON_PURPLE_40, size=10)

# ===================================================================
# SECTION 02 — NAMED ENTITY RECOGNITION
# ===================================================================
section_slide(prs, "SECTION 02", "Named Entity Recognition", [
    "Why a model instead of field mappings  ·  The checkpoint and its labels",
    "Serving mechanics  ·  What Apollo sends and what it keeps",
    "The extraction queue  ·  Thresholds, exclusions, and frozen results",
], CARBON_PURPLE_40)

# ===== Why NER =====
slide = new_slide(prs, "Why a Model and Not More Field Mappings",
                  "The dedup parser needs a vendor's field names. The extractor does not.")
panel(slide, LEFT, 1.3, 4.4, 1.9, "What field mapping costs", [
    "Eleven aliases for ip. Nine for user. Six for domain.",
    "Every new vendor, every renamed CEF key, every product",
    "upgrade is another entry — and a missing entry is a",
    "silent miss, not an error.",
    "",
    "It also cannot read anything unstructured at all.",
], color=CARBON_RED_50, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.9, "What the model does instead", [
    "It reads the TEXT and returns typed character spans.",
    "No field names are consulted at any point.",
    "",
    "The same checkpoint works identically on raw pasted",
    "text, a serialized record, and a CEF JSON blob — which",
    "is what makes it reusable beyond alerts.",
], color=CARBON_GREEN_40, size=10)
panel(slide, LEFT, 3.45, FULL_W, 1.6, "What it is used for in Apollo today", [
    "Entities tab on alert detail — the typed values an analyst can pivot on, with the model's score beside each one.",
    "Entity fingerprint — a second, independent signature over what the alert is ABOUT.",
    "Deduplication tab — the fingerprint comparison that turns into the auto-close recommendation on the SOAR verdict API.",
    "",
    "It is advisory output, and the response says so explicitly: extracted entities are never a command.",
], color=CARBON_PURPLE_40, size=10)

# ===== The model =====
slide = new_slide(prs, "The Checkpoint",
                  "securebert2-ner-alerts-v1 — a fine-tuned SecureBERT2.0 / ModernBERT token classifier")
types = ["IPV4", "IPV6", "HASH", "EMAIL", "USERNAME", "MACHINE", "FILENAME", "FILEPATH", "URL"]
for i, t in enumerate(types):
    col = i % 5
    row = i // 5
    x = 0.5 + col * 1.83
    y = 1.35 + row * 0.72
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
                                   Inches(1.68), Inches(0.58))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = CARBON_PURPLE_40
    shape.line.width = Pt(1.5)
    p = shape.text_frame.paragraphs[0]
    p.text = t
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.name = MONO
    p.font.color.rgb = CARBON_PURPLE_40
    p.alignment = PP_ALIGN.CENTER

text(slide, 7.8, 2.07, 1.7, 0.6, "9 entity types\n19 BIO labels", size=12, bold=True,
     color=CARBON_GRAY_30, align=PP_ALIGN.CENTER, line_spacing=1.3)

panel(slide, LEFT, 2.9, 4.4, 2.0, "Loaded, not bundled", [
    "Artifacts are pulled from S3 at startup",
    "(cip-ml-training / ner/transformers/...) into a local",
    "cache directory, then loaded.",
    "",
    "A failed startup load retries in the background —",
    "5 attempts, 30s doubling to a 300s ceiling — so a",
    "transient S3 blip cannot strand the endpoint on 503",
    "for the life of the task.",
], color=CARBON_BLUE_40, size=10)
panel(slide, 5.1, 2.9, 4.4, 2.0, "The label map is validated before the weights", [
    "config.json's id2label is checked first: ids contiguous",
    "from zero, every expected label present, nothing extra,",
    "no duplicates, exactly 19 entries.",
    "",
    "A checkpoint whose label set has drifted is rejected",
    "before the model is paid for — rather than serving",
    "confident nonsense under the right type names.",
], color=CARBON_YELLOW_30, size=10)

# ===== Serving mechanics =====
slide = new_slide(prs, "How a Span Becomes an Answer",
                  "Three mechanics, each of which changes what the model can see or claim")
mech = [
    ("Rendering", "A JSON object is rendered\none 'key: value' line per\nfield, mirroring the training\nserializer. Placeholder values\n('-', 'n/a', 'unknown', 'true')\nare dropped. Anything not a\nJSON object passes through\nuntouched.", CARBON_TEAL_40),
    ("Windowing", "Long input runs in\noverlapping character windows\n— 3,000 chars with 300 of\noverlap, under a 1,024-token\ncap. Nothing past the\nsequence limit is silently\ndropped, and spans straddling\na seam survive.", CARBON_CYAN_40),
    ("Scoring", "A span's score is the MINIMUM\nwinning-token probability\nacross its tokens: a span is\nonly as certain as its weakest\ntoken.\n\nRanking scores, NOT calibrated\nprobabilities.", CARBON_YELLOW_30),
]
for i, (name, desc, color) in enumerate(mech):
    x = 0.45 + i * 3.06
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.35),
                                   Inches(2.85), Inches(2.85))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.75)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.14)
    tf.margin_right = Inches(0.14)
    p = tf.paragraphs[0]
    p.text = name
    p.font.size = Pt(14)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = desc
    p2.font.size = Pt(10)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.space_before = Pt(8)

text(slide, LEFT, 4.4, FULL_W, 0.6,
     "Duplicate and nested spans at a window seam are collapsed by keeping the longest span of any overlapping same-type pair,\n"
     "and a span seen twice keeps the higher score — so a seam can never lower an entity's rank.",
     size=10, color=CARBON_GRAY_50, line_spacing=1.3)

# ===== What Apollo sends =====
slide = new_slide(prs, "What Apollo Sends",
                  "The CEF fields, and nothing else — narrowing belongs in the caller, not the engine")
panel(slide, LEFT, 1.3, 4.4, 1.85, "Before: the whole payload", [
    "The engine was asked to descend to a SOAR envelope",
    "path that no Apollo payload actually has, so the",
    "descent never matched and the ENTIRE payload rendered.",
    "",
    "The model was handed SOAR bookkeeping and saved-search",
    "text, and returned entities from it.",
], color=CARBON_RED_50, size=10)
kv_rows(slide, 5.1, 1.35, 4.4, [
    ("HASH      <container source_data_identifier>", CARBON_RED_50),
    ("USERNAME  \"high\"        (the severity value)", CARBON_RED_50),
    ("FILEPATH  WinEventLog\\, XmlWin   (SPL text)", CARBON_RED_50),
    ("URL       .../mission/7217  (the SOAR link)", CARBON_RED_50),
], row_h=0.40, gap=0.06, size=9)
text(slide, 5.1, 3.05, 4.4, 0.3, "Roughly four of twelve entities were real.", size=10,
     color=CARBON_GRAY_50)

panel(slide, LEFT, 3.4, FULL_W, 1.6, "Now: Apollo narrows to soar_cef before calling", [
    "The alert payload carries soar_cef — the verbatim CEF maps of the container's OBSERVABLE artifacts, with SOAR's own",
    "bookkeeping labels already filtered out. ALL artifacts are included, not just the first, and a key present on more than",
    "one artifact becomes a list of its distinct values rather than last-write-wins.",
    "An empty soar_cef is still recognised as a SOAR payload: the presence of the key is the signal, not its length — otherwise",
    "the alerts with the least real content would be exactly the ones that fell back to sending everything.",
], color=CARBON_GREEN_40, size=10)

# ===== What Apollo keeps =====
slide = new_slide(prs, "What Apollo Keeps",
                  "The per-type rollup — not the spans, not the rendered text")
code_block(slide, LEFT, 1.3, 4.4, 1.9, [
    "// stored on the alert row",
    "{ modelName, renderedFromJson,",
    "  byType: {",
    "    IPV4:     [{ value, score, count }, ...],",
    "    USERNAME: [{ value, score, count }, ...],",
    "  },",
    "  entityCount: 12  // SPANS, as reported",
    "}",
], size=10, color=CARBON_CYAN_40)
panel(slide, 5.1, 1.3, 4.4, 1.9, "Why the spans are dropped", [
    "The response also carries every span with character",
    "offsets, and the rendered text those offsets index into.",
    "",
    "The offsets are meaningless without that text; the text",
    "is a near-duplicate of the raw payload already on the row;",
    "and the UI shows distinct VALUES, not spans. Keeping all",
    "three would multiply every alert row for a view nothing",
    "renders.",
], color=CARBON_YELLOW_30, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.55, "Null and empty are not the same thing", [
    "null       extraction did not happen or did not return — the engine was unreachable, the input was empty, or",
    "           no client is configured. The Entities tab is hidden.",
    "empty      the model ran and recognised nothing. The tab is shown, saying so.",
    "",
    "Collapsing the two would make a degraded engine look like a clean alert — which is exactly the ambiguity the",
    "ner_skipped audit reasons exist to remove.",
], color=CARBON_PURPLE_40, size=10)

# ===== Queue =====
slide = new_slide(prs, "The Extraction Queue",
                  "Detached is not enough — a burst fired concurrently is still a burst")
panel(slide, LEFT, 1.3, 4.4, 1.9, "What happened without it", [
    "SOAR does not deliver alerts evenly; it delivers bursts —",
    "seven inside fifteen seconds.",
    "",
    "Firing a burst concurrently does not make a single",
    "CPU-bound engine faster. Every request queues INSIDE the",
    "engine and they all cross the client timeout together.",
    "Five consecutive timeouts then open the breaker.",
], color=CARBON_RED_50, size=10)
kv_rows(slide, 5.1, 1.4, 4.4, [
    ("45-minute window:  10 completed,  47 failed", CARBON_RED_50),
    ("38 timeouts  ·  9 rejected on an open circuit", CARBON_RED_50),
    ("One alert was rejected 2 ms after arriving.", CARBON_RED_50),
], row_h=0.42, gap=0.07, size=9)
text(slide, 5.1, 2.75, 4.4, 0.45,
     "A longer timeout alone was not the fix. The fix was to\nstop sending a burst as a burst.",
     size=10, color=CARBON_GRAY_30, line_spacing=1.3)

text(slide, LEFT, 3.4, FULL_W, 0.3, "Three bounds, each for a different failure",
     size=13, bold=True, color=CARBON_WHITE)
card(slide, LEFT, 3.8, 2.85, 0.85, "caps what the engine sees at once — this is the fix", "concurrency  1",
     CARBON_GREEN_40, value_size=12, title_size=9)
card(slide, 3.55, 3.8, 2.85, 0.85, "caps memory when the engine is down and work keeps arriving", "maxPending  500",
     CARBON_BLUE_40, value_size=12, title_size=9)
card(slide, 6.65, 3.8, 2.85, 0.85, "drops work that waited so long it stopped being useful", "maxAge  10 min",
     CARBON_YELLOW_30, value_size=12, title_size=9)
text(slide, LEFT, 4.75, FULL_W, 0.4,
     "When full the OLDEST item is dropped, not the newest: a saturated queue means the alert an analyst is looking at NOW is "
     "worth more than one from twenty minutes ago. Every drop still writes an audit row, with the reason.",
     size=9, color=CARBON_GRAY_50, line_spacing=1.3)

# ===== Resilience =====
table_slide(prs, "Enrichment, Not a Gate",
    ["", "Dedup service client", "NER service client"],
    [
        ["Position", "Synchronous, on the hot path", "Detached, queued, nothing waits"],
        ["Timeout", "3,000 ms", "45,000 ms (overridable)"],
        ["Retries", "0 — a retry spends the budget twice", "0 — same convention"],
        ["Breaker", "5 consecutive / 30s half-open", "5 consecutive / 30s half-open, OWN name"],
        ["On failure", "Fail open: alert processed as NEW", "Alert simply has no entity list"],
        ["Caller does", "Acts on the error", "Records it and carries on"],
    ],
    col_widths=[1.4, 3.9, 3.7],
    kicker="Named Entity Recognition",
    subtitle="Dedup's failure changes what happens to the alert; this one does not. Two clients, two breakers — a degraded engine cannot take the other down.",
    size=10)

slide = new_slide(prs, "Why 45 Seconds",
                  "The budget has to clear the tail the engine actually produces, or the fix reads as no fix")
kv_rows(slide, LEFT, 1.35, FULL_W, [
    ("  2 s   every real SOAR payload timed out; the breaker tripped and the alerts behind it were never tried", CARBON_RED_50),
    (" 20 s   measured successes at 4.7, 8.9, 11.2, 16.0, 17.1 and 28.8 s — the tail sat ABOVE the budget", CARBON_YELLOW_30),
    (" 45 s   clears the observed tail. The engine's own contention is addressed at its source, by the queue.", CARBON_GREEN_40),
], row_h=0.5, gap=0.1, size=10)
panel(slide, LEFT, 3.35, FULL_W, 1.7, "Two separate causes, two separate fixes", [
    "The CONTENTION was the queue's to fix: serialise the burst, and pin the engine's torch thread count so one extraction",
    "cannot starve the others. The BUDGET was the client's: a transformer forward pass over up to 100,000 characters costs",
    "what it costs, and the timeout should reflect that rather than what an alert can afford to wait — because nothing waits.",
    "",
    "Latency on an enrichment nobody waits for is not a user-visible number. Losing 83% of it very much is.",
], color=CARBON_BLUE_40, size=10)

# ===== Threshold =====
slide = new_slide(prs, "The Display Threshold",
                  "One number, measured against production before it was chosen")
code_block(slide, LEFT, 1.3, 4.6, 1.55, [
    "11,625 distinct values / 999 alerts",
    "",
    "min  0.322   p01 0.449   p05 0.536",
    "p10  0.619   p25 0.952   p50 0.9989",
    "p75  0.9999  max 1.0",
], size=10, color=CARBON_CYAN_40)
kv_rows(slide, 5.25, 1.35, 4.25, [
    ("  0.50  withholds   2.0%      0.99   34.5%", CARBON_GRAY_30),
    ("  0.80  withholds  16.6%      0.999  50.7%", CARBON_GRAY_30),
    ("  0.90  withholds  20.5%      0.9999 72.0%", CARBON_GREEN_40),
], row_h=0.42, gap=0.07, size=9)
text(slide, 5.25, 2.85, 4.25, 0.3, "Default: 0.90 — one value in five withheld.",
     size=11, bold=True, color=CARBON_GREEN_40)

panel(slide, LEFT, 3.1, FULL_W, 1.9, "What the measurement does and does not establish", [
    "The engine's contract says the scores cluster near 1.0, and the median does. But the tail is real and not thin: a tenth of",
    "all values sit below 0.62. So a threshold in this range does substantive work rather than being decoration — which is",
    "why measuring was a precondition for choosing at all.",
    "",
    "What it does NOT say is whether the withheld fifth is the WRONG fifth. The run selected score and label only, never an",
    "extracted value, so it measures the shape of the distribution and nothing about precision. Establishing that needs",
    "labelled values, and is separate work. One threshold applies to every entity type; there is no per-type table.",
], color=CARBON_YELLOW_30, size=10)

# ===== Exclusions =====
slide = new_slide(prs, "Exclusion Rules",
                  "An entity type plus a value pattern — the operator's statement about what is noise")
panel(slide, LEFT, 1.3, 4.4, 1.85, "A blacklist, deliberately", [
    "The default is to keep everything the model returns and",
    "remove only what an operator has NAMED as noise: the",
    "vendor console URL on every alert from one connector,",
    "the literal word \"user\" recognised as a USERNAME.",
    "",
    "A whitelist would silently drop every legitimate value",
    "nobody had thought of yet.",
], color=CARBON_GREEN_40, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.85, "Bound to one type", [
    "\"user\" excluded as a USERNAME is not \"user\" excluded",
    "as a MACHINE or a FILENAME. A rule for every type has",
    "to be written once per type — which is the point: the",
    "operator states what they mean.",
    "",
    "`*` matches any run of characters including none.",
    "Matching is case-insensitive on both type and value.",
], color=CARBON_PURPLE_40, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.55, "Counted separately from the threshold", [
    "The tab reports extracted values, shown values and excluded values as three distinct numbers, so it can name which of",
    "the two hid a value. A value that is both below the threshold AND matches a rule counts as excluded, not as below",
    "threshold: the rule is the operator's explicit statement, and the more useful thing to report.",
    "",
    "Each rule carries a human-readable code (EX-1, EX-2, ...) so a withheld value can be traced to the rule that withheld it.",
], color=CARBON_BLUE_40, size=10)

# ===== Snapshot =====
slide = new_slide(prs, "Frozen Results",
                  "A settings change applies to alerts extracted from that moment on. Nothing rewrites the past.")
panel(slide, LEFT, 1.3, 4.4, 1.9, "The problem with read-time filtering", [
    "The threshold and the exclusion rules used to be applied",
    "when an alert was READ.",
    "",
    "So moving the threshold rewrote what every past alert",
    "showed — and the fingerprint every past alert carried.",
    "An analyst could not cite what they saw yesterday.",
], color=CARBON_RED_50, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.9, "What is frozen, and when", [
    "The result is computed ONCE, when the extraction is",
    "stored, and written with everything that produced it:",
    "the threshold in force, the exclusion rules as stored,",
    "the kept values, the four counts, and the fingerprint.",
    "",
    "Alerts predating this were frozen once by a backfill,",
    "marked as such, so every alert follows the same rule.",
], color=CARBON_GREEN_40, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.45, "The raw output is never discarded", [
    "The model's full response stays in ner_extraction, and the verbatim request and response stay in the audit rows.",
    "Extraction is expensive — successes measured from 4.7 to 28.8 seconds — so re-running it to answer \"what would this",
    "look like at a different threshold?\" is not the design. Re-deriving it from what is already stored is.",
], color=CARBON_YELLOW_30, size=10)

# ===================================================================
# SECTION 03 — FINGERPRINTING
# ===================================================================
section_slide(prs, "SECTION 03", "Fingerprinting", [
    "Three hashes, three jobs, one shared technique",
    "IOC slot hashes  ·  Entity fingerprints  ·  Disposition pattern hashes",
    "And what it means when two of them agree",
], CARBON_YELLOW_30)

table_slide(prs, "Three Fingerprints, Three Jobs",
    ["", "IOC slot hash", "Entity fingerprint", "Layer-0 pattern hash"],
    [
        ["Input", "Normalized IOC values per slot", "Extracted entity VALUES", "rule | vendor | technique"],
        ["Source", "Vendor fields, by mapping", "The NER model", "Vendor fields, by adapter"],
        ["Shape", "One SHA-256 per slot", "One SHA-256 over the set", "SHA-256, first 16 hex"],
        ["Vocabulary", "6 IOC + 2 prerequisite", "9 entity types, values only", "16 disposition slots"],
        ["Answers", "Which indicators match?", "Are these about the same things?", "Is this the same pattern?"],
        ["Used by", "Dedup Layer 1", "Auto-close recommendation", "FP/BP disposition engine"],
    ],
    col_widths=[1.35, 2.6, 2.65, 2.4],
    kicker="Fingerprinting",
    subtitle="What is shared is the per-slot SHA-256 technique. The vocabularies are deliberately not shared.",
    size=10)

# ===== Entity fingerprint =====
slide = new_slide(prs, "The Entity Fingerprint",
                  "One hash over the set of extracted values that survive the threshold")
code_block(slide, LEFT, 1.3, FULL_W, 1.25, [
    "canonical = sorted(set(values))              # distinct, sorted by code unit",
    "sha256    = SHA256(JSON.stringify(canonical))  # a JSON array — injective, no separator a value could contain",
    "return { algorithm: 'sha256', sha256, valueCount, minScore, values: canonical }",
], size=10, color=CARBON_CYAN_40)
panel(slide, LEFT, 2.75, 2.85, 2.2, "Values only", [
    "Not the type, not the score,",
    "not the count.",
    "",
    "\"10.0.0.5\" contributes the",
    "same bytes whether the model",
    "called it an IPV4 or a",
    "MACHINE.",
    "",
    "De-duplicated across types.",
], color=CARBON_TEAL_40, size=10)
panel(slide, 3.55, 2.75, 2.85, 2.2, "The threshold is an input", [
    "The caller passes the values",
    "at or above the operator's",
    "threshold, so the fingerprint",
    "describes what the analyst is",
    "looking at.",
    "",
    "minScore rides alongside, so",
    "nothing compares across two",
    "thresholds by accident.",
], color=CARBON_PURPLE_40, size=10)
panel(slide, 6.65, 2.75, 2.85, 2.2, "No values, no fingerprint", [
    "An empty set hashes to a",
    "constant — so two alerts with",
    "nothing extracted would",
    "\"match\" on it.",
    "",
    "Returns null instead, and null",
    "is review_required.",
    "",
    "Absence never reads as",
    "agreement.",
], color=CARBON_RED_50, size=10)

# ===== Cross-check =====
slide = new_slide(prs, "Two Independent Signatures, One Question",
                  "Dedup Layer 1 hashes the alert's FIELDS. The fingerprint hashes what a model READ.")
flow_box(slide, 0.6, 1.4, 2.2, 0.85, "Layer 1 hash", CARBON_BLUE_40, "vendor fields,\nby mapping", label_size=11)
flow_box(slide, 0.6, 2.5, 2.2, 0.85, "Entity fingerprint", CARBON_PURPLE_40, "model output,\nno field names", label_size=11)
flow_diamond(slide, 3.3, 1.75, 1.5, 1.35, "Agree?", CARBON_YELLOW_30, label_size=11)
flow_arrow(slide, 2.8, 1.82, 3.3, 2.2, CARBON_GRAY_50)
flow_arrow(slide, 2.8, 2.92, 3.3, 2.62, CARBON_GRAY_50)
flow_box(slide, 5.3, 1.5, 2.1, 0.8, "auto_close", CARBON_GREEN_40, "identical digests", label_size=12)
flow_box(slide, 5.3, 2.6, 2.1, 0.8, "review_required", CARBON_YELLOW_30, "anything else", label_size=12)
flow_arrow(slide, 4.8, 2.2, 5.3, 1.9, CARBON_GREEN_40)
flow_arrow(slide, 4.8, 2.62, 5.3, 3.0, CARBON_YELLOW_30)
panel(slide, 7.65, 1.5, 1.85, 1.9, "Why it counts", [
    "Two alerts Layer 1 calls",
    "duplicates should, if the",
    "call is right, be ABOUT",
    "the same things.",
    "",
    "The fingerprint checks",
    "that without asking the",
    "same question twice.",
], color=CARBON_GRAY_50, size=9)
text(slide, LEFT, 3.85, FULL_W, 0.9,
     "The two signatures share no inputs and no code path: one reads named vendor fields through a configured map, the other\n"
     "reads text through a transformer that never sees a field name. That independence is what makes agreement worth\n"
     "something — and it is what lets Apollo hand SOAR a recommendation rather than just a verdict.",
     size=11, color=CARBON_GRAY_30, line_spacing=1.3)

# ===== Layer 0 =====
slide = new_slide(prs, "Layer 0 — Disposition Fingerprints",
                  "The same technique, a different vocabulary, a different question")
slot_text = ("src_ip   dest_ip   user   host   process   command_line   file_hash   rule_name\n"
             "technique_id   vendor_risk   vendor_status   geo_country   geo_city   geo_lat   geo_lon   asn")
code_block(slide, LEFT, 1.3, FULL_W, 0.85, slot_text.split("\n"), size=11, color=CARBON_TEAL_40)
panel(slide, LEFT, 2.35, 4.4, 1.6, "Per-slot hashing, lifted from dedup", [
    "One SHA-256 per slot over \"<slot>:<value>\", never one",
    "hash over a blob — so an explanation can say WHICH slots",
    "matched a registry entry and which were missing.",
    "",
    "Normalization is trim + lowercase, matched to the pattern",
    "hash so the two hashes in a block agree on sameness.",
], color=CARBON_YELLOW_30, size=10)
panel(slide, 5.1, 2.35, 4.4, 1.6, "The pattern hash", [
    "sha256(rule_name | source_vendor | technique_id),",
    "lowercased and trimmed, truncated to 16 hex.",
    "",
    "It identifies a DETECTION PATTERN rather than an alert,",
    "which is what a false-positive / benign-positive",
    "judgement attaches to.",
], color=CARBON_MAGENTA_40, size=10)
panel(slide, LEFT, 4.15, FULL_W, 0.95, "A frozen vocabulary, enforced", [
    "The slot schema is strict and the hash map is keyed to the same enum, so an invented slot — or an invented hash key —",
    "is rejected rather than validating. Multi-value observables belong to dedup's IOC slots; Layer 0 takes one value per slot.",
], color=CARBON_GRAY_50, size=10)

# ===================================================================
# SECTION 04 — DETECTION ANALYTICS
# ===================================================================
section_slide(prs, "SECTION 04", "Detection Analytics", [
    "What is firing, for whom, and what it covers",
    "Insights  ·  Topology  ·  ATT&CK Coverage  ·  Rule Catalog  ·  Customers",
    "Plus the dedup charts: is the engine actually earning its place?",
], CARBON_CYAN_40)

slide = new_slide(prs, "What Detection Analytics Answers",
                  "Dedup tells you about one alert. This tells you about the detection estate.")
card(slide, LEFT, 1.3, 2.85, 0.95, "every alert ingested, all time", "Total Alerts", CARBON_BLUE_40, value_size=15)
card(slide, 3.55, 1.3, 2.85, 0.95, "active and onboarding", "Onboarded Customers", CARBON_GREEN_40, value_size=15)
card(slide, 6.65, 1.3, 2.85, 0.95, "distinct rule names received", "Unique Detection Rules", CARBON_CYAN_40, value_size=15)

tabs = [
    ("Insights", "Volume: alerts per rule,\nper alert title, per\nsecurity domain and\nseverity.", CARBON_BLUE_40),
    ("Topology", "A graph of what a rule\nis connected to —\ncustomers, domains,\nseverities, titles.", CARBON_TEAL_40),
    ("ATT&CK\nCoverage", "Which techniques the\ndetections cover, and\nwhich they do not.\nThe gap is the product.", CARBON_RED_50),
    ("Rule Catalog", "Every rule, its\ndescription, its SPL,\nits volume and its\ndedup split.", CARBON_YELLOW_30),
    ("Customers", "The transpose: which\nrules fired for this\ncustomer, and how\nrecently.", CARBON_PURPLE_40),
]
for i, (name, desc, color) in enumerate(tabs):
    x = 0.4 + i * 1.87
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(2.5),
                                   Inches(1.72), Inches(2.1))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.5)
    tf = shape.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = name
    p.font.size = Pt(12)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = desc
    p2.font.size = Pt(9)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.alignment = PP_ALIGN.CENTER
    p2.space_before = Pt(8)

text(slide, LEFT, 4.75, FULL_W, 0.4,
     "Every tab is tenant-scoped by row-level security. SOC staff may narrow to one customer; everyone else is pinned to their own.",
     size=10, color=CARBON_GRAY_50)

table_slide(prs, "The Endpoint Surface",
    ["Endpoint", "Returns", "Shape"],
    [
        ["/detection-analytics", "Unique rules + total alerts", "The two KPI counters, one scan"],
        ["/rule-volume", "Alerts per (rule, customer)", "Stacked bar, unpaginated"],
        ["/cef-field-volume", "Alerts per security_domain and severity", "Two branches, one query"],
        ["/alert-titles", "Distinct CEF titles, busiest first", "Bounded by an explicit limit"],
        ["/topology", "Rule → customer / domain / severity / title", "Edge facts, drawn as a graph"],
        ["/attack-coverage", "Rule → technique / tactic / customer", "Edge facts, drawn as a matrix"],
        ["/rules  ·  /rule-detail", "Rule catalog and one rule's drill-down", "Paginated; searchable on name"],
        ["/customers  ·  /customer-detail", "Customer catalog and one customer's rules", "The transpose of rule-detail"],
        ["/dedup-analytics", "Verdicts, severity x verdict, slot hit rate", "Reads the engine's own reply"],
    ],
    col_widths=[2.6, 3.5, 2.9],
    kicker="Detection Analytics",
    subtitle="Ten endpoints behind the dashboard plugin. Query schemas are strict: an unsupported filter is a 400, not a silently ignored 200.",
    size=10)

# ===== Topology =====
slide = new_slide(prs, "Topology",
                  "A rule is not just a volume — it is what it is connected to")
edges = [
    ("FIRED_FOR", "customer", CARBON_GREEN_40),
    ("IN_SECURITY_DOMAIN", "security domain", CARBON_TEAL_40),
    ("HAS_SEVERITY", "severity", CARBON_YELLOW_30),
    ("HAS_DESCRIPTION", "rule description", CARBON_PURPLE_40),
    ("FORWARDED_ALERT", "alert title", CARBON_CYAN_40),
]
for i, (kind, target, color) in enumerate(edges):
    y = 1.35 + i * 0.58
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(LEFT), Inches(y),
                                   Inches(4.4), Inches(0.48))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.25)
    tf = shape.text_frame
    tf.margin_left = Inches(0.12)
    p = tf.paragraphs[0]
    p.text = f"{kind:<22} → {target}"
    p.font.size = Pt(10)
    p.font.name = MONO
    p.font.color.rgb = color

panel(slide, 5.1, 1.35, 4.4, 1.5, "Six node types, fixed hues", [
    "Rule names · Customers · Security domains · Severities",
    "Rule descriptions · Alert titles",
    "",
    "A type keeps its colour however the graph is filtered,",
    "so a node's identity never depends on the current view.",
], color=CARBON_BLUE_40, size=10)
panel(slide, 5.1, 3.0, 4.4, 1.25, "Naming the data honestly", [
    "\"Alert titles\", not \"rule titles\". The CEF title is",
    "PER NOTABLE — a vendor alert title, not a rule property.",
    "It is also why that field sits near 45% coverage while",
    "the genuinely rule-level ones are at 100%.",
], color=CARBON_YELLOW_30, size=10)
text(slide, LEFT, 4.45, 4.4, 0.6,
     "soar_cef is an array of artifacts; a payload without one\ncontributes FIRED_FOR edges only, never CEF edges.",
     size=10, color=CARBON_GRAY_50, line_spacing=1.3)

# ===== ATT&CK =====
slide = new_slide(prs, "ATT&CK Coverage",
                  "A coverage map, not a volume chart. A technique is covered or it is not.")
panel(slide, LEFT, 1.3, 4.4, 1.9, "Where the mapping comes from", [
    "Three CEF fields per artifact, comma-split:",
    "  mitre_technique_id · MITRE · mitre_sub_technique",
    "validated as T#### or T####.### before counting.",
    "",
    "Tactic IDs (TA####) are kept alongside because they are",
    "version-stable, while tactic SHORTNAMES are not: ATT&CK",
    "v19 deleted defense-evasion and split it in two.",
], color=CARBON_TEAL_40, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.9, "Attribution is RULE-level", [
    "A technique belongs to the RULE. Every customer that",
    "rule fired for is covered by it — even if only one",
    "customer's alerts happened to carry the MITRE fields.",
    "",
    "Filtering the teaching set instead was the original",
    "shape: of eight sampled customers, five reported ZERO",
    "techniques while their rules cover two to four. One had",
    "fired 23 rules and rendered an empty matrix.",
], color=CARBON_RED_50, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.6, "The gap IS the product", [
    "An empty cell must mean \"we do not detect this\", never \"enrichment did not reach this customer\" — so nothing in the",
    "rendering may make an uncovered cell look like an artifact. Technique ids the ontology does not know (newly published,",
    "revoked, or vendor-invented) are kept and surfaced, so the matrix can say it is not the whole story rather than dropping",
    "them silently. A customer site narrows which RULES count, never which alerts teach; a site with no customer beside it",
    "is rejected with a 400, because two unrelated tenants may both run a site called \"North\".",
    "The whole matrix exports as an ATT&CK Navigator layer.",
], color=CARBON_GREEN_40, size=10)

# ===== Dedup analytics =====
slide = new_slide(prs, "Dedup Analytics",
                  "Is the engine earning its place? Three charts, all read from the engine's own reply.")
charts = [
    ("Verdict distribution", "DUPLICATE / SIMILAR / NEW\nacross the window.\n\nHow much is the engine\nactually suppressing?", CARBON_RED_50),
    ("Severity x verdict", "The same split, broken\ndown by severity.\n\nIs it suppressing the\nnoisy end or the urgent\nend?", CARBON_YELLOW_30),
    ("Slot match / miss", "Per IOC slot, how often\nit matched and how often\nit missed.\n\nWhich indicators are\ncarrying the decisions?", CARBON_CYAN_40),
]
for i, (name, desc, color) in enumerate(charts):
    x = 0.45 + i * 3.06
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(1.35),
                                   Inches(2.85), Inches(2.4))
    shape.fill.solid()
    shape.fill.fore_color.rgb = CARBON_GRAY_90
    shape.line.color.rgb = color
    shape.line.width = Pt(1.75)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.margin_left = Inches(0.14)
    p = tf.paragraphs[0]
    p.text = name
    p.font.size = Pt(13)
    p.font.bold = True
    p.font.color.rgb = color
    p.alignment = PP_ALIGN.CENTER
    p2 = tf.add_paragraph()
    p2.text = desc
    p2.font.size = Pt(10)
    p2.font.color.rgb = CARBON_GRAY_10
    p2.space_before = Pt(8)

panel(slide, LEFT, 3.95, FULL_W, 1.2, "Unscored is counted, not folded into NEW", [
    "Alerts in the window carrying no engine verdict — ingested before dedup existed, or skipped, or the engine was",
    "unavailable — are reported as their own number. Folding them into NEW would make a dedup outage read as a quiet week.",
    "Alert STATUS is not used as a proxy: status moves as an alert is worked, while the stored verdict never changes.",
], color=CARBON_GREEN_40, size=10)

# ===== Catalog =====
slide = new_slide(prs, "Rule Catalog and Customer Drill-Down",
                  "The same measures, rotated — \"which of these is misbehaving, and how recently\"")
panel(slide, LEFT, 1.3, 4.4, 1.9, "Rule detail", [
    "For one rule: the customers it fired for, its volume,",
    "its recency, and its dedup split.",
    "",
    "Plus its description and its SPL query — read from the",
    "latest payload, with the CEF-blob walk, because",
    "production carries the description inside soar_cef",
    "artifacts where no SQL expression practically reaches.",
], color=CARBON_YELLOW_30, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.9, "Customer detail", [
    "The transpose: for one customer, the rules that fired,",
    "with the same recency and the same dedup split.",
    "",
    "Because the question is the same one rotated — is this",
    "rule noisy for this customer, and has anything changed",
    "recently?",
], color=CARBON_PURPLE_40, size=10)
panel(slide, LEFT, 3.4, FULL_W, 1.55, "Two things the queries are careful about", [
    "IDENTITY. Everything groups by rule NAME, the same identity the charts use, and search matches the name alone —",
    "matching the description too would desync the table from the charts' client-side filter.",
    "",
    "CARDINALITY. Titles are per-notable, so production carries thousands of distinct values. Grouping by title would",
    "multiply every row of the unpaginated endpoints, so the title vocabulary comes from its own bounded endpoint instead.",
], color=CARBON_BLUE_40, size=10)

# ===================================================================
# SECTION 05 — WHERE IT STANDS
# ===================================================================
section_slide(prs, "SECTION 05", "Where It Stands", [
    "What is running  ·  What is still open  ·  What comes next",
], CARBON_GREEN_40)

slide = new_slide(prs, "What Is Running Today")
running = [
    ("Deduplication", "Two-layer engine in ml-service, tenant-namespaced Redis, per-customer thresholds from PolicyForge, flat-star canonical clusters, four audit event types, fail-open with a circuit breaker.", CARBON_RED_50),
    ("Entity extraction", "A fine-tuned SecureBERT2.0 token classifier over nine entity types, queued at concurrency one, with a measured display threshold, operator exclusion rules, and results frozen at extraction.", CARBON_PURPLE_40),
    ("Fingerprinting", "Per-slot IOC hashes for dedup, an entity fingerprint for the auto-close recommendation, and Layer-0 slot and pattern hashes for the disposition engine.", CARBON_YELLOW_30),
    ("Detection analytics", "Ten endpoints behind five tabs: volume, topology, ATT&CK coverage with Navigator export, rule and customer drill-downs, and the dedup performance charts.", CARBON_CYAN_40),
]
for i, (name, desc, color) in enumerate(running):
    y = 1.1 + i * 1.02
    text(slide, LEFT, y, FULL_W, 0.3, name, size=14, bold=True, color=color)
    text(slide, 0.75, y + 0.32, 8.75, 0.6, desc, size=10.5, color=CARBON_GRAY_10, line_spacing=1.2)

slide = new_slide(prs, "What Is Still Open")
open_items = [
    ("Precision of the entity threshold is unmeasured", "The distribution run recorded score and label only, never a value. We know how much a cut withholds; we do not know whether it withholds the right things. Labelled values are the missing input.", CARBON_YELLOW_30),
    ("The fingerprint cross-check is comparison, not enforcement", "Agreement drives the SOAR recommendation. It does not yet feed back into the dedup verdict itself, so a disagreement is visible but not acted on automatically.", CARBON_YELLOW_30),
    ("Canonical divergence is reported, not resolved", "'disagree' between the engine's canonical and Apollo's is surfaced for a person. Nothing reconciles the two stores.", CARBON_BLUE_40),
    ("Window boundaries are hard edges", "Fixed buckets make the lookup one set operation. The cost is that two alerts minutes apart across a boundary are in different buckets and cannot match.", CARBON_BLUE_40),
    ("No semantic layer", "Two vendors describing one attack in different words are invisible to both hashing and character-level fuzzy matching.", CARBON_PURPLE_40),
]
for i, (name, desc, color) in enumerate(open_items):
    y = 1.05 + i * 0.83
    text(slide, LEFT, y, FULL_W, 0.28, name, size=12.5, bold=True, color=color)
    text(slide, 0.75, y + 0.28, 8.75, 0.5, desc, size=10, color=CARBON_GRAY_10, line_spacing=1.15)

# ===== Layer 3 =====
table_slide(prs, "What Comes Next — Layer 3",
    ["Layer", "Method", "Cost", "What it catches"],
    [
        ["1 — Hash", "Per-slot SHA-256 + inverted index", "Fast", "Exact duplicates: same rule, same indicators"],
        ["2 — Fuzzy", "Token Sort Ratio over IOC values", "Medium", "Cross-rule: different rule, similar indicators"],
        ["3 — Semantic", "Cosine similarity over embeddings", "Slower", "Same meaning, different words"],
    ],
    col_widths=[1.5, 3.1, 0.9, 3.5],
    kicker="Roadmap",
    subtitle="Each layer catches what the layers before it structurally cannot",
    size=11)

slide = new_slide(prs, "When Layer 3 Becomes Critical",
                  "Not now — and the reason it is not now is worth being explicit about")
panel(slide, LEFT, 1.3, 4.4, 1.75, "Why it can wait", [
    "Today's data is structured. Indicators arrive in typed",
    "fields, not free text, and Layers 1 and 2 handle",
    "structured data well.",
    "",
    "Entity extraction has also already absorbed part of the",
    "problem: it reads text without field names.",
], color=CARBON_GREEN_40, size=10)
panel(slide, 5.1, 1.3, 4.4, 1.75, "What would change that", [
    "Multi-vendor ingestion at scale — CrowdStrike and",
    "Sentinel and Defender describing one attack in three",
    "vocabularies.",
    "",
    "At that point the alert DESCRIPTION becomes a dedup",
    "signal, and no amount of character overlap reaches it.",
], color=CARBON_RED_50, size=10)
panel(slide, LEFT, 3.25, FULL_W, 1.65, "What it would need", [
    "An embedding model — a general sentence encoder, or a security-specific one; the NER checkpoint's encoder is a",
    "candidate starting point.  ·  A vector store: Redis vector search is already in the stack.  ·  An inference budget",
    "that fits the path it runs on — which, on the evidence of the NER rollout, means measuring the tail before choosing it.",
    "",
    "It belongs in ml-service beside the two engines that are already there, not in Apollo.",
], color=CARBON_PURPLE_40, size=10)

# ===== Q&A =====
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide)
text(slide, LEFT, 1.7, FULL_W, 1.0, "Questions", size=42, bold=True, color=CARBON_WHITE,
     align=PP_ALIGN.CENTER)
text(slide, LEFT, 2.9, FULL_W, 0.4, "Backup slides follow: score arithmetic, key reference, wire contracts",
     size=13, color=CARBON_BLUE_40, align=PP_ALIGN.CENTER)
text(slide, LEFT, 3.5, FULL_W, 0.4, "Cosmos Platform Engineering  |  CIP SP-015",
     size=11, color=CARBON_GRAY_50, align=PP_ALIGN.CENTER)

# ===================================================================
# BACKUP SLIDES
# ===================================================================
section_slide(prs, "BACKUP", "Reference", [
    "Score arithmetic, worked  ·  Redis key reference  ·  Wire contracts",
], CARBON_GRAY_50)

# --- Backup: hash confidence ---
slide = new_slide(prs, "Score 1 — Hash Confidence", kicker="Backup", title_size=24)
bullets(slide, LEFT, 1.15, FULL_W, 0.9, [
    "confidence = matched_ioc_slots / total_filled_ioc_slots",
    "Only the six IOC slots count. Prerequisites are a gate, not a score — they filter candidates before comparison.",
], size=12, space=8)
table_slide(prs, "Hash Confidence — Every Possible Value",
    ["Matched", "Total slots", "Confidence", "What it means"],
    [
        ["6", "6", "100.0%", "All six IOC types identical"],
        ["5", "6", "83.3%", "Five match, one differs"],
        ["4", "6", "66.7%", "Four match, two differ"],
        ["3", "6", "50.0%", "Half match — below the 0.80 default"],
        ["2", "6", "33.3%", "Two IOC types match"],
        ["1", "6", "16.7%", "One IOC type matches"],
        ["0", "6", "0.0%", "No match — falls through to Layer 2"],
        ["3", "3", "100.0%", "The alert carries three types and all three match"],
        ["1", "3", "33.3%", "Three types carried, one matches"],
    ],
    col_widths=[1.3, 1.5, 1.5, 4.7],
    kicker="Backup",
    size=10)

# --- Backup: fuzzy ---
slide = new_slide(prs, "Score 2 — Fuzzy Score (Layer 2)", kicker="Backup", title_size=24)
bullets(slide, LEFT, 1.1, FULL_W, 2.0, [
    "Method: Token Sort Ratio (rapidfuzz)",
    "   1. Tokenize both strings      'jdoe@lsu.edu' → ['edu', 'jdoe', 'lsu']",
    "   2. Sort tokens alphabetically",
    "   3. Compute the character-level match ratio, 0–100",
    "",
    "Per type: each incoming value takes its BEST match among the stored values; the type score is the mean of those bests.",
    "Overall: the mean across every IOC type present in either alert. A type on one side only scores 0 and still counts.",
], size=11, space=6)
text(slide, LEFT, 3.6, FULL_W, 0.3, "Worked example", size=13, bold=True, color=CARBON_YELLOW_30)
code_block(slide, LEFT, 3.95, FULL_W, 1.2, [
    "ip     '6.244.192.143'        vs  '6.244.192.143'      →  100.0   identical",
    "user   'myekrangian'          vs  'jreaga3'            →   44.4   little overlap",
    "email  'myekrangian@lsu.edu'  vs  'jreaga3@lsu.edu'    →   65.0   shared @lsu.edu",
    "overall = (100.0 + 44.4 + 65.0) / 3 = 69.8",
], size=10, color=CARBON_CYAN_40)

# --- Backup: STIX object ---
slide = new_slide(prs, "Score 3 — STIX Context Similarity", kicker="Backup", title_size=24)
bullets(slide, LEFT, 1.15, FULL_W, 1.9, [
    "Uses the stix2 library's object_similarity() — a weighted per-property comparison, per STIX object type.",
    "Compares threat CONTEXT, not indicator values: same rule? same organisation? same techniques?",
    "Best-match per object, averaged across every matched pair. Objects compared: indicator, identity, attack-pattern.",
    "Scored against the cluster ORIGIN's bundle, not the intermediate the alert happened to match — and when a",
    "substitution happens, the response records it, so scores can never describe one alert while naming another.",
], size=11, space=7)
table_slide(prs, "STIX Context — Property Weights",
    ["Object type", "Property", "Weight", "Comparison"],
    [
        ["indicator", "pattern", "80", "STIX pattern match"],
        ["indicator", "indicator_types", "15", "List intersection"],
        ["indicator", "valid_from", "5", "Timestamp proximity"],
        ["identity", "name", "60", "Fuzzy string"],
        ["identity", "identity_class", "20", "Exact match"],
        ["identity", "sectors", "20", "List intersection"],
        ["attack-pattern", "name", "30", "Fuzzy string"],
        ["attack-pattern", "external_references", "70", "Reference comparison"],
    ],
    col_widths=[2.0, 2.6, 1.0, 3.4],
    kicker="Backup",
    size=10)

# --- Backup: observable type/value ---
slide = new_slide(prs, "Scores 4 and 5 — Observable Similarity", kicker="Backup", title_size=24)
panel(slide, LEFT, 1.2, 4.4, 1.5, "By type — Jaccard per type, averaged", [
    "For each IOC type present in either alert:",
    "   score = |intersection| / |union| x 100",
    "Overall = the mean across types.",
    "Every type weighs the same, whatever it holds.",
], color=CARBON_TEAL_40, size=10)
panel(slide, 5.1, 1.2, 4.4, 1.5, "By value — Jaccard over the pool", [
    "All values across all types in ONE set:",
    "   score = matched_values / union_values x 100",
    "True data overlap: a type holding ten addresses",
    "counts for more than a type holding one account.",
], color=CARBON_CYAN_40, size=10)
text(slide, LEFT, 2.9, FULL_W, 0.3, "Worked example — same URL, different account",
     size=13, bold=True, color=CARBON_CYAN_40)
code_block(slide, LEFT, 3.25, FULL_W, 1.8, [
    "url    incoming {portal.acme.com/login}   stored {portal.acme.com/login}   → 1/1 = 100%",
    "user   incoming {jdoe@acme.com}           stored {abirds8@acme.com}        → 0/2 =   0%",
    "email  incoming {jdoe@acme.com}           stored {abirds8@acme.com}        → 0/2 =   0%",
    "",
    "by type   (100 + 0 + 0) / 3          = 33.3%     each type weighs equally",
    "by value  1 matched / 5 union values = 20.0%     the URL is one value out of five",
], size=10, color=CARBON_CYAN_40)

# --- Backup: reading the scores ---
table_slide(prs, "Reading the Scores Together",
    ["Hash", "Context", "Obs/type", "Obs/value", "Interpretation"],
    [
        ["100%", "100", "100", "100", "Exact duplicate — every signal agrees"],
        ["33%", "100", "33", "20", "Same rule and threat, different target account"],
        ["0%", "100", "100", "100", "Cross-rule, identical indicators — Layer 2 SIMILAR"],
        ["0%", "100", "44", "33", "Cross-rule, partial indicator overlap"],
        ["0%", "50", "0", "0", "Different rule, different indicators, shared techniques"],
        ["100%", "50", "100", "100", "Same indicators, different techniques — unusual, worth a look"],
    ],
    col_widths=[0.85, 1.05, 1.05, 1.1, 4.95],
    kicker="Backup",
    subtitle="Hash and Obs/type should correlate — both measure per-type overlap. Context is independent of indicators entirely.",
    size=10)

# --- Backup: Redis keys ---
slide = new_slide(prs, "Redis Key Reference", kicker="Backup", title_size=24)
code_block(slide, LEFT, 1.2, FULL_W, 2.5, [
    "dedup:{customer_id}:l1:idx:{slot}:{hash}     SET of alert ids carrying that slot hash",
    "dedup:{customer_id}:l1:idx:alert_name:{h}    SET — prerequisite, SINTER'd with time_bucket",
    "dedup:{customer_id}:l1:idx:time_bucket:{h}   SET — also the Layer 2 candidate pool",
    "dedup:{customer_id}:l1:{alert_id}            hashes + timestamp + canonical id",
    "dedup:{customer_id}:l1:bundle:{alert_id}     STIX bundle + parsed IOCs + alert_name + ts",
    "dedup:{customer_id}:result:{alert_id}        the verdict as returned",
    "",
    "Every key carries the window TTL (2880 min). Index writes are pipelined.",
    "Per-customer flush escapes the id before it reaches SCAN, so a glob in a",
    "customer id can never widen the pattern into another tenant's namespace.",
], size=10, color=CARBON_CYAN_40)
panel(slide, LEFT, 3.9, FULL_W, 1.2, "Lookup cost", [
    "Prerequisites: one SINTER.  IOC candidates: one pipelined SMEMBERS per filled slot, then a count per candidate id.",
    "Only candidates already at or above the threshold are fetched in full and compared slot by slot.",
], color=CARBON_GREEN_40, size=10)

# --- Backup: wire contract ---
slide = new_slide(prs, "Wire Contracts", kicker="Backup", title_size=24)
code_block(slide, LEFT, 1.15, 4.5, 3.6, [
    "POST /api/v1/dedup/alerts",
    "  { stix_bundle, source, severity,",
    "    customer_id, alert_id, created_at }",
    "",
    "→ { alert_id, verdict, layer,",
    "    hash_confidence,",
    "    stix_context_similarity,",
    "    stix_observable_by_type,",
    "    stix_observable_by_value,",
    "    matched_alert_id,",
    "    canonical_alert_id,",
    "    score_breakdown,",
    "    stix_context_details[],",
    "    stix_observable_details[],",
    "    iocs, filled_slots,",
    "    matched_slots, missed_slots,",
    "    applied_thresholds,",
    "    threshold_source }",
], size=9.5, color=CARBON_CYAN_40)
code_block(slide, 5.0, 1.15, 4.5, 3.6, [
    "POST /api/v1/ner/extract",
    "  { text }                  # <= 100k chars",
    "",
    "→ { advisory: true,         # never a command",
    "    model_name, text,",
    "    rendered_from_json,",
    "    windows,",
    "    entities[]  { start, end, label,",
    "                  value, score },",
    "    by_type     { LABEL: [{ value,",
    "                  score, count }] },",
    "    entity_count, latency_ms }",
    "",
    "GET /api/v1/ner/status",
    "→ { ready, model_name, n_labels,",
    "    entity_types[], max_len,",
    "    window_chars, overlap_chars,",
    "    manifest_version }",
], size=9.5, color=CARBON_PURPLE_40)

# --- Backup: config ---
slide = new_slide(prs, "Configuration Reference", kicker="Backup", title_size=24)
code_block(slide, LEFT, 1.15, FULL_W, 3.6, [
    "# dedup_defaults.yaml",
    "redis:   { key_prefix: dedup, ttl_minutes: 2880 }",
    "stix:    { extension_key: extension-definition--a85759ca-...,",
    "           hash_algorithms: [SHA-256, SHA-1, MD5],",
    "           notable_field_mappings: { ip: 11 aliases, user: 9, email: 5,",
    "                                     domain: 6, url: 2, file_hash: 2 },",
    "           ioc_field_map: { ips: ip, domains: domain, urls: url,",
    "                            file_hashes: file_hash, emails: email,",
    "                            usernames: user } }",
    "dedup:   { window_minutes: 2880,",
    "           layer1: { slots: [ip, user, email, domain, url, file_hash,",
    "                              alert_name, time_bucket],",
    "                     confidence_threshold: 0.80,",
    "                     stix_equivalence: { enabled: true, threshold: 80 } },",
    "           layer2: { similarity_threshold: 80 } }",
    "",
    "# NER engine        NER_TIMEOUT_MS 45000   window 3000 / overlap 300 / max_len 1024",
    "# Entity threshold  default 0.90, adjustable in Settings > Artificial Intelligence",
], size=9.5, color=CARBON_CYAN_40)

# ===== Blue ribbon on every slide =====
for _slide in prs.slides:
    ribbon = _slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Emu(0), Emu(0), Inches(0.15), prs.slide_height,
    )
    ribbon.fill.solid()
    ribbon.fill.fore_color.rgb = CARBON_BLUE_60
    ribbon.line.fill.background()

# ===== Save =====
output = os.path.join(os.path.dirname(__file__), "apollo_dedup_presentation.pptx")
prs.save(output)
print(f"Saved to {output}")
print(f"Total slides: {len(prs.slides)}")
