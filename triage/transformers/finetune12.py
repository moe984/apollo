"""Fine-tune ATTACK-BERT on the 12-month table to classify alerts directly.

The frozen-encoder question is *do ATTACK-BERT's existing notions of similarity
help?* This asks the harder one: *can the encoder learn what the analysts'
verdicts look like?* The weights move, so the vector space reshapes around the
labels - two alerts that read alike but get ruled differently can end up far
apart, which a frozen encoder cannot express.

    .venv/bin/python finetune12.py --field text_cef --max-len 256 --epochs 3
    .venv/bin/python finetune12.py --field text_name --max-len 64 --epochs 3

THE CONSTRAINT THAT SHAPES THIS. `text_cef` is a median 612 tokens against
ATTACK-BERT's hard 512 cap - only 29% of alerts fit whole. The transformer
therefore reads a fraction of what the TF-IDF router reads, and no amount of
fine-tuning recovers what was truncated away.

Worse, the blob's keys are sorted alphabetically, so a truncation keeps
`applicationid` and drops `user` for no better reason than the letter it starts
with. `--order priority` re-emits the fields most likely to matter first, so the
budget is spent on signal rather than on alphabet. `--order asis` keeps the blob
exactly as the router sees it, for the honest like-for-like number.

Trains on the first 70% by close_time and evaluates on the 30% that came after -
the same forward split as every other model here.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from sklearn.metrics import (accuracy_score, average_precision_score, f1_score,
                             precision_score, recall_score, roc_auc_score)
from torch.utils.data import DataLoader, TensorDataset
from transformers import AutoModelForSequenceClassification, AutoTokenizer

HERE = Path(__file__).resolve().parent
DATASET = HERE.parent / "machine-learning" / "dataset12.parquet"
ENCODER = "basel/ATTACK-BERT"
SEED = 7

# The cap that decides this experiment. ATTACK-BERT, SecureBERT, SecBERT and
# DeBERTa-v3 all stop at 512 tokens; the alerts are a median 612 and reach 5,301,
# so a 512-token encoder reads part of the alert and guesses the rest. ModernBERT
# and the gte/nomic retrieval encoders reach 8192, which fits every alert whole.
CONTEXT = {
    "basel/ATTACK-BERT": 512,
    "ehsanaghaei/SecureBERT": 512,
    "jackaduma/SecBERT": 512,
    "microsoft/deberta-v3-small": 512,
    "answerdotai/ModernBERT-base": 8192,
    "Alibaba-NLP/gte-base-en-v1.5": 8192,
    "nomic-ai/nomic-embed-text-v1.5": 8192,
}

# Fields worth spending a truncated context on, best first. Drawn from what the
# linear router actually weights: the detection's own names, then the entities,
# then the ES grading. Everything not listed keeps its alphabetical place after.
PRIORITY = (
    "rule_name", "search_name", "container_name", "rule_title", "rule_description",
    "signature", "label", "security_domain", "severity", "urgency", "app",
    "user", "destinationusername", "sourceusername", "user_email",
    "userprincipalname", "accountname", "user_category", "user_priority",
    "user_watchlist", "user_bunit", "src", "dest", "sourceaddress",
    "destinationaddress", "ipaddress", "clientip", "destinationhostname",
    "computer", "dvc", "filename", "filepath", "process", "country", "city",
    "os", "browsertype", "risk_message", "risk_object", "indicator",
)
# fields that are long and say little - JSON blobs and opaque identifiers
NOISE = ("extract_artifacts", "investigation_profiles", "applicationid",
         "user_identity_id", "normalized_risk_object", "_serial")


def reorder(blob: str) -> str:
    """Re-emit `key value` pairs with the informative keys first.

    The blob is built as sorted `key value` pairs, so splitting on the keys
    recovers them. A pair whose key is unknown keeps its original order, after
    the prioritised ones; the noisy keys go last, where truncation eats them.
    """
    parts = blob.split()
    if not parts:
        return blob
    ranked: dict[str, int] = {k: i for i, k in enumerate(PRIORITY)}
    head, tail, junk = [], [], []
    current, buf = None, []

    def flush():
        if current is None:
            return
        chunk = current + " " + " ".join(buf)
        if current in NOISE:
            junk.append((0, chunk))
        elif current in ranked:
            head.append((ranked[current], chunk))
        else:
            tail.append((0, chunk))

    for token in parts:
        if token in ranked or token in NOISE:
            flush()
            current, buf = token, []
        elif current is None:
            current, buf = token, []
        else:
            buf.append(token)
    flush()
    head.sort(key=lambda p: p[0])
    return " ".join(c for _, c in head + tail + junk)


def device() -> str:
    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def coverage(y, score, allowed=0):
    order = np.argsort(score)
    hit = np.asarray(y)[order].cumsum()
    ok = np.flatnonzero(hit <= allowed)
    return (ok[-1] + 1) / len(y) * 100 if len(ok) else 0.0


@torch.no_grad()
def predict(model, loader, dev):
    model.eval()
    out = []
    for ids, mask, _ in loader:
        logits = model(input_ids=ids.to(dev), attention_mask=mask.to(dev)).logits
        out.append(torch.softmax(logits.float(), dim=-1)[:, 1].cpu().numpy())
    return np.concatenate(out)


def report(tag, y, score):
    roc = roc_auc_score(y, score)
    pr = average_precision_score(y, score)
    pred = (score >= 0.5).astype(int)
    cov = [coverage(y, score, int(round(r / 100 * y.sum()))) for r in (0.5, 1, 2, 5)]
    print(f"  {tag:18s} ROC {roc:.4f}  PR {pr:.4f}  "
          f"acc {accuracy_score(y, pred)*100:5.2f}%  "
          f"prec {precision_score(y, pred, zero_division=0)*100:5.2f}%  "
          f"rec {recall_score(y, pred)*100:5.2f}%  F1 {f1_score(y, pred)*100:5.2f}%  "
          "| AI% @miss 0.5/1/2/5: " + " ".join(f"{c:5.1f}" for c in cov), flush=True)
    return roc, pr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--encoder", default=ENCODER,
                    help="any HF encoder; see CONTEXT for the ones whose limits are known")
    ap.add_argument("--field", default="text_cef")
    ap.add_argument("--order", default="asis", choices=["asis", "priority"])
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args()

    torch.manual_seed(SEED)
    np.random.seed(SEED)
    dev = device()

    frame = pd.read_parquet(DATASET).sort_values("t").reset_index(drop=True)
    text = frame[args.field].astype(str)
    if args.order == "priority":
        text = text.map(reorder)
    n_train = int((frame["t"] <= frame["t"].quantile(0.70)).sum())
    y_test = frame["y_human"].to_numpy()[n_train:]

    tok = AutoTokenizer.from_pretrained(args.encoder)
    limit = CONTEXT.get(args.encoder, getattr(tok, "model_max_length", 512))
    if args.max_len > limit:
        raise SystemExit(f"{args.encoder} caps at {limit} tokens, asked for {args.max_len}")
    enc = tok(text.tolist(), truncation=True, max_length=args.max_len,
              padding="max_length", return_tensors="pt")
    labels = torch.tensor(frame["y_human"].to_numpy(), dtype=torch.long)
    data = TensorDataset(enc["input_ids"], enc["attention_mask"], labels)
    g = torch.Generator().manual_seed(SEED)
    train_dl = DataLoader(torch.utils.data.Subset(data, range(n_train)),
                          batch_size=args.batch, shuffle=True, generator=g)
    test_dl = DataLoader(torch.utils.data.Subset(data, range(n_train, len(data))),
                         batch_size=args.batch * 2)

    model = AutoModelForSequenceClassification.from_pretrained(
        args.encoder, num_labels=2).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.OneCycleLR(
        opt, max_lr=args.lr, total_steps=len(train_dl) * args.epochs,
        pct_start=0.1, anneal_strategy="linear")

    kept = (enc["attention_mask"].sum(1) < args.max_len).float().mean().item()
    print(f"fine-tuning {args.encoder} on {args.field} ({args.order} order)")
    print(f"  {n_train:,} train / {len(frame)-n_train:,} test · device {dev} · "
          f"max_len {args.max_len} · batch {args.batch} · lr {args.lr:g} · "
          f"{args.epochs} epochs")
    print(f"  {kept*100:.1f}% of alerts fit inside {args.max_len} tokens "
          f"(the rest are truncated)\n", flush=True)

    best = (-1.0, None, None)
    for epoch in range(1, args.epochs + 1):
        model.train()
        started, total = time.perf_counter(), 0.0
        for ids, mask, lab in train_dl:
            opt.zero_grad()
            loss = model(input_ids=ids.to(dev), attention_mask=mask.to(dev),
                         labels=lab.to(dev)).loss
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            total += loss.item()
        score = predict(model, test_dl, dev)
        print(f"epoch {epoch}  loss {total/len(train_dl):.4f}  "
              f"{(time.perf_counter()-started)/60:.1f} min", flush=True)
        _, pr = report(f"after epoch {epoch}", y_test, score)
        if pr > best[0]:
            best = (pr, epoch, score)

    print(f"\nbest epoch by PR AUC: {best[1]}", flush=True)
    roc, pr = report("BEST", y_test, best[2])

    out = HERE / "finetuned"
    out.mkdir(exist_ok=True)
    slug = args.encoder.split("/")[-1].lower()
    tag = f"{slug}__{args.field}__{args.order}__{args.max_len}"
    np.save(out / f"scores__{tag}.npy", best[2])
    (out / f"result__{tag}.json").write_text(json.dumps({
        "encoder": args.encoder, "field": args.field, "order": args.order,
        "max_len": args.max_len, "epochs": args.epochs, "best_epoch": best[1],
        "lr": args.lr, "batch": args.batch,
        "fits_in_context_pct": round(kept * 100, 1),
        "roc_auc": round(float(roc), 4), "pr_auc": round(float(pr), 4),
        "train_rows": n_train, "test_rows": len(frame) - n_train,
        "dataset": "dataset12.parquet", "dataset_rows": len(frame),
    }, indent=2) + "\n")
    if args.save:
        model.save_pretrained(out / f"model__{tag}")
        tok.save_pretrained(out / f"model__{tag}")
    print(f"scores -> finetuned/scores__{tag}.npy", flush=True)


if __name__ == "__main__":
    main()
