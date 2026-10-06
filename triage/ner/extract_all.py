"""Extract entities from every training alert, once, capturing everything.

This run costs about an hour, so it is written to be run once and never again:
the CSV keeps **every span the model emitted**, at every confidence, under every
label - including USERNAME, which we currently distrust. Any later decision about
thresholds or which labels to keep is then a pandas filter, not another GPU hour.

Output columns
--------------
    container_id, close_time, split, disposition, y_human   join keys and label
    has_notable, notable_keys, text_chars, text_tokens       provenance
    n_windows, covered_pct                                   how much was read
    entities_all      JSON array of every span: label, value, score, start, end
    entities_hi       the same, filtered to score >= 0.999 (all labels)
    n_all, n_hi       counts
    all_<LABEL>       unique values for that label, any score, "|" separated
    hi_<LABEL>        unique values for that label at >= 0.999
    n_all_<LABEL>, n_hi_<LABEL>                              per-label counts

Windowing
---------
The rendered alert is a median 586 tokens but reaches 106,557 - one alert is
332 KB. Truncating at the model's 8192 limit would silently drop entities from
the tail of the long ones, so the text is cut into overlapping character windows,
each is encoded separately, and span offsets are mapped back to the global
string. `covered_pct` records how much of the text was actually read; it is 100
for every alert unless a cap is hit.

    python3 extract_all.py --n 200 --out sample.csv     # validate the schema
    python3 extract_all.py --out entities.csv           # the full run
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

import torch

import local_ner as L

HERE = Path(__file__).resolve().parent
ML = HERE.parent / "machine-learning"
LABELS = ["EMAIL", "IPV4", "IPV6", "MACHINE", "FILEPATH", "FILENAME", "HASH", "URL",
          "USERNAME"]
HI = 0.999
WINDOW_CHARS = 6000        # ~1,875 tokens at the 3.2 chars/token seen here
OVERLAP_CHARS = 500
MAX_LEN = 2048
MAX_WINDOWS = 200          # 200 x 6k = 1.2 MB, past the largest alert


def windows(text: str):
    """(offset, chunk) pairs covering the whole string, with overlap."""
    if not text:
        return []
    step = WINDOW_CHARS - OVERLAP_CHARS
    out = [(i, text[i:i + WINDOW_CHARS])
           for i in range(0, len(text), step)][:MAX_WINDOWS]
    return out


@torch.no_grad()
def spans_for(chunks, tok, model, cfg, device, batch, max_len):
    """Every BIO span in each chunk, unfiltered - label, value, score, offsets."""
    results = []
    for i in range(0, len(chunks), batch):
        block = chunks[i:i + batch]
        enc = tok(block, truncation=True, max_length=max_len, padding=True,
                  return_tensors="pt", return_offsets_mapping=True)
        offsets = enc.pop("offset_mapping")
        logits = model(**{k: v.to(device) for k, v in enc.items()}).logits
        probs = torch.softmax(logits.float(), dim=-1).cpu()
        score, idx = probs.max(-1)
        for row, text in enumerate(block):
            found, current = [], None
            for pos in range(enc["input_ids"].shape[1]):
                start, end = offsets[row][pos].tolist()
                if start == end:
                    continue
                label = cfg.id2label[int(idx[row][pos])]
                conf = float(score[row][pos])
                tag, _, kind = label.partition("-")
                if tag == "O" or not kind:
                    current = None
                    continue
                if tag == "B" or current is None or current["label"] != kind:
                    current = {"label": kind, "start": start, "end": end, "score": conf}
                    found.append(current)
                else:
                    current["end"] = end
                    current["score"] = min(current["score"], conf)
            results.append(found)
    return results


def dedupe(spans):
    """Overlapping windows re-find the same span; keep one per (label, value, start)."""
    seen, out = set(), []
    for s in sorted(spans, key=lambda s: (s["start"], s["label"])):
        key = (s["label"], s["value"], s["start"])
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def rows_from_source(limit: int | None):
    csv.field_size_limit(sys.maxsize)
    pre = {}
    with (ML / "training-data" / "preprocessed.csv").open() as fh:
        for r in csv.DictReader(fh):
            pre[r["container_id"]] = (r["split"], r["close_time"])
    with (ML / "training-data" / "original.csv").open() as fh:
        for i, r in enumerate(csv.DictReader(fh)):
            if limit is not None and i >= limit:
                return
            split, close = pre.get(r["container_id"], ("", ""))
            notable = json.loads(r["notable_json"]) if r["notable_json"] else None
            yield {"container_id": r["container_id"], "close_time": close,
                   "split": split, "disposition": r["disposition"],
                   "y_human": r["y_human"], "has_notable": r["has_notable"],
                   "notable_keys": r["notable_keys"], "notable": notable}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=None)
    ap.add_argument("--out", default="entities.csv")
    ap.add_argument("--device", default="auto")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--max-len", type=int, default=MAX_LEN)
    args = ap.parse_args()

    device = L.pick_device(args.device)
    tok, model, cfg = L.load(device)
    print(f"{L.MODEL.name} on {device} · max_len {args.max_len} · "
          f"windows {WINDOW_CHARS} chars / {OVERLAP_CHARS} overlap", flush=True)

    out_path = ML / "training-data" / args.out
    fields = (["container_id", "close_time", "split", "disposition", "y_human",
               "has_notable", "notable_keys", "text_chars", "n_windows",
               "covered_pct", "n_all", "n_hi", "entities_all", "entities_hi"]
              + [f"{p}_{lab}" for lab in LABELS for p in ("all", "hi")]
              + [f"n_{p}_{lab}" for lab in LABELS for p in ("all", "hi")])

    started, done, ents_total = time.perf_counter(), 0, 0
    with out_path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for rec in rows_from_source(args.n):
            text = L.render(rec["notable"]) if rec["notable"] else ""
            wins = windows(text)
            spans = []
            if wins:
                found = spans_for([c for _, c in wins], tok, model, cfg,
                                  device, args.batch, args.max_len)
                for (offset, chunk), got in zip(wins, found):
                    for s in got:
                        value = chunk[s["start"]:s["end"]].strip()
                        if value:
                            spans.append({"label": s["label"], "value": value,
                                          "score": round(s["score"], 6),
                                          "start": offset + s["start"],
                                          "end": offset + s["end"]})
                spans = dedupe(spans)
            hi = [s for s in spans if s["score"] >= HI]
            covered = (min(len(text), MAX_WINDOWS * WINDOW_CHARS) / len(text) * 100
                       if text else 0.0)

            row = {"container_id": rec["container_id"], "close_time": rec["close_time"],
                   "split": rec["split"], "disposition": rec["disposition"],
                   "y_human": rec["y_human"], "has_notable": rec["has_notable"],
                   "notable_keys": rec["notable_keys"], "text_chars": len(text),
                   "n_windows": len(wins), "covered_pct": round(covered, 2),
                   "n_all": len(spans), "n_hi": len(hi),
                   "entities_all": json.dumps(spans, separators=(",", ":")),
                   "entities_hi": json.dumps(hi, separators=(",", ":"))}
            for lab in LABELS:
                a = list(dict.fromkeys(s["value"] for s in spans if s["label"] == lab))
                h = list(dict.fromkeys(s["value"] for s in hi if s["label"] == lab))
                row[f"all_{lab}"] = "|".join(a)
                row[f"hi_{lab}"] = "|".join(h)
                row[f"n_all_{lab}"] = len(a)
                row[f"n_hi_{lab}"] = len(h)
            writer.writerow(row)

            done += 1
            ents_total += len(spans)
            if done % 250 == 0:
                rate = (time.perf_counter() - started) / done
                print(f"  {done:6,d} alerts · {ents_total:7,d} spans · "
                      f"{rate*1000:5.0f} ms/alert · eta "
                      f"{rate*(32427-done)/60:5.1f} min", flush=True)

    dt = time.perf_counter() - started
    print(f"\n{done:,} alerts in {dt/60:.1f} min · {dt/done*1000:.0f} ms/alert")
    print(f"{ents_total:,} spans captured -> training-data/{args.out}")


if __name__ == "__main__":
    main()
