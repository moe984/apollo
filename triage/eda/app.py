"""EDA dashboard for the 90-day SOAR container disposition extract.

    python3 eda/app.py     ->  http://127.0.0.1:8050
"""
import sys
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Dash, Input, Output, State, ctx, dash_table, dcc, html

sys.path.insert(0, str(Path(__file__).resolve().parent))
import data as D  # noqa: E402
import theme as T  # noqa: E402

DF = D.load()
OPT = D.options(DF)
OPT["top_tenant"] = DF["tenant_name"].value_counts().index[0]
OPT["top_rule"] = (DF[DF["container_status"].eq("closed")
                      & DF["sa_decision"].eq("close_with_disposition")]
                   [D.RULE_COL].value_counts().index[0])
TOP_N = 15
PAGE_SIZE = 30          # rules per page in the paginated verdict chart
RANK_N = 25             # rules shown in the ranked dot plot
# closure dispositions as recorded: verdicts first, then the ones that say nothing
DISP_ORDER = ["Benign Positive - Suspicious But Expected",
              "True Positive - Suspicious Activity", "Undetermined",
              "False Positive - Incorrect Analytic Logic",
              "False Positive - Inaccurate Data", "Other", "NA", "(none)"]

app = Dash(__name__, title="SOAR container disposition - 90d EDA")
server = app.server


# ---------------------------------------------------------------- components

def stat_tile(label, value, sub):
    return html.Div(className="tile", children=[
        html.Div(label, className="tile-label"),
        html.Div(value, className="tile-value"),
        html.Div(sub, className="tile-sub"),
    ])


ZOOM_CONFIG = {                       # zoom / pan / reset, nothing else
    "displayModeBar": True, "displaylogo": False, "responsive": True,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d",
                               "toggleSpikelines", "hoverCompareCartesian"],
}


def card(title, note, graph_id, height=340, span=False, zoom=False):
    return html.Div(className="card span2" if span else "card", children=[
        html.Div(className="card-head", children=[
            html.H2(title), html.P(note),
        ]),
        dcc.Graph(id=graph_id,
                  config=ZOOM_CONFIG if zoom else {"displayModeBar": False,
                                                   "responsive": True},
                  style={"height": f"{height}px"}),
    ])


app.layout = html.Div(id="root", **{"data-theme": "light"}, children=[
    dcc.Store(id="theme-store", data="light"),
    dcc.Store(id="final-page", data=0),
    dcc.Store(id="rdisp-page", data=0),

    html.Header(className="head", children=[
        html.Div([
            html.H1("SOAR container disposition"),
            html.P(f"{len(DF):,} containers · {OPT['tmin'].date()} to {OPT['tmax'].date()} · "
                   f"{DF['soar_instance'].nunique()} instances · "
                   f"{DF['tenant_name'].nunique()} tenants · "
                   f"{DF[D.RULE_COL].nunique()} rules"),
        ]),
        html.Button("Dark", id="theme-btn", className="ghost-btn", n_clicks=0),
    ]),

    # One filter row above everything it scopes.
    html.Div(className="filters", children=[
        html.Div(className="filter", children=[
            html.Label("Date range", htmlFor="f-date"),
            dcc.DatePickerRange(
                id="f-date",
                min_date_allowed=OPT["tmin"].date(), max_date_allowed=OPT["tmax"].date(),
                start_date=OPT["tmin"].date(), end_date=OPT["tmax"].date(),
                display_format="MMM D", updatemode="bothdates"),
        ]),
        html.Div(className="filter", children=[
            html.Label("SOAR instance", htmlFor="f-instance"),
            dcc.Dropdown(id="f-instance", options=OPT["instances"], multi=True,
                         placeholder="all instances"),
        ]),
        html.Div(className="filter grow", children=[
            html.Label("Tenant", htmlFor="f-tenant"),
            dcc.Dropdown(id="f-tenant", options=OPT["tenants"], multi=True,
                         placeholder="all tenants"),
        ]),
        html.Div(className="filter grow", children=[
            html.Label("Rule", htmlFor="f-rule"),
            dcc.Dropdown(id="f-rule", options=OPT["rules"], multi=True,
                         placeholder="all rules"),
        ]),
        html.Div(className="filter", children=[
            html.Label("Time grain", htmlFor="f-grain"),
            dcc.RadioItems(id="f-grain", inline=True, value="W",
                           options=[{"label": "Day", "value": "D"},
                                    {"label": "Week", "value": "W"},
                                    {"label": "Month", "value": "ME"},
                                    {"label": "Quarter", "value": "QE"}]),
        ]),
    ]),

    html.Div(id="tiles", className="tiles"),

    dcc.Tabs(id="tabs", value="overview", className="tabs",
             parent_className="tabs-parent", content_className="tabs-content",
             children=[
        dcc.Tab(label="Overview", value="overview", className="tab",
                selected_className="tab--selected", children=[
            html.Div(className="grid", children=[
                card("Volume by container status",
                     "Stacked counts over time. Duplicate is what the current dedup "
                     "already absorbs.",
                     "g-time", 360),
                card("Dedup capture by rule",
                     f"Top {TOP_N} rules by volume, split duplicate vs. still worked. "
                     f"Label is duplicate share.",
                     "g-rule", 460),
                card("Tenant profile",
                     "Volume against duplicate share, by SOAR instance. Hover for "
                     "escalation rate.",
                     "g-tenant", 360),
                card("Closure disposition",
                     "Analyst verdict on containers that reached a close.",
                     "g-disp", 360),
                card("Residual recurrence",
                     "Containers NOT marked duplicate, by time since an identical "
                     "tenant + name.",
                     "g-recur", 320),
                card("Duplicate share over time",
                     "Share of containers the dedup absorbs, per period. Endpoint is "
                     "labelled.",
                     "g-trend", 320),
                card("Rule x tenant density",
                     "Container volume, top rules against top tenants.",
                     "g-heat", 460, span=True),
            ]),
        ]),
        dcc.Tab(label="Disposition", value="disposition", className="tab",
                selected_className="tab--selected", children=[
            html.Div(className="grid", children=[
                card("Final disposition by rule",
                     "Top 12 rules by volume. Closed containers decided with a "
                     "disposition, benign/true/false positive only. Label is the "
                     "true-positive share.",
                     "g-final", 560, span=True),
                card("Final disposition by customer",
                     "Every customer with a verdict, biggest first, with each "
                     "verdict's share printed in its segment.",
                     "g-final-cust", 1500, span=True),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Rules ranked by true-positive share"),
                        html.P(f"Top {RANK_N} rules on one baseline, so the ranking "
                               f"reads straight off. Dot size is how many verdicts "
                               f"the rule has."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum verdicts", htmlFor="final-minvol"),
                            dcc.Dropdown(
                                id="final-minvol", value=10, clearable=False,
                                options=[{"label": f"{n}+", "value": n}
                                         for n in (1, 5, 10, 25, 50)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-final-rank", config={"displayModeBar": False,
                                                         "responsive": True},
                              style={"height": "700px"}),
                ]),
                card("Every rule at once",
                     "One dot per rule: how much it fires against how much of it "
                     "lands as a true positive. Hover for the full mix.",
                     "g-final-all", 420, span=True),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Every rule, page by page"),
                        html.P(f"The same verdict mix as above, {PAGE_SIZE} rules at a "
                               f"time - so no rule is left off. Sort by volume or by "
                               f"any verdict's share."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Button("Previous", id="final-prev", className="ghost-btn",
                                    n_clicks=0),
                        html.Span(id="final-page-label", className="pager-label"),
                        html.Button("Next", id="final-next", className="ghost-btn",
                                    n_clicks=0),
                        html.Div(className="pager-sort", children=[
                            html.Label("Sort by", htmlFor="final-sort"),
                            dcc.Dropdown(
                                id="final-sort", value="containers", clearable=False,
                                options=[
                                    {"label": "Volume", "value": "containers"},
                                    {"label": "True positive %", "value": "True Positive %"},
                                    {"label": "Benign positive %", "value": "Benign Positive %"},
                                    {"label": "False positive %", "value": "False Positive %"},
                                ]),
                        ]),
                    ]),
                    dcc.Graph(id="g-final-page", config={"displayModeBar": False,
                                                         "responsive": True},
                              style={"height": "940px"}),
                ]),
                card("Spread of each verdict across rules",
                     "One point per rule, one box per verdict: the middle 50% of "
                     "rules sits in the box, the line is the median.",
                     "g-final-box", 420, span=True),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("All rules by verdict"),
                        html.P("Every rule with a verdict in the current slice - "
                               "sortable and filterable, the readable twin of the "
                               "charts above."),
                    ]),
                    dash_table.DataTable(
                        id="t-final", page_size=15, sort_action="native",
                        filter_action="native", style_as_list_view=True,
                        style_table={"overflowX": "auto"},
                        style_cell_conditional=[{"if": {"column_id": D.RULE_COL},
                                                 "textAlign": "left",
                                                 "minWidth": "280px"}],
                    ),
                ]),
            ]),
        ]),
        dcc.Tab(label="By customer", value="customer", className="tab",
                selected_className="tab--selected", children=[
            html.Div(className="card span2 standalone", children=[
                html.Div(className="card-head", children=[
                    html.H2("Which rules fire for each customer"),
                    html.P("Every customer, every container the platform opened - not "
                           "just the ones that reached a verdict. The busiest rules get "
                           "their own colour; the rest fold into Other. This one ignores "
                           "the customer filter so the whole book stays comparable."),
                ]),
                html.Div(className="pager", children=[
                    html.Div(className="pager-sort", children=[
                        html.Label("Show", htmlFor="c-mix-mode"),
                        dcc.Dropdown(id="c-mix-mode", value="share", clearable=False,
                                     options=[{"label": "Share of customer", "value": "share"},
                                              {"label": "Container count", "value": "count"}]),
                    ]),
                ]),
                dcc.Graph(id="g-c-rulemix", config={"displayModeBar": False,
                                                    "responsive": True},
                          style={"height": "1500px"}),
            ]),
            html.Div(id="c-summary", className="tiles"),
            html.Div(className="grid", children=[
                card("Rules for this customer",
                     "Verdict mix per rule, biggest first. Label is the true-positive "
                     "share.",
                     "g-c-rules", 760, span=True),
                card("When each verdict happened",
                     "One lane per rule, one mark per container, on the day it was "
                     "decided. Drag across the plot to zoom into a date range; "
                     "double-click to reset.",
                     "g-c-timeline", 560, span=True, zoom=True),
                card("Last verdict predicts the next",
                     "Pairs are consecutive firings of the same rule for this customer: "
                     "what it was ruled last time against this time.",
                     "g-c-transition", 380),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("How much the last verdict moves the odds"),
                        html.P("One dot per previous verdict: the chance this firing "
                               "lands on the chosen outcome. A dot only appears where "
                               "there are enough pairs to mean something."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Chance of", htmlFor="c-outcome"),
                            dcc.Dropdown(
                                id="c-outcome", value="True Positive", clearable=False,
                                options=[{"label": v, "value": v} for v in T.FINAL_ORDER]),
                        ]),
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum pairs", htmlFor="c-minpairs"),
                            dcc.Dropdown(
                                id="c-minpairs", value=3, clearable=False,
                                options=[{"label": f"{n}+", "value": n}
                                         for n in (2, 3, 5, 10)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-c-lift", config={"displayModeBar": False,
                                                     "responsive": True},
                              style={"height": "440px"}),
                ]),
            ]),
        ]),
        dcc.Tab(label="By rule", value="rule", className="tab",
                selected_className="tab--selected", children=[
            html.Div(id="r-summary", className="tiles"),
            html.Div(className="grid", children=[
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Every rule and its dispositions"),
                        html.P(f"Containers per rule by closure disposition as "
                               f"recorded - not rolled up - {PAGE_SIZE} rules a page. "
                               f"Every rule unless the rule filter narrows it."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Button("Previous", id="rdisp-prev", className="ghost-btn",
                                    n_clicks=0),
                        html.Span(id="rdisp-page-label", className="pager-label"),
                        html.Button("Next", id="rdisp-next", className="ghost-btn",
                                    n_clicks=0),
                        html.Div(className="pager-sort", children=[
                            html.Label("Sort by", htmlFor="rdisp-sort"),
                            dcc.Dropdown(
                                id="rdisp-sort", value="containers", clearable=False,
                                options=[{"label": "Volume", "value": "containers"}]
                                + [{"label": f"{v} %", "value": v}
                                   for v in DISP_ORDER]),
                        ]),
                        html.Div(className="pager-sort", children=[
                            html.Label("Show", htmlFor="rdisp-mode"),
                            dcc.Dropdown(
                                id="rdisp-mode", value="share", clearable=False,
                                options=[{"label": "Share of rule", "value": "share"},
                                         {"label": "Container count", "value": "count"}]),
                        ]),
                    ]),
                    dcc.Graph(id="g-r-dispositions", config={"displayModeBar": False,
                                                             "responsive": True},
                              style={"height": "940px"}),
                ]),
                card("Customers running this rule",
                     "Verdict mix per customer, biggest first. Label is the "
                     "true-positive share.",
                     "g-r-customers", 620, span=True),
                card("When each verdict happened",
                     "One lane per customer, one mark per container. Drag to zoom into "
                     "a date range; double-click to reset.",
                     "g-r-timeline", 440, span=True, zoom=True),
                card("Does this rule mean the same thing everywhere?",
                     "True-positive share per customer with a 95% interval. Overlapping "
                     "intervals mean the difference is not real yet.",
                     "g-r-consistency", 440),
                card("Verdict mix over time",
                     "Per period, for this rule. A step change is usually a tuning "
                     "change, not the threat landscape.",
                     "g-r-drift", 440),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("What this rule actually catches"),
                        html.P("The alert titles this rule fires on, ranked by "
                               "true-positive rate. Line is the rule's own rate."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum rows", htmlFor="r-mintitle"),
                            dcc.Dropdown(id="r-mintitle", value=5, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (2, 5, 10, 25)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-r-titles", config={"displayModeBar": False,
                                                       "responsive": True},
                              style={"height": "520px"}),
                ]),
                card("Last verdict predicts the next",
                     "Pairs are consecutive firings of this rule for the same customer.",
                     "g-r-transition", 380),
                card("When this rule fires",
                     "Day of week against hour of day, UTC. Sparse off-hours firing is "
                     "a feature; a flat block is usually a scheduled job.",
                     "g-r-clock", 380),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("How much the last verdict moves the odds"),
                        html.P("Per customer: the chance this firing lands on the "
                               "chosen outcome, after each previous verdict."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Chance of", htmlFor="r-outcome"),
                            dcc.Dropdown(id="r-outcome", value="True Positive",
                                         clearable=False,
                                         options=[{"label": v, "value": v}
                                                  for v in T.FINAL_ORDER]),
                        ]),
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum pairs", htmlFor="r-minpairs"),
                            dcc.Dropdown(id="r-minpairs", value=3, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (2, 3, 5, 10)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-r-lift", config={"displayModeBar": False,
                                                     "responsive": True},
                              style={"height": "420px"}),
                ]),
            ]),
        ]),
        dcc.Tab(label="Signals", value="signals", className="tab",
                selected_className="tab--selected", children=[
            html.Div(className="grid", children=[
                card("Rule tape",
                     "Daily volume with a 7-day and 28-day average and a 20-day "
                     "±2σ band. Diamonds mark where the fast average crosses the slow "
                     "one - a regime change in how much this rule fires.",
                     "g-s-tape", 420, span=True),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Detection portfolio"),
                        html.P("Every rule as a position: what it costs in containers "
                               "an analyst must touch against what it yields in true "
                               "positives. The staircase is the best yield available at "
                               "each scale; the line is the platform average."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum verdicts", htmlFor="s-minverdicts"),
                            dcc.Dropdown(id="s-minverdicts", value=20, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (10, 20, 50, 100)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-portfolio", config={"displayModeBar": False,
                                                          "responsive": True},
                              style={"height": "460px"}),
                ]),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2(id="s-breadth-title"),
                        html.P(id="s-breadth-note"),
                    ]),
                    dcc.Graph(id="g-s-breadth",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "400px"}),
                ]),
                card("Rules that move together",
                     "Correlation of daily volume between the busiest rules. A hot "
                     "pair is one signal wearing two names - your dedup candidates.",
                     "g-s-corr", 520),
                card("Equity curve",
                     "Running true positives minus benign positives for the selected "
                     "rule: what it has paid back against what it has cost. Shading "
                     "is drawdown from its own high-water mark.",
                     "g-s-equity", 520),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Which readings actually move the odds"),
                        html.P("Every indicator on this tab, scored the same way. A dot "
                               "is one reading; it sits at how far that reading moves "
                               "the chance of a true positive compared with the same "
                               "rule's own average. Comparing inside the rule is the "
                               "point: without it an indicator can look strong when it "
                               "is only telling you which rule fired. The number on the "
                               "right is the spread from the worst reading to the best "
                               "- long rows are indicators worth using, short rows are "
                               "decoration."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum verdicts per reading",
                                       htmlFor="s-minreading"),
                            dcc.Dropdown(id="s-minreading", value=50, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (20, 50, 100, 250)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-scorecard",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "620px"}),
                ]),
                card("A losing streak keeps losing",
                     "Verdict mix by how many benign positives the same rule already "
                     "produced in a row for the same customer, counting back from each "
                     "firing. The first bar is a firing with a clean slate.",
                     "g-s-streak", 420, span=True),
                card("Does the last verdict keep working?",
                     "True-positive rate over time, split by what the same rule was "
                     "ruled last time for the same customer. A 14-day window, so each "
                     "point is a fortnight of evidence. The grey line is everything "
                     "together - the gap between it and the top line is the edge.",
                     "g-s-momentum", 420, span=True),
                card("Verdict mix by hour of day",
                     "When a container was decided, in UTC. Business hours carry the "
                     "noise; the quiet hours carry a higher share of real ones.",
                     "g-s-hour", 400),
                card("Busy days against quiet days",
                     "Verdict mix by how broad that day was - how many customers were "
                     "busier than their own normal week, minus how many were quieter. "
                     "Quiet days are thinner but truer.",
                     "g-s-breadthmix", 400),

                # ---- readings that use no calendar and no clock at all ----
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Where the true positives actually come from"),
                        html.P("Rules lined up best-yield first. Walk left to right and "
                               "the line tells you: for this much of the analyst "
                               "workload, that much of everything real was found. The "
                               "straight line is what you would get if every rule paid "
                               "back the same - the gap above it is the whole value of "
                               "picking."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum verdicts per rule", htmlFor="s-minconc"),
                            dcc.Dropdown(id="s-minconc", value=20, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (5, 20, 50, 100)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-concentration",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "440px"}),
                ]),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("What the last verdict is worth, rule by rule"),
                        html.P("For each rule: the chance a firing is real after the "
                               "same customer's previous one was ruled true, against "
                               "the chance after it was ruled benign. A long row means "
                               "the previous verdict tells you almost everything; a "
                               "short row means the rule ignores its own history."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum pairs each way", htmlFor="s-minmom"),
                            dcc.Dropdown(id="s-minmom", value=5, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (3, 5, 10, 25)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-rulemom",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "560px"}),
                ]),
                html.Div(className="card", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Rules that never change their mind"),
                        html.P("How often a rule's verdict differs from the one it got "
                               "the time before, for the same customer. Rules along the "
                               "bottom have never been ruled two ways - the safest "
                               "ground for automating a closure."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum pairs", htmlFor="s-minflip"),
                            dcc.Dropdown(id="s-minflip", value=20, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (10, 20, 50, 100)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-flip",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "460px"}),
                ]),
                card("Rules that share a customer base",
                     "How much two rules overlap in the customers they run for, ignoring "
                     "when either of them fires. A hot pair is deployed as a set - "
                     "consolidate them once and every one of those customers benefits.",
                     "g-s-overlap", 520),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("The same alert, different desks"),
                        html.P("The verdict partly depends on who closed it. Each bar is "
                               "a closing role, scored against the true-positive rate of "
                               "the very same rule at the very same customer, so rule mix "
                               "and customer mix are already taken out. What is left is "
                               "how that desk calls the same work. Treat it as label "
                               "noise to control for, not a scoreboard - a desk that sees "
                               "the hard queue will read low here."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum verdicts per role", htmlFor="s-minrater"),
                            dcc.Dropdown(id="s-minrater", value=75, clearable=False,
                                         options=[{"label": f"{n}+", "value": n}
                                                  for n in (25, 75, 150, 300)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-s-rater",
                              config={"displayModeBar": False, "responsive": True},
                              style={"height": "500px"}),
                ]),
            ]),
        ]),
        dcc.Tab(label="Modeling", value="modeling", className="tab",
                selected_className="tab--selected", children=[
            html.Div(className="grid", children=[
                card("Last verdict predicts the next",
                     "For an alert seen before on the same tenant: what it was ruled "
                     "last time against what it was ruled this time.",
                     "g-ml-transition", 380),
                card("Verdict mix over time",
                     "Does the target drift? A drifting mix means a time-based split "
                     "and a retraining cadence.",
                     "g-ml-drift", 380),
                html.Div(className="card span2", children=[
                    html.Div(className="card-head", children=[
                        html.H2("Alert title carries signal"),
                        html.P("True-positive rate by the tail of the container name - "
                               "the alert title the rule fired on. Line is the base rate."),
                    ]),
                    html.Div(className="pager", children=[
                        html.Div(className="pager-sort", children=[
                            html.Label("Minimum rows", htmlFor="ml-mintitle"),
                            dcc.Dropdown(
                                id="ml-mintitle", value=25, clearable=False,
                                options=[{"label": f"{n}+", "value": n}
                                         for n in (10, 25, 50, 100)]),
                        ]),
                    ]),
                    dcc.Graph(id="g-ml-title", config={"displayModeBar": False,
                                                       "responsive": True},
                              style={"height": "560px"}),
                ]),
                card("Analysts disagree with each other",
                     "Among alerts that recurred on a tenant, how often the same alert "
                     "got a different verdict. This is the model's accuracy ceiling.",
                     "g-ml-noise", 520, span=True),
            ]),
        ]),
    ]),

    # Table view: the WCAG-clean twin of every chart above, same filtered slice.
    html.Div(className="card wide", children=[
        html.Div(className="card-head", children=[
            html.H2("Table view"),
            html.P("Per-rule aggregate of the current slice - every plotted value is readable here."),
        ]),
        dash_table.DataTable(
            id="t-table", page_size=15, sort_action="native", filter_action="native",
            style_as_list_view=True,
            style_table={"overflowX": "auto"},
            style_cell={"fontFamily": T.FONT, "fontSize": "12px", "padding": "8px 12px",
                        "border": "none", "textAlign": "right",
                        "fontVariantNumeric": "tabular-nums"},
            style_cell_conditional=[{"if": {"column_id": D.RULE_COL},
                                     "textAlign": "left", "minWidth": "280px"}],
            style_header={"fontWeight": "600", "textTransform": "uppercase",
                          "fontSize": "10px", "letterSpacing": "0.06em",
                          "border": "none"},
        ),
    ]),

    html.Footer(className="foot", children=[
        html.P("Normalised on load: 12 rows have no timestamp and fall outside date filtering; "
               "1,528 rows carry no rule name and show as (unattributed); the "
               "\"Incorrect analyatical logic\" disposition typo is folded into its correct spelling."),
    ]),
])


