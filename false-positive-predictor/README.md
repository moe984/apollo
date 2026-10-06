# False Positive Predictor — Data Pipeline

Fetch and preprocess LSU Splunk alerts from Apollo's production database for ML training.

## Prerequisites

- `gh` CLI installed and authenticated (`brew install gh && gh auth login`)
- Python 3.10+

## Quick Start

```bash
# 1. Fetch fresh data from Apollo DB
python fetch_alerts.py

# 2. Preprocess into flat ML-ready CSV
python preprocess.py
```

## fetch_alerts.py

Triggers a GitHub Actions workflow that runs inside Apollo's VPC, queries Postgres, and downloads the result as a CSV artifact. No AWS credentials or VPN needed.

```bash
python fetch_alerts.py                                # default: CUST-LSUAM, unlabeled
python fetch_alerts.py --customer CUST-ULM            # different customer
python fetch_alerts.py --mode triaged                 # alerts + LLM triage results
python fetch_alerts.py --mode labeled                 # alerts + analyst feedback (ground truth)
python fetch_alerts.py --output my_data.csv           # custom output filename
python fetch_alerts.py --run-id 25507058358           # re-download from an existing run
```

| Mode | What it returns |
|------|----------------|
| `unlabeled` | Raw alerts with Splunk payloads and STIX bundles |
| `triaged` | Above + fp_score, disposition, confidence, rationale |
| `labeled` | Above + analyst is_false_positive verdict |

## preprocess.py

Expands the nested JSON columns (`stix_features`, `splunk_features`, `derived_features`) into 76 flat columns.

```bash
python preprocess.py                                  # auto-finds latest fetched CSV
python preprocess.py --input my_data.csv              # specific input file
python preprocess.py --output clean.csv               # custom output filename
```

Output includes:
- Alert metadata (name, search_name, rule_description, security_domain)
- MITRE ATT&CK techniques
- Network observables (source/dest IP, ports)
- User/identity fields
- Defender alert fields
- Geo data (country, city, lat/lon)
- IOC and file/process indicators
- Engineered features (severity_numeric, alert_category, hour_of_day, binary flags)
