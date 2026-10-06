"""
STIX Equivalence — Cross-validation of dedup confidence.

Two separate similarity scores:

1. STIX Object similarity — threat context:
   indicator, identity, attack-pattern
   Uses the stix2 library's object_similarity().

2. STIX Observable similarity — IOC comparison:
   Compares parsed IOC values (from parser output, not raw STIX objects).
   This ensures both hash confidence and observable similarity
   look at the same data — no gap from vendor-specific notable fields.
"""

from stix2 import Environment, parse

_env = Environment()

# Threat context objects (supported by stix2 library)
CONTEXT_TYPES = {"indicator", "identity", "attack-pattern"}

# IOC slot types used for observable comparison (same as hash confidence)
OBSERVABLE_IOC_TYPES = ["ip", "user", "email", "domain", "url", "file_hash"]


def compare_bundles(
    incoming_bundle: dict,
    stored_bundle: dict,
    incoming_iocs: dict[str, list[str]],
    stored_iocs: dict[str, list[str]],
) -> dict:
    """Compare two alerts and return both context and observable scores.

    Args:
        incoming_bundle: raw STIX bundle (for context comparison via stix2 library)
        stored_bundle: raw STIX bundle (for context comparison via stix2 library)
        incoming_iocs: parsed IOC dict from parser (for observable comparison)
        stored_iocs: parsed IOC dict from parser (for observable comparison)

    Returns:
        dict with context and observable scores and details.
    """
    context = _compare_context(incoming_bundle, stored_bundle)
    observable = _compare_observables(incoming_iocs, stored_iocs)

    return {
        "context_score": context["overall_score"],
        "context_details": context["object_scores"],
        "context_objects_compared": context["objects_compared"],
        "observable_score_by_type": observable["score_by_type"],
        "observable_score_by_value": observable["score_by_value"],
        "observable_details": observable["object_scores"],
        "observable_objects_compared": observable["objects_compared"],
        "observable_values_matched": observable["values_matched"],
        "observable_values_total": observable["values_total"],
    }


def _compare_context(incoming_bundle: dict, stored_bundle: dict) -> dict:
    """Compare STIX context objects using the stix2 library's object_similarity()."""
    incoming_by_type = _group_by_type(incoming_bundle, CONTEXT_TYPES)
    stored_by_type = _group_by_type(stored_bundle, CONTEXT_TYPES)

    object_scores = []
    total_score = 0.0
    total_compared = 0

    for obj_type in CONTEXT_TYPES:
        incoming_objs = incoming_by_type.get(obj_type, [])
        stored_objs = stored_by_type.get(obj_type, [])

        if not incoming_objs or not stored_objs:
            continue

        for inc_raw in incoming_objs:
            best_score = 0.0
            best_details = {}

            for sto_raw in stored_objs:
                try:
                    inc_obj = parse(inc_raw, allow_custom=True)
                    sto_obj = parse(sto_raw, allow_custom=True)
                    prop_scores = {}
                    score = _env.object_similarity(inc_obj, sto_obj, prop_scores=prop_scores)

                    if score > best_score:
                        best_score = score
                        best_details = {
                            k: v for k, v in prop_scores.items()
                            if k not in ("matching_score", "sum_weights")
                        }
                except Exception:
                    continue

            if best_score > 0 or best_details:
                object_scores.append({
                    "type": obj_type,
                    "score": round(best_score, 2),
                    "details": best_details,
                })
                total_score += best_score
                total_compared += 1

    overall_score = round(total_score / total_compared, 2) if total_compared > 0 else 0.0

    return {
        "overall_score": overall_score,
        "object_scores": object_scores,
        "objects_compared": total_compared,
    }


def _compare_observables(
    incoming_iocs: dict[str, list[str]],
    stored_iocs: dict[str, list[str]],
) -> dict:
    """Compare parsed IOC values per type using set intersection.

    Uses the same parsed data as the hash confidence — no gap from
    vendor-specific notable fields.

    Returns two scores:
      - score_by_type: average per-type similarity (which IOC types matched)
      - score_by_value: overall value-level similarity (how much data matched)
    """
    object_scores = []
    total_type_score = 0.0
    total_types_compared = 0

    # For per-value counting across all types
    all_matched = 0
    all_union = 0

    # Check all IOC types that are present in either alert
    all_types = set(incoming_iocs.keys()) | set(stored_iocs.keys())

    for ioc_type in sorted(all_types):
        incoming_values = set(v.lower() for v in incoming_iocs.get(ioc_type, []))
        stored_values = set(v.lower() for v in stored_iocs.get(ioc_type, []))

        if not incoming_values and not stored_values:
            continue

        intersection = incoming_values & stored_values
        union = incoming_values | stored_values

        type_score = (len(intersection) / len(union) * 100) if union else 0.0

        object_scores.append({
            "type": ioc_type,
            "score": round(type_score, 2),
            "incoming_values": sorted(incoming_values),
            "stored_values": sorted(stored_values),
            "matched_values": sorted(intersection),
        })
        total_type_score += type_score
        total_types_compared += 1
        all_matched += len(intersection)
        all_union += len(union)

    score_by_type = round(total_type_score / total_types_compared, 2) if total_types_compared > 0 else 0.0
    score_by_value = round((all_matched / all_union * 100), 2) if all_union > 0 else 0.0

    return {
        "score_by_type": score_by_type,
        "score_by_value": score_by_value,
        "object_scores": object_scores,
        "objects_compared": total_types_compared,
        "values_matched": all_matched,
        "values_total": all_union,
    }


def _group_by_type(bundle: dict, type_set: set[str]) -> dict[str, list[dict]]:
    """Group STIX objects by type, filtering to the given type set."""
    grouped: dict[str, list[dict]] = {}
    for obj in bundle.get("objects", []):
        obj_type = obj.get("type", "")
        if obj_type in type_set:
            grouped.setdefault(obj_type, []).append(obj)
    return grouped
