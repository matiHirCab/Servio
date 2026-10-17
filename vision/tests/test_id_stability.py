import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from id_stability import stability


def test_stable_ids():
    m = stability([["p1", "p2"], ["p1", "p2"], ["p2", "p1"]])
    assert m["conservan"] == 1.0
    assert (m["ids_distintos"], m["ids_nuevos"]) == (2, 0)


def test_id_change_counts_as_lost():
    # p2 pasa a llamarse p3 en el segundo frame
    m = stability([["p1", "p2"], ["p1", "p3"], ["p1", "p3"]])
    assert m["conservan"] == 0.75
    assert (m["ids_distintos"], m["max_platos"], m["ids_nuevos"]) == (3, 2, 1)


def test_empty():
    assert stability([])["conservan"] is None


def test_failed_reading_is_not_compared_across():
    m = stability([["p1", "p2"], None, ["p1"]])
    assert m["conservan"] is None
    assert m["frames"] == 3
