import json
from collections import Counter
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from app.config import settings

router = APIRouter(tags=["dashboard"])

LOG_FILE = Path(__file__).resolve().parent.parent.parent / "logs" / "dedup.jsonl"
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "dashboard.html"


def _load_logs() -> list[dict]:
    if not LOG_FILE.exists():
        return []
    entries = []
    with open(LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return entries


def _aggregate(entries: list[dict]) -> dict:
    total = len(entries)
    verdicts = Counter(e["verdict"] for e in entries)
    layers = Counter(str(e["layer"]) for e in entries)

    # Severity x Verdict
    severity_verdicts: dict[str, dict[str, int]] = {}
    for e in entries:
        sev = e.get("severity", "UNKNOWN") or "UNKNOWN"
        v = e["verdict"]
        severity_verdicts.setdefault(sev, {"DUPLICATE": 0, "SIMILAR": 0, "NEW": 0})
        severity_verdicts[sev][v] = severity_verdicts[sev].get(v, 0) + 1

    # Confidence distribution — buckets from config (based on matched/total slots)
    bucket_values = [float(b) for b in settings.layer1_confidence_buckets]
    confidence_buckets = {b: 0 for b in settings.layer1_confidence_buckets}
    for e in entries:
        c = round(e.get("hash_confidence", e.get("confidence", e.get("hash_confidence", 0))) * 100, 1)
        # Find the closest bucket
        closest = min(bucket_values, key=lambda b: abs(b - c))
        confidence_buckets[str(closest)] += 1

    # Slot match/miss frequency — all 8 slots from config
    slot_stats: dict[str, dict[str, int]] = {
        s: {"matched": 0, "missed": 0} for s in settings.layer1_slots
    }
    for e in entries:
        for s in e.get("matched_slots", []):
            if s in slot_stats:
                slot_stats[s]["matched"] += 1
        for s in e.get("missed_slots", []):
            if s in slot_stats:
                slot_stats[s]["missed"] += 1

    # Top rules
    rule_stats: dict[str, dict[str, int]] = {}
    for e in entries:
        rule = e.get("rule", "") or ""
        if not rule:
            continue
        rule_stats.setdefault(rule, {"count": 0, "DUPLICATE": 0, "NEW": 0})
        rule_stats[rule]["count"] += 1
        rule_stats[rule][e["verdict"]] = rule_stats[rule].get(e["verdict"], 0) + 1
    top_rules = sorted(
        [{"rule": r, **s} for r, s in rule_stats.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:15]

    # Data quality — alerts with no IOCs
    data_quality_count = sum(
        1 for e in entries
        if not e.get("filled_slots")
    )

    # All alerts (newest first)
    all_alerts = list(reversed(entries))

    return {
        "total": total,
        "verdicts": dict(verdicts),
        "layers": dict(layers),
        "severity_verdicts": severity_verdicts,
        "confidence_buckets": confidence_buckets,
        "slot_stats": slot_stats,
        "top_rules": top_rules,
        "data_quality_count": data_quality_count,
        "all_alerts": all_alerts,
        # Actual configured decision thresholds — shown in the "why" panel so
        # analysts see the real cutoffs the engine applied, not hardcoded values.
        "thresholds": {
            "layer1_confidence": round(settings.layer1_confidence_threshold * 100, 1),
            "layer2_fuzzy": settings.layer2_similarity_threshold,
        },
    }


@router.get("/api/v1/dashboard/data")
async def dashboard_data():
    entries = _load_logs()
    return _aggregate(entries)


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return TEMPLATE.read_text(encoding="utf-8")
