"""
Apollo API Client

Authenticates via JWT cookie and provides methods for invoking
Apollo feedback endpoints (and other APIs).

Usage:
    from apollo_client import ApolloClient

    client = ApolloClient()
    client.login()

    # Submit triage feedback with verdict/disposition
    client.submit_triage_feedback(
        alert_id="alert-001",
        verdict="FP",
        disposition="fp_logic",
        is_false_positive=True,
        quality_rating=3,
    )

    # List alerts
    alerts = client.list_alerts()
"""

import os
import requests
from dotenv import load_dotenv

load_dotenv()


class ApolloClient:
    def __init__(self):
        self.base_url = os.environ["APOLLO_BASE_URL"].rstrip("/")
        self.email = os.environ["APOLLO_EMAIL"]
        self.password = os.environ["APOLLO_PASSWORD"]
        self.session = requests.Session()

    # -----------------------------------------------------------------
    # Auth
    # -----------------------------------------------------------------

    def login(self):
        """Authenticate and store the JWT cookie in the session."""
        resp = self.session.post(
            f"{self.base_url}/api/v1/auth/login",
            json={"email": self.email, "password": self.password},
        )
        resp.raise_for_status()
        token = self.session.cookies.get("apollo_token")
        if not token:
            raise RuntimeError("Login succeeded but no apollo_token cookie returned")
        print(f"Logged in as {self.email}")
        return resp.json()

    # -----------------------------------------------------------------
    # Alerts
    # -----------------------------------------------------------------

    def list_alerts(self, limit=20, offset=0):
        """GET /api/v1/alerts"""
        resp = self.session.get(
            f"{self.base_url}/api/v1/alerts",
            params={"limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        return resp.json()

    def get_alert(self, alert_id: str):
        """GET /api/v1/alerts/:id"""
        resp = self.session.get(f"{self.base_url}/api/v1/alerts/{alert_id}")
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # Feedback — Triage (per-tier)
    # -----------------------------------------------------------------

    def submit_triage_feedback(
        self,
        alert_id: str,
        verdict: str | None = None,
        disposition: str | None = None,
        is_false_positive: bool | None = None,
        is_false_negative: bool | None = None,
        corrected_disposition: str | None = None,
        correct_severity: str | None = None,
        quality_rating: int | None = None,
        rationale_quality: int | None = None,
        comment: str | None = None,
    ):
        """POST /api/v1/alerts/:id/feedback"""
        payload = {}
        if corrected_disposition is not None:
            payload["corrected_disposition"] = corrected_disposition
        if is_false_positive is not None:
            payload["is_false_positive"] = is_false_positive
        if is_false_negative is not None:
            payload["is_false_negative"] = is_false_negative
        if correct_severity is not None:
            payload["correct_severity"] = correct_severity
        if quality_rating is not None:
            payload["quality_rating"] = quality_rating
        if rationale_quality is not None:
            payload["rationale_quality"] = rationale_quality
        if comment is not None:
            payload["comment"] = comment
        if verdict is not None:
            payload["verdict"] = verdict
        if disposition is not None:
            payload["disposition"] = disposition

        resp = self.session.post(
            f"{self.base_url}/api/v1/alerts/{alert_id}/feedback",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # Feedback — Investigation (per-tier)
    # -----------------------------------------------------------------

    def submit_investigation_feedback(
        self,
        investigation_id: str,
        quality_rating: int,
        findings_accurate: bool,
        iocs_complete: bool,
        timeline_accurate: bool,
        attack_mapping_accurate: bool,
        false_positive: bool,
        verdict: str | None = None,
        disposition: str | None = None,
        additional_findings: str | None = None,
        comments: str | None = None,
    ):
        """POST /api/v1/investigations/:id/feedback"""
        payload = {
            "quality_rating": quality_rating,
            "findings_accurate": findings_accurate,
            "iocs_complete": iocs_complete,
            "timeline_accurate": timeline_accurate,
            "attack_mapping_accurate": attack_mapping_accurate,
            "false_positive": false_positive,
        }
        if verdict is not None:
            payload["verdict"] = verdict
        if disposition is not None:
            payload["disposition"] = disposition
        if additional_findings is not None:
            payload["additional_findings"] = additional_findings
        if comments is not None:
            payload["comments"] = comments

        resp = self.session.post(
            f"{self.base_url}/api/v1/investigations/{investigation_id}/feedback",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # Feedback — Response (per-tier)
    # -----------------------------------------------------------------

    def submit_response_feedback(
        self,
        response_id: str,
        containment_appropriate: bool,
        playbook_correct: bool,
        actions_effective: bool,
        false_positive: bool,
        quality_rating: int,
        verdict: str | None = None,
        disposition: str | None = None,
        comments: str | None = None,
    ):
        """POST /api/v1/responses/:id/feedback"""
        payload = {
            "containment_appropriate": containment_appropriate,
            "playbook_correct": playbook_correct,
            "actions_effective": actions_effective,
            "false_positive": false_positive,
            "quality_rating": quality_rating,
        }
        if verdict is not None:
            payload["verdict"] = verdict
        if disposition is not None:
            payload["disposition"] = disposition
        if comments is not None:
            payload["comments"] = comments

        resp = self.session.post(
            f"{self.base_url}/api/v1/responses/{response_id}/feedback",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # Feedback — Unified (all tiers)
    # -----------------------------------------------------------------

    def submit_feedback_unified(self, tier: str, feedback: dict, **kwargs):
        """POST /api/v1/feedback (unified endpoint)

        Args:
            tier: 'triage' | 'investigation' | 'response' | 'dedup'
            feedback: tier-specific feedback dict
            **kwargs: alert_id, investigation_id, or response_id
        """
        payload = {"tier": tier, "feedback": feedback}
        if "alert_id" in kwargs:
            payload["alert_id"] = kwargs["alert_id"]
        if "investigation_id" in kwargs:
            payload["investigation_id"] = kwargs["investigation_id"]
        if "response_id" in kwargs:
            payload["response_id"] = kwargs["response_id"]

        resp = self.session.post(
            f"{self.base_url}/api/v1/feedback",
            json=payload,
        )
        resp.raise_for_status()
        return resp.json()

    # -----------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------

    def health(self):
        """GET /health"""
        resp = self.session.get(f"{self.base_url}/health")
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------
# Quick smoke test
# ---------------------------------------------------------------------

if __name__ == "__main__":
    client = ApolloClient()

    # Check health (no auth needed)
    print("--- Health Check ---")
    h = client.health()
    print(f"Status: {h.get('status', 'unknown')}")

    # Login
    print("\n--- Login ---")
    client.login()

    # List alerts
    print("\n--- Alerts (first 5) ---")
    alerts = client.list_alerts(limit=5)
    if isinstance(alerts, dict) and "data" in alerts:
        for a in alerts["data"][:5]:
            print(f"  {a['id']} | {a.get('status', '?')} | {a.get('severity', '?')}")
    elif isinstance(alerts, list):
        for a in alerts[:5]:
            print(f"  {a['id']} | {a.get('status', '?')} | {a.get('severity', '?')}")
    else:
        print(f"  Response: {alerts}")
