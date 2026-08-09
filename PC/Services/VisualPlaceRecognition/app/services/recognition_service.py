"""Query descriptor extraction, FAISS lookup, and target-place verification."""

from __future__ import annotations

from collections.abc import Iterable

from PIL import Image

from app.config import Settings
from app.descriptors.base import GlobalDescriptor, validate_descriptor
from app.errors import VPRServiceError, model_not_ready
from app.schemas import SearchCandidate, SearchResponse, VerifyResponse
from app.services.decision_service import DecisionService
from app.services.indexing_service import IndexingService


class RecognitionService:
    """Map uploaded images to stable place/image IDs without exposing FAISS positions."""

    def __init__(
        self,
        settings: Settings,
        descriptor: GlobalDescriptor,
        indexing_service: IndexingService,
        decision_service: DecisionService,
    ) -> None:
        self.settings = settings
        self.descriptor = descriptor
        self.indexing_service = indexing_service
        self.decision_service = decision_service

    def search(self, image: Image.Image, top_k: int | None = None) -> SearchResponse:
        self._ensure_ready()
        requested_k = top_k or self.settings.top_k
        if requested_k <= 0:
            raise VPRServiceError(400, "INVALID_REQUEST", "top_k must be greater than zero")
        snapshot = self.indexing_service.get_snapshot()
        if snapshot.index.size == 0:
            result = self.decision_service.decide([])
            return SearchResponse(
                decision=result.decision,
                candidates=[],
                top1_score=None,
                top2_score=None,
                margin=None,
                unknown_threshold=self.decision_service.unknown_threshold,
                ambiguous_margin=self.decision_service.ambiguous_margin,
            )
        vector = self._encode(image)
        try:
            scores, indices = snapshot.index.search(vector, requested_k)
        except ValueError as exc:
            raise VPRServiceError(
                503,
                "INDEX_DIMENSION_MISMATCH",
                "Query descriptor dimension does not match the active index",
            ) from exc
        candidates = self._map_candidates(scores[0], indices[0], snapshot.entries)
        result = self.decision_service.decide([candidate.score for candidate in candidates])
        return SearchResponse(
            decision=result.decision,
            candidates=candidates,
            top1_score=result.top1_score,
            top2_score=result.top2_score,
            margin=result.margin,
            unknown_threshold=self.decision_service.unknown_threshold,
            ambiguous_margin=self.decision_service.ambiguous_margin,
        )

    def verify(self, image: Image.Image, target_place_id: str) -> VerifyResponse:
        self._ensure_ready()
        snapshot = self.indexing_service.get_snapshot()
        if snapshot.index.size == 0:
            return VerifyResponse(
                verified=False,
                decision="empty_index",
                target_place_id=target_place_id,
                target_rank=None,
                target_score=None,
                top1_place_id=None,
                top1_score=None,
                top2_place_id=None,
                top2_score=None,
                margin=None,
                reasons=["empty_index"],
            )
        vector = self._encode(image)
        try:
            scores, indices = snapshot.index.search(vector, snapshot.index.size)
        except ValueError as exc:
            raise VPRServiceError(
                503,
                "INDEX_DIMENSION_MISMATCH",
                "Query descriptor dimension does not match the active index",
            ) from exc
        image_candidates = self._map_candidates(scores[0], indices[0], snapshot.entries)
        place_candidates = self._aggregate_by_place(image_candidates)
        top_places = place_candidates[:2]
        result = self.decision_service.decide([candidate.score for candidate in top_places])
        target = next(
            (candidate for candidate in place_candidates if candidate.place_id == target_place_id),
            None,
        )
        top1 = top_places[0] if top_places else None
        top2 = top_places[1] if len(top_places) > 1 else None
        reasons: list[str] = []
        if target is None:
            reasons.append("target_not_indexed")
        elif target.rank == 1:
            reasons.append("target_is_top1")
        else:
            reasons.append("target_is_not_top1")
        if target is not None:
            if target.score >= self.decision_service.unknown_threshold:
                reasons.append("target_score_above_threshold")
            else:
                reasons.append("target_score_below_threshold")
        if result.margin is None:
            reasons.append("no_second_candidate")
        elif result.margin >= self.decision_service.ambiguous_margin:
            reasons.append("margin_above_threshold")
        else:
            reasons.append("margin_below_threshold")
        verified = bool(target and target.rank == 1 and result.decision == "matched")
        return VerifyResponse(
            verified=verified,
            decision=result.decision,
            target_place_id=target_place_id,
            target_rank=target.rank if target else None,
            target_score=target.score if target else None,
            top1_place_id=top1.place_id if top1 else None,
            top1_score=top1.score if top1 else None,
            top2_place_id=top2.place_id if top2 else None,
            top2_score=top2.score if top2 else None,
            margin=result.margin,
            reasons=reasons,
        )

    def _encode(self, image: Image.Image):
        try:
            return validate_descriptor(self.descriptor.encode(image.convert("RGB")))
        except VPRServiceError:
            raise
        except Exception as exc:
            raise VPRServiceError(
                500,
                "MODEL_INFERENCE_FAILED",
                "Unable to extract query image descriptor",
            ) from exc

    def _ensure_ready(self) -> None:
        if not self.descriptor.model_loaded:
            raise model_not_ready()
        if not self.indexing_service.get_snapshot().loaded:
            raise VPRServiceError(503, "INDEX_NOT_READY", "The visual index is not ready")

    @staticmethod
    def _map_candidates(
        scores: Iterable[float],
        indices: Iterable[int],
        entries: tuple,
    ) -> list[SearchCandidate]:
        candidates: list[SearchCandidate] = []
        for score, position in zip(scores, indices):
            position = int(position)
            if position < 0:
                continue
            if position >= len(entries):
                raise VPRServiceError(
                    503,
                    "INDEX_NOT_READY",
                    "FAISS position mapping is inconsistent",
                )
            entry = entries[position]
            candidates.append(
                SearchCandidate(
                    rank=len(candidates) + 1,
                    place_id=entry.place_id,
                    image_id=entry.image_id,
                    score=float(score),
                )
            )
        return candidates

    @staticmethod
    def _aggregate_by_place(candidates: list[SearchCandidate]) -> list[SearchCandidate]:
        """Keep the best image per place; this is the extension point for future pooling."""
        best: dict[str, SearchCandidate] = {}
        for candidate in candidates:
            if candidate.place_id not in best:
                best[candidate.place_id] = candidate
        ranked = sorted(best.values(), key=lambda candidate: candidate.score, reverse=True)
        return [candidate.model_copy(update={"rank": rank}) for rank, candidate in enumerate(ranked, 1)]

