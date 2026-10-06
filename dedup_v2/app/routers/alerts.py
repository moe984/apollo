import traceback as tb

from fastapi import APIRouter, HTTPException

from app.models import AlertIn, AlertOut
from app.services import dedup_engine, redis_client
from app.logger import log_error

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])


@router.post("", response_model=AlertOut)
async def process_single_alert(alert: AlertIn):
    """Process a single STIX bundle through the dedup engine."""
    try:
        result = await dedup_engine.process_alert(
            stix_bundle=alert.stix_bundle,
            source=alert.source,
            severity=alert.severity,
            customer_id=alert.customer_id,
            alert_id=alert.alert_id,
            created_at=alert.created_at,
        )
        return result
    except Exception as e:
        log_error(
            alert_id=alert.alert_id or "unknown",
            customer_id=alert.customer_id,
            stage="api_process_alert",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=tb.format_exc(),
        )
        raise HTTPException(status_code=500, detail=f"Dedup engine error: {type(e).__name__}: {str(e)}")


@router.get("/{alert_id}", response_model=AlertOut)
async def get_alert_result(alert_id: str, customer_id: str = ""):
    """Retrieve a stored dedup result by alert ID."""
    try:
        result = await redis_client.get_result(customer_id, alert_id)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Alert {alert_id} not found")
        return AlertOut(**result)
    except HTTPException:
        raise
    except Exception as e:
        log_error(
            alert_id=alert_id,
            customer_id=customer_id,
            stage="api_get_result",
            error_type=type(e).__name__,
            error_message=str(e),
            traceback=tb.format_exc(),
        )
        raise HTTPException(status_code=500, detail=f"Redis error: {type(e).__name__}: {str(e)}")
