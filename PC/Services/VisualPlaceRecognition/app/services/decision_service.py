"""Side-effect-free recognition decision policy."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from app.schemas import Decision


@dataclass(frozen=True, slots=True)
class DecisionResult:
    decision: Decision
    top1_score: float | None
    top2_score: float | None
    margin: float | None


class DecisionService:
    """Classify ranked similarity scores using configured open-set thresholds."""

    def __init__(self, unknown_threshold: float, ambiguous_margin: float) -> None:
        self.unknown_threshold = unknown_threshold
        self.ambiguous_margin = ambiguous_margin

    def decide(self, scores: Sequence[float]) -> DecisionResult:
        if not scores:
            return DecisionResult("empty_index", None, None, None)
        top1 = float(scores[0])
        top2 = float(scores[1]) if len(scores) > 1 else None
        margin = top1 - top2 if top2 is not None else None
        if top1 < self.unknown_threshold:
            decision: Decision = "unknown"
        elif margin is not None and margin < self.ambiguous_margin:
            decision = "ambiguous"
        else:
            decision = "matched"
        return DecisionResult(decision, top1, top2, margin)

