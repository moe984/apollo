"""Run securebert2-ner-alerts-v2 locally, no API round trip.

The endpoint costs ~2 s per alert, which is 18 hours for the full 32,427. This
loads the same family of model from disk and runs it in-process, so the whole
corpus becomes a batched offline job.

    python3 local_ner.py --n 5              # extract from 5 real alerts
    python3 local_ner.py --n 200 --device cpu --batch 8

A NOTE ON DEVICE. ModernBERT *training* is pathological on MPS - measured on this
machine at 126,467 ms/step against 1,443 on CPU, ~88x slower, because the
backward pass through its unpadding and rotary kernels has no Metal path.

**Inference is not.** Measured on 24 real alerts:

    cpu   246 ms/alert   ->  2.21 h for 32,427
    mps   117 ms/alert   ->  1.05 h for 32,427

So `--device auto` prefers MPS. The training result does not generalise to the
forward pass, and it was wrong of me to assume it would.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import torch
from transformers import AutoConfig, AutoModelForTokenClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
MODEL = HERE / "models" / "securebert2-ner-alerts-v2"
ML = HERE.parent / "machine-learning"

# See tally.py: at a 0.999 floor these eight were right on 164/164 spans, while
# USERNAME was ~48% - CEF field names and severity words tagged as people.
TRUST = {"EMAIL", "IPV4", "IPV6", "MACHINE", "FILEPATH", "FILENAME", "HASH", "URL"}
MIN_SCORE = 0.999
SKIP = {"_bkt", "_cd", "_si", "_indextime", "_sourcetype", "_time", "_eventtype_color",
        "event_id", "rule_id", "orig_sid", "orig_rid", "source_event_id", "source_guid",
        "info_max_time", "info_min_time", "info_search_time", "timeendpos", "timestartpos",
        "investigation_profiles", "extract_artifacts", "contributing_events_search"}


def pick_device(choice: str) -> str:
    if choice != "auto":
        return choice
    if torch.cuda.is_available():
        return "cuda"
    return "mps" if torch.backends.mps.is_available() else "cpu"


def load(device: str):
    cfg = AutoConfig.from_pretrained(MODEL)
    if hasattr(cfg, "reference_compile"):
        cfg.reference_compile = False        # torch.compile is a trap off-CUDA
    tok = AutoTokenizer.from_pretrained(MODEL)
    model = AutoModelForTokenClassification.from_pretrained(
        MODEL, config=cfg, attn_implementation="sdpa").to(device).eval()
    return tok, model, cfg


def render(notable: dict) -> str:
    """The alert as `key: value` lines - the shape the endpoint renders JSON into."""
    return "\n".join(f"{k}: {v}" for k, v in sorted(notable.items()) if k not in SKIP)


@torch.no_grad()
def extract(texts, tok, model, cfg, device, batch=8, max_len=1024,
            min_score=MIN_SCORE, trust=TRUST):
    """BIO spans above `min_score`, merged, for each text."""
    out = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        enc = tok(chunk, truncation=True, max_length=max_len, padding=True,
                  return_tensors="pt", return_offsets_mapping=True)
        offsets = enc.pop("offset_mapping")
        logits = model(**{k: v.to(device) for k, v in enc.items()}).logits
        probs = torch.softmax(logits.float(), dim=-1).cpu()
        score, idx = probs.max(-1)
        for row, text in enumerate(chunk):
            spans, current = [], None
            for pos in range(enc["input_ids"].shape[1]):
                start, end = offsets[row][pos].tolist()
                if start == end:                      # special token
                    continue
                label = cfg.id2label[int(idx[row][pos])]
                conf = float(score[row][pos])
                tag, _, kind = label.partition("-")
                if tag == "O" or not kind:
                    current = None
                    continue
                if tag == "B" or current is None or current["label"] != kind:
                    current = {"label": kind, "start": start, "end": end, "score": conf}
                    spans.append(current)
                else:                                  # I- continues the span
                    current["end"] = end
                    current["score"] = min(current["score"], conf)
            kept = []
            for s in spans:
                if s["score"] < min_score or s["label"] not in trust:
                    continue
                # offsets include the leading space the tokenizer attached to the
                # word, so the raw slice comes back as " user@example.com"
                value = text[s["start"]:s["end"]].strip()
                if value:
                    kept.append(dict(s, value=value))
            out.append(kept)
    return out


def sample_alerts(n: int):
    csv.field_size_limit(sys.maxsize)
    with (ML / "training-data" / "original.csv").open() as fh:
        rows = []
        for row in csv.DictReader(fh):
            if row["has_notable"] == "True" and row["notable_json"]:
                rows.append((row["container_id"], row["disposition"],
                             json.loads(row["notable_json"])))
            if len(rows) >= n:
                return rows
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--device", default="auto", choices=["auto", "cpu", "mps", "cuda"])
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=1024)
    ap.add_argument("--min-score", type=float, default=MIN_SCORE)
    ap.add_argument("--all-labels", action="store_true")
    args = ap.parse_args()

    device = pick_device(args.device)
    t0 = time.perf_counter()
    tok, model, cfg = load(device)
    print(f"{MODEL.name} on {device} · loaded in {time.perf_counter()-t0:.1f}s")
    print(f"  {sum(p.numel() for p in model.parameters())/1e6:.0f}M params · "
          f"context {cfg.max_position_embeddings} · labels {len(cfg.id2label)}")

    alerts = sample_alerts(args.n)
    texts = [render(n) for _, _, n in alerts]
    trust = None if args.all_labels else TRUST
    t0 = time.perf_counter()
    found = extract(texts, tok, model, cfg, device, args.batch, args.max_len,
                    args.min_score, trust if trust else set(cfg.id2label.values()))
    dt = time.perf_counter() - t0
    per = dt / len(texts)
    print(f"  {len(texts)} alerts in {dt:.1f}s · {per*1000:,.0f} ms/alert · "
          f"32,427 would take {per*32427/3600:.1f} h\n")

    for (cid, dispo, _), ents in list(zip(alerts, found))[:8]:
        by = {}
        for e in ents:
            by.setdefault(e["label"], []).append(f"{e['value']}({e['score']:.4f})")
        print(f"── {cid}  ({dispo})  {len(ents)} entities")
        for label in sorted(by):
            print(f"     {label:9s} {', '.join(by[label][:4])}")


if __name__ == "__main__":
    main()
