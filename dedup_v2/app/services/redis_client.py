"""
Async Redis client for the dedup engine.

Manages connection pool lifecycle and provides operations
for Layer 1 inverted index lookups and storage.
"""

import json
from collections import Counter

import redis.asyncio as redis

from app.config import settings


_pool: redis.Redis | None = None


async def connect() -> redis.Redis:
    global _pool
    _pool = redis.from_url(
        settings.redis_url,
        decode_responses=True,
    )
    await _pool.ping()
    return _pool


async def disconnect() -> None:
    global _pool
    if _pool:
        await _pool.aclose()
        _pool = None


def get_redis() -> redis.Redis:
    if _pool is None:
        raise RuntimeError("Redis not connected. Call connect() first.")
    return _pool


# ---------------------------------------------------------------------------
# Key helpers
# ---------------------------------------------------------------------------
def _key(*parts: str) -> str:
    return settings.redis_key_prefix + ":" + ":".join(parts)


TTL_SECONDS = settings.redis_ttl_minutes * 60


# ---------------------------------------------------------------------------
# Layer 1: Forward index — store the full hash array per alert
# ---------------------------------------------------------------------------
async def store_l1_alert(customer_id: str, alert_id: str, hashes: dict[str, str], timestamp: str) -> None:
    """Store the full hash array for an alert."""
    r = get_redis()
    key = _key(customer_id, "l1", alert_id)
    value = json.dumps({
        "alert_id": alert_id,
        "timestamp": timestamp,
        "hashes": hashes,
    })
    await r.set(key, value, ex=TTL_SECONDS)


async def get_l1_alert(customer_id: str, alert_id: str) -> dict | None:
    """Fetch stored hash array for an alert."""
    r = get_redis()
    key = _key(customer_id, "l1", alert_id)
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Layer 1: Inverted index — map each slot hash to alert IDs
# ---------------------------------------------------------------------------
async def index_l1_hashes(customer_id: str, alert_id: str, hashes: dict[str, str]) -> None:
    """Add alert_id to the inverted index for each filled slot."""
    r = get_redis()
    pipe = r.pipeline()
    for slot_type, slot_hash in hashes.items():
        key = _key(customer_id, "l1", "idx", slot_type, slot_hash)
        pipe.sadd(key, alert_id)
        pipe.expire(key, TTL_SECONDS)
    await pipe.execute()


async def lookup_l2_time_bucket(customer_id: str, time_bucket_hash: str) -> set[str]:
    """Layer 2: Find all alerts in the same time bucket (any rule).

    Returns a set of alert_ids that share the same time_bucket hash.
    """
    r = get_redis()
    key = _key(customer_id, "l1", "idx", "time_bucket", time_bucket_hash)
    return await r.smembers(key)


async def lookup_l1_prerequisites(customer_id: str, prerequisites: dict[str, str]) -> set[str]:
    """Stage 1: Find candidates that match ALL prerequisite slots (alert_name + time_bucket).

    Returns a set of alert_ids that match both prerequisites.
    Empty set if no matches.
    """
    r = get_redis()

    keys = [
        _key(customer_id, "l1", "idx", slot_type, slot_hash)
        for slot_type, slot_hash in prerequisites.items()
    ]

    if not keys:
        return set()

    # Intersect all prerequisite sets — candidates must match ALL
    result = await r.sinter(*keys)
    return result


async def lookup_l1_ioc_candidates(customer_id: str, ioc_hashes: dict[str, str], prerequisite_matches: set[str]) -> list[tuple[str, int]]:
    """Stage 2: Among prerequisite-matched candidates, count IOC slot matches.

    Returns a list of (alert_id, ioc_match_count) sorted by count descending.
    Only considers candidates that passed the prerequisite filter.
    """
    r = get_redis()

    if not prerequisite_matches or not ioc_hashes:
        return []

    # Query each IOC slot's index set
    keys = [
        _key(customer_id, "l1", "idx", slot_type, slot_hash)
        for slot_type, slot_hash in ioc_hashes.items()
    ]

    pipe = r.pipeline()
    for key in keys:
        pipe.smembers(key)
    results = await pipe.execute()

    # Count IOC matches only for candidates that passed prerequisites
    counts: Counter[str] = Counter()
    for members in results:
        for alert_id in members:
            if alert_id in prerequisite_matches:
                counts[alert_id] += 1

    return counts.most_common()


# ---------------------------------------------------------------------------
# Layer 1: Raw STIX bundle store (for equivalence cross-validation)
# ---------------------------------------------------------------------------
async def store_l1_bundle(customer_id: str, alert_id: str, stix_bundle: dict, parsed_iocs: dict, alert_name: str = "") -> None:
    """Store the raw STIX bundle, parsed IOCs, and alert name for an alert."""
    r = get_redis()
    key = _key(customer_id, "l1", "bundle", alert_id)
    value = json.dumps({"bundle": stix_bundle, "iocs": parsed_iocs, "alert_name": alert_name})
    await r.set(key, value, ex=TTL_SECONDS)


async def get_l1_bundle(customer_id: str, alert_id: str) -> dict | None:
    """Fetch stored STIX bundle and parsed IOCs for an alert."""
    r = get_redis()
    key = _key(customer_id, "l1", "bundle", alert_id)
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


# ---------------------------------------------------------------------------
# Results store
# ---------------------------------------------------------------------------
async def store_result(customer_id: str, alert_id: str, result: dict) -> None:
    r = get_redis()
    key = _key(customer_id, "result", alert_id)
    await r.set(key, json.dumps(result), ex=TTL_SECONDS)


async def get_result(customer_id: str, alert_id: str) -> dict | None:
    r = get_redis()
    key = _key(customer_id, "result", alert_id)
    raw = await r.get(key)
    if raw is None:
        return None
    return json.loads(raw)
