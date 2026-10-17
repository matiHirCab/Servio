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
