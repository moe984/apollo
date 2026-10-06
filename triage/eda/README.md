# Container disposition EDA

Plotly Dash dashboard over `mom_container_disposition_90d_searches.csv`
(27,054 containers, 2026-06-18 to 2026-09-16, 3 SOAR instances, 54 tenants, 314 rules).

## Run

```bash
pip install -r eda/requirements.txt      # pandas, dash, plotly
python3 eda/app.py                       # http://127.0.0.1:8050
```

## Layout

One filter row (date range, SOAR instance, tenant, rule, day/week/month/quarter grain) scopes
every tile, chart and the table below it. The header toggles light/dark. Charts sit
in six tabs - **Overview** (volume, dedup, tenants, recurrence), **Disposition**
(verdict analysis), **By customer** (pick a customer, then walk its rules), **By rule**
(the mirror: pick a rule, then walk its customers), **Signals** (market-style indicators
over rule volume and yield) and **Modeling** (diagnostics for a disposition classifier) - with the filters, tiles and table view shared by all of them.
There is one filter row and no per-tab pickers: the global tenant and rule filters
scope every tab, including By customer, By rule and Signals. Both are multi-select,
so a tab that reads as "one entity" simply shows whatever the filter selected -
one, several or all. Panels that need exactly one customer to make sense (the
breadth panel) say so in their own wording and switch what they count when the
filter is pinned to a single customer.

