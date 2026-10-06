"""How reliable is each entity type at a given confidence floor?"""
import collections, json, re, sys
import sample_ner as S

N = int(sys.argv[1]) if len(sys.argv) > 1 else 30
FLOOR = float(sys.argv[2]) if len(sys.argv) > 2 else 0.999

# tokens that are obviously not the entity they were tagged as: CEF field names,
# severity/status vocabulary, and bare hex (ids and hashes, not people)
JUNK = {"high", "medium", "low", "critical", "user", "unassigned", "un", "igned",
        "notable", "alert", "true", "false", "none", "new", "generic", "threat",
        "access", "endpoint", "audit", "system", "unknown", "informational"}
HEX = re.compile(r"^[0-9a-f]{8,}$", re.I)

by_type = collections.defaultdict(collections.Counter)
judged = collections.defaultdict(lambda: [0, 0])   # label -> [clean, junk]
lat, n = [], 0

for cid, dispo, notable in S.sample_alerts(N):
    code, resp, ms = S.call("/api/ner/extract", {"text": json.dumps(
        {k: v for k, v in notable.items() if k not in S.SKIP})})
    if code != 200:
        continue
    lat.append(ms); n += 1
    for e in resp.get("entities") or []:
        if e.get("score", 0) < FLOOR or e["label"] not in S.TRUST:
            continue
        v = str(e["value"]).strip()
        by_type[e["label"]][v] += 1
        bad = v.lower() in JUNK or (HEX.match(v) and e["label"] not in ("HASH",)) or len(v) < 3
        judged[e["label"]][1 if bad else 0] += 1

print(f"{n} alerts · score >= {FLOOR} · trusted labels only "
      f"({', '.join(sorted(S.TRUST))}) · mean {sum(lat)/len(lat):,.0f} ms\n")
print(f"{'type':10s} {'kept':>5s} {'clean':>6s} {'junk':>5s} {'precision':>10s}   most common values")
for label in sorted(by_type, key=lambda l: -sum(by_type[l].values())):
    clean, junk = judged[label]
    tot = clean + junk
    top = ", ".join(f"{v}({c})" for v, c in by_type[label].most_common(5))
    print(f"{label:10s} {tot:5d} {clean:6d} {junk:5d} {clean/tot*100:9.0f}%   {top[:78]}")