# ------------------------------------------------------------------ callbacks

# Clientside so the theme attribute is mutated in place: re-rendering #root from
# the server would remount the date picker and blank it out.
app.clientside_callback(
    """
    function (n) {
        const theme = (n % 2) ? "dark" : "light";
        const root = document.getElementById("root");
        if (root) { root.setAttribute("data-theme", theme); }
        return [theme, theme === "dark" ? "Light" : "Dark"];
    }
    """,
    Output("theme-store", "data"), Output("theme-btn", "children"),
    Input("theme-btn", "n_clicks"),
)


FILTERS = [Input("f-date", "start_date"), Input("f-date", "end_date"),
           Input("f-instance", "value"), Input("f-tenant", "value"),
           Input("f-rule", "value"), Input("f-grain", "value"),
           Input("theme-store", "data")]


def sliced(start, end, inst, ten, rule):
    return D.apply_filters(DF, start, end, inst, ten, rule)


def verdicts(d):
    """Containers whose verdict stands: closed by disposition, and one of the
    three kept classes ("Other" and blank say nothing about the alert)."""
    return d[d["container_status"].eq("closed")
             & d["sa_decision"].eq("close_with_disposition")
             & d["Final_Disposition"].isin(T.FINAL_ORDER)]


def verdict_by_rule(d):
    """One row per rule: verdict counts, shares, and the rule's container total."""
    m = (d.pivot_table(index=D.RULE_COL, columns="Final_Disposition",
                       values="container_id", aggfunc="size", fill_value=0)
         .reindex(columns=T.FINAL_ORDER, fill_value=0))
    m["containers"] = m[T.FINAL_ORDER].sum(axis=1)
    for v in T.FINAL_ORDER:
        m[f"{v} %"] = (m[v] / m["containers"] * 100).round(1)
    return m.sort_values("containers", ascending=False)


@app.callback(Output("tiles", "children"), *FILTERS)
def tiles(start, end, inst, ten, rule, grain, tm):
    d = sliced(start, end, inst, ten, rule)
    n = len(d)
    if n == 0:
        return [stat_tile("Containers", "0", "no rows in this slice")]
    nd = d[~d["is_dup"]]
    near = nd["recur_bucket"].isin(["within 1h", "1h - 24h"]).sum()
    closed = d[d["is_closed"]]
    bp = (closed["closure_disposition"] == "Benign Positive - Suspicious But Expected").sum()
    return [
        stat_tile("Containers", f"{n:,}", f"{d['t'].dt.date.nunique()} days covered"),
        stat_tile("Duplicate", f"{d['is_dup'].mean() * 100:.1f}%",
                  f"{int(d['is_dup'].sum()):,} absorbed by dedup"),
        stat_tile("Escalated", f"{d['is_esc'].mean() * 100:.1f}%",
                  f"{int(d['is_esc'].sum()):,} sent on"),
        stat_tile("Benign positive", f"{(bp / len(closed) * 100) if len(closed) else 0:.1f}%",
                  f"of {len(closed):,} closures"),
        stat_tile("Recurs within 24h", f"{(near / len(nd) * 100) if len(nd) else 0:.1f}%",
                  f"{int(near):,} of {len(nd):,} still worked"),
        stat_tile("Rules", f"{d[D.RULE_COL].nunique():,}",
                  f"across {d['tenant_name'].nunique():,} tenants"),
    ]


def chosen(values):
    """A global filter's value as a filter argument: empty means no filter."""
    return list(values) if values else None


def only_one(values):
    """The single entity the global filter is pinned to, else None."""
    return values[0] if values and len(values) == 1 else None


def scope_name(values, all_label, noun):
    """What to call the current global selection in prose."""
    if not values:
        return all_label
    return values[0] if len(values) == 1 else f"{len(values)} {noun}"