| Panel | Question it answers |
|---|---|
| Stat tiles | How many containers, how much the dedup absorbs, how much escalates, how much recurs |
| Volume by container status | Does volume or the duplicate share move over the window |
| Dedup capture by rule | Which rules the dedup already covers and which it does not |
| Tenant profile | Volume against duplicate share per tenant, split by instance |
| Closure disposition | What analysts conclude on containers that reach a close |
| Final disposition by rule *(Disposition tab)* | Verdict mix per rule for containers closed with a disposition (`container_status = closed` and `sa_decision = close_with_disposition`), counting Benign / True / False Positive only - `Other` and blank verdicts are excluded. True-positive share labelled |
| Residual recurrence | Among containers *not* marked duplicate, how soon an identical tenant + container name came back |
| Duplicate share over time | Trend in dedup capture |
| Rule x tenant density | Where the volume actually sits |
| Final disposition by customer *(Disposition tab)* | Verdict mix for every customer with a verdict, biggest first |
| Rules ranked by true-positive share *(Disposition tab)* | Top 25 rules as dots on one baseline, dot size = verdict count, with a minimum-verdict control to drop the tiny-sample noise |
| Every rule at once *(Disposition tab)* | One dot per rule - volume against true-positive share, all rules in the slice, not a top-N |
| Every rule, page by page *(Disposition tab)* | The same verdict mix, 30 rules per page with prev/next - covers all 283 rules; sortable by volume or by any of the three verdict shares (volume breaks ties) |
| Spread of each verdict across rules *(Disposition tab)* | Box per verdict, point per rule - where the middle 50% of rules sits and how long the tail is |
| All rules by verdict *(Disposition tab)* | Every rule's benign / true / false positive counts and shares, sortable and filterable |
| Which rules fire for each customer *(By customer tab)* | Every customer as a bar, the seven busiest rules as coloured segments and the rest as Other; share or raw count. Covers all containers, not only those with a verdict, and ignores the customer filter so the whole book stays comparable |
| Rules for this customer *(By customer tab)* | Verdict mix per rule for the selected customer, true-positive share labelled |
| When each verdict happened *(By customer tab)* | Event timeline: one mark per container on the day it was decided, one lane per rule (top 20), coloured by verdict |
| Last verdict predicts the next *(By customer tab)* | Transition matrix for that customer, pairing **consecutive firings of the same rule** |
| How much the last verdict moves the odds *(By customer tab)* | Per rule: chance of the chosen outcome after each of the three previous verdicts, connected as one row. Outcome and minimum-pairs are selectable; a dot is drawn only where the pairs support it |
| Customers running this rule *(By rule tab)* | Verdict mix per customer for the selected rule |
| When each verdict happened *(By rule tab)* | Event timeline, one lane per customer, with zoom |
| Does this rule mean the same thing everywhere? *(By rule tab)* | True-positive share per customer with a 95% Wilson interval against the rule's pooled rate - shows whether a per-tenant difference is real |
| Verdict mix over time *(By rule tab)* | Verdict counts per period for this rule - step changes usually mean a tuning change |
| What this rule actually catches *(By rule tab)* | The rule's own alert titles ranked by true-positive rate, against the rule's overall rate |
| Last verdict predicts the next *(By rule tab)* | Transition matrix for this rule across its customers |
| When this rule fires *(By rule tab)* | Day of week against hour of day, shaded by volume, true-positive rate on hover |
| How much the last verdict moves the odds *(By rule tab)* | Per customer, for the selected rule |
| Rule tape *(Signals tab)* | Daily volume with 7/28-day averages, a 20-day ±2σ band and crossover markers |
| Detection portfolio *(Signals tab)* | Every rule as cost (verdicts) against yield (true-positive share), sized by true positives, with the best-yield-at-scale staircase and the platform average |
| Busy day for everyone, or just one customer? *(Signals tab)* | Daily count of customers busier than their own normal week, minus those quieter - separates platform-wide events from single-customer ones. With the tenant filter on exactly one customer it retitles to *Busy day across &lt;customer&gt;, or just one rule?* and counts that customer's rules instead |
| Rules that move together *(Signals tab)* | Daily-volume correlation between the busiest rules - dedup candidates |
| Equity curve *(Signals tab)* | Running true positives minus benign positives for a rule, with drawdown from its high-water mark |
| Which readings actually move the odds *(Signals tab)* | Every indicator scored the same way: how far each reading shifts the true-positive rate **against the same rule's own average**, so an indicator cannot score by proxying for rule identity. Row length = worst-to-best spread; minimum verdicts per reading is selectable |
| A losing streak keeps losing *(Signals tab)* | Verdict mix by how many benign positives the same customer + rule produced in a row before this firing |
| Does the last verdict keep working? *(Signals tab)* | Rolling 14-day true-positive rate split by the previous verdict on the same customer + rule, against the overall rate |
| Verdict mix by hour of day *(Signals tab)* | Verdict mix per UTC hour of decision |
| Busy days against quiet days *(Signals tab)* | Verdict mix by that day's breadth reading |
| Where the true positives actually come from *(Signals tab)* | Concentration curve: rules ordered best-yield first, cumulative share of workload against cumulative share of true positives, with the equal-yield diagonal. **No time** |
| What the last verdict is worth, rule by rule *(Signals tab)* | Per rule: true-positive rate after a true positive against after a benign positive, connected as one row, ranked by the gap. **No time** |
| Rules that never change their mind *(Signals tab)* | Per rule: share of firings whose verdict differed from the previous one for the same customer, against pair count, shaded by true-positive rate. Flip rate 0 = auto-closure candidate. **No time** |
| Rules that share a customer base *(Signals tab)* | Jaccard overlap of the customer sets two rules run for - the deployment twin of the daily-volume correlation. **No time** |
| The same alert, different desks *(Signals tab)* | Closing role scored against the true-positive rate of the same rule at the same customer, restricted to rule+customer combos two desks both worked. Label noise, not a scoreboard. **No time** |
| Last verdict predicts the next *(Modeling tab)* | Transition matrix at **alert level** - previous verdict for the same tenant + container name. The By customer tab pairs at rule level instead |
| Verdict mix over time *(Modeling tab)* | Target drift per period - decides random vs time-based split and retraining cadence |
| Alert title carries signal *(Modeling tab)* | True-positive rate by the tail of the container name, against the base rate |
| Analysts disagree with each other *(Modeling tab)* | Share of recurring alerts that drew conflicting verdicts, per rule - the accuracy ceiling |
| Table view | Per-rule aggregate of the current slice - the readable twin of every chart |

## Indicator scoring

Every reading on the Signals tab is scored as **within-rule lift**: the mean of
`row is a true positive - that rule's own true-positive rate`, in percentage
points. The control matters. Raw spreads make several indicators look strong when
they are only correlated with *which rule fired* - a rule's volume z-score shows a
25.2% vs 8.0% raw spread but only ±2pp once the rule's own rate is removed.

Measured over the full 90 days, 8,173 verdicts, readings with 50+ verdicts:

