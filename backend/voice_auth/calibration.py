"""Threshold selection helper. Pure maths on scores YOU collected; it validates nothing by itself.

A voice-verification threshold is only meaningful when computed from genuine trials (same speaker) and impostor trials
(different speakers) recorded with representative users, devices, rooms and languages. Typical use: pick the threshold
whose false-accept rate (FAR) meets your risk target, then read off the false-reject rate (FRR) you must accept.
"""
from dataclasses import dataclass
from typing import Sequence

import numpy as np


@dataclass(frozen=True)
class Operating:
    threshold: float
    far: float        # impostor trials accepted
    frr: float        # genuine trials rejected


def rates(genuine: Sequence[float], impostor: Sequence[float], threshold: float) -> Operating:
    g, i = np.asarray(genuine, dtype=float), np.asarray(impostor, dtype=float)
    if g.size == 0 or i.size == 0:
        raise ValueError("need both genuine and impostor scores")
    return Operating(float(threshold), float(np.mean(i >= threshold)), float(np.mean(g < threshold)))


def threshold_for_far(genuine: Sequence[float], impostor: Sequence[float], target_far: float) -> Operating:
    """Lowest threshold whose FAR on the supplied impostor scores is <= target_far (so FRR is as small as possible)."""
    if not 0.0 <= target_far < 1.0:
        raise ValueError("target_far must be in [0, 1)")
    cands = np.unique(np.concatenate([np.asarray(genuine, float), np.asarray(impostor, float)]))
    best = None
    for t in cands:
        op = rates(genuine, impostor, t)
        if op.far <= target_far:
            best = op
            break
    if best is None:                                              # even the highest score admits too many impostors
        best = rates(genuine, impostor, float(cands.max()) + 1e-6)
    return best


def equal_error_rate(genuine: Sequence[float], impostor: Sequence[float]) -> Operating:
    cands = np.unique(np.concatenate([np.asarray(genuine, float), np.asarray(impostor, float)]))
    best = min((rates(genuine, impostor, t) for t in cands), key=lambda o: abs(o.far - o.frr))
    return best
