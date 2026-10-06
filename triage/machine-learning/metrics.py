"""Scoring helpers shared by every router stack.

These describe the routing decision, not any particular model, so they outlive
whichever estimator is in favour.
"""
from __future__ import annotations

import numpy as np


def zero_miss_point(y, score, allowed: int = 0):
    """The most work the AI can take while missing at most `allowed` human-lane
    cases. Returns (share of the queue as a percentage, the threshold)."""
    order = np.argsort(score)
    hit = np.asarray(y)[order].cumsum()
    ok = np.flatnonzero(hit <= allowed)
    if len(ok) == 0:
        return 0.0, 0.0
    k = ok[-1] + 1
    return k / len(y) * 100, float(np.sort(score)[k - 1])


def coverage(y, score, allowed: int = 0) -> float:
    """Just the share of the queue, for call sites that do not want the threshold."""
    return zero_miss_point(y, score, allowed)[0]
