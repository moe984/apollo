"""
Hash Dedup Engine — POC

Listens on a TCP socket for JSON alert payloads, computes composite key
hashes (same algorithm as Apollo's DeduplicationService), and detects
duplicates using an in-memory time-windowed store.

Full visibility logging — every step of the dedup process is logged:
  RECV      raw payload received
  DETECT    vendor format detected
  EXTRACT   observables extracted from payload
  MERGE     domains merged into destIps
  KEY_IN    raw components before hashing
  KEY_OUT   SHA-256 hash produced
  CLEAN     expired entries removed from store
  LOOKUP    hash checked against store
  REGISTER  new hash added to store
  DECISION  final duplicate/new verdict
  STORE     current store state

Usage:
    python engine.py                        # start engine on port 9800
    python engine.py --port 9900            # custom port
    python engine.py --window-minutes 15    # custom dedup window

Then stream alerts to it:
    python stream.py                        # sends sample alerts
    echo '{"sourceIps":["10.0.1.5"],...}' | nc localhost 9800
"""

import argparse
import hashlib
import json
import logging
import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Logging — structured JSON to file + colored summary to console
# ---------------------------------------------------------------------------

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")

_log_lock = threading.Lock()
_alert_counter = 0
_alert_counter_lock = threading.Lock()
_file_logger: logging.Logger | None = None