| Indicator | Worst-to-best spread |
| --- | --- |
| What it was ruled last time | 22.8 pts |
| Benign positives in a row before it | 15.0 pts |
| When it landed (session) | 5.0 pts |
| How broad the day was | 4.3 pts |
| Rule's volume against its own band | 3.9 pts |
| Rule's running score (drawdown) | 3.2 pts |
| Times this customer saw the rule | 2.4 pts |
| Fast average against slow (MA crossover) | 1.0 pts |

Only the first two carry usable signal, and both are path indicators - what this
exact customer + rule pair did on its previous firings. False positive is too rare
(2.4% of verdicts) for any of these to predict.

The readings are computed once per slice in `_indicator_frame`, `lru_cache`d and
shared by all five panels; callers treat the frame as read-only.

## Time dependence

`_time` is the only timestamp in the extract - there is no separate decision
timestamp. It is the **arrival** time: containers marked duplicate, which no
analyst ever touches, have nearly the same hour-of-day profile as worked ones
(r = 0.816, both peaking 13:00-20:00 UTC).

Three panels read clock position: *Verdict mix by hour of day* and the
*"When it landed"* row of the scorecard (Signals), and *When this rule fires*
(By rule). The session effect scores 5.0 pts against rule identity but only
3.5 pts once the benign-streak state is also controlled, and it is one fixed UTC
window applied to 54 tenants - treat it as a staffing profile, not a predictor,
and keep hour and weekday out of a model's feature set.

The five panels marked **No time** above read no date, hour or weekday at all.
Two of them use the *order* firings arrived in, which is not clock position and
does not move when the roster changes.


## `Final_Rule_Name`

A 14th column: the rule each container really came from. It is `rule_name` where that
field is populated, and recovered from `search_name` where it is not:

| Source | Rows |
|---|---|
| `rule_name` kept as-is | 25,526 |
| Looked up - the `rule_name` other rows carry for the same `search_name` | 1,130 |
| Derived - category prefix and ` - Rule` / ` - Rule Clone` suffix stripped off `search_name` | 384 |
| Still `(unattributed)` - no rule and no search name | 14 |

Every chart groups by this column (`data.RULE_COL`); set it back to `"rule_name"` there
to chart the raw field instead.

## `Final_Disposition`

A 13th column added to the CSV, rolling `closure_disposition` up to four verdicts:

| Final_Disposition | From | Rows |
|---|---|---|
| Benign Positive | Benign Positive - Suspicious But Expected | 7,715 |
| True Positive | True Positive - Suspicious Activity + Undetermined | 2,686 |
| False Positive | all three False Positive variants | 240 |
| Other | Other | 144 |
| *(blank)* | no disposition recorded (duplicates, open, preparing) | 16,269 |

The original twelve columns are untouched - the value is appended to each line as
written, so the source formatting and quoting are preserved byte for byte.

## Normalisations (applied in `data.py`, stated in the app footer)

* `_time` literal `"None"` -> NaT (12 rows); those rows fall outside date filtering.
* `rule_name` `""` / `"None"` -> `(unattributed)` (1,528 rows).
* `closure_disposition` typo `"False Positive - Incorrect analyatical logic"` folded
  into `"False Positive - Incorrect Analytic Logic"`; `""` / `"None"` -> `(none)`.
* Recurrence gaps are computed on the full non-duplicate set *before* filtering, so a
  prior occurrence still counts when the current filter hides it.

## Colour

Palettes come from the dataviz reference palette and were checked with its
validator (`scripts/validate_palette.js`) rather than by eye:

| Set | Modes | Result |
|---|---|---|
| 5 categorical slots (container status) | light / dark, adjacent pairs | PASS; light run warns on contrast, so the table view and direct labels carry the values |
| 3 categorical slots (SOAR instance, final disposition) | light / dark, all pairs | PASS |
| 7 categorical slots (rule mix per customer) | light / dark, adjacent pairs | PASS; the tail folds into a neutral "Other rules" rather than an 8th generated hue |
| 5-step ordinal ramp (recurrence buckets) | light / dark | PASS |

Sequential magnitude (the heatmap) is one hue, light to dark, with a scale legend.
Status colours are never reused as series colours, and a hue always belongs to an
entity, so filtering never repaints the survivors.
