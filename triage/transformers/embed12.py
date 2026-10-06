"""Embed the 12-month alerts with ATTACK-BERT, frozen or fine-tuned.

Mean-pooled last hidden state, L2-normalised - the recipe the sentence-transformers
wrapper uses, so a frozen vector and a fine-tuned one are directly comparable.

    .venv/bin/python embed12.py --field text_cef --order priority --max-len 256
    .venv/bin/python embed12.py --field text_cef --order priority --max-len 256 \
        --model finetuned/model__text_cef__priority__256

Deduplicates before encoding: 32,427 alerts carry about 30,000 distinct texts, so
the saving is small here, but it costs nothing and matters on the short fields
where duplication is heavy.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer

import finetune12 as FT

HERE = Path(__file__).resolve().parent
DATASET = HERE.parent / "machine-learning" / "dataset12.parquet"
OUT = HERE / "embeddings"


@torch.no_grad()
def embed_chunked(texts, model, tok, dev, window=512, stride=512, max_chunks=8, batch=32):
    """Embed the WHOLE alert by splitting it into windows and pooling.

    ATTACK-BERT stops at 512 tokens and the alerts are a median 612, so a single
    pass reads part of the alert and guesses the rest. Splitting into 512-token
    windows, encoding each, and mean-pooling the windows gives one 768-dim vector
    that has actually seen every token.

    This is the cheap way to buy the context a long-context encoder would have
    given us. ModernBERT reaches 8192 tokens but runs 88x slower on MPS than on
    CPU - its kernels have no Metal implementation - so it is not an option here.

    `max_chunks` caps the tail: p99 is about 3,600 tokens, and eight windows
    covers essentially everything without letting one pathological alert dominate
    the run.
    """
    encoder = getattr(model, "base_model", model)
    encoder.eval()
    pieces, owner = [], []
    for row, text in enumerate(texts):
        ids = tok(text, truncation=False, add_special_tokens=False)["input_ids"]
        if not ids:
            ids = tok("", add_special_tokens=False)["input_ids"] or [tok.pad_token_id]
        for start in range(0, min(len(ids), stride * max_chunks), stride):
            pieces.append(ids[start:start + window - 2])
            owner.append(row)
    print(f"  {len(texts):,} alerts -> {len(pieces):,} windows "
          f"({len(pieces)/len(texts):.2f} per alert)", flush=True)

    vecs = []
    for i in range(0, len(pieces), batch):
        block = pieces[i:i + batch]
        # transformers 5.x dropped build_inputs_with_special_tokens from the
        # tokenizer API, so wrap each window by hand
        wrapped = [[tok.cls_token_id] + p + [tok.sep_token_id] for p in block]
        enc = tok.pad({"input_ids": wrapped}, return_tensors="pt").to(dev)
        hidden = encoder(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        vecs.append(pooled.float().cpu().numpy())
        if (i // batch) % 100 == 0:
            print(f"  {i:,}/{len(pieces):,} windows", flush=True)
    V = np.vstack(vecs)

    out = np.zeros((len(texts), V.shape[1]), dtype=np.float32)
    counts = np.zeros(len(texts), dtype=np.float32)
    np.add.at(out, np.asarray(owner), V)
    np.add.at(counts, np.asarray(owner), 1.0)
    out /= np.maximum(counts, 1)[:, None]
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    return out / np.maximum(norms, 1e-9)


@torch.no_grad()
def embed(texts, model, tok, dev, max_len, batch=64):
    uniq = pd.unique(pd.Series(texts))
    index = {t: i for i, t in enumerate(uniq)}
    encoder = getattr(model, "base_model", model)
    encoder.eval()
    out = []
    for i in range(0, len(uniq), batch):
        enc = tok(list(uniq[i:i + batch]), truncation=True, max_length=max_len,
                  padding=True, return_tensors="pt").to(dev)
        hidden = encoder(**enc).last_hidden_state
        mask = enc["attention_mask"].unsqueeze(-1).float()
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        out.append(torch.nn.functional.normalize(pooled.float(), p=2, dim=1).cpu().numpy())
        if (i // batch) % 40 == 0:
            print(f"  {i:,}/{len(uniq):,} unique texts", flush=True)
    vectors = np.vstack(out)
    return vectors[[index[t] for t in texts]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--field", default="text_cef")
    ap.add_argument("--order", default="priority", choices=["asis", "priority"])
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--chunked", action="store_true",
                    help="cover the whole alert in 512-token windows, then pool")
    ap.add_argument("--model", default=None,
                    help="path to a fine-tuned checkpoint; omit for frozen ATTACK-BERT")
    args = ap.parse_args()

    dev = FT.device()
    frame = pd.read_parquet(DATASET).sort_values("t").reset_index(drop=True)
    text = frame[args.field].astype(str)
    if args.order == "priority":
        text = text.map(FT.reorder)

    src = args.model or FT.ENCODER
    tok = AutoTokenizer.from_pretrained(src)
    if args.model:
        model = AutoModelForSequenceClassification.from_pretrained(src).to(dev)
        tag = "finetuned"
    else:
        model = AutoModel.from_pretrained(src).to(dev)
        tag = "attack-bert"
    print(f"encoder {src} · device {dev} · {args.field} ({args.order}) · "
          f"max_len {args.max_len}", flush=True)

    if args.chunked:
        X = embed_chunked(text.tolist(), model, tok, dev)
        span = "chunked"
    else:
        X = embed(text.tolist(), model, tok, dev, args.max_len)
        span = str(args.max_len)
    OUT.mkdir(exist_ok=True)
    name = f"{tag}__{args.field}__{args.order}__{span}.npy"
    np.save(OUT / name, X.astype(np.float32))
    print(f"embedded {X.shape[0]:,} rows x {X.shape[1]} -> embeddings/{name}", flush=True)


if __name__ == "__main__":
    main()