def init_logging(port: int):
    """Initialize file logger. Creates logs/ directory and writes JSON lines."""
    global _file_logger
    os.makedirs(LOG_DIR, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(LOG_DIR, f"engine_{ts}_port{port}.jsonl")

    _file_logger = logging.getLogger("dedup_engine")
    _file_logger.setLevel(logging.DEBUG)
    _file_logger.handlers.clear()

    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(message)s"))  # raw JSON lines
    _file_logger.addHandler(fh)

    return log_path


COLORS = {
    "RECV":     "\033[37m",
    "DETECT":   "\033[35m",
    "EXTRACT":  "\033[34m",
    "MERGE":    "\033[34m",
    "KEY_IN":   "\033[33m",
    "KEY_OUT":  "\033[33m",
    "CLEAN":    "\033[90m",
    "LOOKUP":   "\033[36m",
    "REGISTER": "\033[36m",
    "NEW":      "\033[92m",
    "DUPLICATE":"\033[91m",
    "DECISION": "\033[1m",
    "STORE":    "\033[90m",
    "STATS":    "\033[96m",
    "INFO":     "\033[94m",
    "CONN":     "\033[95m",
    "ERROR":    "\033[93m",
}
RESET = "\033[0m"


def next_alert_num() -> int:
    global _alert_counter
    with _alert_counter_lock:
        _alert_counter += 1
        return _alert_counter


def log(level: str, msg: str, alert_num: int | None = None, **extra):
    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    ts_short = ts_str[11:23]

    # Write structured JSON to file
    if _file_logger:
        record = {
            "ts": ts_str,
            "level": level,
            "alert_num": alert_num,
            "msg": msg,
            **extra,
        }
        _file_logger.info(json.dumps(record, default=str))

    # Write colored summary to console
    color = COLORS.get(level, "")
    prefix = f"  #{alert_num:<4}" if alert_num else "      "
    with _log_lock:
        print(f"{ts_short}{prefix}{color}[{level:>9}]{RESET} {msg}")


# ---------------------------------------------------------------------------
# Composite Key — mirrors Apollo's composite-key.ts
# ---------------------------------------------------------------------------

def generate_dedup_keys(
    source_ips: list[str],
    dest_ips: list[str],
    alert_category: str,
    window_ms: int,
    timestamp_ms: int,
    alert_num: int,
) -> list[tuple[str, str]]:
    """
    Generate SHA-256 composite keys for deduplication.
    One key per source IP (subset matching). Same algorithm as Apollo.

    Returns list of (hash, raw_components) tuples for logging.
    """
    sorted_dest = ",".join(sorted(dest_ips))
    time_bucket = timestamp_ms // window_ms

    log("KEY_IN", f"time_bucket = floor({timestamp_ms} / {window_ms}) = {time_bucket}", alert_num,
        timestamp_ms=timestamp_ms, window_ms=window_ms, time_bucket=time_bucket)
    log("KEY_IN", f"sorted_dest_ips = {sorted_dest!r}", alert_num,
        sorted_dest_ips=sorted_dest)

    src_list = source_ips if source_ips else [""]
    if not source_ips:
        log("KEY_IN", "no source IPs — using empty string as single key", alert_num,
            source_ips=[], fallback="empty_string")

    keys = []
    for src_ip in sorted(set(src_list)):
        components = f"{src_ip}|{sorted_dest}|{alert_category}|{time_bucket}"
        h = hashlib.sha256(components.encode()).hexdigest()
        log("KEY_IN", f"components = {components!r}", alert_num,
            raw_components=components, source_ip=src_ip)
        log("KEY_OUT", f"SHA-256    = {h}", alert_num,
            sha256=h, raw_components=components)
        keys.append((h, components))

    if len(keys) > 1:
        log("KEY_OUT", f"{len(keys)} keys generated (1 per source IP for subset matching)", alert_num,
            key_count=len(keys))

    return keys


# ---------------------------------------------------------------------------
# Dedup Store — in-memory equivalent of Redis sorted set + hash map
# ---------------------------------------------------------------------------

@dataclass
class DedupEntry:
    alert_id: str
    timestamp_ms: int
    composite_hash: str
    raw_components: str
    source: str
    alert_category: str


class DedupStore:
    """Time-windowed dedup store. Mirrors the Redis Lua script logic."""

    def __init__(self, window_ms: int):
        self.window_ms = window_ms
        self.entries: dict[str, DedupEntry] = {}  # hash -> entry
        self.lock = threading.Lock()
        self.stats = {"total": 0, "duplicates": 0, "new": 0}

    def check_and_set(
        self,
        composite_hash: str,
        raw_components: str,
        alert_id: str,
        timestamp_ms: int,
        source: str,
        alert_category: str,
        alert_num: int,
    ) -> tuple[bool, str | None, DedupEntry | None]:
        """
        Atomic check-and-set. Returns (is_duplicate, existing_alert_id, existing_entry).
        Mirrors the Redis Lua script: clean expired → check → set.
        """
        with self.lock:
            self.stats["total"] += 1

            # Step 1: Clean expired entries (ZREMRANGEBYSCORE equivalent)
            min_time = timestamp_ms - self.window_ms
            expired = [
                (h, e) for h, e in self.entries.items()
                if e.timestamp_ms < min_time
            ]
            if expired:
                for h, e in expired:
                    del self.entries[h]
                    age_ms = timestamp_ms - e.timestamp_ms
                    log("CLEAN", f"expired hash={h[:16]}... alert={e.alert_id} (age={age_ms}ms > window={self.window_ms}ms)", alert_num,
                        expired_hash=h, expired_alert_id=e.alert_id, age_ms=age_ms, window_ms=self.window_ms)
                log("CLEAN", f"{len(expired)} entries expired", alert_num, expired_count=len(expired))
            else:
                log("CLEAN", f"no expired entries (min_time={min_time})", alert_num,
                    expired_count=0, min_time=min_time)

            # Step 2: Check if hash exists (ZSCORE equivalent)
            active_count = len(self.entries)
            log("LOOKUP", f"checking hash={composite_hash[:16]}... in store ({active_count} active entries)", alert_num,
                hash=composite_hash, active_entries=active_count)

            if composite_hash in self.entries:
                existing = self.entries[composite_hash]
                age_ms = timestamp_ms - existing.timestamp_ms
                self.stats["duplicates"] += 1
                log("LOOKUP", f"FOUND — matches alert={existing.alert_id} (registered {age_ms}ms ago)", alert_num,
                    result="FOUND", matched_alert_id=existing.alert_id, age_ms=age_ms,
                    matched_components=existing.raw_components, matched_source=existing.source)
                return True, existing.alert_id, existing

            log("LOOKUP", "NOT FOUND — this is a new alert", alert_num,
                result="NOT_FOUND", hash=composite_hash)

            # Step 3: Register new hash (ZADD + HSET equivalent)
            entry = DedupEntry(
                alert_id=alert_id,
                timestamp_ms=timestamp_ms,
                composite_hash=composite_hash,
                raw_components=raw_components,
                source=source,
                alert_category=alert_category,
            )
            self.entries[composite_hash] = entry
            self.stats["new"] += 1
            log("REGISTER", f"stored hash={composite_hash[:16]}... alert={alert_id} (store now has {len(self.entries)} entries)", alert_num,
                hash=composite_hash, alert_id=alert_id, store_size=len(self.entries))
            return False, None, None

    def get_stats(self) -> dict:
        with self.lock:
            return {
                **self.stats,
                "active_entries": len(self.entries),
                "dedup_rate": (
                    f"{self.stats['duplicates'] / self.stats['total'] * 100:.1f}%"
                    if self.stats["total"] > 0
                    else "0%"
                ),
            }

    def dump_store(self, alert_num: int):
        with self.lock:
            now_ms = int(time.time() * 1000)
            entries_list = []
            for h, e in self.entries.items():
                age_s = (now_ms - e.timestamp_ms) / 1000
                entries_list.append({
                    "hash": h, "alert_id": e.alert_id, "source": e.source,
                    "category": e.alert_category, "age_s": round(age_s, 1),
                    "components": e.raw_components,
                })
            if not entries_list:
                log("STORE", "store is empty", alert_num, entries=[])
                return
            log("STORE", f"--- store snapshot ({len(entries_list)} entries) ---", alert_num,
                entry_count=len(entries_list), entries=entries_list)
            for entry in entries_list:
                log("STORE", f"  {entry['hash'][:16]}... alert={entry['alert_id']} src={entry['source']} age={entry['age_s']}s cat={entry['category']}", alert_num)
            log("STORE", "--- end snapshot ---", alert_num)


# ---------------------------------------------------------------------------
# Alert Processing — mirrors pipeline.ts extractObservables + dedup.check
# ---------------------------------------------------------------------------

def detect_vendor(payload: dict) -> str:
    if "DetectName" in payload or "DetectId" in payload or "CompositeId" in payload:
        return "crowdstrike"
    if "search_name" in payload or "rule_name" in payload or "_raw" in payload:
        return "splunk"
    if "AlertName" in payload:
        return "sentinel"
    if "sourceIps" in payload or "source_ips" in payload:
        return "pre-extracted"
    return "generic"


def extract_observables(payload: dict, alert_num: int) -> dict:
    """
    Extract observables from a raw alert payload.
    Supports the same vendor formats as Apollo's adapters.
    """
    vendor = detect_vendor(payload)
    log("DETECT", f"vendor format = {vendor}", alert_num)

    if vendor == "pre-extracted":
        obs = {
            "source_ips": payload.get("sourceIps", payload.get("source_ips", [])),
            "dest_ips": payload.get("destIps", payload.get("dest_ips", [])),
            "domains": payload.get("domains", []),
            "alert_category": payload.get("alertCategory", payload.get("alert_category", "unknown")),
            "source": payload.get("source", "unknown"),
            "alert_id": payload.get("alertId", payload.get("alert_id", f"alert-{int(time.time()*1000)}")),
        }

    elif vendor == "crowdstrike":
        dest_ips = []
        if isinstance(payload.get("NetworkAccesses"), list):
            for conn in payload["NetworkAccesses"]:
                if isinstance(conn, dict) and conn.get("RemoteAddress"):
                    dest_ips.append(conn["RemoteAddress"])
        obs = {
            "source_ips": [payload["LocalIP"]] if payload.get("LocalIP") else [],
            "dest_ips": dest_ips,
            "domains": [],
            "alert_category": payload.get("Name", payload.get("DetectName", payload.get("Tactic", "CrowdStrike Detection"))),
            "source": "crowdstrike",
            "alert_id": payload.get("CompositeId", payload.get("DetectId", f"cs-{int(time.time()*1000)}")),
        }

    elif vendor == "splunk":
        obs = {
            "source_ips": [payload["src_ip"]] if payload.get("src_ip") else [],
            "dest_ips": [payload["dest_ip"]] if payload.get("dest_ip") else [],
            "domains": [],
            "alert_category": payload.get("search_name", payload.get("rule_name", "Splunk Alert")),
            "source": "splunk",
            "alert_id": payload.get("source_guid", payload.get("event_id", f"splunk-{int(time.time()*1000)}")),
        }

    elif vendor == "sentinel":
        source_ips = []
        if isinstance(payload.get("Entities"), str):
            try:
                for e in json.loads(payload["Entities"]):
                    if e.get("Type") in ("ip", "IP") and e.get("Address"):
                        source_ips.append(e["Address"])
            except (json.JSONDecodeError, TypeError):
                pass
        obs = {
            "source_ips": source_ips,
            "dest_ips": [],
            "domains": [],
            "alert_category": payload.get("AlertName", "Sentinel Alert"),
            "source": "sentinel",
            "alert_id": payload.get("SystemAlertId", f"sentinel-{int(time.time()*1000)}"),
        }

    else:
        src = None
        for k in ["src_ip", "source_ip", "sourceAddress", "attacker_ip"]:
            if payload.get(k):
                src = payload[k]
                break
        dst = None
        for k in ["dest_ip", "destination_ip", "destinationAddress", "target_ip"]:
            if payload.get(k):
                dst = payload[k]
                break
        obs = {
            "source_ips": [src] if src else [],
            "dest_ips": [dst] if dst else [],
            "domains": [],
            "alert_category": payload.get("alert_name", payload.get("rule_name", payload.get("title", "unknown"))),
            "source": payload.get("source", "generic"),
            "alert_id": payload.get("alert_id", f"gen-{int(time.time()*1000)}"),
        }

    log("EXTRACT", f"alert_id={obs['alert_id']} source={obs['source']} src_ips={obs['source_ips']} dest_ips={obs['dest_ips']} domains={obs['domains']} category={obs['alert_category']!r}", alert_num,
        **obs)

    return obs


def process_alert(payload: dict, store: DedupStore, window_ms: int) -> dict:
    """Process a single alert through the hash dedup engine."""
    alert_num = next_alert_num()
    now_ms = int(time.time() * 1000)

    log("RECV", f"--- incoming alert ---", alert_num,
        raw_payload=payload)

    # Extract observables
    obs = extract_observables(payload, alert_num)

    # Merge domains into dest_ips (same as pipeline.ts:419)
    dest_combined = obs["dest_ips"] + obs["domains"]
    if obs["domains"]:
        log("MERGE", f"dest_ips + domains = {dest_combined}", alert_num,
            dest_ips=obs["dest_ips"], domains=obs["domains"], merged=dest_combined)
    else:
        log("MERGE", f"no domains to merge, dest_ips = {dest_combined}", alert_num,
            dest_ips=dest_combined, domains=[], merged=dest_combined)

    # Generate composite keys (one per source IP)
    keys = generate_dedup_keys(
        source_ips=obs["source_ips"],
        dest_ips=dest_combined,
        alert_category=obs["alert_category"],
        window_ms=window_ms,
        timestamp_ms=now_ms,
        alert_num=alert_num,
    )

    # Check each key — any match = duplicate
    for i, (composite_hash, raw_components) in enumerate(keys):
        if len(keys) > 1:
            log("LOOKUP", f"checking key {i+1}/{len(keys)} (source IP: {obs['source_ips'][i] if i < len(obs['source_ips']) else '(empty)'})", alert_num)

        is_dup, existing_id, existing_entry = store.check_and_set(
            composite_hash=composite_hash,
            raw_components=raw_components,
            alert_id=obs["alert_id"],
            timestamp_ms=now_ms,
            source=obs["source"],
            alert_category=obs["alert_category"],
            alert_num=alert_num,
        )
        if is_dup:
            log("DECISION", f"\033[91m*** DUPLICATE *** alert={obs['alert_id']} matches={existing_id}\033[0m", alert_num,
                verdict="DUPLICATE", alert_id=obs["alert_id"], existing_alert_id=existing_id,
                hash=composite_hash, source=obs["source"], category=obs["alert_category"],
                source_ips=obs["source_ips"], dest_ips=dest_combined)
            store.dump_store(alert_num)
            log("RECV", f"--- end alert ---", alert_num)
            return {
                "alert_id": obs["alert_id"],
                "status": "DUPLICATE",
                "existing_alert_id": existing_id,
                "composite_hash": composite_hash[:16] + "...",
                "source": obs["source"],
                "alert_category": obs["alert_category"],
                "source_ips": obs["source_ips"],
                "dest_ips": dest_combined,
            }

    # Not duplicate — register remaining hashes (first already registered by check_and_set)
    if len(keys) > 1:
        log("REGISTER", f"first key already registered, registering {len(keys)-1} additional keys", alert_num)
        # Note: in Apollo, the Lua script registers during check_and_set for each key.
        # Here check_and_set already registers, so all keys are covered.

    log("DECISION", f"\033[92m*** NEW *** alert={obs['alert_id']} ({len(keys)} keys registered)\033[0m", alert_num,
        verdict="NEW", alert_id=obs["alert_id"], key_count=len(keys),
        source=obs["source"], category=obs["alert_category"],
        source_ips=obs["source_ips"], dest_ips=dest_combined)

    stats = store.get_stats()
    log("STATS", f"total={stats['total']} new={stats['new']} dup={stats['duplicates']} rate={stats['dedup_rate']} active={stats['active_entries']}", alert_num,
        **stats)
    store.dump_store(alert_num)
    log("RECV", f"--- end alert ---", alert_num)

    return {
        "alert_id": obs["alert_id"],
        "status": "NEW",
        "composite_hashes": [h[:16] + "..." for h, _ in keys],
        "source": obs["source"],
        "alert_category": obs["alert_category"],
        "source_ips": obs["source_ips"],
        "dest_ips": dest_combined,
    }


# ---------------------------------------------------------------------------
# TCP Socket Server
# ---------------------------------------------------------------------------

def handle_client(conn: socket.socket, addr: tuple, store: DedupStore, window_ms: int):
    """Handle a single client connection. Each line is a JSON alert."""
    log("CONN", f"client connected from {addr[0]}:{addr[1]}")
    buffer = b""
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            buffer += data

            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue

                try:
                    payload = json.loads(line)
                except json.JSONDecodeError as e:
                    log("ERROR", f"invalid JSON from {addr}: {e}")
                    continue

                if payload.get("command") == "stats":
                    stats = store.get_stats()
                    log("STATS", json.dumps(stats))
                    response = json.dumps(stats) + "\n"
                    conn.sendall(response.encode())
                    continue

                result = process_alert(payload, store, window_ms)
                response = json.dumps(result) + "\n"
                conn.sendall(response.encode())

    except (ConnectionResetError, BrokenPipeError):
        pass
    finally:
        log("CONN", f"client disconnected {addr[0]}:{addr[1]}")
        conn.close()


def run_server(port: int, store: DedupStore, window_ms: int):
    log_path = init_logging(port)

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("0.0.0.0", port))
    server.listen(5)

    window_min = window_ms // 60_000
    print()
    log("INFO", "========================================")
    log("INFO", "  Hash Dedup Engine — POC")
    log("INFO", "========================================")
    log("INFO", f"port            = {port}")
    log("INFO", f"window          = {window_min} minutes ({window_ms}ms)")
    log("INFO", f"algorithm       = SHA-256(srcIp|sortedDestIps|alertCategory|timeBucket)")
    log("INFO", f"time_bucket     = floor(timestamp_ms / {window_ms})")
    log("INFO", f"subset_matching = 1 hash per source IP")
    log("INFO", f"log_file        = {log_path}")
    log("INFO", "")
    log("INFO", "console shows summary, log file has full structured JSON:")
    log("INFO", f"  tail -f {log_path} | python3 -m json.tool --no-ensure-ascii")
    log("INFO", "")
    log("INFO", "waiting for connections...")
    log("INFO", "========================================")
    print()

    while True:
        conn, addr = server.accept()
        thread = threading.Thread(
            target=handle_client,
            args=(conn, addr, store, window_ms),
            daemon=True,
        )
        thread.start()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Hash Dedup Engine — POC")
    parser.add_argument("--port", type=int, default=9800, help="TCP port (default: 9800)")
    parser.add_argument("--window-minutes", type=int, default=30, help="Dedup window in minutes (default: 30)")
    args = parser.parse_args()

    window_ms = args.window_minutes * 60 * 1000
    store = DedupStore(window_ms=window_ms)
    run_server(args.port, store, window_ms)


if __name__ == "__main__":
    main()
