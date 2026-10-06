# Transformers for the disposition router

Does a transformer beat TF-IDF at routing an alert to an AI or a human analyst?

**No.** Every variant tried lands 0.02–0.05 ROC below `ml/v9`, the linear TF-IDF
router, on the same forward split. This folder is the record of why.

All numbers below are the 70/30 forward split of `machine-learning/dataset12.parquet`
(22,699 train / 9,728 test, 22.6% human-lane), so they compare directly with each
other and with `ml/v9` measured the same way.

| Approach | ROC | PR | Accuracy | F1 |
| --- | ---: | ---: | ---: | ---: |
| **`ml/v9` — TF-IDF + SGD** | **0.8097** | **0.5549** | **80.22%** | **42.87%** |
| ATTACK-BERT fine-tuned, classifies directly | 0.7866 | 0.5006 | 78.28% | 39.19% |
| fine-tuned ATTACK-BERT → embedding → SGD | 0.7822 | 0.4922 | 79.18% | 30.10% |
| frozen ATTACK-BERT, chunked → SGD | 0.7596 | 0.4638 | 77.45% | 34.74% |
| frozen ATTACK-BERT, truncated 512 → SGD | 0.7588 | 0.4656 | 77.49% | 35.78% |

## The context-length hypothesis, and its refutation

The obvious explanation for the gap was that the transformer simply could not see
the alert. `text_cef` is a median 612 tokens; ATTACK-BERT stops at 512. At the 256
tokens the fine-tune ran on, **5.5%** of alerts fitted whole.

So the whole alert was fed in anyway, by splitting it into 512-token windows,
encoding each and mean-pooling them - 32,427 alerts became 60,744 windows, 1.87
per alert, 100% coverage.

**It made no difference: 0.7596 chunked against 0.7588 truncated.** Reading the
entire alert instead of 44% of it is worth +0.0008 ROC.

That is the useful finding here, because it closes the question rather than
deferring it. Context is not the bottleneck, so a long-context encoder would not
have helped either, and there is no point paying for one.

## Why a transformer loses on this data

The signal is **lexical identity, not semantics**. What separates a benign alert
from a real one is *which* service account, *which* subnet, *which* detection
name - a specific token that recurs and always closes the same way. TF-IDF keeps
168,679 such tokens as independent dimensions. Mean-pooling a transformer's last
hidden state into 768 dimensions averages exactly that detail away: one
distinctive username in a 600-token alert barely moves the mean.

ATTACK-BERT is also pretrained to make *ATT&CK techniques* similar, which is a
genuinely different notion of similarity from *ruled the same way*. Fine-tuning
recovers some of it - it is worth +0.023 ROC over the frozen encoder - but not
enough to close the gap.

## ModernBERT: right idea, wrong hardware

`answerdotai/ModernBERT-base` reaches 8192 tokens and would read every alert
whole. It is unusable here:

| | MPS | CPU |
| --- | ---: | ---: |
| ModernBERT-base | 126,467 ms/step | 1,443 ms/step |
| ATTACK-BERT | ~1,100 ms/step | 1,499 ms/step |

**88x slower on the GPU than the CPU** - its unpadding and rotary-embedding
kernels have no Metal implementation and fall back to scalar ops. On CPU it is
~136 min/epoch, about 7 hours for three epochs. Given that chunking showed
context is not the constraint, that spend is not justified.

`finetune12.py --encoder` accepts any HF encoder, with a `CONTEXT` table that
refuses a `--max-len` the model cannot honour.

## Scripts

    finetune12.py    fine-tune an encoder to classify directly
    embed12.py       embed with a frozen or fine-tuned encoder; --chunked for full coverage
    sgd_embed12.py   embeddings -> SGDClassifier, swept over alpha

`--order priority` re-emits the alert's fields most-informative-first, so a
truncation spends its budget on signal rather than on alphabetical accident (the
blob is built as sorted `key value` pairs, so truncating it raw keeps
`applicationid` and drops `user`).

## Stale

`models/v2`, `v3`, `v4`, `finetuned/model__text_title` and the `attack-bert__*`
/ `minilm__*` embeddings were built against the retired 90-day table and cannot
be re-verified against anything that still exists.
