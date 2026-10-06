from __future__ import annotations

from pydantic import BaseModel


class AlertIn(BaseModel):
    stix_bundle: dict
    source: str = ""
    severity: str = ""
    customer_id: str = ""
    alert_id: str | None = None
    created_at: str | None = None


class AlertOut(BaseModel):
    alert_id: str
    verdict: str  # DUPLICATE, NEW
    hash_confidence: float
    stix_context_similarity: float
    stix_observable_by_type: float
    stix_observable_by_value: float
    layer: int  # 0=no match, 1=Layer 1
    matched_alert_id: str
    score_breakdown: dict
    stix_context_details: list[dict]
    stix_observable_details: list[dict]
    iocs: dict[str, list[str]]
    filled_slots: list[str]
    matched_slots: list[str]
    missed_slots: list[str]


class HealthResponse(BaseModel):
    status: str
    redis: str