def _alpha(hex_color, alpha):
    """Same hue, softened - for washes and bands that must stay behind the data."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


# A segment narrower than this cannot hold its label without clipping.
FITS = 10


def seg_labels(share, fits=FITS):
    """Percent inside a stacked segment, blank where the segment is too thin."""
    return [f"{v:.0f}%" if v >= fits else "" for v in share]


def label_ink(theme, colors):
    """Ink that reads on the fills: near-white on light steps, near-black on dark."""
    return "#ffffff" if theme != "dark" else colors["surface"]


# how a period reads in a tooltip, per grain
GRAIN_FMT = {"D": "%b %-d", "W": "week of %b %-d", "ME": "%B %Y", "QE": "quarter to %b %Y"}


def grain_fmt(grain):
    return GRAIN_FMT.get(grain or "W", "%b %-d")


def empty_fig(theme, msg="No rows in this slice"):
    c = T.colors(theme)
    f = go.Figure()
    f.update_layout(**T.layout(theme, xaxis=dict(visible=False), yaxis=dict(visible=False)))
    f.add_annotation(text=msg, showarrow=False, font=dict(color=c["muted"], size=13))
    return f


@app.callback(Output("g-time", "figure"), *FILTERS)
def fig_time(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme)
    c, cols = T.colors(theme), T.status_colors(theme)
    p = (d.set_index("t").groupby("container_status").resample(grain)
         .size().rename("n").reset_index())
    fig = go.Figure()
    for status in T.STATUS_ORDER:            # fixed order -> fixed hue
        s = p[p["container_status"] == status]
        if s.empty:
            continue
        fig.add_bar(x=s["t"], y=s["n"], name=status, marker_color=cols[status],
                    # surface-coloured line = the 2px gap between stacked fills
                    marker_line=dict(color=c["surface"], width=1),
                    hovertemplate="%{x|" + grain_fmt(grain) + "}<br>%{y:,} "
                                  + status + "<extra></extra>")
    fig.update_layout(**T.layout(theme, barmode="stack", hovermode="x unified",
                                 yaxis=dict(title="containers")))
    return fig


@app.callback(Output("g-rule", "figure"), *FILTERS)
def fig_rule(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    g = (d.groupby(D.RULE_COL)
         .agg(n=("is_dup", "size"), dup=("is_dup", "sum"), esc=("is_esc", "mean"))
         .sort_values("n", ascending=False).head(TOP_N).iloc[::-1])
    g["worked"] = g["n"] - g["dup"]
    g["pct"] = g["dup"] / g["n"] * 100
    labels = [r if len(r) <= 38 else r[:36] + "…" for r in g.index]
    fig = go.Figure()
    for name, col, key in (("duplicate", c["series"][0], "dup"),
                           ("still worked", c["series"][1], "worked")):
        fig.add_bar(y=labels, x=g[key], name=name, orientation="h", marker_color=col,
                    marker_line=dict(color=c["surface"], width=1),
                    customdata=g.index,
                    hovertemplate="%{customdata}<br>%{x:,} " + name + "<extra></extra>")
    # one selective direct label per row: the number the chart is about
    fig.add_scatter(y=labels, x=g["n"], mode="text", text=[f"{p:.0f}%" for p in g["pct"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.30,
        margin=dict(l=8, r=48, t=52, b=8),
        xaxis=dict(title="containers", range=[0, g["n"].max() * 1.16]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-tenant", "figure"), *FILTERS)
def fig_tenant(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    g = (d.groupby(["tenant_name", "soar_instance"])
         .agg(n=("is_dup", "size"), dup=("is_dup", "mean"), esc=("is_esc", "mean"))
         .reset_index())
    fig = go.Figure()
    for i, sid in enumerate(OPT["instances"]):      # fixed slot per instance
        s = g[g["soar_instance"] == sid]
        if s.empty:
            continue
        fig.add_scatter(
            x=s["n"], y=s["dup"] * 100, mode="markers", name=sid,
            marker=dict(color=c["series"][i], size=11,
                        line=dict(color=c["surface"], width=2)),  # 2px surface ring
            customdata=s[["tenant_name", "esc"]].assign(esc=s["esc"] * 100),
            hovertemplate=("<b>%{customdata[0]}</b><br>%{x:,} containers<br>"
                           "%{y:.1f}% duplicate<br>%{customdata[1]:.1f}% escalated"
                           "<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, hovermode="closest",
        xaxis=dict(title="containers (log scale)", type="log", dtick=1,
                   minor=dict(ticks="", showgrid=False)),
        yaxis=dict(title="duplicate share (%)", range=[0, 100], ticksuffix="%")))
    return fig


@app.callback(Output("g-disp", "figure"), *FILTERS)
def fig_disp(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    d = d[d["is_closed"] & (d["closure_disposition"] != "(none)")]
    if d.empty:
        return empty_fig(theme, "No closed containers in this slice")
    c = T.colors(theme)
    g = d["closure_disposition"].value_counts().iloc[::-1]
    labels = [r if len(r) <= 40 else r[:38] + "…" for r in g.index]
    total = g.sum()
    fig = go.Figure()
    # one measure, one series -> one colour; the title names it, so no legend
    fig.add_bar(y=labels, x=g.values, orientation="h", marker_color=c["series"][0],
                marker_line=dict(color=c["surface"], width=1), customdata=g.index,
                hovertemplate="%{customdata}<br>%{x:,} containers<extra></extra>")
    fig.add_scatter(y=labels, x=g.values, mode="text",
                    text=[f"{v / total * 100:.0f}%" for v in g.values],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.30, margin=dict(l=8, r=48, t=52, b=8),
        xaxis=dict(title="containers", range=[0, g.values.max() * 1.16]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-final", "figure"), *FILTERS)
def fig_final(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    rules = d[D.RULE_COL].value_counts().head(12).index[::-1]
    m = (d[d[D.RULE_COL].isin(rules)]
         .pivot_table(index=D.RULE_COL, columns="Final_Disposition",
                      values="container_id", aggfunc="size", fill_value=0)
         .reindex(index=rules, columns=T.FINAL_ORDER, fill_value=0))
    total = m.sum(axis=1)
    labels = [r if len(r) <= 44 else r[:42] + "…" for r in m.index]
    ink = label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        if m[verdict].sum() == 0:
            continue
        share = m[verdict] / total * 100
        fig.add_bar(y=labels, x=share, name=verdict, orientation="h",
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(share),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m.index, m[verdict]], axis=-1),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]:,} "
                                  + verdict + " (%{x:.0f}%)<extra></extra>")
    # the true-positive share still shows where its own segment was too thin
    tp = (m["True Positive"] / total * 100)
    fig.add_scatter(y=labels, x=[101] * len(m), mode="text",
                    text=[f"{v:.0f}%" if v < FITS else "" for v in tp],
                    textposition="middle right",
                    showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.26,
        margin=dict(l=8, r=52, t=52, b=8),
        legend=dict(traceorder="normal"),
        uniformtext=dict(mode="hide", minsize=8),
        xaxis=dict(title="share of containers with a verdict",
                   range=[0, 108], tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-final-cust", "figure"), *FILTERS)
def fig_final_cust(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    m = (d.pivot_table(index="tenant_name", columns="Final_Disposition",
                       values="container_id", aggfunc="size", fill_value=0)
         .reindex(columns=T.FINAL_ORDER, fill_value=0))
    m["containers"] = m[T.FINAL_ORDER].sum(axis=1)
    m = m.sort_values("containers", ascending=False)[::-1]     # biggest at the top
    share = m[T.FINAL_ORDER].div(m["containers"], axis=0) * 100
    ink = label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        fig.add_bar(y=list(m.index), x=share[verdict], name=verdict, orientation="h",
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(share[verdict]),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m[verdict], m["containers"]], axis=-1),
                    hovertemplate="%{y}<br>%{customdata[0]:,} " + verdict
                                  + " of %{customdata[1]:,} (%{x:.0f}%)<extra></extra>")
    # the true-positive share still shows on rows where its segment was too thin
    outside = [f"{v:.0f}%" if v < FITS else "" for v in share["True Positive"]]
    fig.add_scatter(y=list(m.index), x=[101] * len(m), mode="text", text=outside,
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.22, legend=dict(traceorder="normal"),
        margin=dict(l=8, r=52, t=52, b=8),
        uniformtext=dict(mode="hide", minsize=8),
        xaxis=dict(title="share of the customer's verdicts", range=[0, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-final-rank", "figure"),
              Input("final-minvol", "value"), *FILTERS)
def fig_final_rank(min_vol, start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    g = verdict_by_rule(d)
    g = g[g["containers"] >= (min_vol or 1)]
    if g.empty:
        return empty_fig(theme, f"No rule reached {min_vol} verdicts in this slice")
    g = (g.sort_values(["True Positive %", "containers"], ascending=False)
         .head(RANK_N)[::-1])                      # highest share at the top
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in g.index]
    # dot area carries volume; sqrt keeps a 1,500-container rule from swamping a 10
    size = 9 + 19 * (np.sqrt(g["containers"]) / np.sqrt(g["containers"].max()))
    fig = go.Figure()
    # thin connector to the baseline: the eye can trace a row without a gridline
    for y, x in zip(labels, g["True Positive %"]):
        fig.add_shape(type="line", y0=y, y1=y, x0=0, x1=x, layer="below",
                      line=dict(color=c["grid"], width=2))
    fig.add_scatter(
        x=g["True Positive %"], y=labels, mode="markers",
        marker=dict(color=cols["True Positive"], size=size,
                    line=dict(color=c["surface"], width=2)),   # 2px surface ring
        customdata=np.stack([g.index, g["containers"], g["True Positive"],
                             g["Benign Positive"], g["False Positive"]], axis=-1),
        hovertemplate=("<b>%{customdata[0]}</b><br>%{x:.0f}% true positive"
                       "<br>%{customdata[2]:,} true · %{customdata[3]:,} benign"
                       " · %{customdata[4]:,} false<br>%{customdata[1]:,} verdicts"
                       "<extra></extra>"))
    # the volume each dot stands for, spelled out rather than left to dot area
    fig.add_scatter(x=[104] * len(g), y=labels, mode="text",
                    text=[f"n={int(v):,}" for v in g["containers"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, hovermode="closest",
        margin=dict(l=8, r=64, t=52, b=8),
        xaxis=dict(title="true-positive share of the rule's verdicts",
                   range=[-2, 116], tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-final-all", "figure"), *FILTERS)
def fig_final_all(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c = T.colors(theme)
    g = verdict_by_rule(d).reset_index()
    fig = go.Figure()
    # one measure, one series -> one colour; every rule is a dot
    fig.add_scatter(
        x=g["containers"], y=g["True Positive %"], mode="markers",
        marker=dict(color=c["series"][0], size=9, opacity=0.85,
                    line=dict(color=c["surface"], width=2)),   # 2px surface ring
        customdata=np.stack([g[D.RULE_COL], g["Benign Positive"], g["True Positive"],
                             g["False Positive"], g["False Positive %"]], axis=-1),
        hovertemplate=("<b>%{customdata[0]}</b><br>%{x:,} with a verdict<br>"
                       "%{customdata[2]:,} true positive (%{y:.0f}%)<br>"
                       "%{customdata[1]:,} benign positive<br>"
                       "%{customdata[3]:,} false positive (%{customdata[4]:.0f}%)"
                       "<extra></extra>"))
    # direct-label only the extremes: the busiest rule and the most true-positive one
    top = g.nlargest(1, "containers")
    tp = g[g["containers"] >= 50].nlargest(1, "True Positive %")
    mark = pd.concat([top, tp]).drop_duplicates(D.RULE_COL)
    # a label near the right edge points inward so it cannot overflow the plot
    cut = g["containers"].max() / 6
    fig.add_scatter(
        x=mark["containers"], y=mark["True Positive %"], mode="text",
        text=[r if len(r) <= 34 else r[:32] + "…" for r in mark[D.RULE_COL]],
        textposition=["top left" if n > cut else "top center"
                      for n in mark["containers"]],
        showlegend=False, hoverinfo="skip",
        textfont=dict(color=c["ink2"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, hovermode="closest",
        xaxis=dict(title="containers with a verdict (log scale)", type="log", dtick=1,
                   minor=dict(ticks="", showgrid=False)),
        yaxis=dict(title="true-positive share", range=[-4, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"])))
    return fig


PAGE_FILTERS = [Input("f-date", "start_date"), Input("f-date", "end_date"),
                Input("f-instance", "value"), Input("f-tenant", "value"),
                Input("f-rule", "value")]


@app.callback(Output("final-page", "data"),
              Input("final-prev", "n_clicks"), Input("final-next", "n_clicks"),
              *PAGE_FILTERS, Input("final-sort", "value"),
              State("final-page", "data"))
def page_state(prev, nxt, start, end, inst, ten, rule, sort_by, current):
    d = verdicts(sliced(start, end, inst, ten, rule))
    pages = max(1, -(-d[D.RULE_COL].nunique() // PAGE_SIZE))
    if ctx.triggered_id == "final-prev":
        current = (current or 0) - 1
    elif ctx.triggered_id == "final-next":
        current = (current or 0) + 1
    else:
        current = 0                # a filter or sort change starts over at page 1
    return min(max(current, 0), pages - 1)


@app.callback(Output("g-final-page", "figure"), Output("final-page-label", "children"),
              Input("final-page", "data"), Input("final-sort", "value"), *FILTERS)
def fig_final_page(page, sort_by, start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice"), ""
    c, cols = T.colors(theme), T.final_colors(theme)
    g = verdict_by_rule(d)
    if sort_by and sort_by != "containers":
        # volume breaks ties, so equal shares still read biggest-first
        g = g.sort_values([sort_by, "containers"], ascending=False)
    total_rules = len(g)
    pages = max(1, -(-total_rules // PAGE_SIZE))
    page = min(max(page or 0, 0), pages - 1)
    lo, hi = page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, total_rules)
    m = g.iloc[lo:hi][::-1]                       # biggest rule at the top
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in m.index]
    ink = label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:                 # fixed order -> fixed hue
        fig.add_bar(y=labels, x=m[f"{verdict} %"], name=verdict, orientation="h",
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(m[f"{verdict} %"]),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m.index, m[verdict]], axis=-1),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]:,} "
                                  + verdict + " (%{x:.0f}%)<extra></extra>")
    # the true-positive share still shows where its own segment was too thin
    fig.add_scatter(y=labels, x=[101] * len(m), mode="text",
                    text=[f"{v:.0f}%" if v < FITS else ""
                          for v in m["True Positive %"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.24, legend=dict(traceorder="normal"),
        uniformtext=dict(mode="hide", minsize=8),
        margin=dict(l=8, r=52, t=52, b=8),
        xaxis=dict(title="share of a rule's verdicts", range=[0, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    label = (f"Page {page + 1} of {pages} · rules {lo + 1}-{hi} of {total_rules:,}"
             f" · {int(m['containers'].sum()):,} containers on this page")
    return fig, label


@app.callback(Output("g-final-box", "figure"), *FILTERS)
def fig_final_box(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    g = verdict_by_rule(d).reset_index()
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        fig.add_box(
            y=g[f"{verdict} %"], name=verdict, width=0.35,
            marker=dict(color=cols[verdict], size=5, opacity=0.45,
                        line=dict(color=c["surface"], width=1)),
            line=dict(color=cols[verdict], width=2),
            fillcolor="rgba(0,0,0,0)", boxmean=True,
            boxpoints="all", jitter=0.5, pointpos=0,
            customdata=np.stack([g[D.RULE_COL], g["containers"], g[verdict]], axis=-1),
            hovertemplate=("<b>%{customdata[0]}</b><br>%{y:.0f}% " + verdict.lower()
                           + "<br>%{customdata[2]:,} of %{customdata[1]:,} verdicts"
                           + "<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", boxgap=0.5,
        yaxis=dict(title="share of a rule's verdicts", range=[-6, 106],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        xaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("t-final", "data"), Output("t-final", "columns"),
              Output("t-final", "style_data_conditional"),
              Output("t-final", "style_header"), Output("t-final", "style_cell"),
              *FILTERS)
def table_final(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    c = T.colors(theme)
    if d.empty:
        rows, cols = [], [{"name": "rule", "id": "rule"}]
    else:
        g = verdict_by_rule(d).reset_index()
        g = g[[D.RULE_COL, "containers", "Benign Positive", "True Positive",
               "False Positive", "True Positive %", "False Positive %"]]
        rows = g.to_dict("records")
        cols = [{"name": ("rule" if k == D.RULE_COL else k), "id": k} for k in g.columns]
    header = {"fontWeight": "600", "textTransform": "uppercase", "fontSize": "10px",
              "letterSpacing": "0.06em", "border": "none", "color": c["muted"],
              "backgroundColor": c["surface"], "borderBottom": f"1px solid {c['axis']}"}
    cell = {"fontFamily": T.FONT, "fontSize": "12px", "padding": "8px 12px",
            "border": "none", "textAlign": "right", "fontVariantNumeric": "tabular-nums",
            "backgroundColor": c["surface"], "color": c["ink"]}
    data_style = [{"if": {"row_index": "odd"}, "backgroundColor": c["plane"]}]
    return rows, cols, data_style, header, cell


@app.callback(Output("g-recur", "figure"), *FILTERS)
def fig_recur(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    d = d[d["recur_bucket"].notna()]
    if d.empty:
        return empty_fig(theme, "No worked containers in this slice")
    c, cols = T.colors(theme), T.recur_colors(theme)
    g = d["recur_bucket"].value_counts().reindex(T.RECUR_ORDER).fillna(0)
    total = g.sum()
    fig = go.Figure()
    fig.add_bar(x=T.RECUR_ORDER, y=g.values,
                marker_color=[cols[b] for b in T.RECUR_ORDER],
                marker_line=dict(color=c["surface"], width=1),
                text=[f"{v / total * 100:.0f}%" for v in g.values],
                textposition="outside", textfont=dict(color=c["ink2"], size=11),
                hovertemplate="%{x}<br>%{y:,} containers<extra></extra>")
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.4,
        yaxis=dict(title="containers", range=[0, g.values.max() * 1.18]),
        xaxis=dict(title="time since an identical tenant + container name",
                   showgrid=False)))
    return fig


@app.callback(Output("g-trend", "figure"), *FILTERS)
def fig_trend(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    p = (d.set_index("t").resample(grain)
         .agg(n=("is_dup", "size"), dup=("is_dup", "mean")).reset_index())
    p = p[p["n"] > 0]
    fig = go.Figure()
    # one series -> one colour, no legend box; the title names it
    fig.add_scatter(x=p["t"], y=p["dup"] * 100, mode="lines+markers",
                    line=dict(color=c["series"][0], width=2),
                    marker=dict(color=c["series"][0], size=8,
                                line=dict(color=c["surface"], width=2)),
                    hovertemplate="%{x|" + grain_fmt(grain) + "}<br>%{y:.1f}% duplicate"
                                  "<br>%{customdata:,} containers<extra></extra>",
                    customdata=p["n"])
    last = p.iloc[-1]
    fig.add_scatter(x=[last["t"]], y=[last["dup"] * 100], mode="text",
                    text=[f"{last['dup'] * 100:.0f}%"], textposition="top center",
                    showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, showlegend=False, hovermode="x unified",
        margin=dict(l=8, r=24, t=52, b=8),
        yaxis=dict(title="duplicate share (%)", range=[0, 100], ticksuffix="%")))
    return fig


@app.callback(Output("g-heat", "figure"), *FILTERS)
def fig_heat(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    rules = d[D.RULE_COL].value_counts().head(12).index
    tenants = d["tenant_name"].value_counts().head(12).index
    m = (d[d[D.RULE_COL].isin(rules) & d["tenant_name"].isin(tenants)]
         .pivot_table(index=D.RULE_COL, columns="tenant_name",
                      values="container_id", aggfunc="size", fill_value=0)
         .reindex(index=rules[::-1], columns=tenants, fill_value=0))
    fig = go.Figure(go.Heatmap(
        z=m.values, x=list(m.columns),
        y=[r if len(r) <= 44 else r[:42] + "…" for r in m.index],
        customdata=[[r] * len(m.columns) for r in m.index],
        colorscale=T.sequential_scale(theme), xgap=2, ygap=2,
        colorbar=dict(title=dict(text="containers", font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="%{customdata}<br>%{x}<br>%{z:,} containers<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(showgrid=False, tickangle=-35, tickfont=dict(size=11, color=c["ink2"])),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


# ---------------------------------------------------------- customer tab ----

def customer_slice(start, end, inst, tenant, rule_pick):
    """Verdicts inside the global customer and rule selection."""
    return verdicts(D.apply_filters(DF, start, end, inst,
                                    chosen(tenant), chosen(rule_pick)))


def pairs_by_rule(d):
    """Consecutive firings of the same rule for the same customer, each row
    carrying the verdict the previous firing got."""
    d = d.sort_values("t")
    d = d.assign(prev=d.groupby(["tenant_name", D.RULE_COL])["Final_Disposition"].shift())
    return d[d["prev"].notna()]


CUST_INPUTS = [Input("f-tenant", "value"), Input("f-rule", "value"),
               Input("f-date", "start_date"), Input("f-date", "end_date"),
               Input("f-instance", "value"), Input("theme-store", "data")]


MIX_N = 7                 # coloured rules; everything past this folds into Other


@app.callback(Output("g-c-rulemix", "figure"),
              Input("c-mix-mode", "value"), Input("f-date", "start_date"),
              Input("f-date", "end_date"), Input("f-instance", "value"),
              Input("f-rule", "value"), Input("theme-store", "data"))
def fig_customer_rulemix(mode, start, end, inst, rule, theme):
    d = D.apply_filters(DF, start, end, inst, None, rule)   # every customer, all rows
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    top = d[D.RULE_COL].value_counts().head(MIX_N).index.tolist()
    d = d.assign(mix=np.where(d[D.RULE_COL].isin(top), d[D.RULE_COL], "Other rules"))
    order = top + ["Other rules"]                  # fixed order -> fixed hue
    m = (d.pivot_table(index="tenant_name", columns="mix", values="container_id",
                       aggfunc="size", fill_value=0)
         .reindex(columns=order, fill_value=0))
    m["total"] = m[order].sum(axis=1)
    m = m.sort_values("total", ascending=False)[::-1]       # biggest at the top
    share = m[order].div(m["total"], axis=0) * 100
    hues = dict(zip(top, c["series"][:MIX_N]))
    hues["Other rules"] = c["neutral"]
    ink = label_ink(theme, c)
    fig = go.Figure()
    for rule_name in order:
        if m[rule_name].sum() == 0:
            continue
        x = share[rule_name] if mode == "share" else m[rule_name]
        label = rule_name if len(rule_name) <= 42 else rule_name[:40] + "…"
        fig.add_bar(y=list(m.index), x=x, name=label, orientation="h",
                    marker_color=hues[rule_name],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(share[rule_name]),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m[rule_name], share[rule_name],
                                         m["total"]], axis=-1),
                    hovertemplate="%{y}<br>" + label
                                  + "<br>%{customdata[0]:,} of %{customdata[2]:,}"
                                    " containers (%{customdata[1]:.0f}%)<extra></extra>")
    axis = (dict(title="share of the customer's containers", range=[0, 104],
                 tickvals=[0, 25, 50, 75, 100],
                 ticktext=["0%", "25%", "50%", "75%", "100%"])
            if mode == "share" else dict(title="containers"))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.22, legend=dict(traceorder="normal"),
        margin=dict(l=8, r=24, t=52, b=8),
        uniformtext=dict(mode="hide", minsize=8),
        xaxis=axis,
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("c-summary", "children"), *CUST_INPUTS)
def customer_summary(tenant, rule_pick, start, end, inst, theme):
    d = customer_slice(start, end, inst, tenant, rule_pick)
    if d.empty:
        return [stat_tile("Verdicts", "0", "nothing closed with a disposition")]
    p = pairs_by_rule(d)
    tp = d["Final_Disposition"].eq("True Positive")
    scope = scope_name(rule_pick, "all rules", "rules")
    base = DF if not tenant else DF[DF["tenant_name"].isin(tenant)]
    fires = base[D.RULE_COL].nunique()
    return [
        stat_tile("Verdicts", f"{len(d):,}",
                  f"{scope_name(tenant, 'all customers', 'customers')}, {scope}"),
        stat_tile("True positive", f"{tp.mean() * 100:.1f}%", f"{int(tp.sum()):,} containers"),
        stat_tile("Rules with a verdict", f"{d[D.RULE_COL].nunique():,}",
                  f"of {fires:,} this selection fires"),
        stat_tile("Repeat pairs", f"{len(p):,}",
                  "firings with a previous verdict to learn from"),
    ]


@app.callback(Output("g-c-rules", "figure"), *CUST_INPUTS)
def fig_customer_rules(tenant, rule_pick, start, end, inst, theme):
    d = customer_slice(start, end, inst, tenant, rule_pick)
    if d.empty:
        return empty_fig(theme,
                         f"No verdict for {scope_name(tenant, 'any customer', 'customers')} here")
    c, cols = T.colors(theme), T.final_colors(theme)
    m = verdict_by_rule(d).head(20)[::-1]
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in m.index]
    ink = label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        fig.add_bar(y=labels, x=m[f"{verdict} %"], name=verdict, orientation="h",
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(m[f"{verdict} %"]),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m.index, m[verdict], m["containers"]], axis=-1),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]:,} " + verdict
                                  + " of %{customdata[2]:,} (%{x:.0f}%)<extra></extra>")
    fig.add_scatter(y=labels, x=[101] * len(m), mode="text",
                    text=[f"{v:.0f}%" if v < FITS else ""
                          for v in m["True Positive %"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.24, legend=dict(traceorder="normal"),
        uniformtext=dict(mode="hide", minsize=8),
        margin=dict(l=8, r=52, t=52, b=8),
        xaxis=dict(title="share of the rule's verdicts", range=[0, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-c-timeline", "figure"), *CUST_INPUTS)
def fig_customer_timeline(tenant, rule_pick, start, end, inst, theme):
    d = customer_slice(start, end, inst, tenant, rule_pick).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme,
                         f"No verdict for {scope_name(tenant, 'any customer', 'customers')} here")
    c, cols = T.colors(theme), T.final_colors(theme)
    order = d[D.RULE_COL].value_counts().head(20).index          # busiest at the top
    d = d[d[D.RULE_COL].isin(order)]
    lane = {r: i for i, r in enumerate(order[::-1])}
    d = d.assign(lane=d[D.RULE_COL].map(lane))       # dead straight: no jitter
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in order[::-1]]
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:                     # fixed order -> fixed hue
        s = d[d["Final_Disposition"] == verdict]
        if s.empty:
            continue
        fig.add_scatter(
            x=s["t"], y=s["lane"], mode="markers", name=verdict,
            marker=dict(color=cols[verdict], size=8, opacity=0.8,
                        line=dict(color=c["surface"], width=1)),
            customdata=np.stack([s[D.RULE_COL],
                                 s["container_name"].str.slice(0, 70),
                                 s["container_id"]], axis=-1),
            hovertemplate="%{x|%b %-d, %H:%M}<br><b>" + verdict + "</b>"
                          "<br>%{customdata[0]}<br>%{customdata[1]}"
                          "<br>container %{customdata[2]}<extra></extra>")
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        dragmode="zoom",                 # box-zoom on the time axis
        margin=dict(l=8, r=24, t=52, b=8),
        xaxis=dict(title="", showgrid=True, fixedrange=False),
        yaxis=dict(showgrid=False, tickmode="array",
                   tickvals=list(range(len(order))), ticktext=labels,
                   range=[-0.6, len(order) - 0.4], fixedrange=True,
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-c-transition", "figure"), *CUST_INPUTS)
def fig_customer_transition(tenant, rule_pick, start, end, inst, theme):
    p = pairs_by_rule(customer_slice(start, end, inst, tenant, rule_pick))
    if p.empty:
        return empty_fig(theme, "Nothing fired twice for "
                         f"{scope_name(tenant, 'any customer', 'customers')} here")
    c = T.colors(theme)
    n = pd.crosstab(p["prev"], p["Final_Disposition"]).reindex(
        index=T.FINAL_ORDER, columns=T.FINAL_ORDER, fill_value=0)
    pct = (n.div(n.sum(axis=1).replace(0, np.nan), axis=0) * 100).fillna(0)
    fig = go.Figure(go.Heatmap(
        z=pct.values, x=list(pct.columns), y=list(pct.index),
        customdata=n.values, colorscale=T.sequential_scale(theme),
        zmin=0, zmax=100, xgap=2, ygap=2,
        text=[[f"{v:.0f}%" for v in row] for row in pct.values],
        texttemplate="%{text}", textfont=dict(size=13),
        colorbar=dict(title=dict(text="% of row", font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="was %{y}<br>now %{x}<br>%{customdata:,} firings"
                      " (%{z:.1f}% of row)<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(title="this firing", showgrid=False,
                   tickfont=dict(size=11, color=c["ink2"])),
        yaxis=dict(title="previous firing", showgrid=False, autorange="reversed",
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-c-lift", "figure"),
              Input("c-outcome", "value"), Input("c-minpairs", "value"), *CUST_INPUTS)
def fig_customer_lift(outcome, min_pairs, tenant, rule_pick, start, end, inst, theme):
    p = pairs_by_rule(customer_slice(start, end, inst, tenant, rule_pick))
    if p.empty:
        return empty_fig(theme, "Nothing fired twice for "
                         f"{scope_name(tenant, 'any customer', 'customers')} here")
    c, cols = T.colors(theme), T.final_colors(theme)
    outcome = outcome or "True Positive"
    min_pairs = min_pairs or 3
    p = p.assign(hit=p["Final_Disposition"].eq(outcome))
    n = (p.pivot_table(index=D.RULE_COL, columns="prev", values="hit", aggfunc="size")
         .reindex(columns=T.FINAL_ORDER).fillna(0))
    rate = (p.pivot_table(index=D.RULE_COL, columns="prev", values="hit", aggfunc="mean")
            .reindex(columns=T.FINAL_ORDER) * 100)
    rate = rate.where(n >= min_pairs)                 # too few pairs -> no dot
    keep = rate.notna().sum(axis=1) >= 2              # a row needs two to compare
    if not keep.any():
        return empty_fig(
            theme, f"No rule has {min_pairs}+ pairs after two different verdicts here")
    rate, n = rate[keep], n[keep]
    spread = rate.max(axis=1) - rate.min(axis=1)
    order = spread.sort_values(ascending=False).head(12).index[::-1]
    rate, n, spread = rate.loc[order], n.loc[order], spread.loc[order]
    labels = [r if len(r) <= 44 else r[:42] + "…" for r in order]
    fig = go.Figure()
    # connector spans the drawn dots, so each rule reads as one row
    for y, lo, hi in zip(labels, rate.min(axis=1), rate.max(axis=1)):
        fig.add_shape(type="line", y0=y, y1=y, x0=lo, x1=hi, layer="below",
                      line=dict(color=c["grid"], width=2))
    for prev in T.FINAL_ORDER:                        # fixed order -> fixed hue
        if rate[prev].isna().all():
            continue
        fig.add_scatter(
            x=rate[prev], y=labels, mode="markers",
            name=f"after {prev.lower()}",
            marker=dict(color=cols[prev], size=11,
                        line=dict(color=c["surface"], width=2)),
            customdata=np.stack([order, n[prev]], axis=-1),
            hovertemplate="%{customdata[0]}<br>%{x:.0f}% " + outcome.lower()
                          + " after " + prev.lower()
                          + "<br>%{customdata[1]:,} pairs<extra></extra>")
    # the spread is what the chart is about, so it is the one printed number
    fig.add_scatter(x=[104] * len(order), y=labels, mode="text",
                    text=[f"{v:.0f} pts" for v in spread],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        margin=dict(l=8, r=64, t=52, b=8),
        xaxis=dict(title=f"chance this firing is {outcome.lower()}", range=[-2, 118],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


# ----------------------------------------------------------- signals tab ----

def signal_slice(start, end, inst, rule, tenant_pick, only_verdicts=False):
    """Every container in the global selection - volume, not verdicts, unless the
    caller needs labels."""
    d = D.apply_filters(DF, start, end, inst, chosen(tenant_pick), chosen(rule))
    return verdicts(d) if only_verdicts else d


def daily(d, start, end):
    """Daily counts on a continuous calendar - gaps are zeros, not missing days."""
    s = d.dropna(subset=["t"]).set_index("t").resample("D").size()
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"),
                        freq="D")
    return s.reindex(idx, fill_value=0)


SIG_INPUTS = [Input("f-rule", "value"), Input("f-tenant", "value"),
              Input("f-date", "start_date"), Input("f-date", "end_date"),
              Input("f-instance", "value"), Input("theme-store", "data")]


@app.callback(Output("g-s-tape", "figure"), *SIG_INPUTS)
def fig_signal_tape(rule, tenant_pick, start, end, inst, theme):
    d = signal_slice(start, end, inst, rule, tenant_pick)
    if d.empty:
        return empty_fig(theme, "Nothing fired in this slice")
    c = T.colors(theme)
    v = daily(d, start, end)
    ma_f, ma_s = v.rolling(7).mean(), v.rolling(28).mean()
    mid = v.rolling(20).mean()
    sd = v.rolling(20).std()
    upper, lower = mid + 2 * sd, (mid - 2 * sd).clip(lower=0)
    fig = go.Figure()
    # the band is context, so it sits behind everything in a wash of the slow hue
    fig.add_scatter(x=upper.index, y=upper, mode="lines", line=dict(width=0),
                    hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=lower.index, y=lower, mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor=_alpha(c["series"][0], 0.12),
                    name="20-day ±2σ band", hoverinfo="skip")
    # raw volume recedes: it is the thing being smoothed, not a series to compare
    fig.add_bar(x=v.index, y=v, name="containers",
                marker_color=_alpha(c["ink2"], 0.35),
                marker_line=dict(width=0),
                hovertemplate="%{x|%b %-d}<br>%{y:,} containers<extra></extra>")
    fig.add_scatter(x=ma_f.index, y=ma_f, mode="lines", name="7-day average",
                    line=dict(color=c["series"][0], width=2),
                    hovertemplate="%{x|%b %-d}<br>7-day %{y:.1f}<extra></extra>")
    fig.add_scatter(x=ma_s.index, y=ma_s, mode="lines", name="28-day average",
                    line=dict(color=c["series"][1], width=2),
                    hovertemplate="%{x|%b %-d}<br>28-day %{y:.1f}<extra></extra>")
    # crossovers in neutral ink: they are events, not another series
    above = (ma_f > ma_s)
    cross = above.ne(above.shift()) & ma_f.notna() & ma_s.notna()
    cross.iloc[:28] = False
    if cross.any():
        cx = ma_f[cross]
        fig.add_scatter(x=cx.index, y=cx, mode="markers", name="crossover",
                        marker=dict(symbol="diamond", size=9, color=c["ink2"],
                                    line=dict(color=c["surface"], width=1)),
                        customdata=["up" if u else "down" for u in above[cross]],
                        hovertemplate="%{x|%b %-d}<br>7-day crossed %{customdata}"
                                      " through 28-day<extra></extra>")
    fig.update_layout(**T.layout(
        theme, hovermode="x unified", bargap=0.2,
        yaxis=dict(title="containers per day", rangemode="tozero")))
    return fig


@app.callback(Output("g-s-portfolio", "figure"),
              Input("s-minverdicts", "value"), Input("f-tenant", "value"),
              Input("f-date", "start_date"), Input("f-date", "end_date"),
              Input("f-instance", "value"), Input("theme-store", "data"))
def fig_signal_portfolio(min_n, tenant_pick, start, end, inst, theme):
    d = verdicts(D.apply_filters(DF, start, end, inst, chosen(tenant_pick), None))
    if d.empty:
        return empty_fig(theme, "No verdicts in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    g = d.groupby(D.RULE_COL)["Final_Disposition"].agg(
        cost="size", tp=lambda s: (s == "True Positive").sum())
    g = g[g["cost"] >= (min_n or 20)]
    if g.empty:
        return empty_fig(theme, f"No rule reached {min_n} verdicts in this slice")
    g["yield_"] = g["tp"] / g["cost"] * 100
    platform = (d["Final_Disposition"] == "True Positive").mean() * 100
    # the staircase: best yield available at this scale or larger
    front = g.sort_values("cost", ascending=False)
    front = front.assign(best=front["yield_"].cummax())
    size = 9 + 17 * np.sqrt(g["tp"]) / max(np.sqrt(g["tp"]).max(), 1)
    fig = go.Figure()
    fig.add_scatter(x=front["cost"], y=front["best"], mode="lines",
                    line=dict(color=c["axis"], width=2, shape="hv"),
                    name="best yield at this scale", hoverinfo="skip")
    fig.add_scatter(
        x=g["cost"], y=g["yield_"], mode="markers", name="rule",
        marker=dict(color=cols["True Positive"], size=size, opacity=0.85,
                    line=dict(color=c["surface"], width=2)),
        customdata=np.stack([g.index, g["tp"]], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b><br>%{y:.0f}% true positive"
                      "<br>%{customdata[1]:,} true positives from %{x:,} verdicts"
                      "<extra></extra>")
    # name the positions worth acting on: best yield, and the biggest sink
    mark = pd.concat([g.nlargest(2, "yield_"), g.nlargest(1, "cost"),
                      g[g["yield_"] < 1].nlargest(1, "cost")]).drop_duplicates()
    fig.add_scatter(
        x=mark["cost"], y=mark["yield_"], mode="text",
        text=[r if len(r) <= 34 else r[:32] + "…" for r in mark.index],
        textposition=["top left" if x > g["cost"].max() / 8 else "top center"
                      for x in mark["cost"]],
        showlegend=False, hoverinfo="skip",
        textfont=dict(color=c["ink2"], size=10))
    fig.add_hline(y=platform, line=dict(color=c["axis"], width=1),
                  annotation_text=f"platform average {platform:.0f}%",
                  annotation_position="top left",
                  annotation_font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", showlegend=False,
        margin=dict(l=8, r=40, t=60, b=8),
        xaxis=dict(title="cost - containers an analyst dispositioned (log scale)",
                   type="log", dtick=1, minor=dict(ticks="", showgrid=False)),
        yaxis=dict(title="yield - true-positive share", range=[-4, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"])))
    return fig


@app.callback(Output("s-breadth-title", "children"),
              Output("s-breadth-note", "children"),
              Input("f-tenant", "value"))
def breadth_copy(tenant):
    """One customer selected leaves nothing to count across customers, so the
    panel counts that customer's rules instead - and says whose they are."""
    if not only_one(tenant):
        return ("Busy day for everyone, or just one customer?",
                "Each day we check every customer against their own normal week. "
                "The bar counts how many were busier than usual, minus how many "
                "were quieter. A tall blue bar means almost everyone was busy at "
                "once - a campaign or a broken feed - while a short bar means "
                "whatever happened was one customer's own.")
    tenant = only_one(tenant)
    return (f"Busy day across {tenant}, or just one rule?",
            f"{tenant} is the only customer selected, so the bar counts their "
            f"rules instead. Each day we check every rule that fires for {tenant} "
            f"against its own normal week, then take how many were busier than "
            f"usual minus how many were quieter. A tall blue bar means most of "
            f"{tenant}'s detections lit up together - a campaign or a broken feed "
            f"- while a short bar means it was one rule's own day.")


