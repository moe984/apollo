"""
Convert all_customers.json to CSV — all fields included, nested JSON flattened.

Usage:
    python convert_to_csv.py
    python convert_to_csv.py --input other_file.json --output other_file.csv
    python convert_to_csv.py --drop-null       # exclude columns that are null in every row
"""

import argparse
import csv
import json
import sys


def flatten_event(event: dict) -> dict:
    """Flatten an event dict — unpack prediction_value and metadata JSON strings."""
    row = {}
    for key, value in event.items():
        if key == "prediction_value" and isinstance(value, str):
            try:
                pv = json.loads(value)
                for pk, pv_val in pv.items():
                    row[f"prediction_{pk}"] = pv_val
            except (json.JSONDecodeError, TypeError):
                row["prediction_value_raw"] = value
        elif key == "metadata" and isinstance(value, str):
            try:
                meta = json.loads(value)
                for mk, mv in meta.items():
                    row[f"metadata_{mk}"] = mv
            except (json.JSONDecodeError, TypeError):
                row["metadata_raw"] = value
        else:
            row[key] = value

    return row


def convert(input_path: str, output_path: str, drop_null: bool = False) -> int:
    with open(input_path) as f:
        data = json.load(f)

    events = data.get("events", data) if isinstance(data, dict) else data

    if not events:
        print("No events found.", file=sys.stderr)
        return 0

    # Flatten all events
    rows = [flatten_event(e) for e in events]

    # Collect all columns in stable order (preserves first-seen order)
    all_columns = []
    seen = set()
    for row in rows:
        for key in row:
            if key not in seen:
                all_columns.append(key)
                seen.add(key)

    # Optionally drop columns that are None/null in every row
    if drop_null:
        non_null_cols = []
        for col in all_columns:
            if any(row.get(col) is not None for row in rows):
                non_null_cols.append(col)
            else:
                pass  # drop this column
        dropped = len(all_columns) - len(non_null_cols)
        all_columns = non_null_cols
        print(f"Dropped {dropped} all-null columns", file=sys.stderr)

    # Write CSV
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=all_columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Replace None with empty string for clean CSV
            clean = {k: ("" if row.get(k) is None else row.get(k)) for k in all_columns}
            writer.writerow(clean)

    return len(rows)


def main():
    parser = argparse.ArgumentParser(description="Convert playbook JSON to CSV")
    parser.add_argument("--input", default="all_customers.json", help="Input JSON file")
    parser.add_argument("--output", default="all_customers.csv", help="Output CSV file")
    parser.add_argument("--drop-null", action="store_true", help="Exclude columns that are null in every row")
    args = parser.parse_args()

    count = convert(args.input, args.output, drop_null=args.drop_null)
    print(f"Converted {count} events -> {args.output}")


if __name__ == "__main__":
    main()
