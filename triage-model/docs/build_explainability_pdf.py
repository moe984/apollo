"""Build docs/model-explainability.pdf - how the alert-triage model decides, and
how each decision can be explained.

Every figure quoted comes from the repo: models/v20/manifest.json,
config/preprocessing.yaml, the saved outputs of 02-train-model.ipynb, and the
worked example in ../triage/machine-learning/README.md. The TF-IDF example is
computed here with the production vectoriser settings and checked against the
hand formulas.

    pip install reportlab matplotlib scikit-learn scipy pyyaml
    python docs/build_explainability_pdf.py
"""
from __future__ import annotations

import io
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import yaml
from matplotlib.patches import FancyBboxPatch
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (CondPageBreak, Image, KeepTogether, Paragraph,
                                SimpleDocTemplate, Spacer, Table, TableStyle)
from scipy.stats import beta
from sklearn.feature_extraction.text import TfidfVectorizer

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = HERE / "model-explainability.pdf"

MANIFEST = json.loads((ROOT / "models" / "v20" / "manifest.json").read_text())
CONFIG = yaml.safe_load((ROOT / "config" / "preprocessing.yaml").read_text())
THR = CONFIG["operating_threshold"]

# ----------------------------------------------------------------- fonts
FONT_DIR = Path(matplotlib.get_data_path()) / "fonts" / "ttf"
for name, file in [("Sans", "DejaVuSans.ttf"), ("Sans-Bold", "DejaVuSans-Bold.ttf"),
                   ("Sans-Italic", "DejaVuSans-Oblique.ttf"),
                   ("Sans-BoldItalic", "DejaVuSans-BoldOblique.ttf"),
                   ("Mono", "DejaVuSansMono.ttf")]:
    pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
pdfmetrics.registerFontFamily("Sans", normal="Sans", bold="Sans-Bold",
                              italic="Sans-Italic", boldItalic="Sans-BoldItalic")

NAVY, INK, MUTED = "#1f3a5f", "#1a202c", "#4a5568"
HUMAN, AI, RULE, SOFT = "#c53030", "#2b6cb0", "#cbd5e0", "#f4f7fb"

S = {
    "title": ParagraphStyle("title", fontName="Sans-Bold", fontSize=24, leading=29,
                            textColor=NAVY, spaceAfter=8),
    "subtitle": ParagraphStyle("subtitle", fontName="Sans", fontSize=12.5, leading=17,
                               textColor=MUTED, spaceAfter=12),
    "h1": ParagraphStyle("h1", fontName="Sans-Bold", fontSize=16, leading=21,
                         textColor=NAVY, spaceBefore=6, spaceAfter=8,
                         keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName="Sans-Bold", fontSize=12, leading=16,
                         textColor=NAVY, spaceBefore=10, spaceAfter=5,
                         keepWithNext=1),
    "body": ParagraphStyle("body", fontName="Sans", fontSize=9.6, leading=15.5,
                           textColor=INK, spaceAfter=6),
    "bullet": ParagraphStyle("bullet", fontName="Sans", fontSize=9.6, leading=15.5,
                             textColor=INK, leftIndent=14, bulletIndent=3, spaceAfter=3),
    "small": ParagraphStyle("small", fontName="Sans", fontSize=8, leading=11,
                            textColor=MUTED, spaceAfter=4),
    "caption": ParagraphStyle("caption", fontName="Sans-Italic", fontSize=8.2, leading=11,
                              textColor=MUTED, alignment=TA_CENTER, spaceAfter=10),
    "cell": ParagraphStyle("cell", fontName="Sans", fontSize=8.4, leading=11, textColor=INK),
    "cellb": ParagraphStyle("cellb", fontName="Sans-Bold", fontSize=8.4, leading=11,
                            textColor=INK),
    "mono": ParagraphStyle("mono", fontName="Mono", fontSize=7.6, leading=10.4,
                           textColor=INK),
    "callout": ParagraphStyle("callout", fontName="Sans", fontSize=9.6, leading=15.5,
                              textColor=INK),
}

story: list = []


def p(text, style="body"):
    story.append(Paragraph(text, S[style]))


def bullets(items):
    for it in items:
        story.append(Paragraph(it, S["bullet"], bulletText="•"))


def gap(h=6):
    story.append(Spacer(1, h))


def callout(text, colour=NAVY, title=None):
    body = (f"<b>{title}</b><br/>" if title else "") + text
    t = Table([[Paragraph(body, S["callout"])]], colWidths=[6.9 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SOFT)),
        ("LINEBEFORE", (0, 0), (0, -1), 3, colors.HexColor(colour)),
        ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
    story.append(t)
    gap(8)


def table(rows, widths, header=True, align_right=(), zebra=True, pad=3.5):
    data = []
    for r, row in enumerate(rows):
        style = S["cellb"] if (header and r == 0) else S["cell"]
        data.append([c if not isinstance(c, str) else Paragraph(c, style) for c in row])
    t = Table(data, colWidths=[w * inch for w in widths], repeatRows=1 if header else 0)
    ts = [("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("LINEBELOW", (0, 0), (-1, 0), 0.8, colors.HexColor(NAVY)),
          ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor(RULE)),
          ("TOPPADDING", (0, 0), (-1, -1), pad), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]
    if zebra:
        for r in range(1 if header else 0, len(rows)):
            if r % 2 == 0:
                ts.append(("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f7f9fc")))
    for c in align_right:
        ts.append(("ALIGN", (c, 0), (c, -1), "RIGHT"))
    t.setStyle(TableStyle(ts))
    story.append(t)
    gap(8)


def figure(fig, width_in, caption=None):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=220, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    w, h = fig.get_size_inches() if False else (None, None)
    img = Image(buf)
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth, img.drawHeight = width_in * inch, width_in * inch * ratio
    block = [img]
    if caption:
        block.append(Paragraph(caption, S["caption"]))
    story.append(KeepTogether(block))