@app.callback(Output("g-s-breadth", "figure"),
              Input("f-rule", "value"), Input("f-tenant", "value"),
              Input("f-date", "start_date"),
              Input("f-date", "end_date"), Input("f-instance", "value"),
              Input("theme-store", "data"))
def fig_signal_breadth(rule, tenant, start, end, inst, theme):
    d = D.apply_filters(DF, start, end, inst, chosen(tenant),
                        chosen(rule)).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme)
    one_tenant = bool(only_one(tenant))
    unit = D.RULE_COL if one_tenant else "tenant_name"
    noun = "rules" if one_tenant else "customers"
    c = T.colors(theme)
    m = (d.assign(day=d["t"].dt.floor("D"))
         .pivot_table(index="day", columns=unit, values="container_id",
                      aggfunc="size", fill_value=0))
    if m.shape[1] < 2:
        return empty_fig(theme, f"Breadth needs at least two {noun} in the slice")
    idx = pd.date_range(m.index.min(), m.index.max(), freq="D", tz="UTC")
    m = m.reindex(idx, fill_value=0)
    base = m.rolling(7, min_periods=4).median().shift(1)     # each customer vs itself
    adv = ((m > base) & base.notna()).sum(axis=1)
    dec = ((m < base) & base.notna()).sum(axis=1)
    net = (adv - dec).iloc[7:]
    adv, dec = adv.iloc[7:], dec.iloc[7:]
    # diverging pair: one hue per direction, nothing at the zero line
    up, down = c["series"][0], (T.PALETTES["dark" if theme == "dark" else "light"]
                                ["series"][7])
    fig = go.Figure()
    fig.add_bar(x=net.index, y=net,
                marker_color=[up if v >= 0 else down for v in net],
                marker_line=dict(width=0),
                customdata=np.stack([adv, dec], axis=-1),
                hovertemplate="%{x|%b %-d}<br>net %{y:+d}"
                              f"<br>%{{customdata[0]}} {noun} busier than usual, "
                              f"%{{customdata[1]}} quieter<extra></extra>")
    fig.add_hline(y=0, line=dict(color=c["axis"], width=1))
    fig.update_layout(**T.layout(
        theme, showlegend=False, hovermode="x unified", bargap=0.15,
        yaxis=dict(title=f"{noun} busier than usual − {noun} quieter")))
    return fig


