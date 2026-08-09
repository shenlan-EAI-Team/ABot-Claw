from pathlib import Path

import numpy as np
import pytest

from app.indexes.faiss_flat_ip import FaissFlatIPIndex


def test_l2_normalization_and_exact_ranking() -> None:
    index = FaissFlatIPIndex()
    original = np.asarray([[10.0, 0.0], [0.0, 3.0]], dtype=np.float64)
    untouched = original.copy()
    index.rebuild(original)
    scores, indices = index.search(np.asarray([2, 0], dtype=np.int64), 2)
    assert np.array_equal(original, untouched)
    assert indices.tolist() == [[0, 1]]
    assert scores[0, 0] == pytest.approx(1.0)
    assert scores.dtype == np.float32


def test_batch_input_and_top_k_is_capped() -> None:
    index = FaissFlatIPIndex()
    index.rebuild(np.eye(3, dtype=np.float32))
    scores, indices = index.search(np.asarray([[1, 0, 0], [0, 1, 0]]), 10)
    assert scores.shape == (2, 3)
    assert indices[:, 0].tolist() == [0, 1]


def test_dimension_mismatch() -> None:
    index = FaissFlatIPIndex()
    index.rebuild(np.eye(2, dtype=np.float32))
    with pytest.raises(ValueError, match="dimension"):
        index.search(np.ones(3, dtype=np.float32), 1)


def test_zero_vector_rejected() -> None:
    index = FaissFlatIPIndex()
    with pytest.raises(ValueError, match="Zero"):
        index.rebuild(np.asarray([[0.0, 0.0]], dtype=np.float32))


def test_non_finite_vector_rejected() -> None:
    index = FaissFlatIPIndex()
    with pytest.raises(ValueError, match="non-finite"):
        index.rebuild(np.asarray([[1.0, np.nan]], dtype=np.float32))


def test_empty_index_search() -> None:
    index = FaissFlatIPIndex()
    scores, indices = index.search(np.asarray([1.0, 0.0]), 2)
    assert scores.shape == (1, 0)
    assert indices.shape == (1, 0)


def test_save_and_load(tmp_path: Path) -> None:
    path = tmp_path / "index.faiss"
    index = FaissFlatIPIndex()
    index.rebuild(np.eye(2, dtype=np.float32))
    index.save(path)
    restored = FaissFlatIPIndex(2)
    restored.load(path)
    scores, indices = restored.search(np.asarray([0.0, 5.0]), 1)
    assert restored.size == 2
    assert indices.tolist() == [[1]]
    assert scores[0, 0] == pytest.approx(1.0)

