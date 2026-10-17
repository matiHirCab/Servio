"""Tests del clasificador falso (modos script y replay)."""

import json

import pytest

from servio_vision.classifier import get_classifier
from servio_vision.classifiers.fake import FakeClassifier

IMAGE = b"no se usa"


def run(classifier, table_id: str, readings: int):
    return [classifier.classify(IMAGE, table_id, []) for _ in range(readings)]


def test_registered_as_fake():
    assert isinstance(get_classifier("fake"), FakeClassifier)


def test_script_goes_through_a_whole_meal():
    script = [
        ("lleno", 2),
        ("comiendo", 1),
        ("por_terminar", 1),
        ("vacio", 1),
        (None, 1),
    ]
    reports = run(FakeClassifier(script=script, plates=2), "T1", 7)
    states = [{i.state for i in r.items} for r in reports]
    assert states == [
        {"lleno"},
        {"lleno"},
        {"comiendo"},
        {"por_terminar"},
        {"vacio"},
        set(),  # retiraron los platos
        {"lleno"},  # vuelve a empezar
    ]
    assert [len(r.items) for r in reports[:5]] == [2] * 5


def test_script_new_meal_gets_new_ids():
    script = [("lleno", 1), (None, 1)]
    first, _, second = run(FakeClassifier(script=script), "T1", 3)
    assert [i.id for i in first.items] == ["p1", "p2"]
    assert [i.id for i in second.items] == ["p3", "p4"]


def test_script_tables_are_out_of_phase():
    fake = FakeClassifier()

    def states(table_id: str) -> list[str | None]:
        return [r.items[0].state if r.items else None for r in run(fake, table_id, 17)]

    t1, t2 = states("T1"), states("T2")
    assert t2 != t1
    assert t2[:-3] == t1[3:]  # la mesa 2 va 3 lecturas adelantada


@pytest.fixture
def fixture_file(tmp_path):
    lines = [
        {"table_id": "T1", "people_present": False, "items": []},
        {"table_id": "T2", "people_present": None, "items": []},
        {
            "table_id": "T1",
            "people_present": True,
            "items": [
                {
                    "track_id": "T1-p1",
                    "state": "lleno",
                    "fill_ratio": 0.9,
                    "confidence": 0.8,
                    "position": "centro",
                }
            ],
        },
    ]
    path = tmp_path / "obs.jsonl"
    path.write_text(
        "\n".join(
            json.dumps({"schema_version": 1, "ts": "x", **line}) for line in lines
        )
    )
    return path


def test_replay_returns_each_table_in_order(fixture_file):
    fake = get_classifier("fake", mode="replay", fixture=fixture_file)
    first, second, done = run(fake, "T1", 3)
    assert first.items == [] and first.people_present is False
    assert [(i.id, i.state) for i in second.items] == [("p1", "lleno")]
    assert done is None  # se terminó el archivo
    assert fake.classify(IMAGE, "T2", []).people_present is True  # null → True


def test_replay_needs_a_fixture():
    with pytest.raises(ValueError):
        FakeClassifier(mode="replay")


def test_unknown_mode():
    with pytest.raises(ValueError):
        FakeClassifier(mode="nope")
