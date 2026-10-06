"""Palette + Plotly template.

Values come from the dataviz reference palette; every categorical set used here
was run through scripts/validate_palette.js:

  5-slot status set   light/dark  adjacent pairs   PASS (light contrast WARN -> table view ships)
  7-slot rule-mix set light/dark  adjacent pairs   PASS (8th slot reserved, tail folds into "Other")
  3-slot instance set light/dark  all pairs        PASS
  5-step ordinal ramp light/dark                   PASS
"""

PALETTES = {
    "light": {
        "surface": "#fcfcfb",
        "plane": "#f9f9f7",
        "ink": "#0b0b0b",
        "ink2": "#52514e",
        "muted": "#898781",
        "grid": "#e1e0d9",
        "axis": "#c3c2b7",
        "border": "rgba(11,11,11,0.10)",
        # categorical slots 1-8, fixed order, never cycled
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4",
                   "#008300", "#4a3aa7", "#e34948"],
        # single-hue sequential, light -> dark
        "seq": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#2a78d6",
                "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b"],
        # ordinal ramp (discrete ordered marks); light end clears 2:1
        "ordinal": ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"],
        "neutral": "#c3c2b7",
    },
    "dark": {
        "surface": "#1a1a19",
        "plane": "#0d0d0d",
        "ink": "#ffffff",
        "ink2": "#c3c2b7",
        "muted": "#898781",
        "grid": "#2c2c2a",
        "axis": "#383835",
        "border": "rgba(255,255,255,0.10)",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181",
                   "#008300", "#9085e9", "#e66767"],
        "seq": ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf",
                "#2a78d6", "#3987e5", "#5598e7", "#6da7ec", "#86b6ef"],
        "ordinal": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#184f95"],
        "neutral": "#52514e",
    },
}

FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'

# Status of a container -> fixed categorical slot. Colour follows the entity, so
# filtering a status out never repaints the survivors.
STATUS_ORDER = ["duplicate", "closed", "escalated", "open", "preparing"]

# Rolled-up verdict -> fixed categorical slot (3 slots, all pairs validated in
# both modes). "Other" and blank verdicts are out of scope for this chart.
FINAL_ORDER = ["Benign Positive", "True Positive", "False Positive"]

# Ordered buckets -> ordinal ramp; "first occurrence" is neutral, not a step.
RECUR_ORDER = ["within 1h", "1h - 24h", "24h - 7d", "over 7d", "first occurrence"]


def colors(theme):
    return PALETTES["dark" if theme == "dark" else "light"]


def status_colors(theme):
    c = colors(theme)
    return dict(zip(STATUS_ORDER, c["series"]))


def final_colors(theme):
    c = colors(theme)
    return dict(zip(FINAL_ORDER, c["series"]))


def recur_colors(theme):
    c = colors(theme)
    return dict(zip(RECUR_ORDER, c["ordinal"][:4] + [c["neutral"]]))


def sequential_scale(theme):
    stops = colors(theme)["seq"]
    return [[i / (len(stops) - 1), s] for i, s in enumerate(stops)]


def diverging_scale(theme):
    """Two hues that read as opposite, meeting at the neutral - never a hue at
    the midpoint. Blue <-> red per the reference palette."""
    dark = theme == "dark"
    mid = "#383835" if dark else "#f0efec"
    blues = ["#0d366b", "#1c5cab", "#3987e5", "#9ec5f4"]
    reds = ["#f2a6a6", "#e34948", "#b52d2d", "#7d1f1f"]
    stops = blues + [mid] + reds
    return [[i / (len(stops) - 1), col] for i, col in enumerate(stops)]


def layout(theme, **kw):
    """Base layout: recessive solid hairline grid, muted axes, generous padding."""
    c = colors(theme)
    base = dict(
        paper_bgcolor=c["surface"],
        plot_bgcolor=c["surface"],
        font=dict(family=FONT, size=12, color=c["ink2"]),
        title=dict(font=dict(size=14, color=c["ink"]), x=0, xanchor="left",
                   y=0.97, yanchor="top", pad=dict(b=8)),
        margin=dict(l=8, r=16, t=52, b=8),
        hoverlabel=dict(bgcolor=c["surface"], bordercolor=c["border"],
                        font=dict(family=FONT, size=12, color=c["ink"])),
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="left", x=0,
                    font=dict(size=11, color=c["ink2"]), bgcolor="rgba(0,0,0,0)",
                    itemsizing="constant"),
        xaxis=dict(gridcolor=c["grid"], griddash="solid", zeroline=False,
                   linecolor=c["axis"], linewidth=1, ticks="outside",
                   ticklen=4, tickcolor=c["axis"],
                   tickfont=dict(size=11, color=c["muted"]),
                   title=dict(font=dict(size=11, color=c["muted"]))),
        yaxis=dict(gridcolor=c["grid"], griddash="solid", zeroline=False,
                   linecolor=c["axis"], linewidth=1, ticks="outside",
                   ticklen=4, tickcolor=c["axis"],
                   tickfont=dict(size=11, color=c["muted"]),
                   title=dict(font=dict(size=11, color=c["muted"]))),
        bargap=0.35,
        dragmode=False,
    )
    for k, v in kw.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    return base