def eq(tex, size=13.5, label=None):
    """A display formula, rendered by matplotlib mathtext."""
    fig = plt.figure(figsize=(0.01, 0.01))
    fig.text(0, 0, f"${tex}$", fontsize=size, color=INK)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight", pad_inches=0.04,
                transparent=True)
    plt.close(fig)
    buf.seek(0)
    img = Image(buf)
    img.drawWidth, img.drawHeight = img.imageWidth * 72 / 300, img.imageHeight * 72 / 300
    cells = [[img, Paragraph(f"({label})", S["small"]) if label else ""]]
    t = Table(cells, colWidths=[6.3 * inch, 0.6 * inch])
    t.setStyle(TableStyle([("ALIGN", (0, 0), (0, 0), "CENTER"),
                           ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(t)


def logit(q):
    return math.log(q / (1 - q))


# =============================================================== numbers
M = MANIFEST["metrics"]
OPS = M["operating_points"]
THRESHOLD = THR["value"]
THR_LOGIT = logit(THRESHOLD)

# worked example - prod-soar:229895, README of triage/machine-learning
EX_SCORE, EX_THR = 0.1785, 0.0087
EX_TERMS = [("defender", 0.102, 1.50, 0.153), ("severity alerts", 0.117, 0.81, 0.095),
            ("search incidentid", 0.055, 1.45, 0.080), ("sourceaddress 216", 0.083, 0.74, 0.062),
            ("malicious", 0.076, 0.76, 0.058), ("98", 0.124, -1.54, -0.190),
            ("org", 0.073, -1.97, -0.145), ("potentially malicious", 0.145, -0.59, -0.085),
            ("detectionsource officeatp", 0.066, -0.96, -0.064)]
EX_LOGIT = logit(EX_SCORE)
EX_SHOWN = sum(t[3] for t in EX_TERMS)
EX_REST = EX_LOGIT - EX_SHOWN

# Clopper-Pearson: smallest clean lane that proves <= 1% at 95%
CONF, TOL = 0.95, THR["tolerance"]
N_CLEAN = next(n for n in range(1, 5000) if beta.ppf(CONF, 1, n) <= TOL)

# toy TF-IDF, production vectoriser settings
TOY = ["rule_name multiple user creations severity low actor jdoe",
       "rule_name defender high severity alerts severity high",
       "rule_name multiple user creations severity low actor asmith"]
vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1, sublinear_tf=True,
                      strip_accents="unicode", lowercase=True)
Xtoy = vec.fit_transform(TOY)
vocab = vec.get_feature_names_out()
DOC = 1
n_docs = len(TOY)
analyse = vec.build_analyzer()
toy_rows, raw = [], {}
for term in sorted(set(analyse(TOY[DOC]))):
    count = analyse(TOY[DOC]).count(term)
    df = sum(term in set(analyse(d)) for d in TOY)
    tf = 1 + math.log(count)
    idf = math.log((1 + n_docs) / (1 + df)) + 1
    raw[term] = (count, tf, df, idf, tf * idf)
norm = math.sqrt(sum(v[4] ** 2 for v in raw.values()))
for term, (count, tf, df, idf, w) in raw.items():
    sk = Xtoy[DOC, vec.vocabulary_[term]]
    assert abs(sk - w / norm) < 1e-12, (term, sk, w / norm)   # hand formula == sklearn
    toy_rows.append((term, count, tf, df, idf, w, w / norm))
toy_rows.sort(key=lambda r: -r[6])
cos_01 = float((Xtoy[0] @ Xtoy[1].T).toarray()[0, 0])
cos_02 = float((Xtoy[0] @ Xtoy[2].T).toarray()[0, 0])


# =============================================================== charts
def chart_pipeline():
    steps = [("1  Select", "the ES notable\nonly"),
             ("2  Drop", "44 keys:\nplumbing, ids,\ntime, triage"),
             ("3  Keep", "937 fields Apollo\nforwards"),
             ("4  Scrub", "dates, epochs,\nclock times,\nempty values"),
             ("5  Render", "'key value' text,\nlower-cased"),
             ("6  TF-IDF", "sparse vector\nx, ||x|| = 1"),
             ("7  Score", "p = σ(w·x + b)\nvs threshold")]
    fig, ax = plt.subplots(figsize=(12, 2.3))
    ax.set_xlim(0, len(steps)); ax.set_ylim(0, 1); ax.axis("off")
    for i, (head, body) in enumerate(steps):
        colour = NAVY if i < 5 else ("#2c5282" if i == 5 else HUMAN)
        ax.add_patch(FancyBboxPatch((i + 0.06, 0.08), 0.86, 0.84,
                                    boxstyle="round,pad=0.02,rounding_size=0.06",
                                    fc="white", ec=colour, lw=1.6))
        ax.text(i + 0.49, 0.77, head, ha="center", va="center", fontsize=10.5,
                fontweight="bold", color=colour)
        ax.text(i + 0.49, 0.40, body, ha="center", va="center", fontsize=7.8, color=INK,
                linespacing=1.35)
        if i < len(steps) - 1:
            ax.annotate("", xy=(i + 1.06, 0.5), xytext=(i + 0.92, 0.5),
                        arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.2))
    return fig


def chart_contributions():
    labels = [t[0] for t in EX_TERMS] + ["all other terms\n+ intercept"]
    vals = [t[3] for t in EX_TERMS] + [EX_REST]
    order = list(np.argsort(vals[:-1])[::-1]) + [len(vals) - 1]
    labels = [labels[i] for i in order]; vals = [vals[i] for i in order]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(11, 3.9),
                                  gridspec_kw={"width_ratios": [2.3, 1]})
    y = np.arange(len(vals))[::-1]
    cols = [HUMAN if v > 0 else AI for v in vals[:-1]] + ["#a0aec0"]
    ax.barh(y[:-1], vals[:-1], color=cols[:-1], height=0.62)
    for yi, v in zip(y[:-1], vals[:-1]):
        ax.text(v + (0.006 if v > 0 else -0.006), yi, f"{v:+.3f}", va="center",
                ha="left" if v > 0 else "right", fontsize=8.5, color=INK)
    ax.set_yticks(y[:-1]); ax.set_yticklabels(labels[:-1], fontsize=9)
    ax.axvline(0, color=MUTED, lw=0.8)
    ax.set_xlim(-0.26, 0.22)
    ax.set_xlabel("contribution to the log-odds  =  tf-idf × weight", fontsize=9)
    ax.set_title("the nine largest single terms", fontsize=10.5, fontweight="bold",
                 color=NAVY, loc="left")
    ax.text(0.2, y[-2] - 0.15, "→ toward a human", color=HUMAN, fontsize=8.5, ha="right")
    ax.text(-0.25, y[0], "toward the AI ←", color=AI, fontsize=8.5, ha="left", va="center")
    ax.spines[["top", "right"]].set_visible(False)

    parts = [("nine terms", EX_SHOWN), ("everything else", EX_REST)]
    ax2.bar([0, 1], [EX_SHOWN, EX_REST], color=["#718096", "#a0aec0"], width=0.55)
    ax2.bar([2], [EX_LOGIT], color=NAVY, width=0.55)
    ax2.axhline(THR_LOGIT_EX := logit(EX_THR), color=HUMAN, ls="--", lw=1.2)
    ax2.text(2.35, THR_LOGIT_EX - 0.12, f"threshold\nz = {THR_LOGIT_EX:.2f}", fontsize=7.8,
             color=HUMAN, va="top", ha="right")
    for xi, v in zip([0, 1, 2], [EX_SHOWN, EX_REST, EX_LOGIT]):
        ax2.text(xi, v - 0.08, f"{v:+.3f}", ha="center", va="top", fontsize=8.5)
    ax2.set_xticks([0, 1, 2]); ax2.set_xticklabels(["9 terms", "other 246\n+ intercept",
                                                    "total z"], fontsize=8.5)
    ax2.set_ylim(min(THR_LOGIT_EX, EX_LOGIT) - 0.9, 0.3)
    ax2.axhline(0, color=MUTED, lw=0.8)
    ax2.set_title(f"z = {EX_LOGIT:.3f}  →  p = {EX_SCORE}", fontsize=10.5,
                  fontweight="bold", color=NAVY, loc="left")
    ax2.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    return fig


