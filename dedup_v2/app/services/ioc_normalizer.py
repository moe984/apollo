"""
IOC Normalizer — Normalize indicators of compromise for consistent comparison.

Handles 6 IOC types: ip, user, email, domain, url, file_hash.
"""

from urllib.parse import urlparse, urlunparse


# ---------------------------------------------------------------------------
# Normalization functions
# ---------------------------------------------------------------------------
def normalize_ip(value: str) -> str:
    value = value.strip()
    if "/" in value:
        value = value.split("/")[0]
    if ":" in value and "." not in value:
        return _expand_ipv6(value)
    parts = value.split(".")
    if len(parts) == 4:
        try:
            return ".".join(str(int(p)) for p in parts)
        except ValueError:
            return value.lower()
    return value.lower()


def _expand_ipv6(value: str) -> str:
    value = value.strip().lower()
    if "/" in value:
        value = value.split("/")[0]
    if value.startswith("::ffff:") and "." in value:
        ipv4_part = value.split("::ffff:")[1]
        octets = ipv4_part.split(".")
        if len(octets) == 4:
            try:
                high = (int(octets[0]) << 8) + int(octets[1])
                low = (int(octets[2]) << 8) + int(octets[3])
                value = f"::ffff:{high:04x}:{low:04x}"
            except ValueError:
                pass
    if "::" in value:
        left, right = value.split("::", 1)
        left_groups = left.split(":") if left else []
        right_groups = right.split(":") if right else []
        missing = 8 - len(left_groups) - len(right_groups)
        groups = left_groups + ["0"] * missing + right_groups
    else:
        groups = value.split(":")
    return ":".join(g.zfill(4) for g in groups[:8])


def normalize_domain(value: str) -> str:
    value = value.strip().lower()
    if value.endswith("."):
        value = value[:-1]
    return value


def normalize_url(value: str) -> str:
    value = value.strip()
    try:
        parsed = urlparse(value)
        scheme = (parsed.scheme or "http").lower()
        host = (parsed.hostname or "").lower()
        port = parsed.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            port = None
        netloc = host
        if port:
            netloc = f"{host}:{port}"
        if parsed.username:
            userinfo = parsed.username
            if parsed.password:
                userinfo += f":{parsed.password}"
            netloc = f"{userinfo}@{netloc}"
        return urlunparse((scheme, netloc, parsed.path, parsed.params, parsed.query, ""))
    except Exception:
        return value.lower().strip()


def normalize_email(value: str) -> str:
    return value.strip().lower()


def normalize_hash(value: str) -> str:
    return value.strip().lower()


def normalize_user(value: str) -> str:
    return value.strip().lower()


_NORMALIZERS = {
    "ip": normalize_ip,
    "domain": normalize_domain,
    "url": normalize_url,
    "email": normalize_email,
    "file_hash": normalize_hash,
    "user": normalize_user,
}


def normalize(ioc_type: str, value: str) -> str:
    fn = _NORMALIZERS.get(ioc_type)
    if fn:
        return fn(value)
    return value.strip().lower()


# ---------------------------------------------------------------------------
# IOC extraction from alert dict
# ---------------------------------------------------------------------------
from app.config import settings

IOC_FIELD_MAP = settings.ioc_field_map


def extract_iocs(alert: dict) -> dict[str, set[str]]:
    """Extract and normalize IOCs from an alert dict.

    Returns a dict mapping IOC type to a set of normalized values.
    No blocklist filtering — Layer 1 uses all extracted IOCs.
    """
    iocs: dict[str, set[str]] = {}
    for field, ioc_type in IOC_FIELD_MAP.items():
        values = alert.get(field, [])
        if not values:
            continue
        for raw in values:
            if not raw:
                continue
            normalized = normalize(ioc_type, raw)
            if not normalized:
                continue
            iocs.setdefault(ioc_type, set()).add(normalized)
    return iocs
