"""
SOC Alert Dedup Engine (async, Redis-backed)

Layer 1: Two-stage dedup with STIX equivalence cross-validation
Layer 2: Fuzzy matching across different detection rules

All errors are caught, logged to error.jsonl, and the alert returns as NEW.
"""

import time
import traceback as tb

from app.config import settings
from app.logger import log_dedup_result, log_error
from app.services import redis_client
from app.services.ioc_normalizer import extract_iocs
from app.services.stix_parser import parse_stix_bundle
from app.services.fingerprinter import fingerprint
from app.services.layer1 import find_best_match
from app.services.layer2 import find_best_fuzzy_match
from app.services.stix_equivalence import compare_bundles
from app.models import AlertOut


def _error_result(alert_id: str, stage: str, error: str, iocs: dict | None = None) -> AlertOut:
    """Build a NEW result with error info when processing fails."""
    return AlertOut(
        alert_id=alert_id or "unknown",
        verdict="NEW",
        hash_confidence=0.0,
        stix_context_similarity=0.0,
        stix_observable_by_type=0.0,
        stix_observable_by_value=0.0,
        layer=0,
        matched_alert_id="",
        score_breakdown={"error": error, "stage": stage},
        stix_context_details=[],
        stix_observable_details=[],
        iocs=iocs or {},
        filled_slots=[],
        matched_slots=[],
        missed_slots=[],
    )


