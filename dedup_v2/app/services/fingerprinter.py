"""
Fingerprinter — Hash each slot individually for Layer 1 dedup.

Returns two separate dicts:
  - prerequisites: alert_name + time_bucket (Stage 1 filter)
  - ioc_hashes: the 6 IOC slots (Stage 2 confidence scoring)
"""

import hashlib
import time
from datetime import datetime, timezone

from app.config import settings

PREREQUISITE_SLOTS = {"alert_name", "time_bucket"}


def fingerprint(iocs: dict[str, set[str]], alert_name: str) -> tuple[dict[str, str], dict[str, str], dict]:
    """Build hash arrays from extracted IOCs, alert name, and ingestion time.

    Returns:
        (prerequisites, ioc_hashes, time_bucket_info)
        - prerequisites: dict with alert_name and time_bucket hashes
        - ioc_hashes: dict of {ioc_type: hash_hex} with only filled IOC slots.
                      Empty dict if zero IOCs (data quality issue).
        - time_bucket_info: dict with raw bucket number and window range.
    """
    ioc_hashes: dict[str, str] = {}

    # Hash each IOC slot
    for ioc_type in settings.layer1_slots:
        if ioc_type in PREREQUISITE_SLOTS:
            continue
        values = iocs.get(ioc_type, set())
        if not values:
            continue
        sorted_values = ",".join(sorted(values))
        raw = f"{ioc_type}:{sorted_values}"
        ioc_hashes[ioc_type] = hashlib.sha256(raw.encode()).hexdigest()

    # Compute time bucket
    bucket, bucket_info = _compute_time_bucket()

    # Prerequisites — always computed
    prerequisites: dict[str, str] = {}

    if alert_name:
        name_normalized = alert_name.strip().lower()
        raw = f"alert_name:{name_normalized}"
        prerequisites["alert_name"] = hashlib.sha256(raw.encode()).hexdigest()

    raw = f"time_bucket:{bucket}"
    prerequisites["time_bucket"] = hashlib.sha256(raw.encode()).hexdigest()

    return prerequisites, ioc_hashes, bucket_info


def _compute_time_bucket() -> tuple[int, dict]:
    """Compute the time bucket based on current ingestion time."""
    now_ms = int(time.time() * 1000)
    window_ms = settings.window_minutes * 60 * 1000
    bucket = now_ms // window_ms

    window_start_ms = bucket * window_ms
    window_end_ms = window_start_ms + window_ms
    window_start = datetime.fromtimestamp(window_start_ms / 1000, tz=timezone.utc).isoformat()
    window_end = datetime.fromtimestamp(window_end_ms / 1000, tz=timezone.utc).isoformat()

    return bucket, {
        "bucket_number": bucket,
        "window_minutes": settings.window_minutes,
        "window_start": window_start,
        "window_end": window_end,
    }
