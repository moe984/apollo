#!/usr/bin/env python3
"""Send one alert (or the same alert N times) to Apollo's SOAR ingest endpoint
and save exactly what the endpoint returns.

    POST {APOLLO_URL}/api/v1/ingest/soar   (x-api-key + x-client-name)

Config: APOLLO_URL and APOLLO_API_KEY from the .env next to this script.
Output: printed, and saved to captures/<timestamp>.txt next to this script.

Usage:
  python3 send_alert.py                  # 1 alert, client TekStream
  python3 send_alert.py --times 2        # same alert twice (2nd -> DUPLICATE)
  python3 send_alert.py --client LSU
  python3 send_alert.py --payload my_alert.json   # send your own body as-is
"""
import argparse
import json
import os
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ENDPOINT = "/api/v1/ingest/soar"


def load_env():
    env = {}
    with open(os.path.join(HERE, ".env")) as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def sample_alert(client, container_id, tag):
    # Same content on every send; only the container id / sdi change.
    return {
        "container": {
            "id": container_id,
            "name": "Palo Alto - Critical Threat Allowed Inbound",
            "tenant_name": client,
            "severity": "high",
            "source_data_identifier": f"{client.lower()}-{container_id}",
        },
        "artifacts": [{
            "label": "artifact",
            "name": "network-observable",
            "cef": {
                "ClientName": client,
                "sourceAddress": "10.20.30.40",
                "destinationAddress": "172.16.5.6",
                "sourceUserName": f"user_{tag}",
                "destinationHostName": f"host-1.corp{tag}.example",
                "requestURL": f"http://c2.badnet{tag}.example/payload",
            },
        }],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="TekStream")
    ap.add_argument("--times", type=int, default=1, help="send the same alert N times")
    ap.add_argument("--payload", help="JSON file to send as the body instead of the sample")
    args = ap.parse_args()

    env = load_env()
    url = env["APOLLO_URL"].rstrip("/") + ENDPOINT
    tag = int(time.time())

    os.makedirs(os.path.join(HERE, "captures"), exist_ok=True)
    out_path = os.path.join(HERE, "captures", time.strftime("%Y%m%d-%H%M%S") + ".txt")

    with open(out_path, "w") as out:
        for i in range(args.times):
            if args.payload:
                with open(args.payload) as fh:
                    body = json.load(fh)
            else:
                body = sample_alert(args.client, 950_000_000 + tag % 1_000_000 * 10 + i, tag)

            req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
            req.add_header("content-type", "application/json")
            req.add_header("x-api-key", env["APOLLO_API_KEY"])
            req.add_header("x-client-name", args.client)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    status, text = resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                status, text = e.code, e.read().decode()

            try:
                text = json.dumps(json.loads(text), indent=2)
            except ValueError:
                pass  # not JSON: keep it raw

            block = (f"===== #{i + 1}  POST {url}  HTTP {status}\n"
                     f"--- request ---\n{json.dumps(body, indent=2)}\n"
                     f"--- response ---\n{text}\n\n")
            print(block)
            out.write(block)

    print(f"Saved: {out_path}")


if __name__ == "__main__":
    main()