async def process_alert(
    stix_bundle: dict,
    source: str = "",
    severity: str = "",
    customer_id: str = "",
    alert_id: str | None = None,
    created_at: str | None = None,
) -> AlertOut:
    """Process a single STIX bundle through the dedup engine."""

    iocs_serializable = {}
    all_hashes = {}
    candidate_counts = []
    time_bucket_info = {}
    stix_context_similarity = 0.0
    stix_observable_by_type = 0.0
    stix_observable_by_value = 0.0
    stix_context_details: list[dict] = []
    stix_observable_details: list[dict] = []

    # --- Stage: Parse ---
    try:
        row = {
            "id": alert_id or "",
            "source": source,
            "severity": severity,
            "customer_id": customer_id,
            "created_at": created_at or "",
        }
        alert = parse_stix_bundle(stix_bundle, row)
        if alert_id:
            alert["alert_id"] = alert_id
    except Exception as e:
        log_error(alert_id or "unknown", customer_id, "parse", type(e).__name__, str(e), tb.format_exc(),
                  {"stix_bundle_keys": list(stix_bundle.keys()) if isinstance(stix_bundle, dict) else str(type(stix_bundle))})
        return _error_result(alert_id, "parse", str(e))

    # --- Stage: Extract IOCs ---
    try:
        alert_iocs = extract_iocs(alert)
        iocs_serializable = {k: sorted(v) for k, v in alert_iocs.items()}
    except Exception as e:
        log_error(alert["alert_id"], customer_id, "extract_iocs", type(e).__name__, str(e), tb.format_exc(),
                  {"alert_name": alert.get("alert_name", "")})
        return _error_result(alert["alert_id"], "extract_iocs", str(e))

    # --- Stage: Fingerprint ---
    try:
        ingestion_time = str(time.time())
        prerequisites, ioc_hashes, time_bucket_info = fingerprint(alert_iocs, alert.get("alert_name", ""))
        all_hashes = {**prerequisites, **ioc_hashes}
    except Exception as e:
        log_error(alert["alert_id"], customer_id, "fingerprint", type(e).__name__, str(e), tb.format_exc(),
                  {"ioc_types": list(alert_iocs.keys())})
        return _error_result(alert["alert_id"], "fingerprint", str(e), iocs_serializable)

    # Input validation — zero IOCs = data quality issue
    if not ioc_hashes:
        result = AlertOut(
            alert_id=alert["alert_id"],
            verdict="NEW",
            hash_confidence=0.0,
            stix_context_similarity=0.0,
            stix_observable_by_type=0.0,
            stix_observable_by_value=0.0,
            layer=0,
            matched_alert_id="",
            score_breakdown={"data_quality_warning": "no IOCs extracted"},
            stix_context_details=[],
            stix_observable_details=[],
            iocs=iocs_serializable,
            filled_slots=[],
            matched_slots=[],
            missed_slots=[],
        )
        log_dedup_result(
            alert_id=result.alert_id, customer_id=customer_id, source=source, severity=severity,
            rule=alert.get("alert_name", ""), verdict=result.verdict, confidence=result.hash_confidence,
            layer=result.layer, matched_alert_id="", ioc_counts={}, score_breakdown=result.score_breakdown,
            filled_slots=[], matched_slots=[], missed_slots=[],
            input_iocs=iocs_serializable, input_alert=alert, input_hashes=all_hashes,
            candidate_counts=[], time_bucket_info=time_bucket_info,
        )
        try:
            await redis_client.store_l1_alert(customer_id, alert["alert_id"], all_hashes, ingestion_time)
            await redis_client.index_l1_hashes(customer_id, alert["alert_id"], prerequisites)
            await redis_client.store_l1_bundle(customer_id, alert["alert_id"], stix_bundle, iocs_serializable, alert.get("alert_name", ""))
            await redis_client.store_result(customer_id, alert["alert_id"], result.model_dump())
        except Exception as e:
            log_error(alert["alert_id"], customer_id, "redis_store_dq", type(e).__name__, str(e), tb.format_exc())
        return result

    # --- Stage: Layer 1 — Redis lookup ---
    l1_result = None
    try:
        prerequisite_matches = await redis_client.lookup_l1_prerequisites(customer_id, prerequisites)
        candidate_counts = await redis_client.lookup_l1_ioc_candidates(customer_id, ioc_hashes, prerequisite_matches)

        if candidate_counts:
            total_ioc_slots = len(ioc_hashes)
            candidates_to_compare = []
            for cand_id, count in candidate_counts:
                if count / total_ioc_slots >= settings.layer1_confidence_threshold:
                    stored = await redis_client.get_l1_alert(customer_id, cand_id)
                    if stored:
                        stored_ioc_hashes = {
                            k: v for k, v in stored["hashes"].items()
                            if k not in ("alert_name", "time_bucket")
                        }
                        candidates_to_compare.append((cand_id, stored_ioc_hashes, stored["timestamp"]))

            if candidates_to_compare:
                l1_result = find_best_match(ioc_hashes, candidates_to_compare, settings.layer1_confidence_threshold)
    except Exception as e:
        log_error(alert["alert_id"], customer_id, "layer1_lookup", type(e).__name__, str(e), tb.format_exc(),
                  {"ioc_slot_count": len(ioc_hashes)})

    # --- Stage: STIX cross-validation (Layer 1) ---
    if l1_result and settings.stix_equivalence_enabled:
        try:
            stored_data = await redis_client.get_l1_bundle(customer_id, l1_result.matched_alert_id)
            if stored_data:
                equiv_result = compare_bundles(stix_bundle, stored_data["bundle"], iocs_serializable, stored_data["iocs"])
                stix_context_similarity = equiv_result["context_score"]
                stix_observable_by_type = equiv_result["observable_score_by_type"]
                stix_observable_by_value = equiv_result["observable_score_by_value"]
                stix_context_details = equiv_result["context_details"]
                stix_observable_details = equiv_result["observable_details"]
        except Exception as e:
            log_error(alert["alert_id"], customer_id, "stix_equivalence_l1", type(e).__name__, str(e), tb.format_exc(),
                      {"matched_alert_id": l1_result.matched_alert_id})

    # --- Build result ---
    if l1_result:
        result = AlertOut(
            alert_id=alert["alert_id"],
            verdict="DUPLICATE",
            hash_confidence=l1_result.confidence,
            stix_context_similarity=stix_context_similarity,
            stix_observable_by_type=stix_observable_by_type,
            stix_observable_by_value=stix_observable_by_value,
            layer=1,
            matched_alert_id=l1_result.matched_alert_id,
            score_breakdown={"matched": l1_result.matched_slots, "missed": l1_result.missed_slots},
            stix_context_details=stix_context_details,
            stix_observable_details=stix_observable_details,
            iocs=iocs_serializable,
            filled_slots=l1_result.filled_slots,
            matched_slots=l1_result.matched_slots,
            missed_slots=l1_result.missed_slots,
        )
    else:
        # --- Stage: Layer 2 — Fuzzy matching ---
        l2_result = None
        alert_name = alert.get("alert_name", "")
        time_bucket_hash = prerequisites.get("time_bucket", "")

        if time_bucket_hash and ioc_hashes:
            try:
                bucket_alerts = await redis_client.lookup_l2_time_bucket(customer_id, time_bucket_hash)
                if bucket_alerts:
                    l2_candidates = []
                    for cand_id in bucket_alerts:
                        stored_data = await redis_client.get_l1_bundle(customer_id, cand_id)
                        if stored_data:
                            l2_candidates.append((cand_id, stored_data["iocs"], stored_data.get("alert_name", ""), ""))

                    if l2_candidates:
                        l2_result = find_best_fuzzy_match(
                            iocs_serializable, alert_name, l2_candidates, settings.layer2_similarity_threshold,
                        )
            except Exception as e:
                log_error(alert["alert_id"], customer_id, "layer2_fuzzy", type(e).__name__, str(e), tb.format_exc(),
                          {"alert_name": alert_name})

        # --- Stage: STIX cross-validation (Layer 2) ---
        if l2_result and settings.stix_equivalence_enabled:
            try:
                stored_data = await redis_client.get_l1_bundle(customer_id, l2_result.matched_alert_id)
                if stored_data:
                    equiv_result = compare_bundles(stix_bundle, stored_data["bundle"], iocs_serializable, stored_data["iocs"])
                    stix_context_similarity = equiv_result["context_score"]
                    stix_observable_by_type = equiv_result["observable_score_by_type"]
                    stix_observable_by_value = equiv_result["observable_score_by_value"]
                    stix_context_details = equiv_result["context_details"]
                    stix_observable_details = equiv_result["observable_details"]
            except Exception as e:
                log_error(alert["alert_id"], customer_id, "stix_equivalence_l2", type(e).__name__, str(e), tb.format_exc(),
                          {"matched_alert_id": l2_result.matched_alert_id})

        if l2_result:
            result = AlertOut(
                alert_id=alert["alert_id"],
                verdict="SIMILAR",
                hash_confidence=0.0,
                stix_context_similarity=stix_context_similarity,
                stix_observable_by_type=stix_observable_by_type,
                stix_observable_by_value=stix_observable_by_value,
                layer=2,
                matched_alert_id=l2_result.matched_alert_id,
                score_breakdown={
                    "fuzzy_score": l2_result.fuzzy_score,
                    "per_type_scores": l2_result.per_type_scores,
                    "incoming_rule": l2_result.incoming_rule,
                    "matched_rule": l2_result.matched_rule,
                },
                stix_context_details=stix_context_details,
                stix_observable_details=stix_observable_details,
                iocs=iocs_serializable,
                filled_slots=list(ioc_hashes.keys()),
                matched_slots=[],
                missed_slots=[],
            )
        else:
            result = AlertOut(
                alert_id=alert["alert_id"],
                verdict="NEW",
                hash_confidence=0.0,
                stix_context_similarity=0.0,
                stix_observable_by_type=0.0,
                stix_observable_by_value=0.0,
                layer=0,
                matched_alert_id="",
                score_breakdown={},
                stix_context_details=[],
                stix_observable_details=[],
                iocs=iocs_serializable,
                filled_slots=list(ioc_hashes.keys()),
                matched_slots=[],
                missed_slots=[],
            )

    # --- Stage: Audit log ---
    try:
        log_dedup_result(
            alert_id=result.alert_id, customer_id=customer_id, source=source, severity=severity,
            rule=alert.get("alert_name", ""), verdict=result.verdict, confidence=result.hash_confidence,
            layer=result.layer, matched_alert_id=result.matched_alert_id,
            ioc_counts={k: len(v) for k, v in alert_iocs.items()},
            score_breakdown=result.score_breakdown,
            filled_slots=result.filled_slots, matched_slots=result.matched_slots, missed_slots=result.missed_slots,
            input_iocs=iocs_serializable, input_alert=alert, input_hashes=all_hashes,
            candidate_counts=candidate_counts, time_bucket_info=time_bucket_info,
            stix_context_similarity=stix_context_similarity,
            stix_observable_by_type=stix_observable_by_type,
            stix_observable_by_value=stix_observable_by_value,
            stix_context_details=stix_context_details,
            stix_observable_details=stix_observable_details,
        )
    except Exception as e:
        log_error(alert["alert_id"], customer_id, "audit_log", type(e).__name__, str(e), tb.format_exc())

    # --- Stage: Redis store ---
    try:
        await redis_client.store_l1_alert(customer_id, alert["alert_id"], all_hashes, ingestion_time)
        await redis_client.index_l1_hashes(customer_id, alert["alert_id"], all_hashes)
        await redis_client.store_l1_bundle(customer_id, alert["alert_id"], stix_bundle, iocs_serializable, alert.get("alert_name", ""))
        await redis_client.store_result(customer_id, alert["alert_id"], result.model_dump())
    except Exception as e:
        log_error(alert["alert_id"], customer_id, "redis_store", type(e).__name__, str(e), tb.format_exc())

    return result
