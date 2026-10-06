# Feature Engineering — eng_ columns

All `eng_` columns are computed by `preprocess.py`. Each one derives a new value that does not exist in the source data.

## Severity

| Feature | Source Column | Logic |
|---------|--------------|-------|
| `eng_severity_numeric` | `stix_apollo_severity` | Map to int: critical=4, high=3, medium=2, low=1, info=0 |

## Classification

| Feature | Source Column | Logic |
|---------|--------------|-------|
| `eng_alert_category` | `stix_search_name` | Keyword match: "Brute Force"→brute_force, "Port scan"/"Canary"→port_scan, "Failed Login"→failed_login, "Suspicious"→suspicious_activity, "Defender"→defender_alert, "Heartbeat"→heartbeat, "Risk"→risk_threshold, "Added to a role"/"Rights Delegation"→identity_change, "Login from Suspicious"→suspicious_login, else→other |
| `eng_detection_category` | `stix_alert_name` | First word before " - ", lowercased. e.g. "Threat - LSU - ..." → "threat". No match → "unclassified" |
| `eng_detection_domain` | `stix_search_name` | Exact rule name match against three lists: Identity (Azure login, brute force, O365, role changes), Endpoint (Defender alerts), Network (port scan, Palo Alto). No match → "Unclassified" |

## IOC

| Feature | Source Column | Logic |
|---------|--------------|-------|
| `eng_ioc_type` | `splunk_ioc` | Classify IOC value: starts with http→URL, contains @→Email, hex 32-128 chars→Hash, contains :→IPv6, matches digit pattern→IPv4, else→Other. Empty if no IOC. |

## Presence Flags

| Feature | Source Column | Logic |
|---------|--------------|-------|
| `eng_has_source_ip` | `splunk_source_address` | 1 if non-empty, else 0 |
| `eng_has_dest_ip` | `splunk_destination_address` | 1 if non-empty, else 0 |
| `eng_has_user` | `splunk_user` | 1 if non-empty, else 0 |
| `eng_has_ioc` | `splunk_ioc` | 1 if non-empty, else 0 |
| `eng_has_geo` | `splunk_country` | 1 if non-empty, else 0 |
| `eng_has_file_hash` | `splunk_file_hash_sha256` | 1 if non-empty, else 0 |

## Temporal

| Feature | Source Column | Logic |
|---------|--------------|-------|
| `eng_event_date` | `splunk_time` | Parse "2026-05-07 10:01:36.000 CDT", extract date as "2026-05-07" |
| `eng_hour_of_day` | `splunk_time` | Parse, extract hour (0-23) |
| `eng_day_of_week` | `splunk_time` | Parse, extract weekday (0=Mon, 6=Sun) |
| `eng_is_business_hours` | `splunk_time` | 1 if hour 8-17 and Mon-Fri, else 0 |
