"""Clasificador `claude`: solo el modelo, con la mesa entera, y solo si se permite."""

import base64
import io

import numpy as np
import pytest
from PIL import Image

from servio_vision import people
from servio_vision.classifier import ClassifierUnavailable, get_classifier
from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude import ClaudeClassifier
from servio_vision.classifiers.providers.base import ClaudeCallError

NOISE = np.random.default_rng(0).integers(0, 255, (96, 128, 3), dtype=np.uint8)


def table_jpeg() -> bytes:
    buf = io.BytesIO()
    Image.fromarray(NOISE).save(buf, "JPEG", quality=95)
    return buf.getvalue()


class FakePeople:
    """Detector de personas: devuelve las cajas que se le den."""

    def __init__(self, boxes=()):
        self.boxes = list(boxes)
        self.calls = 0

    def detect(self, img):
        self.calls += 1
        return self.boxes


def plate(position, fill=0.9, visible=True, confidence=0.8):
    return {
        "position": position,
        "fill_ratio": fill,
        "confidence": confidence,
        "visible": visible,
    }


@pytest.fixture(autouse=True)
def opted_in(monkeypatch):
    monkeypatch.setenv("CLAUDE_ALLOW_TABLE_IMAGES", "true")
    monkeypatch.delenv("CLAUDE_PROVIDER", raising=False)


@pytest.fixture
def backend(monkeypatch):
    """Reemplaza el proveedor `cli` por respuestas en cola y anota los pedidos."""
    requests, answers = [], []

    def fake(request, model, timeout_s):
        requests.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"input_tokens": 10, "output_tokens": 5}

    monkeypatch.setitem(claude_plates._BACKENDS, "cli", fake)
    monkeypatch.setattr(claude_plates, "RETRY_WAIT_S", 0)
    return requests, answers


def classifier(boxes=()) -> ClaudeClassifier:
    return ClaudeClassifier(person_detector=FakePeople(boxes))


def test_it_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CLAUDE_ALLOW_TABLE_IMAGES")
    with pytest.raises(ClassifierUnavailable, match="CLAUDE_ALLOW_TABLE_IMAGES"):
        get_classifier("claude", person_detector=FakePeople())


def test_without_a_person_detector_it_refuses(monkeypatch):
    """Sin poder pixelar a las personas, la mesa entera no sale de la máquina."""
    monkeypatch.setattr(people, "load_detector", lambda: None)
    with pytest.raises(ClassifierUnavailable, match="personas"):
        ClaudeClassifier()


def test_sends_one_table_image_with_the_people_pixelated(backend):
    requests, answers = backend
    answers.append({"plates": [], "people_present": False})
    detector = FakePeople(
        [(0, 0, 64, 48)]
    )  # una persona en el cuarto de arriba a la izq.
    ClaudeClassifier(person_detector=detector).classify(table_jpeg(), "T1", [])

    (request,) = requests
    images = [b for b in request.content if b["type"] == "image"]
    assert len(images) == 1  # la mesa, una vez
    sent = np.asarray(
        Image.open(io.BytesIO(base64.b64decode(images[0]["source"]["data"])))
    ).astype(float)
    # donde estaba la persona hay un mosaico: mucho menos detalle que el original
    assert sent[:48, :64].std() < NOISE[:48, :64].std() / 2
    # y el resto de la mesa no se pixela (el ruido pierde algo al recomprimir el JPEG)
    assert sent[60:, 80:].std() > NOISE[60:, 80:].std() / 2
    assert detector.calls == 1


def test_the_request_asks_for_the_table_schema(backend):
    from servio_vision import prompt

    requests, answers = backend
    answers.append({"plates": [], "people_present": False})
    classifier().classify(table_jpeg(), "T1", [])
    assert requests[0].schema is prompt.REPORT_TABLE_SCHEMA
    assert requests[0].system == prompt.table_system_prompt()


def test_plates_become_items_with_ids_and_states(backend):
    _, answers = backend
    answers.append(
        {
            "plates": [plate("centro", 0.9), plate("arriba-izquierda", 0.1)],
            "people_present": True,
        }
    )
    report = classifier().classify(table_jpeg(), "T1", [])
    assert report is not None and report.people_present is True
    by_pos = {i.position: i for i in report.items}
    assert by_pos["centro"].id == "p1" and by_pos["centro"].state == "lleno"
    assert by_pos["arriba-izquierda"].id == "p2"
    assert by_pos["arriba-izquierda"].state == "por_terminar"
    assert all(i.bbox is None for i in report.items)


def test_ids_follow_positions_across_readings(backend):
    _, answers = backend
    c = classifier()
    answers.append(
        {
            "plates": [plate("centro"), plate("arriba-izquierda")],
            "people_present": True,
        }
    )
    first = c.classify(table_jpeg(), "T1", [])
    answers.append(
        {
            "plates": [
                plate("abajo-derecha"),  # llegó un plato nuevo
                plate("arriba-izquierda", 0.4),
                plate("centro", 0.5),
            ],
            "people_present": True,
        }
    )
    second = c.classify(table_jpeg(), "T1", first.items)
    ids = {i.position: i.id for i in second.items}
    first_ids = {i.position: i.id for i in first.items}
    assert ids["centro"] == first_ids["centro"] == "p1"
    assert ids["arriba-izquierda"] == first_ids["arriba-izquierda"] == "p2"
    assert ids["abajo-derecha"] == "p3"  # id nuevo, el número solo sube


def test_a_covered_plate_has_no_state(backend):
    _, answers = backend
    answers.append({"plates": [plate("centro", visible=False)], "people_present": True})
    (item,) = classifier().classify(table_jpeg(), "T1", []).items
    assert item.visible is False and item.state is None


def test_people_seen_locally_count_even_if_the_model_says_nobody(backend):
    _, answers = backend
    answers.append({"plates": [plate("centro")], "people_present": False})
    report = classifier([(0, 0, 10, 10)]).classify(table_jpeg(), "T1", [])
    assert report.people_present is True


def test_two_plates_in_one_cell_is_retried_then_none(backend):
    requests, answers = backend
    bad = {"plates": [plate("centro"), plate("centro")], "people_present": True}
    answers.extend([bad, bad])
    assert classifier().classify(table_jpeg(), "T1", []) is None
    assert len(requests) == 2


def test_a_failing_model_gives_none(backend):
    _, answers = backend
    answers.extend([ClaudeCallError("API caída")] * 2)
    assert classifier().classify(table_jpeg(), "T1", []) is None


def test_the_openai_provider_cannot_read_a_whole_table():
    """La Decisions API pregunta por un número fijo de platos: no hay forma de que
    encuentre los platos de una mesa entera."""
    c = ClaudeClassifier(provider="openai", person_detector=FakePeople())
    assert c.classify(table_jpeg(), "T1", []) is None
