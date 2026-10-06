"""Send real alerts to the CIP alert-NER endpoint and see what comes back.

A sample, not a pipeline. The question it answers: **can NER usefully type the
entities in our alerts, and in what shape does the text need to be?**

Three shapes of the same alert are sent, because the endpoint's own capture of a
SOAR CEF blob came back with `USERNAME` hits on "un", "medium", "high" and
"igned" - fragments of key names and severity values. The model is trained on
alert prose; a `key: value` dump is out of distribution for it.

    api_json    the notable artifact as JSON - the endpoint renders it itself
    kv_text     "key: value" lines, what `text_cef` is built from
    values      values only, no keys - closest to prose

    python3 sample_ner.py            # 3 alerts, all three shapes
    python3 sample_ner.py --n 10 --shape values
"""
from __future__ import annotations

import argparse
import json
import pathlib
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ML = HERE.parent / "machine-learning"
ENV = pathlib.Path("/Users/mohammad.yekrangian/Projects/poc/cip/.env")
BASE = "https://cosmos.tekstream.com"

# Entity types we trust. At a 0.999 confidence floor these eight were right on
# 164 of 164 spans across 30 alerts. USERNAME is excluded: it is the most
# frequent label (126 of 290) and about half its spans are CEF field names and
# severity words - `user`, `medium`, `normal`, `system` - scoring 0.9993-0.9999.
# Raising the floor does not help, because the model is confidently wrong rather
# than uncertain. The username is better read from the CEF's own explicit keys
# (`destinationUserName`, `userPrincipalName`, `accountName`) than inferred.
TRUST = {"EMAIL", "IPV4", "IPV6", "MACHINE", "FILEPATH", "FILENAME", "HASH", "URL"}
MIN_SCORE = 0.999

# never sent to the endpoint: they are Splunk plumbing and per-alert ids, and
# they are what produced the junk USERNAME hits in the existing capture
SKIP = {"_bkt", "_cd", "_si", "_indextime", "_sourcetype", "_time", "_eventtype_color",
        "event_id", "rule_id", "orig_sid", "orig_rid", "source_event_id", "source_guid",
        "info_max_time", "info_min_time", "info_search_time", "timeendpos", "timestartpos",
        "investigation_profiles", "extract_artifacts", "contributing_events_search"}


def api_key() -> str:
    for line in ENV.read_text().splitlines():
        if line.startswith("CIP_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(f"CIP_API_KEY not found in {ENV}")


def call(path: str, body: dict | None = None, method: str = "POST") -> tuple[int, dict, float]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("X-API-Key", api_key())
    if data:
        req.add_header("Content-Type", "application/json")
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            status, text = resp.status, resp.read().decode()
    except urllib.error.HTTPError as exc:
        status, text = exc.code, exc.read().decode()
    ms = (time.perf_counter() - start) * 1000
    try:
        return status, json.loads(text), ms
    except json.JSONDecodeError:
        return status, {"raw": text[:400]}, ms


def shapes(notable: dict) -> dict[str, str]:
    """The same alert, three ways."""
    clean = {k: v for k, v in notable.items() if k not in SKIP}
    kv = "\n".join(f"{k}: {v}" for k, v in sorted(clean.items()))
    vals = " ".join(str(v) for _, v in sorted(clean.items()))
    return {"api_json": json.dumps(clean), "kv_text": kv, "values": vals}


def sample_alerts(n: int):
    import csv, sys
    csv.field_size_limit(sys.maxsize)
    path = ML / "training-data" / "original.csv"
    out = []
    with path.open() as fh:
        for row in csv.DictReader(fh):
            if row["has_notable"] == "True" and row["notable_json"]:
                out.append((row["container_id"], row["disposition"],
                            json.loads(row["notable_json"])))
            if len(out) >= n:
                break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--shape", default="all",
                    choices=["all", "api_json", "kv_text", "values"])
    ap.add_argument("--min-score", type=float, default=MIN_SCORE,
                    help="keep only entities at or above this confidence")
    ap.add_argument("--all-labels", action="store_true",
                    help="include USERNAME, which is ~50%% junk; off by default")
    args = ap.parse_args()

    status, body, ms = call("/api/ner/status", method="GET")
    print(f"status {status} in {ms:.0f} ms · model {body.get('model_name')} · "
          f"ready {body.get('ready')}")
    print(f"  entity types: {', '.join(body.get('entity_types', []))}")
    print(f"  max_len {body.get('max_len')} · windows every "
          f"{body.get('window_chars')} chars\n")

    captures = []
    for cid, dispo, notable in sample_alerts(args.n):
        forms = shapes(notable)
        wanted = forms if args.shape == "all" else {args.shape: forms[args.shape]}
        print(f"── {cid}  ({dispo})  {len(notable)} keys")
        for name, text in wanted.items():
            code, resp, ms = call("/api/ner/extract", {"text": text})
            if code != 200:
                print(f"   {name:9s} HTTP {code}: {str(resp)[:120]}")
                continue
            # filter on the per-occurrence list, not by_type, so the threshold
            # applies to the score the model actually emitted for that span
            trusted = None if args.all_labels else TRUST
            ents = [e for e in (resp.get("entities") or [])
                    if e.get("score", 0) >= args.min_score
                    and (trusted is None or e.get("label") in trusted)]
            by_type: dict[str, list] = {}
            for e in ents:
                seen = by_type.setdefault(e["label"], [])
                if not any(x["value"] == e["value"] for x in seen):
                    seen.append({"value": e["value"], "score": e["score"]})
            raw_n = resp.get("entity_count") or 0
            print(f"   {name:9s} {len(text):6,d} chars · {resp.get('windows')} windows · "
                  f"{ms:7.0f} ms · {len(ents)} kept of {raw_n} entities")
            for label in sorted(by_type):
                vals = [f"{e['value']}({e['score']:.4f})" for e in by_type[label][:5]]
                print(f"       {label:9s} {', '.join(vals)}")
            captures.append({"container_id": cid, "disposition": dispo, "shape": name,
                             "chars": len(text), "latency_ms": round(ms, 1),
                             "entity_count_raw": resp.get("entity_count"),
                             "entity_count_kept": len(ents),
                             "min_score": args.min_score,
                             "by_type": by_type})
        print()

    out = HERE / "sample_output.json"
    out.write_text(json.dumps(captures, indent=2) + "\n")
    print(f"captures -> {out.relative_to(HERE.parent)}")
    if captures:
        avg = sum(c["latency_ms"] for c in captures) / len(captures)
        print(f"mean latency {avg:,.0f} ms · 32,427 alerts would take "
              f"{avg * 32427 / 3_600_000:,.1f} hours serially")


if __name__ == "__main__":
    main()