@app.callback(Output("g-s-corr", "figure"),
              Input("f-tenant", "value"), Input("f-date", "start_date"),
              Input("f-date", "end_date"), Input("f-instance", "value"),
              Input("theme-store", "data"))
def fig_signal_corr(tenant_pick, start, end, inst, theme):
    d = D.apply_filters(DF, start, end, inst, chosen(tenant_pick),
                        None).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    m = (d.assign(day=d["t"].dt.floor("D"))
         .pivot_table(index="day", columns=D.RULE_COL, values="container_id",
                      aggfunc="size", fill_value=0))
    keep = m.sum().sort_values(ascending=False).head(12).index
    m = m[keep]
    m = m.loc[:, m.std() > 0]
    if m.shape[1] < 2:
        return empty_fig(theme, "Not enough rules fire daily here to correlate")
    corr = m.corr()
    labels = [r if len(r) <= 34 else r[:32] + "…" for r in corr.columns]
    z = corr.values.copy()
    np.fill_diagonal(z, np.nan)               # a rule correlating with itself says nothing
    fig = go.Figure(go.Heatmap(
        z=z, x=labels, y=labels, colorscale=T.diverging_scale(theme),
        zmid=0, zmin=-1, zmax=1, xgap=2, ygap=2,
        colorbar=dict(title=dict(text="correlation",
                                 font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="%{y}<br>%{x}<br>r = %{z:.2f}<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(showgrid=False, tickangle=-35,
                   tickfont=dict(size=10, color=c["ink2"])),
        yaxis=dict(showgrid=False, autorange="reversed",
                   tickfont=dict(size=10, color=c["ink2"]))))
    return fig


@app.callback(Output("g-s-equity", "figure"), *SIG_INPUTS)
def fig_signal_equity(rule, tenant_pick, start, end, inst, theme):
    d = signal_slice(start, end, inst, rule, tenant_pick, only_verdicts=True)
    d = d.dropna(subset=["t"]).sort_values("t")
    if d.empty:
        return empty_fig(theme, "Nothing reached a verdict in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    step = d["Final_Disposition"].map({"True Positive": 1, "Benign Positive": -1,
                                       "False Positive": -1}).fillna(0)
    eq = step.cumsum()
    eq.index = d["t"].values
    eq = eq.resample("D").last().ffill()
    peak = eq.cummax()
    dd = eq - peak
    worst = dd.min()
    fig = go.Figure()
    # drawdown first, as a wash between the curve and its own high-water mark
    fig.add_scatter(x=peak.index, y=peak, mode="lines", line=dict(width=0),
                    hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=eq.index, y=eq, mode="lines", line=dict(width=0),
                    fill="tonexty", fillcolor=_alpha(c["muted"], 0.25),
                    name="drawdown", hoverinfo="skip")
    fig.add_scatter(x=eq.index, y=eq, mode="lines", name="true minus benign",
                    line=dict(color=cols["True Positive"], width=2),
                    customdata=dd.values,
                    hovertemplate="%{x|%b %-d}<br>running %{y:+,.0f}"
                                  "<br>%{customdata:+,.0f} from its high"
                                  "<extra></extra>")
    fig.add_hline(y=0, line=dict(color=c["axis"], width=1))
    fig.add_annotation(x=dd.idxmin(), y=eq.loc[dd.idxmin()],
                       text=f"worst drawdown {worst:,.0f}", showarrow=True,
                       arrowhead=0, arrowwidth=1, arrowcolor=c["axis"], ay=28, ax=0,
                       font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="x unified", showlegend=False,
        yaxis=dict(title="cumulative true positives − benign positives")))
    return fig


# ------------------------------------------------- signal indicators ----
# Everything below scores a reading the same way: how far it moves the chance of
# a true positive AGAINST THE SAME RULE'S OWN AVERAGE. Without that control an
# indicator scores high just by correlating with which rule fired.

SESSION_ORDER = ["business hours", "off hours", "weekend"]
STREAK_ORDER = ["none", "1-2", "3-5", "6-20", "21+"]
Z_ORDER = ["below -1σ", "-1 to -0.5σ", "its normal", "+0.5 to +1σ",
           "+1 to +2σ", "above +2σ"]
BREADTH_ORDER = ["broadly quiet", "mildly quiet", "mildly busy", "busy",
                 "very broad"]
SCORE_ORDER = ["far below its high", "well below", "a little below",
               "near its high", "at its high"]
SEEN_ORDER = ["first ever", "2nd-5th", "6th-20th", "21st-100th", "101st+"]
REGIME_ORDER = ["7-day above 28-day", "7-day below 28-day"]
HOUR_ORDER = [f"{h:02d}" for h in range(24)]

# one row of the scorecard: what to call it, which column, the reading order
INDICATORS = [
    ("What it was ruled last time", "prev", T.FINAL_ORDER),
    ("Benign positives in a row before it", "streak_b", STREAK_ORDER),
    ("When it landed", "session", SESSION_ORDER),
    ("How broad the day was", "breadth_b", BREADTH_ORDER),
    ("Rule's volume against its own band", "z_b", Z_ORDER),
    ("Fast average against slow", "regime_b", REGIME_ORDER),
    ("Rule's running score", "score_b", SCORE_ORDER),
    ("Times this customer saw the rule", "seen_b", SEEN_ORDER),
]


def _benign_run(s):
    """How many benign positives landed immediately before each row."""
    prior = s.shift(fill_value=False).astype(int)
    return prior.groupby((prior == 0).cumsum()).cumsum()


def _melt_day_rule(m, name):
    out = m.stack(future_stack=True).rename(name).reset_index()
    out.columns = ["day", D.RULE_COL, name]
    return out


@lru_cache(maxsize=16)
def _indicator_frame(start, end, inst, rule, tenant):
    """Every verdict in the slice, tagged with the readings that were knowable
    before an analyst opened it. Cached, so callers treat it as read-only."""
    d = D.apply_filters(DF, start, end, list(inst) or None, list(tenant) or None,
                        list(rule) or None).dropna(subset=["t"])
    v = verdicts(d).sort_values("t").copy()
    if v.empty:
        return v
    v["day"] = v["t"].dt.floor("D")
    v["tp"] = v["Final_Disposition"].eq("True Positive")
    v["bp"] = v["Final_Disposition"].eq("Benign Positive")
    v["lift"] = v["tp"].astype(float) - v.groupby(D.RULE_COL)["tp"].transform("mean")

    pair = ["tenant_name", D.RULE_COL]                  # same customer, same rule
    v["prev"] = v.groupby(pair)["Final_Disposition"].shift()
    v["streak_b"] = pd.cut(v.groupby(pair)["bp"].transform(_benign_run),
                           [-1, 0, 2, 5, 20, 1e9], labels=STREAK_ORDER)
    v["seen_b"] = pd.cut(v.groupby(pair).cumcount(), [-1, 0, 4, 19, 99, 1e9],
                         labels=SEEN_ORDER)

    h, wd = v["t"].dt.hour, v["t"].dt.dayofweek
    v["session"] = np.where(wd >= 5, "weekend",
                            np.where((h >= 13) & (h < 23), "business hours",
                                     "off hours"))
    v["hour_b"] = h.map(lambda x: f"{x:02d}")

    # the tape is built on every container, not just the ones that got a verdict
    day = d.assign(day=d["t"].dt.floor("D"))
    cal = pd.date_range(day["day"].min(), day["day"].max(), freq="D", tz="UTC")
    tape = day.pivot_table(index="day", columns=D.RULE_COL, values="container_id",
                           aggfunc="size", fill_value=0).reindex(cal, fill_value=0)
    z = ((tape - tape.rolling(20, min_periods=10).mean())
         / tape.rolling(20, min_periods=10).std().replace(0, np.nan))
    fast_high = (tape.rolling(7, min_periods=4).mean()
                 > tape.rolling(28, min_periods=10).mean())
    v = v.merge(_melt_day_rule(z, "z"), on=["day", D.RULE_COL], how="left")
    v = v.merge(_melt_day_rule(fast_high, "fast_high"), on=["day", D.RULE_COL],
                how="left")
    v["z_b"] = pd.cut(v["z"], [-1e9, -1, -0.5, 0.5, 1, 2, 1e9], labels=Z_ORDER)
    v["regime_b"] = v["fast_high"].map({True: REGIME_ORDER[0],
                                        False: REGIME_ORDER[1]})

    # breadth: customers busier than their own normal week, minus those quieter
    book = day.pivot_table(index="day", columns="tenant_name", values="container_id",
                           aggfunc="size", fill_value=0).reindex(cal, fill_value=0)
    norm = book.rolling(7, min_periods=4).median().shift(1)
    net = (((book > norm) & norm.notna()).sum(axis=1)
           - ((book < norm) & norm.notna()).sum(axis=1))
    v = v.merge(net.rename("breadth").reset_index().rename(columns={"index": "day"}),
                on="day", how="left")
    v["breadth_b"] = pd.cut(v["breadth"], [-1e9, -5, 0, 5, 10, 1e9],
                            labels=BREADTH_ORDER)

    # the rule's running true-minus-benign, and how far below its own high it sat
    step = v["tp"].astype(int) - v["bp"].astype(int)
    run = step.groupby(v[D.RULE_COL]).cumsum() - step      # the score before this one
    v["score_b"] = pd.cut(run - run.groupby(v[D.RULE_COL]).cummax(),
                          [-1e9, -200, -50, -10, -1e-9, 1e9], labels=SCORE_ORDER)
    return v


def indicator_frame(start, end, inst, rule, tenant):
    return _indicator_frame(start, end, tuple(inst or ()), tuple(rule or ()),
                            tuple(tenant or ()))