def chart_sigmoid():
    z = np.linspace(-8, 4, 400)
    fig, (a, b) = plt.subplots(1, 2, figsize=(11, 3.3))
    for ax in (a, b):
        ax.plot(z, 1 / (1 + np.exp(-z)), color=NAVY, lw=2)
        ax.axvline(THR_LOGIT, color=HUMAN, ls="--", lw=1.2)
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlabel("z = w·x + b   (the evidence, in log-odds)", fontsize=9)
    a.set_ylabel("p = σ(z) = P(not benign)", fontsize=9)
    a.set_title("the logistic link", fontsize=10.5, fontweight="bold", color=NAVY, loc="left")
    a.axhline(0.5, color=RULE, lw=0.8)
    a.text(THR_LOGIT + 0.15, 0.62, f"shipped threshold\np = {THRESHOLD}\nz = {THR_LOGIT:.2f}",
           color=HUMAN, fontsize=8)
    b.set_yscale("log"); b.set_xlim(-8, 0); b.set_ylim(3e-4, 0.6)
    b.fill_between(z, 3e-4, 1 / (1 + np.exp(-z)), where=z < THR_LOGIT, color=AI, alpha=0.12)
    b.text(-7.7, 0.03, "AI lane\n(auto-close)", color=AI, fontsize=9, fontweight="bold")
    b.text(-3.6, 0.0012, "human lane", color=HUMAN, fontsize=9, fontweight="bold")
    b.set_title("the same curve, log scale - where the router operates", fontsize=10.5,
                fontweight="bold", color=NAVY, loc="left")
    fig.tight_layout()
    return fig


def chart_distribution():
    q = ["1%", "5%", "25%", "50%", "95%"]
    benign = [0.003043, 0.006181, 0.022868, 0.063957, 0.591170]
    notben = [0.018535, 0.038744, 0.175780, 0.268050, 0.916769]
    mins = (0.000470, 0.005312)
    fig, ax = plt.subplots(figsize=(11, 2.6))
    for yi, vals, mn, col, name in [(1, benign, mins[0], AI, "benign (7,530)"),
                                    (0, notben, mins[1], HUMAN, "not benign (2,198)")]:
        ax.plot([mn, vals[-1]], [yi, yi], color=col, lw=1, alpha=0.6)
        ax.plot([vals[2], vals[3]], [yi, yi], color=col, lw=9, alpha=0.35, solid_capstyle="butt")
        ax.scatter(vals, [yi] * 5, color=col, zorder=3, s=22)
        ax.scatter([mn], [yi], color=col, marker="|", s=200, zorder=3)
        for qq, v in zip(q, vals):
            ax.text(v, yi + 0.2, qq, ha="center", fontsize=7.5, color=col)
        ax.text(1.4, yi, name, va="center", fontsize=9, color=col, fontweight="bold")
    ax.axvline(0.005300, color=INK, ls=":", lw=1.2)
    ax.text(0.0053, -0.62, " zero-miss threshold\n 0.005300", fontsize=7.8, color=INK)
    ax.set_xscale("log"); ax.set_xlim(3e-4, 3.5); ax.set_ylim(-0.75, 1.55)
    ax.set_yticks([]); ax.spines[["top", "right", "left"]].set_visible(False)
    ax.set_xlabel("P(not benign), log scale  -  held-out window, last ~3 months", fontsize=9)
    return fig


def chart_calibration():
    rows = M["calibration_by_score_fifth"]
    x = np.arange(len(rows))
    fig, ax = plt.subplots(figsize=(7.5, 2.9))
    ax.bar(x - 0.18, [r["predicted_pct"] for r in rows], 0.36, color=NAVY, label="predicted")
    ax.bar(x + 0.18, [r["actual_pct"] for r in rows], 0.36, color="#a0aec0", label="actual")
    for xi, r in zip(x, rows):
        ax.text(xi - 0.18, r["predicted_pct"] + 1.2, f"{r['predicted_pct']}", ha="center", fontsize=7.8)
        ax.text(xi + 0.18, r["actual_pct"] + 1.2, f"{r['actual_pct']}", ha="center", fontsize=7.8)
    ax.set_xticks(x); ax.set_xticklabels([f"fifth {r['fifth']}" for r in rows], fontsize=8.5)
    ax.set_ylabel("% not benign", fontsize=9)
    ax.legend(frameon=False, fontsize=8.5)
    ax.spines[["top", "right"]].set_visible(False)
    return fig


# =============================================================== document
def on_page(canvas, doc):
    canvas.saveState()
    canvas.setFont("Sans", 7.5)
    canvas.setFillColor(colors.HexColor(MUTED))
    if doc.page > 1:
        canvas.drawString(0.8 * inch, 0.5 * inch,
                          "Alert triage model - how it decides and how to explain it")
        canvas.drawRightString(letter[0] - 0.8 * inch, 0.5 * inch, f"{doc.page}")
        canvas.setStrokeColor(colors.HexColor(RULE))
        canvas.line(0.8 * inch, letter[1] - 0.6 * inch, letter[0] - 0.8 * inch,
                    letter[1] - 0.6 * inch)
    canvas.restoreState()


# ---------------------------------------------------------------- cover + summary
p("Explaining the alert triage model", "title")
p("How the model reaches each decision, what it can and cannot explain, and the "
  f"mathematics underneath it. Release {MANIFEST['version']}, frozen "
  f"{MANIFEST['frozen_at'][:10]}.", "subtitle")

callout(
    "Yes, every decision this model makes can be explained, and exactly rather than "
    "approximately. The model is a <b>linear</b> classifier over <b>TF-IDF</b> text "
    "features. Its score is built by adding up one number per word or word-pair in "
    "the alert, then converting the total to a probability. So for any alert we can "
    "list which words and phrases pushed it toward a human analyst, which pushed it "
    "toward auto-close, and by how much. Those numbers add up exactly to the score.",
    title="The short answer")

p("What the model decides", "h2")
p("The model answers one question: <b>is this alert a Benign Positive?</b> It does "
  "<i>not</i> choose between True Positive and False Positive. Both are grouped "
  "together as <i>not benign: a human should look</i>. That grouping is deliberate, "
  "and it sets the limit on what an explanation can claim. The model can explain "
  "why an alert does or does not look like the routine, expected activity analysts "
  "have closed before. It cannot explain why an alert is malicious.")

table([["Analyst disposition", "Model class", "Route", "Alerts", "Share"],
       ["Benign Positive - Suspicious But Expected", "0  benign", "AI lane (auto-close)",
        "25,963", "80.1%"],
       ["True Positive - Suspicious Activity", "1  not benign", "Human analyst", "5,723", "17.6%"],
       ["False Positive - Incorrect Analytic Logic", "1  not benign", "Human analyst", "580", "1.8%"],
       ["False Positive - Inaccurate Data", "1  not benign", "Human analyst", "161", "0.5%"],
       ["Other / Undetermined / never dispositioned", "not used", "-", "91,470", "-"]],
      [2.75, 1.05, 1.45, 0.75, 0.6], align_right=(3, 4))

p("What an explanation can say, at a glance", "h2")
table([["Question a SOC manager asks", "Can the model answer it?", "How"],
       ["Why was this alert auto-closed / sent to a human?", "<b>Yes, exactly</b>",
        "Per-term contributions that sum to the score (Section 3)"],
       ["Which parts of the alert mattered most?", "<b>Yes</b>",
        "Rank terms by contribution; group them by CEF field (Section 4)"],
       ["What does the model look for in general?", "<b>Yes</b>",
        "Global weights: the terms most associated with each lane (Section 4)"],
       ["Have we seen alerts like this before, and what did analysts decide?",
        "<b>Yes, with a small addition</b>",
        "Nearest past alerts by cosine similarity, with their dispositions (Section 4)"],
       ["How sure is it?", "<b>Yes, statistically</b>",
        "A calibrated probability, plus a threshold with a stated error bound (Section 6)"],
       ["Is this a true positive or a false positive?", "<b>No</b>",
        "Not what it was trained on (Section 8)"],
       ["Why is this activity malicious?", "<b>No</b>",
        "It learns associations from analyst decisions, not attacker behaviour"]],
      [2.6, 1.45, 2.85], pad=2)

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 1 pipeline
p("1. How an alert becomes a score", "h1")
p("When Splunk ES raises a notable it becomes a SOAR container, and Apollo forwards "
  "the notable to the model. Seven steps then turn it into one number. The first "
  "five are deterministic text preparation. Nothing is learned in them, so they "
  "can be audited line by line.")
