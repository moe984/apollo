"""
Layer 2 — Fuzzy Matching across different detection rules.

Compares IOC values using Token Sort Ratio (from rapidfuzz).
Only runs on alerts that Layer 1 returned as NEW.
Only compares against alerts with a DIFFERENT alert_name in the same time_bucket.
"""

from dataclasses import dataclass
from rapidfuzz import fuzz


@dataclass
class Layer2Result:
    is_similar: bool
    fuzzy_score: float
    matched_alert_id: str
    per_type_scores: dict[str, float]
    incoming_rule: str
    matched_rule: str


def fuzzy_compare_iocs(
    incoming_iocs: dict[str, list[str]],
    stored_iocs: dict[str, list[str]],
) -> dict[str, float]:
    """Compare IOC values per type using Token Sort Ratio.

    For each IOC type present in either alert:
      - For each incoming value, find the best fuzzy match among stored values
      - Type score = average of best matches

    Returns dict of {ioc_type: score (0-100)}.
    """
    type_scores: dict[str, float] = {}

    all_types = set(incoming_iocs.keys()) | set(stored_iocs.keys())

    for ioc_type in sorted(all_types):
        incoming_values = incoming_iocs.get(ioc_type, [])
        stored_values = stored_iocs.get(ioc_type, [])

        if not incoming_values or not stored_values:
            if incoming_values or stored_values:
                type_scores[ioc_type] = 0.0
            continue

        # For each incoming value, find best fuzzy match
        best_scores = []
        for inc_val in incoming_values:
            best = max(
                fuzz.token_sort_ratio(inc_val.lower(), sto_val.lower())
                for sto_val in stored_values
            )
            best_scores.append(best)

        type_scores[ioc_type] = round(sum(best_scores) / len(best_scores), 2)

    return type_scores


def find_best_fuzzy_match(
    incoming_iocs: dict[str, list[str]],
    incoming_rule: str,
    candidates: list[tuple[str, dict[str, list[str]], str, str]],
    threshold: float,
) -> Layer2Result | None:
    """Find the best fuzzy matching candidate above the threshold.

    Args:
        incoming_iocs: parsed IOCs of incoming alert
        incoming_rule: alert_name of incoming alert
        candidates: list of (alert_id, stored_iocs, alert_name, timestamp) tuples
                    Only candidates with DIFFERENT alert_name are included.
        threshold: minimum fuzzy score (0-100)

    Returns:
        Layer2Result if a match is found above threshold, None otherwise.
    """
    best: Layer2Result | None = None
    best_score = 0.0
    best_timestamp = ""

    for alert_id, stored_iocs, stored_rule, timestamp in candidates:
        # Skip same rule — Layer 1 already handled those
        if stored_rule.strip().lower() == incoming_rule.strip().lower():
            continue

        type_scores = fuzzy_compare_iocs(incoming_iocs, stored_iocs)

        if not type_scores:
            continue

        overall = round(sum(type_scores.values()) / len(type_scores), 2)

        if overall < threshold:
            continue

        # Pick best, earliest on tie
        is_better = overall > best_score
        is_same_but_earlier = overall == best_score and timestamp < best_timestamp

        if is_better or is_same_but_earlier:
            best_score = overall
            best_timestamp = timestamp
            best = Layer2Result(
                is_similar=True,
                fuzzy_score=overall,
                matched_alert_id=alert_id,
                per_type_scores=type_scores,
                incoming_rule=incoming_rule,
                matched_rule=stored_rule,
            )

    return best
