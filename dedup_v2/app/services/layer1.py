"""
Layer 1 — Two-stage comparison and confidence scoring.

Stage 1: Prerequisites (alert_name + time_bucket) must match — quick screen.
Stage 2: IOC slots compared for confidence scoring.

Confidence = matched_ioc_slots / total_filled_ioc_slots (prerequisites excluded).
"""

from dataclasses import dataclass


@dataclass
class Layer1Result:
    is_duplicate: bool
    confidence: float
    matched_alert_id: str
    filled_slots: list[str]
    matched_slots: list[str]
    missed_slots: list[str]


def compare_iocs(incoming_ioc_hashes: dict[str, str], stored_ioc_hashes: dict[str, str]) -> dict:
    """Compare IOC hash arrays slot-by-slot. Prerequisites are NOT included.

    Returns a dict with:
      - filled: list of IOC slot types present in incoming
      - matched: list of IOC slot types that matched
      - missed: list of IOC slot types that did not match
      - confidence: matched / total IOC slots
    """
    filled = list(incoming_ioc_hashes.keys())
    matched = []
    missed = []

    for slot_type, slot_hash in incoming_ioc_hashes.items():
        stored_hash = stored_ioc_hashes.get(slot_type)
        if stored_hash == slot_hash:
            matched.append(slot_type)
        else:
            missed.append(slot_type)

    # Check if stored has IOC slots that incoming doesn't
    for slot_type in stored_ioc_hashes:
        if slot_type not in incoming_ioc_hashes:
            missed.append(slot_type)

    total = len(filled) + len([s for s in stored_ioc_hashes if s not in incoming_ioc_hashes])
    confidence = len(matched) / total if total > 0 else 0.0

    return {
        "filled": filled,
        "matched": matched,
        "missed": missed,
        "confidence": round(confidence, 4),
    }


def find_best_match(
    incoming_ioc_hashes: dict[str, str],
    candidates: list[tuple[str, dict, str]],
    threshold: float,
) -> Layer1Result | None:
    """Find the best matching candidate above the confidence threshold.

    Prerequisites have already been verified — this only compares IOC slots.

    When multiple candidates have the same confidence, picks the earliest
    one (by timestamp) to ensure duplicates always point to the original parent.

    Args:
        incoming_ioc_hashes: IOC hash array of the incoming alert (no prerequisites)
        candidates: list of (alert_id, stored_ioc_hashes, timestamp) tuples
        threshold: minimum confidence to consider a match

    Returns:
        Layer1Result if a match is found above threshold, None otherwise.
    """
    best: Layer1Result | None = None
    best_confidence = 0.0
    best_timestamp = ""

    for alert_id, stored_ioc_hashes, timestamp in candidates:
        result = compare_iocs(incoming_ioc_hashes, stored_ioc_hashes)
        confidence = result["confidence"]

        if confidence < threshold:
            continue

        is_better = confidence > best_confidence
        is_same_but_earlier = confidence == best_confidence and timestamp < best_timestamp

        if is_better or is_same_but_earlier:
            best_confidence = confidence
            best_timestamp = timestamp
            best = Layer1Result(
                is_duplicate=True,
                confidence=confidence,
                matched_alert_id=alert_id,
                filled_slots=result["filled"],
                matched_slots=result["matched"],
                missed_slots=result["missed"],
            )

    return best
