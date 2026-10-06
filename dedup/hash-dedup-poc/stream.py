"""
Alert Streamer — sends sample alerts to the Hash Dedup Engine.

Demonstrates:
  1. Exact duplicate detection (same alert twice)
  2. IP subset matching (alert B has subset of alert A's IPs)
  3. Different categories → not duplicate
  4. Time bucket boundary behavior
  5. Multi-vendor alerts (CrowdStrike, Splunk, Sentinel formats)
  6. Weak fingerprint vendors (Sentinel with only sourceIp + alertName)

Usage:
    python stream.py                    # run all scenarios
    python stream.py --scenario 1       # run specific scenario
    python stream.py --port 9900        # custom port
    python stream.py --delay 1.0        # seconds between alerts
"""

import argparse
import json
import socket
import time


def send_alert(sock: socket.socket, payload: dict) -> dict:
    """Send a JSON alert and read the response."""
    msg = json.dumps(payload) + "\n"
    sock.sendall(msg.encode())
    response = b""
    while b"\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    return json.loads(response.strip())


def get_stats(sock: socket.socket) -> dict:
    return send_alert(sock, {"command": "stats"})


def print_result(label: str, result: dict):
    status = result["status"]
    color = "\033[91m" if status == "DUPLICATE" else "\033[92m"
    reset = "\033[0m"
    alert_id = result.get("alert_id", "?")
    extra = ""
    if status == "DUPLICATE":
        extra = f" → matches {result.get('existing_alert_id', '?')}"
    print(f"  {color}[{status:>9}]{reset} {label} (id={alert_id}){extra}")


def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def scenario_1_exact_duplicate(sock: socket.socket, delay: float):
    """Same alert sent twice → second is duplicate."""
    print_header("Scenario 1: Exact Duplicate")
    print("  Sending the same CrowdStrike alert twice.\n")

    alert = {
        "sourceIps": ["10.0.1.5"],
        "destIps": ["203.0.113.1"],
        "alertCategory": "Suspicious Process Execution",
        "source": "crowdstrike",
        "alertId": "cs-001",
    }

    r1 = send_alert(sock, alert)
    print_result("Alert 1 (first send)", r1)

    time.sleep(delay)

    alert["alertId"] = "cs-002"  # different ID, same observables
    r2 = send_alert(sock, alert)
    print_result("Alert 2 (same observables, different ID)", r2)


def scenario_2_ip_subset(sock: socket.socket, delay: float):
    """Alert B has a subset of alert A's source IPs → still matches."""
    print_header("Scenario 2: IP Subset Matching")
    print("  Alert A has IPs [A, B]. Alert C has only IP [B].")
    print("  Should match because hash is generated per source IP.\n")

    alert_a = {
        "sourceIps": ["10.0.1.5", "10.0.1.6"],
        "destIps": ["203.0.113.50"],
        "alertCategory": "Brute Force Login",
        "source": "splunk",
        "alertId": "sp-001",
    }
    r1 = send_alert(sock, alert_a)
    print_result("Alert A (IPs: 10.0.1.5, 10.0.1.6)", r1)

    time.sleep(delay)

    alert_c = {
        "sourceIps": ["10.0.1.6"],  # subset
        "destIps": ["203.0.113.50"],
        "alertCategory": "Brute Force Login",
        "source": "splunk",
        "alertId": "sp-002",
    }
    r2 = send_alert(sock, alert_c)
    print_result("Alert C (IP: 10.0.1.6 only — subset)", r2)


def scenario_3_different_category(sock: socket.socket, delay: float):
    """Same IPs but different alert category → NOT duplicate."""
    print_header("Scenario 3: Different Category")
    print("  Same IPs but different alert names → different hash.\n")

    alert_1 = {
        "sourceIps": ["172.16.0.100"],
        "destIps": ["8.8.8.8"],
        "alertCategory": "DNS Tunneling",
        "source": "elastic",
        "alertId": "el-001",
    }
    r1 = send_alert(sock, alert_1)
    print_result("Alert 1 (DNS Tunneling)", r1)

    time.sleep(delay)

    alert_2 = {
        "sourceIps": ["172.16.0.100"],
        "destIps": ["8.8.8.8"],
        "alertCategory": "Suspicious DNS Query",
        "source": "elastic",
        "alertId": "el-002",
    }
    r2 = send_alert(sock, alert_2)
    print_result("Alert 2 (Suspicious DNS Query — different category)", r2)


def scenario_4_ip_order(sock: socket.socket, delay: float):
    """Dest IPs in different order → same hash (sorted before hashing)."""
    print_header("Scenario 4: IP Order Doesn't Matter")
    print("  Same dest IPs in different order → sorted before hashing.\n")

    alert_1 = {
        "sourceIps": ["10.0.2.1"],
        "destIps": ["192.168.1.1", "192.168.1.2", "192.168.1.3"],
        "alertCategory": "Lateral Movement",
        "source": "crowdstrike",
        "alertId": "cs-010",
    }
    r1 = send_alert(sock, alert_1)
    print_result("Alert 1 (destIps: .1, .2, .3)", r1)

    time.sleep(delay)

    alert_2 = {
        "sourceIps": ["10.0.2.1"],
        "destIps": ["192.168.1.3", "192.168.1.1", "192.168.1.2"],  # shuffled
        "alertCategory": "Lateral Movement",
        "source": "crowdstrike",
        "alertId": "cs-011",
    }
    r2 = send_alert(sock, alert_2)
    print_result("Alert 2 (destIps: .3, .1, .2 — shuffled)", r2)


