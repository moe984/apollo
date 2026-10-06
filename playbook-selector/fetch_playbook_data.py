"""
Fetch Playbook Selector inference telemetry from the COSMOS API.

Usage:
    python fetch_playbook_data.py                                    # first page, 200 events
    python fetch_playbook_data.py --all                              # fetch ALL events (can be large!)
    python fetch_playbook_data.py --all --max 2000                   # fetch up to 2000 events
    python fetch_playbook_data.py --all --customer cust-acme-corp-002
    python fetch_playbook_data.py --all --max 5000 --output data.json
    python fetch_playbook_data.py --page 2 --page-size 200
"""

import argparse
import json
import sys
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode

BASE_URL = "https://d37lzn65ul43gr.cloudfront.net/api/cosmos/history"
HEADERS = {"X-CIP-Service": "sp-intelligence"}


def fetch_page(page: int = 1, page_size: int = 200, customer_id: str | None = None) -> dict:
    params = {
        "model_name": "playbook_selector",
        "page": str(page),
        "page_size": str(page_size),
    }
    if customer_id:
        params["customer_id"] = customer_id

    url = f"{BASE_URL}?{urlencode(params)}"
    req = Request(url, headers=HEADERS)

    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"HTTP {e.code}: {e.read().decode()}", file=sys.stderr)
        sys.exit(1)
    except URLError as e:
        print(f"Connection error: {e.reason}", file=sys.stderr)
        sys.exit(1)


def fetch_all(customer_id: str | None = None, max_events: int | None = None) -> list[dict]:
    all_events = []
    page = 1
    page_size = 200  # API max

    while True:
        data = fetch_page(page=page, page_size=page_size, customer_id=customer_id)
        events = data.get("events", [])
        total = data.get("total_count", 0)
        has_more = data.get("has_more", False)

        all_events.extend(events)
        print(f"  Page {page}: fetched {len(events)} events (total so far: {len(all_events)}/{total})", file=sys.stderr)

        if max_events and len(all_events) >= max_events:
            all_events = all_events[:max_events]
            print(f"  Reached --max {max_events}, stopping.", file=sys.stderr)
            break

        if not has_more or not events:
            break
        page += 1

    return all_events


def print_summary(events: list[dict]) -> None:
    if not events:
        print("No events found.")
        return

    playbooks = {}
    customers = {}
    confidences = []
    latencies = []

    for e in events:
        pv = json.loads(e.get("prediction_value") or "{}")
        pb = pv.get("recommended_playbook", "unknown")
        cust = e.get("customer_id", "")

        playbooks[pb] = playbooks.get(pb, 0) + 1
        customers[cust] = customers.get(cust, 0) + 1

        if e.get("confidence"):
            confidences.append(float(e["confidence"]))
        if e.get("latency_ms"):
            latencies.append(float(e["latency_ms"]))

    print(f"\nTotal events: {len(events)}")
    print(f"Date range:   {events[-1]['timestamp'][:19]}  ->  {events[0]['timestamp'][:19]}")
    print()

    print("Events per customer:")
    for cust, count in sorted(customers.items(), key=lambda x: -x[1]):
        print(f"  {cust:<30} {count:>4} events")
    print()

    print("Recommended playbooks:")
    for pb, count in sorted(playbooks.items(), key=lambda x: -x[1]):
        print(f"  {pb:<40} {count:>4} predictions")
    print()

    if confidences:
        print(f"Confidence:   min={min(confidences):.3f}  avg={sum(confidences)/len(confidences):.3f}  max={max(confidences):.3f}")
    if latencies:
        print(f"Latency (ms): min={min(latencies):.1f}  avg={sum(latencies)/len(latencies):.1f}  max={max(latencies):.1f}")
    print()

    # Show first 50 rows, then truncate
    show = min(len(events), 50)
    print(f"{'Entity':<16} {'Customer':<24} {'Playbook':<35} {'Conf':>6} {'Latency':>10}")
    print("-" * 95)
    for e in events[:show]:
        pv = json.loads(e.get("prediction_value") or "{}")
        print(
            f"{e['entity_id']:<16} "
            f"{e['customer_id']:<24} "
            f"{pv.get('recommended_playbook', 'N/A'):<35} "
            f"{float(e.get('confidence', 0)):>6.3f} "
            f"{float(e.get('latency_ms', 0)):>8.1f}ms"
        )
    if len(events) > show:
        print(f"  ... and {len(events) - show} more rows (use --output to save all)")


def main():
    parser = argparse.ArgumentParser(description="Fetch Playbook Selector data from COSMOS API")
    parser.add_argument("--all", action="store_true", help="Fetch all pages (auto-paginate)")
    parser.add_argument("--max", type=int, default=None, help="Max events to fetch when using --all (default: unlimited)")
    parser.add_argument("--page", type=int, default=1, help="Page number (default: 1)")
    parser.add_argument("--page-size", type=int, default=200, help="Events per page, max 200 (default: 200)")
    parser.add_argument("--customer", type=str, default=None, help="Filter by customer ID")
    parser.add_argument("--output", type=str, default=None, help="Save raw JSON to file")
    parser.add_argument("--json", action="store_true", help="Print raw JSON instead of summary")
    args = parser.parse_args()

    if args.all:
        events = fetch_all(customer_id=args.customer, max_events=args.max)
    else:
        data = fetch_page(page=args.page, page_size=args.page_size, customer_id=args.customer)
        events = data.get("events", [])
        total = data.get("total_count", 0)
        has_more = data.get("has_more", False)
        print(f"  Page {args.page}: {len(events)} events (total: {total}, has_more: {has_more})", file=sys.stderr)

    if args.output:
        with open(args.output, "w") as f:
            json.dump({"events": events, "count": len(events)}, f, indent=2)
        print(f"Saved {len(events)} events to {args.output}", file=sys.stderr)

    if args.json:
        print(json.dumps({"events": events, "count": len(events)}, indent=2))
    else:
        print_summary(events)


if __name__ == "__main__":
    main()
