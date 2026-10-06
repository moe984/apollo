"""
STIX Bundle Parser — Parse STIX 2.1 bundles into the flat alert dict
format expected by the dedup engine.

Handles two bundle shapes:
  1. Rich bundles: standalone STIX objects (ipv4-addr, user-account, file, etc.)
  2. Minimal bundles: identity + indicator only — extracts IOCs from the
     indicator's vendor_specific.notable_fields as fallback.

Extracts 6 IOC types: ip, user, email, domain, url, file_hash.
All field mappings and extension keys loaded from config.yaml.
"""

import re
import uuid

from app.config import settings

IP_PATTERN = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$")


def parse_stix_bundle(bundle: dict, row: dict | None = None) -> dict:
    """Convert a STIX 2.1 bundle into the flat alert dict format."""
    objects = bundle.get("objects", [])

    ips: list[str] = []
    domains: list[str] = []
    urls: list[str] = []
    file_hashes: list[str] = []
    emails: list[str] = []
    usernames: list[str] = []
    alert_name = ""
    description = ""

    for obj in objects:
        obj_type = obj.get("type", "")

        if obj_type == "ipv4-addr":
            ips.append(obj["value"])

        elif obj_type == "ipv6-addr":
            ips.append(obj["value"])

        elif obj_type == "domain-name":
            domains.append(obj["value"])

        elif obj_type == "url":
            urls.append(obj.get("value", ""))

        elif obj_type == "file":
            hashes = obj.get("hashes", {})
            for algo in settings.stix_hash_algorithms:
                if algo in hashes:
                    file_hashes.append(hashes[algo])

        elif obj_type == "user-account":
            uid = obj.get("user_id", "")
            login = obj.get("account_login", "")
            seen: set[str] = set()
            for val in (uid, login):
                if val and val not in seen:
                    usernames.append(val)
                    seen.add(val)

        elif obj_type == "identity":
            description = obj.get("description", "")

        elif obj_type == "indicator":
            alert_name = obj.get("name", "")
            _extract_from_notable_fields(
                obj, ips, domains, urls, file_hashes, emails, usernames
            )

    alert_id = (row or {}).get("id") or str(uuid.uuid4())
    timestamp = (row or {}).get("created_at", "").strip('"') if row else ""
    if not timestamp:
        for obj in objects:
            if obj.get("type") == "indicator" and obj.get("created"):
                timestamp = obj["created"]
                break

    return {
        "alert_id": alert_id,
        "timestamp": timestamp,
        "source": (row or {}).get("source", ""),
        "severity": (row or {}).get("severity", ""),
        "customer_id": (row or {}).get("customer_id", ""),
        "alert_name": alert_name,
        "ips": _dedup_list(ips),
        "domains": _dedup_list(domains),
        "urls": _dedup_list(urls),
        "file_hashes": _dedup_list(file_hashes),
        "emails": _dedup_list(emails),
        "usernames": _dedup_list(usernames),
        "description": description,
    }


def _extract_from_notable_fields(
    indicator_obj: dict,
    ips: list,
    domains: list,
    urls: list,
    file_hashes: list,
    emails: list,
    usernames: list,
) -> None:
    ext = indicator_obj.get("extensions", {}).get(settings.stix_extension_key, {})
    if not ext:
        for key, val in indicator_obj.get("extensions", {}).items():
            if isinstance(val, dict) and "vendor_specific" in val:
                ext = val
                break
    vs = ext.get("vendor_specific", {})
    nf = vs.get("notable_fields") or vs.get("result") or {}
    if not nf or not isinstance(nf, dict):
        return

    field_mappings = settings.notable_field_mappings
    targets = {
        "ip": ips,
        "user": usernames,
        "domain": domains,
        "email": emails,
        "url": urls,
        "file_hash": file_hashes,
    }

    for ioc_type, fields in field_mappings.items():
        target = targets.get(ioc_type)
        if target is None:
            continue
        for field in fields:
            raw = nf.get(field, "")
            if not raw:
                continue
            values = raw if isinstance(raw, list) else [raw]
            for val in values:
                if not isinstance(val, str) or not val.strip():
                    continue
                val = val.strip()
                if ioc_type == "ip":
                    # Strip port if present (e.g., "20.228.89.52:14792")
                    if ":" in val and "." in val:
                        val = val.split(":")[0]
                    if not IP_PATTERN.match(val):
                        continue
                if ioc_type == "domain" and IP_PATTERN.match(val):
                    continue
                if ioc_type == "email" and "@" not in val:
                    continue
                target.append(val)


def _dedup_list(items: list) -> list:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item and item not in seen:
            result.append(item)
            seen.add(item)
    return result