figure(chart_pipeline(), 7.0, "Figure 1. The preprocessing and scoring pipeline. Steps "
       "1-5 come from config/preprocessing.yaml, which both training and serving read.")

p("Step 1: read only what existed before the analyst did", "h2")
p("A SOAR container holds up to four kinds of artifact, and only one of them existed "
  "before an analyst touched the alert. Reading any of the others would let the "
  "model see the answer:")
table([["Artifact", "How it is identified", "Used?"],
       ["ES notable: the alert as it arrived", "rule_id present", "<b>yes</b>"],
       ["Settings / metrics", "carries SA_DISPOSITION", "no: that field <i>is</i> the label"],
       ["soar_event=*", "incident_closure, sa_decision, ...", "no: written during triage"],
       ["Slack / escalation note", "reply_count, snooze_count", "no: exists only once escalated"]],
      [2.4, 2.3, 2.2])

p("Steps 2-4: remove what would mislead", "h2")
p("44 fields in seven groups are dropped before the model sees anything. Each group "
  "has a stated reason:")
groups = CONFIG["drop_keys"]
table([["Group", "Keys", "Why it is removed"]] +
      [[g.replace("_", " "), str(len(v["keys"])),
        " ".join(v["reason"].split()).replace(" See APOLLO_MAPPING.md", "")]
       for g, v in groups.items()],
      [1.45, 0.55, 4.9], align_right=(1,))
p("Timestamps are also <b>scrubbed from every value</b>: ISO dates, epochs, bare years "
  "and clock times. This matters because the share of not-benign alerts rose from 6.5% "
  "to 25.3% over the training year. A date therefore predicts when an alert happened, "
  "not what it is. An early fit ranked <i>2026 04</i> among its strongest signals. "
  "After scrubbing, the model can only learn from <i>what</i> fired and <i>what it "
  "fired on</i>. Of the remaining fields, only the 937 that Apollo has been seen to "
  "forward are kept, so the model is trained on exactly what it will receive in "
  "production.")

p("Step 5: one string per alert", "h2")
p("Whatever survives is written out as <font face='Mono'>key value</font> pairs and "
  "lower-cased. Pairs rather than bare values let a two-word phrase bind a field to "
  "its value: <font face='Mono'>severity low</font> and <font face='Mono'>urgency "
  "low</font> stay distinguishable. An excerpt of a real alert, with the customer "
  "and user masked:")