def mix_bars(v, col, order, theme, xtitle):
    """Verdict mix as 100% stacked bars across one indicator's readings."""
    c, cols = T.colors(theme), T.final_colors(theme)
    g = v.dropna(subset=[col])
    if g.empty:
        return empty_fig(theme, "No verdicts in this slice")
    n = pd.crosstab(g[col], g["Final_Disposition"])
    n = n.reindex(index=[o for o in order if o in n.index],
                  columns=T.FINAL_ORDER, fill_value=0)
    n = n[n.sum(axis=1) > 0]
    if n.empty:
        return empty_fig(theme, "No verdicts in this slice")
    share = n.div(n.sum(axis=1), axis=0) * 100
    x, ink = [str(i) for i in n.index], label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:                  # fixed order -> fixed hue
        fig.add_bar(x=x, y=share[verdict], name=verdict, marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(share[verdict]), textposition="inside",
                    insidetextanchor="middle", textangle=0, cliponaxis=False,
                    textfont=dict(size=10, color=ink),
                    customdata=np.stack([n[verdict], n.sum(axis=1)], axis=-1),
                    hovertemplate="%{x}<br>%{customdata[0]:,} " + verdict
                                  + " of %{customdata[1]:,} (%{y:.0f}%)<extra></extra>")
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.26, legend=dict(traceorder="normal"),
        uniformtext=dict(mode="hide", minsize=8),
        xaxis=dict(title=xtitle, type="category",
                   tickfont=dict(size=11, color=c["ink2"])),
        yaxis=dict(title="share of verdicts", range=[0, 100],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"])))
    return fig


@app.callback(Output("g-s-scorecard", "figure"),
              Input("s-minreading", "value"), *SIG_INPUTS)
def fig_signal_scorecard(min_n, rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    c = T.colors(theme)
    min_n = min_n or 50
    rows = []
    for label, col, order in INDICATORS:
        g = v.dropna(subset=[col]).groupby(col, observed=True)
        stat = pd.DataFrame({"n": g.size(), "lift": g["lift"].mean() * 100,
                             "tp": g["tp"].mean() * 100})
        stat = stat.reindex([o for o in order if o in stat.index]).dropna()
        stat = stat[stat["n"] >= min_n]
        if len(stat) >= 2:                      # one reading compares with nothing
            rows.append((label, stat))
    if not rows:
        return empty_fig(theme, f"No reading has {min_n}+ verdicts in this slice")
    rows.sort(key=lambda r: r[1]["lift"].max() - r[1]["lift"].min())
    labels = [r[0] for r in rows]
    lifts = pd.concat([r[1]["lift"] for r in rows])
    lo, hi = min(lifts.min(), -1), max(lifts.max(), 1)
    pad = (hi - lo) * 0.10
    fig = go.Figure()
    for label, stat in rows:                    # connector: the row reads as one
        fig.add_shape(type="line", y0=label, y1=label, layer="below",
                      x0=stat["lift"].min(), x1=stat["lift"].max(),
                      line=dict(color=c["grid"], width=2))
    # two hues, one per direction, meeting at a zero line that is not a hue
    for name, keep, colour in [("raises the odds", lambda t: t >= 0, c["series"][0]),
                               ("lowers the odds", lambda t: t < 0, c["series"][7])]:
        xs, ys, cd = [], [], []
        for label, stat in rows:
            for reading, r in stat.iterrows():
                if keep(r["lift"]):
                    xs.append(r["lift"]); ys.append(label)
                    cd.append([str(reading), r["n"], r["tp"]])
        if not xs:
            continue
        fig.add_scatter(x=xs, y=ys, mode="markers", name=name,
                        marker=dict(color=colour, size=11,
                                    line=dict(color=c["surface"], width=2)),
                        customdata=np.array(cd, dtype=object),
                        hovertemplate="<b>%{customdata[0]}</b><br>%{x:+.1f} points"
                                      " against the rule's own rate"
                                      "<br>%{customdata[2]:.1f}% true positive"
                                      " over %{customdata[1]:,} verdicts<extra></extra>")
    fig.add_vline(x=0, line=dict(color=c["axis"], width=1))
    fig.add_scatter(x=[hi + pad] * len(rows), y=labels, mode="text",
                    text=[f"{r[1]['lift'].max() - r[1]['lift'].min():.0f} pts"
                          for r in rows],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        margin=dict(l=8, r=56, t=52, b=8),
        xaxis=dict(title="points added to the rule's own true-positive rate",
                   range=[lo - pad, hi + pad * 4.5], zeroline=False),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-s-streak", "figure"), *SIG_INPUTS)
def fig_signal_streak(rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    return mix_bars(v, "streak_b", STREAK_ORDER, theme,
                    "benign positives in a row before this firing")


@app.callback(Output("g-s-hour", "figure"), *SIG_INPUTS)
def fig_signal_hour(rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    return mix_bars(v, "hour_b", HOUR_ORDER, theme, "hour of the day (UTC)")


@app.callback(Output("g-s-breadthmix", "figure"), *SIG_INPUTS)
def fig_signal_breadthmix(rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    return mix_bars(v, "breadth_b", BREADTH_ORDER, theme, "how broad the day was")


MOM_WINDOW = 14           # days of evidence behind every point on the line
MOM_MIN = 20              # verdicts in the window before a point is drawn


@app.callback(Output("g-s-momentum", "figure"), *SIG_INPUTS)
def fig_signal_momentum(rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    d = v.dropna(subset=["prev"])
    if d.empty:
        return empty_fig(theme, "Nothing fired twice in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    cal = pd.date_range(v["day"].min(), v["day"].max(), freq="D", tz="UTC")

    def roll(frame, by=None):
        tp = (frame.pivot_table(index="day", columns=by, values="tp", aggfunc="sum")
              if by else frame.groupby("day")["tp"].sum())
        n = (frame.pivot_table(index="day", columns=by, values="tp", aggfunc="size")
             if by else frame.groupby("day")["tp"].size())
        tp, n = tp.reindex(cal).fillna(0), n.reindex(cal).fillna(0)
        w = dict(window=MOM_WINDOW, min_periods=MOM_WINDOW)
        tp, n = tp.rolling(**w).sum(), n.rolling(**w).sum()
        return (tp / n.where(n >= MOM_MIN) * 100)

    fig = go.Figure()
    base = roll(v)
    fig.add_scatter(x=base.index, y=base, mode="lines", name="everything together",
                    line=dict(color=c["muted"], width=2, dash="dot"),
                    hovertemplate="%{x|%b %-d}<br>%{y:.1f}% overall<extra></extra>")
    lines = roll(d, by="prev")
    for prev in T.FINAL_ORDER:                    # fixed order -> fixed hue
        if prev not in lines.columns or lines[prev].isna().all():
            continue
        fig.add_scatter(x=lines.index, y=lines[prev], mode="lines",
                        name=f"after {prev.lower()}",
                        line=dict(color=cols[prev], width=2),
                        hovertemplate="%{x|%b %-d}<br>%{y:.1f}% true positive after "
                                      + prev.lower() + "<extra></extra>")
    fig.update_layout(**T.layout(
        theme, hovermode="x unified", legend=dict(traceorder="normal"),
        yaxis=dict(title=f"true positives in the last {MOM_WINDOW} days",
                   rangemode="tozero", ticksuffix="%")))
    return fig



# ------------------------------------------- readings that need no clock ----
# Nothing below reads a date, an hour or a weekday. They use counts, shares and
# the order firings arrived in - none of which moves when the roster changes.


@app.callback(Output("g-s-concentration", "figure"),
              Input("s-minconc", "value"), *SIG_INPUTS)
def fig_signal_concentration(min_n, rule, tenant, start, end, inst, theme):
    d = verdicts(D.apply_filters(DF, start, end, inst, chosen(tenant), chosen(rule)))
    if d.empty:
        return empty_fig(theme, "No verdicts in this slice")
    c = T.colors(theme)
    min_n = min_n or 20
    g = d.groupby(D.RULE_COL)["Final_Disposition"].agg(
        n="size", tp=lambda s: (s == "True Positive").sum())
    g = g[g["n"] >= min_n]
    if len(g) < 2 or g["tp"].sum() == 0:
        return empty_fig(theme, f"Fewer than two rules reach {min_n} verdicts here")
    g = g.assign(y=g["tp"] / g["n"]).sort_values(["y", "n"], ascending=[False, False])
    work = np.r_[0, g["n"].cumsum() / g["n"].sum() * 100]
    finds = np.r_[0, g["tp"].cumsum() / g["tp"].sum() * 100]
    names = np.r_[["", ], g.index.values]
    # the diagonal is "no rule is better than another", so the gap is the edge
    fig = go.Figure()
    fig.add_scatter(x=[0, 100], y=[0, 100], mode="lines", name="if every rule paid the same",
                    line=dict(color=c["axis"], width=1, dash="dash"), hoverinfo="skip")
    fig.add_scatter(x=work, y=work, mode="lines", line=dict(width=0),
                    hoverinfo="skip", showlegend=False)
    fig.add_scatter(x=work, y=finds, mode="lines", name="rules, best yield first",
                    line=dict(color=c["series"][0], width=2.5),
                    fill="tonexty", fillcolor=_alpha(c["series"][0], 0.14),
                    customdata=np.stack([names, np.r_[0, g["n"].cumsum()],
                                         np.r_[0, g["tp"].cumsum()]], axis=-1),
                    hovertemplate="%{x:.0f}% of the workload"
                                  "<br>%{y:.0f}% of the true positives"
                                  "<br>%{customdata[2]:,} found in %{customdata[1]:,}"
                                  " verdicts<br>down to: %{customdata[0]}<extra></extra>")
    # call out where the curve passes the two thresholds people actually ask for
    marked = []
    for target in (50, 80):
        if finds[-1] < target:
            continue
        i = int(np.argmax(finds >= target))
        fig.add_shape(type="line", x0=0, x1=work[i], y0=target, y1=target, layer="below",
                      line=dict(color=c["grid"], width=1, dash="dot"))
        fig.add_shape(type="line", x0=work[i], x1=work[i], y0=0, y1=target, layer="below",
                      line=dict(color=c["grid"], width=1, dash="dot"))
        if any(abs(work[i] - x) < 7 for x in marked):
            continue                 # one step carries both: label it once
        marked.append(work[i])
        fig.add_annotation(x=work[i], y=target, text=f"{work[i]:.0f}% of the work",
                           showarrow=False, xanchor="left", yanchor="top",
                           xshift=6, yshift=-4,
                           font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", margin=dict(l=8, r=24, t=52, b=8),
        xaxis=dict(title="share of the analyst workload", range=[0, 100],
                   ticksuffix="%"),
        yaxis=dict(title="share of all true positives", range=[0, 102],
                   ticksuffix="%")))
    return fig


@app.callback(Output("g-s-rulemom", "figure"),
              Input("s-minmom", "value"), *SIG_INPUTS)
def fig_signal_rule_momentum(min_pairs, rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    p = v.dropna(subset=["prev"])
    if p.empty:
        return empty_fig(theme, "Nothing fired twice in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    min_pairs = min_pairs or 5
    keys = ["True Positive", "Benign Positive"]
    n = (p.pivot_table(index=D.RULE_COL, columns="prev", values="tp", aggfunc="size")
         .reindex(columns=keys).fillna(0))
    rate = (p.pivot_table(index=D.RULE_COL, columns="prev", values="tp", aggfunc="mean")
            .reindex(columns=keys) * 100)
    keep = (n[keys[0]] >= min_pairs) & (n[keys[1]] >= min_pairs)
    if not keep.any():
        return empty_fig(
            theme, f"No rule has {min_pairs}+ pairs after both verdicts here")
    rate, n = rate[keep], n[keep]
    gap = (rate[keys[0]] - rate[keys[1]]).sort_values()
    order = gap.tail(18).index                      # biggest gaps read at the top
    rate, n, gap = rate.loc[order], n.loc[order], gap.loc[order]
    labels = [r if len(r) <= 44 else r[:42] + "…" for r in order]
    fig = go.Figure()
    for y, lo, hi in zip(labels, rate.min(axis=1), rate.max(axis=1)):
        fig.add_shape(type="line", y0=y, y1=y, x0=lo, x1=hi, layer="below",
                      line=dict(color=c["grid"], width=2))
    for prev in keys:                                # fixed order -> fixed hue
        fig.add_scatter(
            x=rate[prev], y=labels, mode="markers", name=f"after {prev.lower()}",
            marker=dict(color=cols[prev], size=11,
                        line=dict(color=c["surface"], width=2)),
            customdata=np.stack([order, n[prev]], axis=-1),
            hovertemplate="%{customdata[0]}<br>%{x:.0f}% true positive after "
                          + prev.lower() + "<br>%{customdata[1]:,} pairs<extra></extra>")
    fig.add_scatter(x=[104] * len(order), y=labels, mode="text",
                    text=[f"{v:+.0f} pts" for v in gap],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        margin=dict(l=8, r=64, t=52, b=8),
        xaxis=dict(title="chance this firing is a true positive", range=[-2, 118],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-s-flip", "figure"),
              Input("s-minflip", "value"), *SIG_INPUTS)
def fig_signal_flip(min_pairs, rule, tenant, start, end, inst, theme):
    v = indicator_frame(start, end, inst, rule, tenant)
    if v.empty:
        return empty_fig(theme, "No verdicts in this slice")
    p = v.dropna(subset=["prev"])
    if p.empty:
        return empty_fig(theme, "Nothing fired twice in this slice")
    c = T.colors(theme)
    min_pairs = min_pairs or 20
    p = p.assign(flip=p["prev"].ne(p["Final_Disposition"]))
    g = p.groupby(D.RULE_COL).agg(pairs=("flip", "size"), flip=("flip", "mean"),
                                  tp=("tp", "mean"))
    g = g[g["pairs"] >= min_pairs]
    if g.empty:
        return empty_fig(theme, f"No rule has {min_pairs}+ pairs in this slice")
    steady = int((g["flip"] == 0).sum())
    fig = go.Figure()
    fig.add_scatter(
        x=g["pairs"], y=g["flip"] * 100, mode="markers",
        marker=dict(color=g["tp"] * 100, colorscale=T.sequential_scale(theme),
                    cmin=0, cmax=100, size=12, opacity=0.9,
                    line=dict(color=c["surface"], width=2),
                    colorbar=dict(title=dict(text="true positive",
                                             font=dict(size=11, color=c["muted"])),
                                  ticksuffix="%", thickness=10, outlinewidth=0,
                                  len=0.85, tickfont=dict(size=10, color=c["muted"]))),
        customdata=np.stack([g.index, g["tp"] * 100], axis=-1),
        hovertemplate="<b>%{customdata[0]}</b><br>%{y:.0f}% of firings differed from"
                      " the one before<br>%{x:,} pairs, %{customdata[1]:.0f}%"
                      " true positive<extra></extra>")
    if steady:
        fig.add_annotation(x=0, y=0, xref="paper", yref="y", xanchor="left",
                           yanchor="bottom", yshift=6, showarrow=False,
                           text=f"{steady} rules never ruled two ways",
                           font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", showlegend=False,
        margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(title="firings with a previous verdict to compare", rangemode="tozero"),
        yaxis=dict(title="share that changed the verdict", rangemode="tozero",
                   ticksuffix="%")))
    return fig


OVERLAP_N = 12            # rules in the overlap matrix


@app.callback(Output("g-s-overlap", "figure"), *SIG_INPUTS)
def fig_signal_overlap(rule, tenant, start, end, inst, theme):
    d = D.apply_filters(DF, start, end, inst, chosen(tenant), chosen(rule))
    if d.empty:
        return empty_fig(theme)
    c = T.colors(theme)
    if d["tenant_name"].nunique() < 2:
        return empty_fig(theme, "Overlap needs more than one customer in the slice")
    keep = d[D.RULE_COL].value_counts().head(OVERLAP_N).index
    if len(keep) < 2:
        return empty_fig(theme, "Overlap needs at least two rules in the slice")
    books = {r: set(d.loc[d[D.RULE_COL] == r, "tenant_name"]) for r in keep}
    z = np.array([[len(books[a] & books[b]) / len(books[a] | books[b])
                   for b in keep] for a in keep])
    shared = np.array([[len(books[a] & books[b]) for b in keep] for a in keep])
    np.fill_diagonal(z, np.nan)               # a rule overlapping itself says nothing
    labels = [r if len(r) <= 34 else r[:32] + "…" for r in keep]
    fig = go.Figure(go.Heatmap(
        z=z, x=labels, y=labels, colorscale=T.sequential_scale(theme),
        zmin=0, zmax=1, xgap=2, ygap=2, customdata=shared,
        colorbar=dict(title=dict(text="shared book",
                                 font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="%{y}<br>%{x}<br>%{customdata} customers in common"
                      "<br>overlap %{z:.2f}<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(showgrid=False, tickangle=-35,
                   tickfont=dict(size=10, color=c["ink2"])),
        yaxis=dict(showgrid=False, autorange="reversed",
                   tickfont=dict(size=10, color=c["ink2"]))))
    return fig


@app.callback(Output("g-s-rater", "figure"),
              Input("s-minrater", "value"), *SIG_INPUTS)
def fig_signal_rater(min_n, rule, tenant, start, end, inst, theme):
    d = verdicts(D.apply_filters(DF, start, end, inst, chosen(tenant), chosen(rule)))
    if d.empty:
        return empty_fig(theme, "No verdicts in this slice")
    c = T.colors(theme)
    min_n = min_n or 75
    d = d.assign(tp=d["Final_Disposition"].eq("True Positive"))
    pair = [D.RULE_COL, "tenant_name"]
    # score every close against the same rule at the same customer
    d = d.assign(lift=d["tp"].astype(float) - d.groupby(pair)["tp"].transform("mean"))
    # only work that two desks both touched can carry a difference between desks
    d = d[d.groupby(pair)["sa_decision_owner_role"].transform("nunique") >= 2]
    if d.empty:
        return empty_fig(theme, "No rule and customer here was closed by two desks")
    g = d.groupby("sa_decision_owner_role").agg(n=("tp", "size"),
                                                lift=("lift", "mean"),
                                                tp=("tp", "mean"))
    g = g[g["n"] >= min_n].sort_values("lift")
    if g.empty:
        return empty_fig(theme, f"No role reached {min_n} shared verdicts here")
    # role strings differ at the END, so elide the middle - and keep the full
    # string as the category so two roles can never collapse onto one bar
    labels = [r if len(r) <= 46 else r[:26] + "…" + r[-18:] for r in g.index]
    fig = go.Figure()
    fig.add_bar(y=list(g.index), x=g["lift"] * 100, orientation="h",
                marker_color=[c["series"][0] if v >= 0 else c["series"][7]
                              for v in g["lift"]],
                marker_line=dict(width=0),
                customdata=np.stack([g.index, g["n"], g["tp"] * 100], axis=-1),
                hovertemplate="%{customdata[0]}<br>%{x:+.1f} points against the same"
                              " rule at the same customer<br>%{customdata[2]:.1f}%"
                              " true positive over %{customdata[1]:,} verdicts"
                              "<extra></extra>")
    fig.add_vline(x=0, line=dict(color=c["axis"], width=1))
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.3, margin=dict(l=8, r=24, t=52, b=8),
        xaxis=dict(title="points against the same rule at the same customer",
                   zeroline=False),
        yaxis=dict(showgrid=False, tickmode="array", tickvals=list(g.index),
                   ticktext=labels, tickfont=dict(size=11, color=c["ink2"]))))
    return fig



# -------------------------------------------------------------- rule tab ----

def rule_slice(start, end, inst, rule, tenant_pick):
    """Verdicts inside the global rule and customer selection."""
    return verdicts(D.apply_filters(DF, start, end, inst,
                                    chosen(tenant_pick), chosen(rule)))


def wilson(k, n, z=1.96):
    """95% interval for a share - honest about rules with a handful of verdicts."""
    k, n = np.asarray(k, float), np.asarray(n, float)
    p = np.divide(k, n, out=np.zeros_like(k), where=n > 0)
    denom = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2)) / denom
    return (centre - half) * 100, (centre + half) * 100


RULE_INPUTS = [Input("f-rule", "value"), Input("f-tenant", "value"),
               Input("f-date", "start_date"), Input("f-date", "end_date"),
               Input("f-instance", "value"), Input("theme-store", "data")]


@app.callback(Output("r-summary", "children"), *RULE_INPUTS)
def rule_summary(rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick)
    if d.empty:
        return [stat_tile("Verdicts", "0", "nothing closed with a disposition")]
    tp = d["Final_Disposition"].eq("True Positive")
    pairs = pairs_by_rule(d)
    raw = D.apply_filters(DF, start, end, inst, chosen(tenant_pick), chosen(rule))
    return [
        stat_tile("Verdicts", f"{len(d):,}",
                  f"of {len(raw):,} containers "
                  f"{scope_name(rule, 'all rules', 'rules')[:28]} opened"),
        stat_tile("True positive", f"{tp.mean() * 100:.1f}%",
                  f"{int(tp.sum()):,} containers"),
        stat_tile("Customers", f"{d['tenant_name'].nunique():,}",
                  "with at least one verdict"),
        stat_tile("Duplicate", f"{raw['is_dup'].mean() * 100:.1f}%"
                  if len(raw) else "0%", "absorbed before an analyst saw it"),
    ]


def disposition_colors(theme):
    """Fixed hue per recorded disposition; each verdict family keeps the hue its
    rolled-up class has elsewhere, the non-verdicts stay grey."""
    c = T.colors(theme)
    s = c["series"]
    return {"Benign Positive - Suspicious But Expected": s[0],
            "True Positive - Suspicious Activity": s[1],
            "Undetermined": s[3],
            "False Positive - Incorrect Analytic Logic": s[2],
            "False Positive - Inaccurate Data": s[5],
            "Other": c["neutral"], "NA": s[4], "(none)": c["grid"]}


def disposition_by_rule(d):
    """One row per rule: container count per recorded disposition, plus shares."""
    m = (d.pivot_table(index=D.RULE_COL, columns="closure_disposition",
                       values="container_id", aggfunc="size", fill_value=0))
    extra = [k for k in m.columns if k not in DISP_ORDER]   # anything new lands last
    m = m.reindex(columns=DISP_ORDER + extra, fill_value=0)
    m["containers"] = m.sum(axis=1)
    for v in DISP_ORDER + extra:
        m[f"{v} %"] = (m[v] / m["containers"] * 100).round(1)
    return m.sort_values("containers", ascending=False), DISP_ORDER + extra


RDISP_FILTERS = [Input("f-date", "start_date"), Input("f-date", "end_date"),
                 Input("f-instance", "value"), Input("f-tenant", "value"),
                 Input("f-rule", "value")]


@app.callback(Output("rdisp-page", "data"),
              Input("rdisp-prev", "n_clicks"), Input("rdisp-next", "n_clicks"),
              *RDISP_FILTERS, Input("rdisp-sort", "value"),
              State("rdisp-page", "data"))
def rdisp_page_state(prev, nxt, start, end, inst, ten, rule, sort_by, current):
    d = D.apply_filters(DF, start, end, inst, chosen(ten), chosen(rule))
    pages = max(1, -(-d[D.RULE_COL].nunique() // PAGE_SIZE))
    if ctx.triggered_id == "rdisp-prev":
        current = (current or 0) - 1
    elif ctx.triggered_id == "rdisp-next":
        current = (current or 0) + 1
    else:
        current = 0                # a filter or sort change starts over at page 1
    return min(max(current, 0), pages - 1)


@app.callback(Output("g-r-dispositions", "figure"),
              Output("rdisp-page-label", "children"),
              Input("rdisp-page", "data"), Input("rdisp-sort", "value"),
              Input("rdisp-mode", "value"), *RDISP_FILTERS,
              Input("theme-store", "data"))
def fig_rule_dispositions(page, sort_by, mode, start, end, inst, ten, rule, theme):
    d = D.apply_filters(DF, start, end, inst, chosen(ten), chosen(rule))
    if d.empty:
        return empty_fig(theme), ""
    c, cols = T.colors(theme), disposition_colors(theme)
    g, order = disposition_by_rule(d)
    if sort_by and sort_by != "containers":
        # volume breaks ties, so equal shares still read biggest-first
        g = g.sort_values([f"{sort_by} %", "containers"], ascending=False)
    total_rules = len(g)
    pages = max(1, -(-total_rules // PAGE_SIZE))
    page = min(max(page or 0, 0), pages - 1)
    lo, hi = page * PAGE_SIZE, min((page + 1) * PAGE_SIZE, total_rules)
    m = g.iloc[lo:hi][::-1]                       # biggest rule at the top
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in m.index]
    share = mode != "count"
    fig = go.Figure()
    for v in order:                               # fixed order -> fixed hue
        if not m[v].any():
            continue
        fig.add_bar(y=labels, x=m[f"{v} %"] if share else m[v], name=v,
                    orientation="h", marker_color=cols.get(v, c["neutral"]),
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(m[f"{v} %"]) if share else None,
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=c["ink"] if v in ("Other", "(none)")
                                  else label_ink(theme, c)),
                    customdata=np.stack([m.index, m[v], m[f"{v} %"]], axis=-1),
                    hovertemplate="%{customdata[0]}<br>%{customdata[1]:,} "
                                  + v + " (%{customdata[2]:.1f}%)<extra></extra>")
    # every rule's volume at the end of its bar, so share never hides size
    fig.add_scatter(y=labels, x=[101] * len(m) if share else m["containers"],
                    mode="text", text=[f"{n:,}" for n in m["containers"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    xaxis = (dict(title="share of a rule's containers", range=[0, 112],
                  tickvals=[0, 25, 50, 75, 100],
                  ticktext=["0%", "25%", "50%", "75%", "100%"]) if share else
             dict(title="containers", range=[0, m["containers"].max() * 1.12]))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.24, legend=dict(traceorder="normal"),
        uniformtext=dict(mode="hide", minsize=8),
        margin=dict(l=8, r=24, t=52, b=8), xaxis=xaxis,
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    label = (f"Page {page + 1} of {pages} · rules {lo + 1}-{hi} of {total_rules:,}"
             f" · {int(m['containers'].sum()):,} containers on this page")
    return fig, label


@app.callback(Output("g-r-customers", "figure"), *RULE_INPUTS)
def fig_rule_customers(rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick)
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    m = (d.pivot_table(index="tenant_name", columns="Final_Disposition",
                       values="container_id", aggfunc="size", fill_value=0)
         .reindex(columns=T.FINAL_ORDER, fill_value=0))
    m["containers"] = m[T.FINAL_ORDER].sum(axis=1)
    m = m.sort_values("containers", ascending=False).head(16)[::-1]
    share = m[T.FINAL_ORDER].div(m["containers"], axis=0) * 100
    ink = label_ink(theme, c)
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        fig.add_bar(y=list(m.index), x=share[verdict], name=verdict, orientation="h",
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    text=seg_labels(share[verdict]),
                    textposition="inside", insidetextanchor="middle",
                    textfont=dict(size=10, color=ink), cliponaxis=False,
                    customdata=np.stack([m[verdict], m["containers"]], axis=-1),
                    hovertemplate="%{y}<br>%{customdata[0]:,} " + verdict
                                  + " of %{customdata[1]:,} (%{x:.0f}%)<extra></extra>")
    fig.add_scatter(y=list(m.index), x=[101] * len(m), mode="text",
                    text=[f"{v:.0f}%" if v < FITS else ""
                          for v in share["True Positive"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["ink2"], size=11))
    fig.update_layout(**T.layout(
        theme, barmode="stack", bargap=0.24, legend=dict(traceorder="normal"),
        margin=dict(l=8, r=52, t=52, b=8),
        uniformtext=dict(mode="hide", minsize=8),
        xaxis=dict(title="share of the customer's verdicts", range=[0, 108],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-r-timeline", "figure"), *RULE_INPUTS)
def fig_rule_timeline(rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    order = d["tenant_name"].value_counts().head(20).index
    d = d[d["tenant_name"].isin(order)]
    lane = {t: i for i, t in enumerate(order[::-1])}
    d = d.assign(lane=d["tenant_name"].map(lane))
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:
        s = d[d["Final_Disposition"] == verdict]
        if s.empty:
            continue
        fig.add_scatter(
            x=s["t"], y=s["lane"], mode="markers", name=verdict,
            marker=dict(color=cols[verdict], size=8, opacity=0.8,
                        line=dict(color=c["surface"], width=1)),
            customdata=np.stack([s["tenant_name"],
                                 s["container_name"].str.slice(0, 70),
                                 s["container_id"]], axis=-1),
            hovertemplate="%{x|%b %-d, %H:%M}<br><b>" + verdict + "</b>"
                          "<br>%{customdata[0]}<br>%{customdata[1]}"
                          "<br>container %{customdata[2]}<extra></extra>")
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        dragmode="zoom", margin=dict(l=8, r=24, t=52, b=8),
        xaxis=dict(title="", showgrid=True, fixedrange=False),
        yaxis=dict(showgrid=False, tickmode="array",
                   tickvals=list(range(len(order))), ticktext=list(order[::-1]),
                   range=[-0.6, len(order) - 0.4], fixedrange=True,
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-r-consistency", "figure"), *RULE_INPUTS)
def fig_rule_consistency(rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick)
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    g = d.groupby("tenant_name")["Final_Disposition"].agg(
        n="size", tp=lambda s: (s == "True Positive").sum())
    g = g[g["n"] >= 3]
    if g.empty:
        return empty_fig(theme, "No customer has 3+ verdicts in this selection")
    g["rate"] = g["tp"] / g["n"] * 100
    lo, hi = wilson(g["tp"], g["n"])
    g["lo"], g["hi"] = lo, hi
    g = g.sort_values(["rate", "n"], ascending=False).head(14)[::-1]
    pooled = (d["Final_Disposition"] == "True Positive").mean() * 100
    fig = go.Figure()
    for y, x0, x1 in zip(g.index, g["lo"], g["hi"]):
        fig.add_shape(type="line", y0=y, y1=y, x0=x0, x1=x1, layer="below",
                      line=dict(color=c["grid"], width=3))
    fig.add_scatter(
        x=g["rate"], y=list(g.index), mode="markers",
        marker=dict(color=cols["True Positive"], size=10,
                    line=dict(color=c["surface"], width=2)),
        customdata=np.stack([g["n"], g["tp"], g["lo"], g["hi"]], axis=-1),
        hovertemplate="%{y}<br>%{x:.0f}% true positive"
                      " (%{customdata[1]:,} of %{customdata[0]:,})"
                      "<br>95% interval %{customdata[2]:.0f}-%{customdata[3]:.0f}%"
                      "<extra></extra>")
    fig.add_scatter(x=[104] * len(g), y=list(g.index), mode="text",
                    text=[f"n={int(v):,}" for v in g["n"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.add_vline(x=pooled, line=dict(color=c["axis"], width=1),
                  annotation_text=f"rule overall {pooled:.0f}%",
                  annotation_position="top",
                  annotation_font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, hovermode="closest",
        margin=dict(l=8, r=60, t=64, b=8),
        xaxis=dict(title="true-positive share", range=[-2, 118],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-r-drift", "figure"), Input("f-grain", "value"), *RULE_INPUTS)
def fig_rule_drift(grain, rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    p = (d.set_index("t").groupby("Final_Disposition").resample(grain or "W")
         .size().rename("n").reset_index()
         .pivot(index="t", columns="Final_Disposition", values="n")
         .reindex(columns=T.FINAL_ORDER).fillna(0))
    p = p[p.sum(axis=1) > 0]
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:
        fig.add_bar(x=p.index, y=p[verdict], name=verdict, marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    hovertemplate="%{x|" + grain_fmt(grain) + "}<br>%{y:,} "
                                  + verdict + "<extra></extra>")
    fig.update_layout(**T.layout(
        theme, barmode="stack", hovermode="x unified",
        legend=dict(traceorder="normal"),
        yaxis=dict(title="verdicts")))
    return fig


@app.callback(Output("g-r-titles", "figure"), Input("r-mintitle", "value"), *RULE_INPUTS)
def fig_rule_titles(min_rows, rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick).copy()
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    d["title"] = d["container_name"].str.split(" - ").str[-1].str.strip()
    g = (d.groupby(d["title"].str.lower())
         .agg(n=("title", "size"),
              tp=("Final_Disposition", lambda s: (s == "True Positive").mean() * 100),
              label=("title", "first")))
    g = g[g["n"] >= (min_rows or 2)]
    if g.empty:
        return empty_fig(theme, f"No alert title reached {min_rows} rows here")
    base = (d["Final_Disposition"] == "True Positive").mean() * 100
    g = g.sort_values(["tp", "n"], ascending=False).head(16)[::-1]
    labels = [t if len(t) <= 46 else t[:44] + "…" for t in g["label"]]
    fig = go.Figure()
    fig.add_bar(y=labels, x=g["tp"], orientation="h",
                marker_color=cols["True Positive"],
                marker_line=dict(color=c["surface"], width=1),
                customdata=np.stack([g["label"], g["n"]], axis=-1),
                hovertemplate="%{customdata[0]}<br>%{x:.0f}% true positive"
                              " of %{customdata[1]:,} rows<extra></extra>")
    fig.add_scatter(y=labels, x=g["tp"], mode="text",
                    text=[f"n={int(v):,}" for v in g["n"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.add_vline(x=base, line=dict(color=c["axis"], width=1),
                  annotation_text=f"rule overall {base:.0f}%",
                  annotation_position="top",
                  annotation_font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.30,
        margin=dict(l=8, r=60, t=64, b=8),
        xaxis=dict(title="true-positive rate",
                   range=[0, max(g["tp"].max() * 1.2, 10)], ticksuffix="%"),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-r-transition", "figure"), *RULE_INPUTS)
def fig_rule_transition(rule, tenant_pick, start, end, inst, theme):
    p = pairs_by_rule(rule_slice(start, end, inst, rule, tenant_pick))
    if p.empty:
        return empty_fig(theme, "Nothing fired twice for the same customer here")
    c = T.colors(theme)
    n = pd.crosstab(p["prev"], p["Final_Disposition"]).reindex(
        index=T.FINAL_ORDER, columns=T.FINAL_ORDER, fill_value=0)
    pct = (n.div(n.sum(axis=1).replace(0, np.nan), axis=0) * 100).fillna(0)
    fig = go.Figure(go.Heatmap(
        z=pct.values, x=list(pct.columns), y=list(pct.index),
        customdata=n.values, colorscale=T.sequential_scale(theme),
        zmin=0, zmax=100, xgap=2, ygap=2,
        text=[[f"{v:.0f}%" for v in row] for row in pct.values],
        texttemplate="%{text}", textfont=dict(size=13),
        colorbar=dict(title=dict(text="% of row", font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="was %{y}<br>now %{x}<br>%{customdata:,} firings"
                      " (%{z:.1f}% of row)<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(title="this firing", showgrid=False,
                   tickfont=dict(size=11, color=c["ink2"])),
        yaxis=dict(title="previous firing", showgrid=False, autorange="reversed",
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


@app.callback(Output("g-r-clock", "figure"), *RULE_INPUTS)
def fig_rule_clock(rule, tenant_pick, start, end, inst, theme):
    d = rule_slice(start, end, inst, rule, tenant_pick).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme, "No verdict for this selection in this slice")
    c = T.colors(theme)
    d = d.assign(dow=d["t"].dt.dayofweek, hour=d["t"].dt.hour,
                 tp=d["Final_Disposition"].eq("True Positive"))
    n = (d.pivot_table(index="dow", columns="hour", values="tp", aggfunc="size")
         .reindex(index=range(7), columns=range(24)).fillna(0))
    rate = (d.pivot_table(index="dow", columns="hour", values="tp", aggfunc="mean")
            .reindex(index=range(7), columns=range(24)) * 100).fillna(0)
    fig = go.Figure(go.Heatmap(
        z=n.values, x=[f"{h:02d}" for h in n.columns], y=WEEKDAYS,
        customdata=rate.values, colorscale=T.sequential_scale(theme),
        xgap=2, ygap=2,
        colorbar=dict(title=dict(text="firings", font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="%{y} %{x}:00 UTC<br>%{z:,} firings"
                      "<br>%{customdata:.0f}% true positive<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(title="hour of day (UTC)", showgrid=False, dtick=2,
                   tickfont=dict(size=10, color=c["muted"])),
        yaxis=dict(showgrid=False, autorange="reversed",
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-r-lift", "figure"),
              Input("r-outcome", "value"), Input("r-minpairs", "value"), *RULE_INPUTS)
def fig_rule_lift(outcome, min_pairs, rule, tenant_pick, start, end, inst, theme):
    p = pairs_by_rule(rule_slice(start, end, inst, rule, tenant_pick))
    if p.empty:
        return empty_fig(theme, "Nothing fired twice for the same customer here")
    c, cols = T.colors(theme), T.final_colors(theme)
    outcome, min_pairs = outcome or "True Positive", min_pairs or 3
    p = p.assign(hit=p["Final_Disposition"].eq(outcome))
    n = (p.pivot_table(index="tenant_name", columns="prev", values="hit", aggfunc="size")
         .reindex(columns=T.FINAL_ORDER).fillna(0))
    rate = (p.pivot_table(index="tenant_name", columns="prev", values="hit",
                          aggfunc="mean").reindex(columns=T.FINAL_ORDER) * 100)
    rate = rate.where(n >= min_pairs)
    keep = rate.notna().sum(axis=1) >= 2
    if not keep.any():
        return empty_fig(
            theme, f"No customer has {min_pairs}+ pairs after two different verdicts")
    rate, n = rate[keep], n[keep]
    spread = rate.max(axis=1) - rate.min(axis=1)
    order = spread.sort_values(ascending=False).head(12).index[::-1]
    rate, n, spread = rate.loc[order], n.loc[order], spread.loc[order]
    fig = go.Figure()
    for y, lo, hi in zip(order, rate.min(axis=1), rate.max(axis=1)):
        fig.add_shape(type="line", y0=y, y1=y, x0=lo, x1=hi, layer="below",
                      line=dict(color=c["grid"], width=2))
    for prev in T.FINAL_ORDER:
        if rate[prev].isna().all():
            continue
        fig.add_scatter(
            x=rate[prev], y=list(order), mode="markers",
            name=f"after {prev.lower()}",
            marker=dict(color=cols[prev], size=11,
                        line=dict(color=c["surface"], width=2)),
            customdata=n[prev],
            hovertemplate="%{y}<br>%{x:.0f}% " + outcome.lower() + " after "
                          + prev.lower() + "<br>%{customdata:,} pairs<extra></extra>")
    fig.add_scatter(x=[104] * len(order), y=list(order), mode="text",
                    text=[f"{v:.0f} pts" for v in spread],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, hovermode="closest", legend=dict(traceorder="normal"),
        margin=dict(l=8, r=64, t=52, b=8),
        xaxis=dict(title=f"chance this firing is {outcome.lower()}", range=[-2, 118],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


def labelled(start, end, inst, ten, rule):
    """The modelling population, with the previous verdict for the same alert."""
    d = verdicts(sliced(start, end, inst, ten, rule)).sort_values("t")
    d = d.assign(prev=d.groupby(["tenant_name", "container_name"])["Final_Disposition"]
                 .shift())
    return d


@app.callback(Output("g-ml-transition", "figure"), *FILTERS)
def fig_ml_transition(start, end, inst, ten, rule, grain, theme):
    d = labelled(start, end, inst, ten, rule)
    d = d[d["prev"].notna()]
    if d.empty:
        return empty_fig(theme, "No alert recurred with a verdict in this slice")
    c = T.colors(theme)
    n = pd.crosstab(d["prev"], d["Final_Disposition"]).reindex(
        index=T.FINAL_ORDER, columns=T.FINAL_ORDER, fill_value=0)
    pct = (n.div(n.sum(axis=1).replace(0, np.nan), axis=0) * 100).fillna(0)
    fig = go.Figure(go.Heatmap(
        z=pct.values, x=list(pct.columns), y=list(pct.index),
        customdata=n.values, colorscale=T.sequential_scale(theme),
        zmin=0, zmax=100, xgap=2, ygap=2,
        text=[[f"{v:.0f}%" for v in row] for row in pct.values],
        texttemplate="%{text}", textfont=dict(size=13),
        colorbar=dict(title=dict(text="% of row", font=dict(size=11, color=c["muted"])),
                      thickness=10, outlinewidth=0, len=0.85,
                      tickfont=dict(size=10, color=c["muted"])),
        hovertemplate="was %{y}<br>now %{x}<br>%{customdata:,} containers"
                      " (%{z:.1f}% of row)<extra></extra>"))
    fig.update_layout(**T.layout(
        theme, margin=dict(l=8, r=8, t=52, b=8),
        xaxis=dict(title="this verdict", showgrid=False,
                   tickfont=dict(size=11, color=c["ink2"])),
        yaxis=dict(title="previous verdict", showgrid=False, autorange="reversed",
                   tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-ml-drift", "figure"), *FILTERS)
def fig_ml_drift(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule)).dropna(subset=["t"])
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    p = (d.set_index("t").groupby("Final_Disposition").resample(grain)
         .size().rename("n").reset_index()
         .pivot(index="t", columns="Final_Disposition", values="n")
         .reindex(columns=T.FINAL_ORDER).fillna(0))
    p = p[p.sum(axis=1) > 0]
    share = p.div(p.sum(axis=1), axis=0) * 100
    fig = go.Figure()
    for verdict in T.FINAL_ORDER:            # fixed order -> fixed hue
        fig.add_bar(x=share.index, y=share[verdict], name=verdict,
                    marker_color=cols[verdict],
                    marker_line=dict(color=c["surface"], width=1),
                    customdata=p[verdict],
                    hovertemplate="%{x|" + grain_fmt(grain) + "}<br>%{y:.0f}% " + verdict
                                  + " (%{customdata:,})<extra></extra>")
    fig.update_layout(**T.layout(
        theme, barmode="stack", hovermode="x unified",
        legend=dict(traceorder="normal"),
        yaxis=dict(title="share of verdicts", range=[0, 100],
                   tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"])))
    return fig


@app.callback(Output("g-ml-title", "figure"),
              Input("ml-mintitle", "value"), *FILTERS)
def fig_ml_title(min_rows, start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule)).copy()
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c, cols = T.colors(theme), T.final_colors(theme)
    # the tail of the container name is the alert title the rule fired on
    d["title"] = d["container_name"].str.split(" - ").str[-1].str.strip()
    g = (d.groupby(d["title"].str.lower())
         .agg(n=("title", "size"),
              tp=("Final_Disposition", lambda s: (s == "True Positive").mean() * 100),
              label=("title", "first")))
    g = g[g["n"] >= (min_rows or 10)]
    if g.empty:
        return empty_fig(theme, f"No alert title reached {min_rows} rows in this slice")
    base = (d["Final_Disposition"] == "True Positive").mean() * 100
    g = g.sort_values(["tp", "n"], ascending=False).head(18)[::-1]
    labels = [t if len(t) <= 46 else t[:44] + "…" for t in g["label"]]
    fig = go.Figure()
    fig.add_bar(y=labels, x=g["tp"], orientation="h",
                marker_color=cols["True Positive"],
                marker_line=dict(color=c["surface"], width=1),
                customdata=np.stack([g["label"], g["n"]], axis=-1),
                hovertemplate="%{customdata[0]}<br>%{x:.0f}% true positive"
                              " of %{customdata[1]:,} rows<extra></extra>")
    fig.add_scatter(y=labels, x=g["tp"], mode="text",
                    text=[f"n={int(v):,}" for v in g["n"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    # the base rate the model has to beat, drawn as a solid hairline
    fig.add_vline(x=base, line=dict(color=c["axis"], width=1),
                  annotation_text=f"base rate {base:.0f}%",
                  annotation_position="top",
                  annotation_font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.30,
        margin=dict(l=8, r=60, t=64, b=8),
        xaxis=dict(title="true-positive rate", range=[0, max(g["tp"].max() * 1.2, 10)],
                   ticksuffix="%"),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("g-ml-noise", "figure"), *FILTERS)
def fig_ml_noise(start, end, inst, ten, rule, grain, theme):
    d = verdicts(sliced(start, end, inst, ten, rule))
    if d.empty:
        return empty_fig(theme, "No container was closed with a disposition in this slice")
    c = T.colors(theme)
    grp = (d.groupby(["tenant_name", "container_name"])
           .agg(rule=(D.RULE_COL, "first"), seen=("Final_Disposition", "size"),
                verdicts=("Final_Disposition", "nunique")))
    rep = grp[grp["seen"] > 1]
    if rep.empty:
        return empty_fig(theme, "No alert recurred with a verdict in this slice")
    overall = (rep["verdicts"] > 1).mean() * 100
    g = (rep.assign(split=rep["verdicts"] > 1).groupby("rule")
         .agg(groups=("seen", "size"), split=("split", "mean")))
    g["split"] *= 100
    g = g[g["groups"] >= 5].sort_values(["split", "groups"], ascending=False).head(18)[::-1]
    if g.empty:
        return empty_fig(theme, "No rule had enough recurring alerts in this slice")
    labels = [r if len(r) <= 46 else r[:44] + "…" for r in g.index]
    fig = go.Figure()
    fig.add_bar(y=labels, x=g["split"], orientation="h",
                marker_color=c["series"][0],
                marker_line=dict(color=c["surface"], width=1),
                customdata=np.stack([g.index, g["groups"]], axis=-1),
                hovertemplate="%{customdata[0]}<br>%{x:.0f}% of %{customdata[1]:,}"
                              " recurring alerts got conflicting verdicts<extra></extra>")
    fig.add_scatter(y=labels, x=g["split"], mode="text",
                    text=[f"n={int(v):,}" for v in g["groups"]],
                    textposition="middle right", showlegend=False, hoverinfo="skip",
                    textfont=dict(color=c["muted"], size=10))
    fig.add_vline(x=overall, line=dict(color=c["axis"], width=1),
                  annotation_text=f"all rules {overall:.0f}%",
                  annotation_position="top",
                  annotation_font=dict(color=c["muted"], size=10))
    fig.update_layout(**T.layout(
        theme, showlegend=False, bargap=0.30,
        margin=dict(l=8, r=60, t=64, b=8),
        xaxis=dict(title="share of recurring alerts with conflicting verdicts",
                   range=[0, 108], tickvals=[0, 25, 50, 75, 100],
                   ticktext=["0%", "25%", "50%", "75%", "100%"]),
        yaxis=dict(showgrid=False, tickfont=dict(size=11, color=c["ink2"]))))
    return fig


@app.callback(Output("t-table", "data"), Output("t-table", "columns"),
              Output("t-table", "style_data_conditional"),
              Output("t-table", "style_header"), Output("t-table", "style_cell"),
              *FILTERS)
def table(start, end, inst, ten, rule, grain, theme):
    d = sliced(start, end, inst, ten, rule)
    c = T.colors(theme)
    if d.empty:
        rows, cols = [], [{"name": "rule", "id": "rule"}]
    else:
        g = (d.groupby(D.RULE_COL)
             .agg(containers=("is_dup", "size"), duplicate=("is_dup", "sum"),
                  escalated=("is_esc", "sum"), tenants=("tenant_name", "nunique"))
             .sort_values("containers", ascending=False).reset_index())
        g["duplicate %"] = (g["duplicate"] / g["containers"] * 100).round(1)
        g["escalated %"] = (g["escalated"] / g["containers"] * 100).round(1)
        rec = (d[d["recur_bucket"].isin(["within 1h", "1h - 24h"])]
               .groupby(D.RULE_COL).size().rename("recurs <24h"))
        g = g.join(rec, on=D.RULE_COL).fillna({"recurs <24h": 0})
        g["recurs <24h"] = g["recurs <24h"].astype(int)
        g = g[[D.RULE_COL, "containers", "duplicate", "duplicate %",
               "escalated", "escalated %", "recurs <24h", "tenants"]]
        rows = g.to_dict("records")
        cols = [{"name": ("rule" if k == D.RULE_COL else k), "id": k} for k in g.columns]
    header = {"fontWeight": "600", "textTransform": "uppercase", "fontSize": "10px",
              "letterSpacing": "0.06em", "border": "none", "color": c["muted"],
              "backgroundColor": c["surface"], "borderBottom": f"1px solid {c['axis']}"}
    cell = {"fontFamily": T.FONT, "fontSize": "12px", "padding": "8px 12px",
            "border": "none", "textAlign": "right", "fontVariantNumeric": "tabular-nums",
            "backgroundColor": c["surface"], "color": c["ink"]}
    data_style = [{"if": {"row_index": "odd"}, "backgroundColor": c["plane"]}]
    return rows, cols, data_style, header, cell


if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
