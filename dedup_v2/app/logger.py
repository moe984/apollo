"""
Structured JSON logger for dedup engine audit trail.

Writes one JSON line per alert to logs/dedup.jsonl.
Rotation settings loaded from config.yaml.
"""

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.config import settings

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = record.msg if isinstance(record.msg, dict) else {"message": record.msg}
        data["level"] = record.levelname
        data["timestamp"] = self.formatTime(record, self.datefmt)
        return json.dumps(data, default=str)


_logger = logging.getLogger("dedup")
_logger.setLevel(logging.INFO)
_logger.propagate = False

_handler = RotatingFileHandler(
    LOG_DIR / "dedup.jsonl",
    maxBytes=settings.log_max_bytes,
    backupCount=settings.log_backup_count,
)
_handler.setFormatter(JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
_logger.addHandler(_handler)

# Error logger — separate file for errors
_error_logger = logging.getLogger("dedup.error")
_error_logger.setLevel(logging.ERROR)
_error_logger.propagate = False

_error_handler = RotatingFileHandler(
    LOG_DIR / "error.jsonl",
    maxBytes=settings.log_max_bytes,
    backupCount=settings.log_backup_count,
)
_error_handler.setFormatter(JSONFormatter(datefmt="%Y-%m-%dT%H:%M:%S"))
_error_logger.addHandler(_error_handler)


def log_error(
    alert_id: str,
    customer_id: str,
    stage: str,
    error_type: str,
    error_message: str,
    traceback: str = "",
    input_data: dict | None = None,
) -> None:
    _error_logger.error({
        "event": "dedup_error",
        "alert_id": alert_id,
        "customer_id": customer_id,
        "stage": stage,
        "error_type": error_type,
        "error_message": error_message,
        "traceback": traceback,
        "input_data": input_data or {},
    })


def log_dedup_result(
    alert_id: str,
    customer_id: str,
    source: str,
    severity: str,
    rule: str,
    verdict: str,
    confidence: float,
    layer: int,
    matched_alert_id: str,
    ioc_counts: dict[str, int],
    score_breakdown: dict,
    filled_slots: list[str] | None = None,
    matched_slots: list[str] | None = None,
    missed_slots: list[str] | None = None,
    # Input data for full visibility
    input_iocs: dict | None = None,
    input_alert: dict | None = None,
    input_hashes: dict | None = None,
    candidate_counts: list | None = None,
    time_bucket_info: dict | None = None,
    stix_context_similarity: float = 0.0,
    stix_observable_by_type: float = 0.0,
    stix_observable_by_value: float = 0.0,
    stix_context_details: list | None = None,
    stix_observable_details: list | None = None,
) -> None:
    _logger.info({
        "event": "dedup_result",
        # Result
        "alert_id": alert_id,
        "customer_id": customer_id,
        "source": source,
        "severity": severity,
        "rule": rule,
        "verdict": verdict,
        "hash_confidence": confidence,
        "stix_context_similarity": stix_context_similarity,
        "stix_observable_by_type": stix_observable_by_type,
        "stix_observable_by_value": stix_observable_by_value,
        "layer": layer,
        "matched_alert_id": matched_alert_id,
        "ioc_counts": ioc_counts,
        "score_breakdown": score_breakdown,
        "stix_context_details": stix_context_details or [],
        "stix_observable_details": stix_observable_details or [],
        "filled_slots": filled_slots or [],
        "matched_slots": matched_slots or [],
        "missed_slots": missed_slots or [],
        # Input data — what was passed to the engine
        "input_iocs": input_iocs or {},
        "input_alert": input_alert or {},
        "input_hashes": input_hashes or {},
        "candidate_counts": candidate_counts or [],
        "time_bucket": time_bucket_info or {},
    })