story.append(Table([[Paragraph(
    "clientname customer-a cat initialaccess ... destinationusername user@customer-a.org "
    "detectionsource officeatp entity_type alert indicator 216.81.249.98 ... "
    "rule_name ts - defender - def07 - new high severity alerts security_domain threat "
    "severity high sourceaddress 216.81.249.98 title a potentially malicious url click "
    "was detected urgency medium ...", S["mono"])]], colWidths=[6.9 * inch],
    style=[("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(SOFT)),
           ("LEFTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 6),
           ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
gap(8)
p("That string is the model's only input: one column, <font face='Mono'>text_cef"
  "</font>. There is no per-customer history, no rule track record and no time of day. "
  "Steps 6 and 7 (TF-IDF and scoring) are explained conceptually in Section 2 and "
  "mathematically in Section 5.")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 2 explainable
p("2. Why this model can be explained exactly", "h1")
p("Many machine-learning models are black boxes, and explaining them means "
  "approximating them, with tools such as SHAP or LIME estimating what the model "
  "probably did. This model needs no approximation, because of its shape:")
eq(r"p \;=\; \sigma(z),\qquad z \;=\; b \;+\; \sum_{j} w_j\, x_j", 16, "1")
bullets([
    "<b>x<sub>j</sub></b> is the TF-IDF value of term <i>j</i> in this alert: how "
    "prominent the word or word-pair is here, discounted by how common it is "
    "across all alerts. It is zero for terms that do not appear.",
    "<b>w<sub>j</sub></b> is the weight the model learned for term <i>j</i>. Positive "
    "means alerts containing it were more often sent to humans; negative means more "
    "often closed as Benign Positive.",
    "<b>b</b> is the intercept, the starting point before any evidence.",
    "<b>σ</b> is the logistic function, which turns any total <i>z</i> into a "
    "probability between 0 and 1.",
])
p("The total <i>z</i> is a plain sum. Each term's share of it, its "
  "<b>contribution</b> <i>c<sub>j</sub> = w<sub>j</sub>x<sub>j</sub></i>, is exact. "
  "The contributions plus the intercept add back to <i>z</i> with nothing left over, "
  "and <i>z</i> fixes the score. An explanation built from contributions is "
  "therefore what the model computed, not a story told about it afterwards.")
eq(r"z \;=\; b + c_1 + c_2 + \cdots + c_k,\qquad c_j = w_j x_j", 14, "2")

p("Reading a contribution", "h2")
p("Contributions are in <b>log-odds</b>, the natural unit of the logistic model. "
  "They have a direct interpretation: a contribution <i>c</i> multiplies the odds "
  "that the alert needs a human by <i>e<super>c</super></i>.")
eq(r"\frac{p}{1-p} \;=\; \exp\left(b+\sum_j c_j\right) \;=\; e^{b}\cdot e^{c_1}\cdot e^{c_2}\cdots e^{c_k}", 15, "3")
table([["Contribution c", "Odds multiplied by e<super>c</super>", "Plain reading"],
       ["+0.153  (defender)", f"{math.exp(0.153):.3f}", "odds of needing a human up 16.5%"],
       ["+0.095  (severity alerts)", f"{math.exp(0.095):.3f}", "up 10.0%"],
       ["-0.145  (org)", f"{math.exp(-0.145):.3f}", "down 13.5%"],
       ["-0.190  (98)", f"{math.exp(-0.190):.3f}", "down 17.3%"]],
      [2.3, 1.8, 2.8], align_right=(1,))
p("Because the threshold is also a point on the same scale, the routing rule can be "
  "stated in the same units. The shipped threshold "
  f"<i>p</i> = {THRESHOLD} corresponds to <i>z</i> = {THR_LOGIT:.2f}. "
  "<b>An alert is auto-closed only if the evidence for and against adds up to below "
  f"{THR_LOGIT:.2f}</b>, which requires a lot of evidence that it is routine.")
figure(chart_sigmoid(), 7.0, "Figure 2. The logistic link from evidence z to score p. "
       "The router works in the far-left tail: an alert must be very confidently "
       "routine before it is auto-closed.")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 3 worked example
p("3. Explaining one real alert", "h1")
p("Container <b>prod-soar:229895</b>, a Microsoft Defender detection (DEF07, "
  "<i>a potentially malicious URL click was detected</i>). The analyst disposition "
  "was <b>True Positive</b>. This example is taken from the project's documented "
  "walkthrough on an earlier release, fitted on the 22,699 alerts closed before this "
  "one, so the model had never seen it. The arithmetic is the same in the current "
  "release; only the learned weights differ.")
bullets(["<b>Arrives:</b> 5 CEF artifacts, 203 keys. Four artifacts are discarded "
         "(label, closure, and two Slack escalation records).",
         "<b>Kept:</b> the ES notable, 84 keys, rendered to one string of 285 words.",
         "<b>Vectorised:</b> 255 non-zero terms out of 168,679 in that release's "
         "vocabulary. Every other entry of x is zero and contributes nothing."])
table([["Term", "tf-idf  x", "weight  w", "contribution  w·x", "Direction"]] +
      [[f"<font face='Mono'>{t}</font>", f"{x:.3f}", f"{w:+.2f}", f"{c:+.3f}",
        "<font color='%s'>toward human</font>" % HUMAN if c > 0 else
        "<font color='%s'>toward AI</font>" % AI] for t, x, w, c in EX_TERMS] +
      [["<i>the other 246 terms + intercept</i>", "", "", f"{EX_REST:+.3f}", ""],
       ["<b>total evidence z</b>", "", "", f"<b>{EX_LOGIT:+.3f}</b>",
        f"<b>p = σ(z) = {EX_SCORE}</b>"]],
      [2.2, 0.9, 0.9, 1.35, 1.55], align_right=(1, 2, 3))
figure(chart_contributions(), 7.0,
       "Figure 3. Left: the nine single terms with the largest contributions. Right: "
       "how all contributions add to the score. The remainder is computed so that the "
       "total equals the documented score.")

p("What the explanation tells an analyst", "h2")
bullets([
    "<b>Decision:</b> score 0.1785 is far above that release's threshold of 0.0087 "
    f"(z = {logit(EX_THR):.2f}), so the alert goes to a human. That was correct: it was "
    "a true positive.",
    "<b>Pushing toward a human:</b> <i>defender</i>, <i>severity alerts</i> and "
    "<i>malicious</i>, the vocabulary of the high-severity Defender rule, plus "
    "<i>search incidentid</i> and the source address.",
    "<b>Pushing toward auto-close:</b> <i>org</i>, <i>98</i> and <i>detectionsource "
    "officeatp</i>, tokens that appear often on routine Office ATP alerts.",
    "<b>What it does not say:</b> the model is not 18% sure this is malicious. It "
    "says the alert is <i>not confidently routine</i>, and that is the only judgement "
    "it is asked to make. At a textbook 0.5 cut-off this alert would have been missed; "
    "the low threshold catches it.",
])
callout("Note <font face='Mono'>98</font> and <font face='Mono'>sourceaddress 216</font>. "
        "The tokeniser splits 216.81.249.98 into octets, so the model reads <i>parts</i> "
        "of an IP address as separate words. Explanations expose quirks like this, "
        "which is a reason to show them. An analyst can see when a decision leans on "
        "something that should not matter.", HUMAN, "Explanations surface quirks")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 4 explanation types
p("4. Four kinds of explanation the model supports", "h1")
p("4.1 Local: why this alert", "h2")
p("This is the per-term breakdown above. The production scoring API already returns "
  "it: every <font face='Mono'>/predict</font> response includes <font face='Mono'>"
  "top_terms</font>, the twelve terms with the largest absolute contributions, "
  "along with the score, the threshold, the margin, every field that was dropped and "
  "why, and how many of the alert's words the model has never seen. A word the model "
  "has never seen contributes exactly zero, so a high unknown-word count means the "
  "explanation is thin.")

def card():
    rows = [
        ["DECISION", f"<b><font color='{HUMAN}'>HUMAN LANE</font></b>: send to an analyst"],
        ["Score", f"P(not benign) = <b>{EX_SCORE}</b> against threshold {EX_THR} "
                  f"(margin +{EX_SCORE - EX_THR:.4f})"],
        ["Toward a human", "defender +0.153 · severity alerts +0.095 · "
                           "search incidentid +0.080"],
        ["Toward auto-close", "98 -0.190 · org -0.145 · potentially malicious -0.085"],
        ["By field", "<i>proposed, Section 4.2</i>: e.g. rule fields, user fields, "
                     "source address, each with its summed contribution"],
        ["Similar past alerts", "<i>proposed, Section 4.4</i>: the 5 nearest alerts, "
                                "their similarity and how analysts closed them"],
        ["Coverage", "255 terms recognised; the API also reports words it has never seen"],
        ["Read as", "<i>not confidently routine</i>, not <i>18% likely malicious</i>"],
    ]
    data = [[Paragraph(f"<b>{a}</b>", S["cell"]), Paragraph(b, S["cell"])] for a, b in rows]
    tb = Table(data, colWidths=[1.45 * inch, 5.0 * inch])
    tb.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor(NAVY)),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#fdf2f2")),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor(RULE)),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    story.append(KeepTogether([
        Paragraph("<b>Mock-up: an explanation card for prod-soar:229895</b>", S["body"]),
        tb, Paragraph("Rows 1-4 and 7 come from today's /predict response; rows 5-6 are "
                      "the proposed additions.", S["caption"])]))


card()

p("4.2 By field: which part of the alert", "h2")
p("Managers usually think in CEF fields, not tokens. Because the input is written as "
  "<font face='Mono'>key value</font> text, each token's position tells us which field "
  "it came from. Contributions can therefore be summed per field: "
  "<i>rule_description +0.31, destinationUserName -0.22, severity +0.08</i>. This is "
  "a small addition to the API, and the sums still add up to <i>z</i>. One caveat is "
  "that a two-word phrase can span the end of one field and the start of the next, "
  "so those phrases have to be split or shown under both fields.")

p("4.3 Global: what the model looks for", "h2")
p("Sorting all weights <i>w<sub>j</sub></i> gives the model's overall vocabulary of "
  "risk: the terms most associated with each lane across all alerts. This is the "
  "right artifact for a model review board. The fitted text is concentrated in a few "
  "fields:")
table([["CEF field", "On % of alerts", "% of fitted text"],
       ["rule_description", "98.4", "11.0"], ["savedsearch_description", "95.4", "10.7"],
       ["orig_rule_description", "78.6", "8.9"], ["source", "100.0", "5.4"],
       ["search_name", "100.0", "5.4"], ["rule_title", "100.0", "4.9"],
       ["orig_rule_title", "78.9", "4.3"], ["rule_name", "100.0", "4.0"],
       ["contributing_events_search", "20.8", "1.5"], ["destinationUserName", "78.9", "1.1"]],
      [2.6, 1.3, 1.3], align_right=(1, 2))
p("The top ten fields make up 57.4% of all fitted text and the top fifty make up "
  "82.0%. The 976 fields that appear on fewer than 1% of alerts make up 3.7%. So the "
  "model mostly learns <i>which detection fired</i> and <i>whom and what it fired "
  "on</i>.", "small")

p("4.4 By precedent: alerts like this one", "h2")
p("This is the explanation analysts trust most, and the model's representation "
  "supports it directly. Every alert is a unit-length TF-IDF vector, so the "
  "similarity of two alerts is just their dot product, the <b>cosine "
  "similarity</b> (Section 5.3). For a new alert we can retrieve the <i>k</i> most "
  "similar past alerts and show how analysts closed them: <i>\"The 5 most similar "
  "alerts in the last 90 days (similarity 0.91-0.96) were all closed Benign Positive "
  "by the MDR desk.\"</i> This is an addition, not something the model does today. "
  "It reuses the same vectoriser and needs an index of recent alerts with their "
  "dispositions.")
callout("Show managers the <b>precedent view</b> first, the <b>field view</b> second, "
        "and keep the <b>term view</b> for analysts and model reviews. All three come "
        "from the same numbers; they differ only in how they are grouped.",
        NAVY, "Recommendation")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 5 math
p("5. The mathematics in depth", "h1")
p("5.1 Tokens and n-grams", "h2")
p("The rendered string is lower-cased, accents are stripped, and it is split into "
  "tokens with the pattern <font face='Mono'>\\b\\w\\w+\\b</font>: runs of two or more "
  "letters or digits. Single characters and punctuation are discarded, which is why "
  "an IP address becomes its octets. The features are every <b>unigram</b> (single "
  "token) and every <b>bigram</b> (pair of adjacent tokens). So <font face='Mono'>"
  "severity high</font> yields <i>severity</i>, <i>high</i> and <i>severity "
  "high</i>. With <font face='Mono'>min_df = 1</font>, any term seen even once in "
  "training is kept. This gives a vocabulary of hundreds of thousands of terms: "
  "635,806 when fitted on the 22,699-alert training split.")

p("5.2 TF-IDF: turning text into numbers", "h2")
p("TF-IDF gives each term a weight that rises with how prominent the term is in this "
  "alert and falls with how common it is across all alerts. A word that appears on "
  "every alert, such as <i>rule_name</i>, tells the model little; a rare one tells it "
  "a lot. For term <i>t</i> in alert <i>d</i>, with <i>n</i> training alerts:")
eq(r"\mathrm{tf}(t,d) \;=\; 1 + \ln\, \mathrm{count}(t,d)"
   r"\qquad(\mathrm{sublinear;\ 0\ if\ absent})", 13.5, "4")
eq(r"\mathrm{idf}(t) \;=\; \ln\frac{1+n}{1+\mathrm{df}(t)} + 1", 13.5, "5")
eq(r"v_t \;=\; \mathrm{tf}(t,d)\cdot\mathrm{idf}(t),\qquad "
   r"x_t \;=\; \frac{v_t}{\sqrt{\sum_{s} v_s^{\,2}}}", 13.5, "6")
bullets([
    "<b>Sublinear tf</b> (4): a term repeated 11 times counts 1 + ln 11 = 3.4, not 11. "
    "Alerts that list the same event repeatedly (<i>a user account was created</i> "
    "eleven times) would otherwise be dominated by the repetition.",
    "<b>Smoothed idf</b> (5): df(<i>t</i>) is the number of training alerts containing "
    "<i>t</i>. The +1 terms prevent division by zero and keep terms that appear "
    "everywhere at a small positive weight.",
    "<b>L2 normalisation</b> (6): every alert vector has length 1, so a long alert "
    "and a short alert are on the same scale. One consequence for explanations: a "
    "term's x<sub>t</sub>, and so its contribution, is smaller in a long alert, "
    "because its share of a longer text is smaller.",
])
p(f"<b>Worked example.</b> Three toy alerts, vectorised here with the production "
  f"settings. The table shows alert 2, <font face='Mono'>{TOY[DOC]}</font>. "
  "<i>severity</i> and <i>high</i> each appear twice, so their tf is 1 + ln 2 = 1.693. "
  "<i>severity</i> and <i>rule_name</i> appear in every toy alert, so their idf is the "
  "minimum, 1.0.", "body")
table([["term t", "count", "tf", "df", "idf", "tf·idf", "x_t  (normalised)"]] +
      [[f"<font face='Mono'>{t}</font>", str(c), f"{tf:.3f}", str(df), f"{idf:.3f}",
        f"{w:.3f}", f"<b>{x:.3f}</b>"] for t, c, tf, df, idf, w, x in toy_rows],
      [2.15, 0.6, 0.65, 0.5, 0.65, 0.75, 1.6], align_right=(1, 2, 3, 4, 5, 6))
p(f"Vector length before normalising: √Σv<super>2</super> = {norm:.3f}. Every value in "
  "the last column was checked against scikit-learn's output to 12 decimal places.",
  "small")

p("5.3 Cosine similarity: the basis for precedents", "h2")
p("Because every vector has unit length, the similarity of two alerts is just a dot "
  "product. It ranges from 0 (no terms in common) to 1 (the same text):")
eq(r"\cos(\mathbf{x}_a,\mathbf{x}_b) \;=\; \frac{\mathbf{x}_a\cdot\mathbf{x}_b}"
   r"{\|\mathbf{x}_a\|\,\|\mathbf{x}_b\|} \;=\; \sum_t x_{a,t}\,x_{b,t}", 13.5, "7")
p(f"In the toy set, the two <i>multiple user creations</i> alerts, which differ only in "
  f"the actor's name, have similarity <b>{cos_02:.3f}</b>. The Defender alert and a "
  f"user-creation alert have <b>{cos_01:.3f}</b>.")

p("5.4 The classifier: logistic regression", "h2")
p("The estimator is scikit-learn's <font face='Mono'>SGDClassifier</font> with "
  "<font face='Mono'>loss = log_loss</font>. That is logistic regression, fitted by "
  "stochastic gradient descent instead of a batch solver. It models the probability "
  "that an alert is not benign as:")
eq(r"P(y=1\mid \mathbf{x}) \;=\; \sigma(\mathbf{w}^{\top}\mathbf{x}+b) \;=\; "
   r"\frac{1}{1+e^{-(\mathbf{w}^{\top}\mathbf{x}+b)}}", 14, "8")
p("Equivalently, the log-odds are linear in the features: "
  "ln(<i>p</i> / (1 - <i>p</i>)) = <b>w</b>·<b>x</b> + <i>b</i>. That linearity is "
  "what makes equations (2) and (3) exact.")

p("5.5 What training minimises", "h2")
p("The weights are chosen to minimise the average <b>log loss</b> (cross-entropy) over "
  "the training alerts, plus an <b>L2 penalty</b> that keeps weights small:")
eq(r"J(\mathbf{w},b) \;=\; \frac{1}{n}\sum_{i=1}^{n}\left[-y_i\ln p_i-(1-y_i)\ln(1-p_i)"
   r"\right] \;+\; \frac{\alpha}{2}\,\|\mathbf{w}\|_2^{2}", 13.5, "9")
bullets([
    "The log loss is small when the model gives high probability to what analysts "
    "actually decided, and large when it is confidently wrong.",
    "<b>α = 10<super>-5</super></b> sets the penalty strength. It was chosen by an "
    "out-of-fold sweep. The penalty stops the model from staking huge weights on "
    "terms seen on only a handful of alerts. Weights, and so contributions, stay "
    "moderate and comparable.",
    "<b>The classes are not reweighted.</b> Balancing the 80/20 split was measured "
    "and cost both ranking quality and auto-close coverage, so the probabilities "
    "reflect the true base rate.",
])

p("5.6 How SGD finds the weights", "h2")
p("Stochastic gradient descent visits the training alerts one at a time, in shuffled "
  "order, and nudges the weights after each one. For logistic loss, the gradient of "
  "one alert's loss with respect to <i>z</i> is just the prediction error "
  "(<i>p<sub>i</sub></i> - <i>y<sub>i</sub></i>), which gives a very readable update:")
eq(r"\mathbf{w} \;\leftarrow\; (1-\eta_t\,\alpha)\,\mathbf{w} \;-\; "
   r"\eta_t\,(p_i-y_i)\,\mathbf{x}_i,\qquad b \;\leftarrow\; b-\eta_t\,(p_i-y_i)", 13.5,
   "10")
bullets([
    "If the analyst said <i>not benign</i> (<i>y</i> = 1) and the model said 0.2, the "
    "error is -0.8. Every term present in that alert has its weight <i>raised</i> in "
    "proportion to its TF-IDF value. A benign alert scored too high lowers them. "
    "Over 32,427 alerts, each weight settles where it best agrees with the analysts' "
    "decisions on the alerts that contain that term.",
    "The (1 - η<sub>t</sub>α) factor is the L2 penalty: every step shrinks all "
    "weights slightly toward zero.",
    "The step size follows scikit-learn's <i>optimal</i> schedule, "
    "η<sub>t</sub> = 1 / (α(<i>t</i> + <i>t</i><sub>0</sub>)), which starts large and "
    "decays as training proceeds.",
])
p("<b>Averaging.</b> The release uses <font face='Mono'>average = True</font> "
  "(averaged SGD). The weights shipped are the mean of the weight vector over all "
  "updates, not the last value:")
eq(r"\bar{\mathbf{w}} \;=\; \frac{1}{T}\sum_{t=1}^{T}\mathbf{w}_t", 13.5, "11")
p("Averaging cancels the jitter of single-alert updates and lands close to the exact "
  "logistic-regression solution, at a fraction of the cost. For explanations, this "
  "means the weights are stable: they do not depend much on which alert happened to "
  "be visited last. Training stops when the loss improves by less than 10<super>-6"
  "</super> for 20 consecutive passes. The shuffle seed is fixed (7), so a release "
  "can be rebuilt exactly.")

p("5.7 How this relates to SHAP", "h2")
p("SHAP is the most common explainability method for black-box models. For a linear "
  "model whose features are treated as independent, SHAP values have a closed form:")
eq(r"\phi_j \;=\; w_j\,\left(x_j-\mathbb{E}[x_j]\right)", 14, "12")
p("The only difference from our contribution <i>c<sub>j</sub></i> = "
  "<i>w<sub>j</sub>x<sub>j</sub></i> is the reference point. SHAP measures each term "
  "against the <i>average</i> alert, and the contributions measure it against an "
  "empty alert. Both are exact and both sum to the score. Running SHAP here would "
  "add cost and approximation and no new information.")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 6 statistics
p("6. How sure is it? The threshold and the evidence", "h1")
p("6.1 Choosing the threshold with a stated error bound", "h2")
p("The model only outputs a score. <b>The threshold is the one setting a person "
  "chooses</b>, and it is chosen statistically rather than by eye. Sort a recent "
  "<i>calibration</i> window of alerts by score and consider auto-closing the lowest "
  "<i>n</i>. If <i>k</i> of them were in fact not benign, the true error rate in the AI "
  "lane is uncertain, so we use its <b>Clopper-Pearson exact upper confidence "
  "bound</b>:")
eq(r"U(k,n) \;=\; B^{-1}\!\left(1-\delta;\; k+1,\; n-k\right)", 14, "13")
p(f"Here <i>B</i><super>-1</super> is the inverse Beta distribution and 1 - δ = "
  f"{CONF:.0%} is the confidence level. The threshold is the score below which the "
  f"<i>widest</i> lane still satisfies U(k, n) ≤ {TOL:.0%}. The method is exact rather "
  "than a normal approximation, because the counts here are small and often zero, "
  "which is where approximations fail.")
eq(r"k=0:\quad U(0,n)=1-\delta^{1/n}\;\leq\;0.01 \;\;\Leftrightarrow\;\; "
   rf"n\;\geq\;{N_CLEAN}", 13.5, "14")
p(f"In plain words: to claim at 95% confidence that fewer than 1% of auto-closed "
  f"alerts are mistakes, you need at least <b>{N_CLEAN} auto-closed alerts with zero "
  "mistakes</b>, and more if any mistakes occur. The bound is set by the evidence "
  "rather than by a safety factor.")
table([["Shipped operating point", "Value"],
       ["Threshold on P(not benign)", f"{THRESHOLD}  (z = {THR_LOGIT:.2f})"],
       ["Rule", f"Clopper-Pearson upper bound ≤ {TOL:.0%} at {CONF:.0%} confidence"],
       ["Trained through / calibrated on / validated on",
        f"{THR['calibrated_on']['train_through']}  /  {THR['calibrated_on']['calibrate']}"
        f"  /  {THR['calibrated_on']['validate']}"],
       ["Measured on the validation window",
        f"{THR['measured']['auto_closed_pct']}% of alerts auto-closed, "
        f"{THR['measured']['misses']} of them not benign"]],
      [2.6, 4.3])

p("6.2 The trade-off: coverage against misses", "h2")
p("Allowing a few mistakes moves the line past the handful of not-benign alerts that "
  "score unusually low, and buys a lot of coverage. Out-of-fold over twelve months "
  f"({M['test_rows']:,} alerts, {M['test_positives']:,} not benign):")
table([["Misses allowed", "Threshold", "Auto-closed", "Alerts in AI lane",
        "Not-benign rate in AI lane"]] +
      [[k, f"{v['threshold']:.6f}", f"{v['ai_share_pct']}%", f"{v['ai_lane_rows']:,}",
        f"{v['residual_rate_pct']}%"] for k, v in OPS.items()],
      [1.2, 1.2, 1.2, 1.5, 1.8], align_right=(0, 1, 2, 3, 4))
p("The thresholds are small because the region where the model is <i>certain</i> is "
  "narrow, not because the model is timid. On recent traffic, the single "
  "lowest-scoring not-benign alert sets the zero-miss line:")
figure(chart_distribution(), 7.0,
       "Figure 4. Score percentiles for benign and not-benign alerts on the held-out "
       "window (last ~3 months). Bars span the 25th-50th percentiles; ticks mark the "
       "minimum. The populations overlap widely; the router uses only the far-left "
       "tail.")

p("6.3 Is the score a real probability?", "h2")
p("A score is <i>calibrated</i> if, among alerts scored around 20%, about 20% turn "
  "out not benign. Log loss tends to produce calibrated scores without extra "
  "correction. Split by score into fifths:")
figure(chart_calibration(), 5.2, "Figure 5. Predicted vs actual not-benign rate by score "
       "fifth, out-of-fold (manifest of release v20).")
p("The top fifth is well calibrated (65.4% predicted, 62.3% actual). The bottom "
  "fifths under-predict: alerts scored 0.6% are not benign 1.8% of the time. "
  "<b>Read low scores as a ranking, not a literal probability.</b> This is exactly "
  "why the threshold comes from measured error rates (6.1) and not from the score "
  "itself.")

p("6.4 Ranking quality", "h2")
eq(r"\mathrm{ROC\ AUC} \;=\; P\left(s(\mathrm{not\ benign}) > s(\mathrm{benign})\right) "
   r"\;=\; \frac{1}{n_P\, n_N}\sum_{i\in P}\;\sum_{j\in N}\mathbb{1}[s_i>s_j]", 13, "15")
eq(r"\mathrm{AP} \;=\; \sum_k (R_k-R_{k-1})\,P_k", 13, "16")
p("P and N are the not-benign and benign alerts, <i>s</i> the score, and "
  "<i>R<sub>k</sub></i>, <i>P<sub>k</sub></i> the recall and precision at the "
  "<i>k</i>-th threshold.", "small")
p(f"<b>ROC AUC {M['roc_auc']}</b>: pick one not-benign and one benign alert at random, "
  f"and 85.4% of the time the not-benign one scores higher. <b>PR AUC (average "
  f"precision) {M['pr_auc']}</b>, against a base rate of {M['base_rate_pct']}%, which "
  "is what random guessing would score. On the most recent, harder three months the "
  "held-out ROC AUC is 0.809. The model ranks well, but it does not separate the two "
  "classes cleanly, which is why it is used to clear the obvious routine alerts and "
  "not to make final calls.")

story.append(CondPageBreak(3.2 * inch))

# ---------------------------------------------------------------- 7 limits
p("7. What explanations from this model do not mean", "h1")
p("An honest explanation states its limits. These should travel with any explanation "
  "shown to analysts or customers.")
bullets([
    "<b>Association, not cause.</b> A positive weight means analysts more often sent "
    "alerts containing that term to the human lane. It is not evidence that the term "
    "is malicious. The model reflects past analyst decisions.",
    "<b>The labels are noisy.</b> Different closing desks disagree by 15.3 percentage "
    "points on the same rule at the same customer. The model learns the average "
    "decision, and no explanation can be more consistent than the labels it was "
    "trained on.",
    "<b>Duplicated text splits the credit.</b> The rule description appears in up to "
    "three fields (rule_description, savedsearch_description, orig_rule_description). "
    "Correlated terms share credit somewhat arbitrarily, so read the field total "
    "rather than any single term.",
    "<b>It memorises entities.</b> Usernames, email domains and IP octets carry real "
    "weight. That is part of its accuracy, a privacy consideration for anything that "
    "displays explanations, and a reason explanations can look odd (the "
    "<i>98</i> example).",
    "<b>Unseen words count for nothing.</b> A new customer, rule or host contributes "
    "zero until the next retrain. New customers' alerts therefore score near the base "
    "rate and go to humans, which is the safe default.",
    "<b>The base rate drifts.</b> The not-benign share rose from 6.5% to 25.3% over "
    "the training year. A threshold set on one period does not carry over to another, "
    "so it must be recalibrated on recent traffic before each deployment.",
    "<b>Confidence is not distance from the threshold.</b> A score of 0.004 against a "
    "threshold of 0.007 is a confident auto-close; a score of 0.45 against the same "
    "threshold is not a confident human-lane call. Near 0.5 the model is about as "
    "good as a coin flip.",
])

# ---------------------------------------------------------------- 8 tp/fp
p("8. The TP / FP / BP question", "h1")
p("SOC managers asked for explanations of <i>true positive</i>, <i>false positive</i> "
  "and <i>benign positive</i>. The current model makes one cut, Benign Positive "
  "against everything else, for three reasons:")
bullets([
    "<b>It is the routing question.</b> Only Benign Positives are safe to auto-close. "
    "True and false positives both need a human action: a response, or a fix to the "
    "detection.",
    "<b>False positives are rare.</b> 741 alerts in a year (2.3%), across two "
    "sub-types and many rules. That is too few to learn a reliable third class, and "
    "a weak FP class would make the BP explanations worse.",
    "<b>The clean class is benign.</b> Benign Positive - Suspicious But Expected "
    "means one thing, <i>we have seen this and it is fine</i>, and that is what the "
    "weights learn.",
])
p("If TP-versus-FP explanations are needed, the sound design is a <b>second-stage "
  "model</b> applied only to human-lane alerts, trained to separate True Positive from "
  "False Positive. It would have the same linear form, so it would be explainable "
  "in exactly the same way. FP is largely a property of the <i>detection rule</i> "
  "(incorrect logic, inaccurate data), so rule-level FP rates over time may answer "
  "the managers' question better than any per-alert model.")

# ---------------------------------------------------------------- 9 recommendations
p("9. Recommendations", "h1")
table([["#", "Recommendation", "Effort", "Value"],
       ["1", "Show the <b>explanation card</b> on every routed alert: decision, score vs "
        "threshold, top terms each way, unknown-word count. The data already exists in "
        "the /predict response.", "Small: UI only", "High"],
       ["2", "Add <b>field-level attribution</b>: sum contributions by the CEF field "
        "each token came from (Section 4.2).", "Small: API", "High for managers"],
       ["3", "Add <b>precedents</b>: the 5 most similar recent alerts by cosine "
        "similarity, with their dispositions (Section 4.4).", "Medium: needs an index",
        "Highest for analysts"],
       ["4", "Publish a <b>global term report</b> per release: top weights each way, "
        "flagging entity-like terms (IP octets, usernames).", "Small: notebook cell",
        "Model governance"],
       ["5", "<b>Recalibrate the threshold</b> on recent traffic before each deployment, "
        "and report the Clopper-Pearson bound with it.", "Existing: Stage 7",
        "Safety"],
       ["6", "If TP vs FP is required, scope a <b>second-stage model</b> and rule-level "
        "FP reporting (Section 8).", "Medium", "Answers the original ask"]],
      [0.3, 3.9, 1.35, 1.35])

# ---------------------------------------------------------------- appendix
p("Appendix: the shipped configuration", "h2")
table([["Setting", "Value"],
       ["Input", "one text column: the ES notable as 'key value' text"],
       ["Fields used", "937 (forwarded by Apollo); 44 dropped; timestamps scrubbed"],
       ["Tokeniser", r"\b\w\w+\b, lower-case, accents stripped"],
       ["n-grams", "1-2 (unigrams and bigrams)"],
       ["TF", "sublinear: 1 + ln(count)"],
       ["IDF", "smoothed: ln((1+n)/(1+df)) + 1"],
       ["Normalisation", "L2 (unit-length vectors)"],
       ["min_df", "1"],
       ["Estimator", "SGDClassifier, log_loss (logistic regression)"],
       ["Penalty", "L2, α = 1e-5"],
       ["Averaging", "averaged SGD (average = True)"],
       ["Stopping", "tol 1e-6, 20 passes without improvement, max 10,000 passes"],
       ["Class weights", "none (80 / 20 left as is)"],
       ["Training data", f"{MANIFEST['data']['rows_total']:,} alerts, 3 SOAR servers, "
        f"12 months to {MANIFEST['data']['trained_through'][:10]}"],
       ["Scores", "P(not benign); auto-close when below the threshold"]],
      [1.6, 5.3], pad=2)
p("Sources: models/v20/manifest.json, config/preprocessing.yaml, "
  "02-train-model.ipynb (saved outputs), and ../triage/machine-learning/README.md "
  "(worked example). This document is generated by docs/build_explainability_pdf.py.",
  "small")

doc = SimpleDocTemplate(str(OUT), pagesize=letter, leftMargin=0.8 * inch,
                        rightMargin=0.8 * inch, topMargin=0.75 * inch,
                        bottomMargin=0.75 * inch, title="Explaining the alert triage model",
                        author="TekStream AI-SOC")
doc.build(story, onFirstPage=on_page, onLaterPages=on_page)
print(f"wrote {OUT}")
