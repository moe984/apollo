# Hash Dedup POC

Tools for auditing Apollo's hash dedup system against production data.

## Prerequisites

- Python 3.10+
- Production CSVs in `../data/` (pulled via `../scripts/fetch_dedup_data.py`)

No external dependencies required. All scripts use the Python standard library.

## Files

### find_fp.py — FP/TP Classification

Reads `dedup_input.csv`, finds all hash-dedup pairs, and classifies each as FP, TP, UNKNOWN, or TEST.

```bash
python find_fp.py
```

**Input:** `../data/dedup_input.csv`
**Output:** `../data/fp_analysis.csv`

Verdict rules:
- **FP** — both IPs empty in the hash AND title, IOC, or account differs between the pair
- **TP** — hash had a real src_ip (the hash had distinguishing data)
- **UNKNOWN** — both IPs empty but no observable difference in the fields we check
- **TEST** — either alert's sourceAlertId starts with `dedup-test-`

Reports at two levels:
- **Per alert** — each individual alert that was wrongly/correctly suppressed
- **Per canonical** — each parent alert that absorbed duplicates (groups)

### visualize_fp.py — Dashboard Generator

Generates `fp_dashboard.html`, a Plotly dashboard that reads from `fp_analysis.csv` at runtime.

```bash
python visualize_fp.py
```

**Output:** `../data/fp_dashboard.html`

To view the dashboard (requires a local server because the HTML fetches CSVs):

```bash
cd ../data && python3 -m http.server 8080
# open http://localhost:8080/fp_dashboard.html
```

### engine.py — Hash Dedup Engine

TCP socket server that replicates Apollo's SHA-256 hash dedup algorithm. Use this to test dedup behavior locally without running Apollo.

```bash
python engine.py                  # default port 9800
python engine.py --port 9900      # custom port
```

Logs every step (RECV, DETECT, EXTRACT, KEY_IN, KEY_OUT, MATCH/NEW) as structured JSONL in `logs/`.

### stream.py — Test Alert Streamer

Sends sample alerts to `engine.py` across 7 test scenarios.

```bash
# Start the engine first in another terminal
python engine.py

# Then stream test alerts
python stream.py                    # run all scenarios
python stream.py --scenario 1       # run specific scenario
python stream.py --port 9900        # custom port
```

Scenarios:
1. Exact duplicate (same alert twice)
2. IP subset matching
3. Different categories (should not match)
4. IP ordering (sorted dest IPs)
5. CrowdStrike format
6. Splunk format
7. Sentinel weak fingerprint

### audit_fp.py — Earlier FP Auditor (superseded)

Earlier version of the FP analysis. Produces `audit_report.json` and `audit_report.txt`. Superseded by `find_fp.py` which adds the verdict column and per-canonical grouping.

```bash
python audit_fp.py
```

## Typical Workflow

```bash
# 1. Pull production data (if not already done)
cd ../scripts && python fetch_dedup_data.py

# 2. Run FP classification
python find_fp.py

# 3. Generate dashboard
python visualize_fp.py

# 4. Serve and view
cd ../data && python3 -m http.server 8080
```
