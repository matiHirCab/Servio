"""Tests del contrato Classifier / TableReport (03-contratos.md)."""

import pytest
from pydantic import ValidationError

from servio_vision.classifier import available_classifiers, get_classifier
from servio_vision.schemas import Item, TableReport, state_from_fill_ratio


@pytest.mark.parametrize(
    ("fill_ratio", "state"),
    [
        (1.0, "lleno"),
        (0.81, "lleno"),
        (0.80, "comiendo"),
        (0.30, "comiendo"),
        (0.29, "por_terminar"),
        (0.05, "por_terminar"),
        (0.04, "vacio"),
        (0.0, "vacio"),
    ],
)
def test_state_from_fill_ratio(fill_ratio, state):
    assert state_from_fill_ratio(fill_ratio) == state


def test_report_from_tool_json():
    report = TableReport.model_validate(
        {
            "items": [
                {
                    "id": "p1",
                    "kind": "plate",
                    "state": "comiendo",
                    "fill_ratio": 0.5,
                    "position": "arriba-izquierda",
                    "confidence": 0.8,
                    "visible": True,
                }
            ],
            "people_present": True,
        }
    )
    assert report.items[0].state == "comiendo"


def test_defaults():
    item = Item(id="p1", state="lleno", position="centro", confidence=0.9)
    assert item.kind == "plate"
    assert item.visible is True
    assert item.fill_ratio is None
    assert TableReport().people_present is True


@pytest.mark.parametrize(
    "bad",
    [
        {"kind": "drink"},
        {"state": "llena"},
        {"confidence": 1.5},
        {"fill_ratio": -0.1},
    ],
)
def test_rejects_out_of_contract(bad):
    base = {"id": "p1", "state": "lleno", "position": "centro", "confidence": 0.9}
    with pytest.raises(ValidationError):
        Item.model_validate(base | bad)


def test_registry_names():
    assert available_classifiers() == ["claude", "fake", "yolo"]


def test_unknown_classifier():
    with pytest.raises(ValueError):
        get_classifier("nope")


def test_not_implemented_yet():
    with pytest.raises(NotImplementedError):
        get_classifier("yolo")


def test_fill_ratio_overrides_state():
    item = Item(
        id="p1", state="lleno", fill_ratio=0.02, position="centro", confidence=0.9
    )
    assert item.state == "vacio"


def test_hidden_plate_without_state():
    item = Item(id="p1", position="centro", visible=False)
    assert item.state is None


def test_visible_plate_needs_state_and_confidence():
    with pytest.raises(ValidationError):
        Item(id="p1", position="centro", confidence=0.9)
    with pytest.raises(ValidationError):
        Item(id="p1", state="lleno", position="centro")


def test_non_plate_items_are_dropped():
    plate = {"id": "p1", "state": "lleno", "position": "centro", "confidence": 0.9}
    glass = {"id": "d1", "kind": "drink", "state": "llena", "position": "centro"}
    report = TableReport.model_validate({"items": [plate, glass]})
    assert [i.id for i in report.items] == ["p1"]


def test_missing_class_is_clear(monkeypatch):
    from servio_vision import classifier

    # el módulo existe pero no define la clase registrada
    monkeypatch.setitem(
        classifier._REGISTRY, "empty", "servio_vision.schemas:NoSuchClass"
    )
    with pytest.raises(NotImplementedError, match="NoSuchClass"):
        get_classifier("empty")