def scenario_5_crowdstrike_format(sock: socket.socket, delay: float):
    """Native CrowdStrike payload format."""
    print_header("Scenario 5: CrowdStrike Native Format")
    print("  Real CrowdStrike payload shape with DetectName, LocalIP, etc.\n")

    cs_alert = {
        "DetectId": "ldt:abc123:456",
        "DetectName": "Suspicious Process Execution",
        "LocalIP": "10.0.1.5",
        "NetworkAccesses": [
            {"RemoteAddress": "203.0.113.1", "RemotePort": 443},
            {"RemoteAddress": "203.0.113.2", "RemotePort": 80},
        ],
        "SHA256String": "e3b0c44298fc1c149afbf4c8996fb924",
        "CommandLine": "cmd.exe /c whoami",
    }
    r1 = send_alert(sock, cs_alert)
    print_result("CrowdStrike alert (first)", r1)

    time.sleep(delay)

    cs_alert["DetectId"] = "ldt:abc123:789"  # different ID, same observables
    r2 = send_alert(sock, cs_alert)
    print_result("CrowdStrike alert (replay — same observables)", r2)


def scenario_6_splunk_format(sock: socket.socket, delay: float):
    """Native Splunk payload format."""
    print_header("Scenario 6: Splunk Native Format")
    print("  Splunk notable with src_ip, dest_ip, search_name.\n")

    splunk_alert = {
        "search_name": "TS - Account Lockout - Rule",
        "src_ip": "10.0.5.10",
        "dest_ip": "10.0.5.1",
        "event_id": "notable-001",
    }
    r1 = send_alert(sock, splunk_alert)
    print_result("Splunk notable (first)", r1)

    time.sleep(delay)

    splunk_alert["event_id"] = "notable-002"
    r2 = send_alert(sock, splunk_alert)
    print_result("Splunk notable (replay)", r2)


def scenario_7_sentinel_weak_hash(sock: socket.socket, delay: float):
    """Sentinel has no dest IPs → weak fingerprint. Two different targets
    with the same source IP and alert name will collide."""
    print_header("Scenario 7: Sentinel Weak Fingerprint")
    print("  Sentinel provides no dest IPs. Two alerts targeting DIFFERENT")
    print("  hosts but same source IP + alert name → FALSE POSITIVE dedup.\n")

    sentinel_1 = {
        "AlertName": "Suspicious Sign-in Activity",
        "Entities": json.dumps([
            {"Type": "ip", "Address": "10.0.3.50"},
            {"Type": "host", "HostName": "SERVER-A"},
        ]),
        "SystemAlertId": "sentinel-001",
    }
    r1 = send_alert(sock, sentinel_1)
    print_result("Sentinel alert (target: SERVER-A)", r1)

    time.sleep(delay)

    sentinel_2 = {
        "AlertName": "Suspicious Sign-in Activity",  # same alert name
        "Entities": json.dumps([
            {"Type": "ip", "Address": "10.0.3.50"},  # same source IP
            {"Type": "host", "HostName": "SERVER-B"},  # DIFFERENT target
        ]),
        "SystemAlertId": "sentinel-002",
    }
    r2 = send_alert(sock, sentinel_2)
    print_result("Sentinel alert (target: SERVER-B — DIFFERENT host, same hash!)", r2)

    if r2["status"] == "DUPLICATE":
        print("\n  ⚠ FALSE POSITIVE: These are different alerts but hash-dedup")
        print("    can't tell because Sentinel provides no dest IPs or hostnames")
        print("    in the hash input. The hash is just: srcIp||AlertName|bucket")


def scenario_8_stats(sock: socket.socket, _delay: float):
    """Request engine stats."""
    print_header("Engine Stats")
    stats = get_stats(sock)
    print(f"  Total processed: {stats['total']}")
    print(f"  New alerts:      {stats['new']}")
    print(f"  Duplicates:      {stats['duplicates']}")
    print(f"  Dedup rate:      {stats['dedup_rate']}")
    print(f"  Active entries:  {stats['active_entries']}")


SCENARIOS = {
    1: ("Exact Duplicate", scenario_1_exact_duplicate),
    2: ("IP Subset Matching", scenario_2_ip_subset),
    3: ("Different Category", scenario_3_different_category),
    4: ("IP Order Invariance", scenario_4_ip_order),
    5: ("CrowdStrike Native Format", scenario_5_crowdstrike_format),
    6: ("Splunk Native Format", scenario_6_splunk_format),
    7: ("Sentinel Weak Fingerprint", scenario_7_sentinel_weak_hash),
    8: ("Engine Stats", scenario_8_stats),
}


def main():
    parser = argparse.ArgumentParser(description="Alert Streamer for Hash Dedup Engine")
    parser.add_argument("--port", type=int, default=9800, help="Engine port (default: 9800)")
    parser.add_argument("--host", default="localhost", help="Engine host (default: localhost)")
    parser.add_argument("--scenario", type=int, default=None, help="Run specific scenario (1-8)")
    parser.add_argument("--delay", type=float, default=0.3, help="Delay between alerts in seconds (default: 0.3)")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.connect((args.host, args.port))
    except ConnectionRefusedError:
        print(f"Cannot connect to {args.host}:{args.port}")
        print("Start the engine first: python engine.py")
        return

    print(f"Connected to Hash Dedup Engine at {args.host}:{args.port}")

    if args.scenario:
        if args.scenario in SCENARIOS:
            _, fn = SCENARIOS[args.scenario]
            fn(sock, args.delay)
        else:
            print(f"Unknown scenario {args.scenario}. Available: {list(SCENARIOS.keys())}")
    else:
        for num, (_, fn) in SCENARIOS.items():
            fn(sock, args.delay)

    print()
    sock.close()


if __name__ == "__main__":
    main()
