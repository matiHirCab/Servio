"""`hybrid` no vuelve a llamar al modelo por una mesa que no cambió."""

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from servio_vision.classifiers import claude_plates, hybrid
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.detector import Detection

# clase `food`: si el modelo falla, YOLO da el estado (con `plate` no hay lectura)
PLATE = Detection((20, 20, 100, 100), 0.9, "food")
OTHER = Detection((220, 20, 300, 100), 0.9, "food")


def table(*, mark=None, noise=0, w=400, h=200) -> bytes:
    """La mesa: blanca; `mark` pinta un cuadrado negro (box) y `noise` le suma ruido."""
    pixels = np.full((h, w, 3), 255, dtype=np.int16)
    if mark:
        x0, y0, x1, y1 = mark
        pixels[y0:y1, x0:x1] = 0
    if noise:
        rng = np.random.default_rng(1)
        pixels += rng.integers(-noise, noise + 1, pixels.shape, dtype=np.int16)
    buf = io.BytesIO()
    Image.fromarray(np.clip(pixels, 0, 255).astype(np.uint8)).save(buf, "JPEG")
    return buf.getvalue()


class FakeDetector:
    def __init__(self, *dets):
        self.dets = list(dets)

    def detect(self, img):
        return self.dets


class FakePeople:
    def __init__(self):
        self.boxes = []

    def detect(self, img):
        return self.boxes


def answer(*fills):
    return PlatesReport(
        plates=[
            PlateEstimate(index=i, fill_ratio=f, confidence=0.9, visible=True)
            for i, f in enumerate(fills, start=1)
        ],
        people_present=False,
    )


@pytest.fixture
def claude(monkeypatch):
    fake = SimpleNamespace(answer=answer(0.9), calls=[])

    def estimate(plates, table_id, **kwargs):
        fake.calls.append(table_id)
        return fake.answer

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    return fake


def make(*dets, reuse=True, people=None) -> HybridClassifier:
    return HybridClassifier(
        detector=FakeDetector(*(dets or (PLATE,))),
        person_detector=people or FakePeople(),
        reuse_unchanged=reuse,
    )


def read(clf, image, previous=None):
    return clf.classify(image, "T1", previous or [])


def warm_up(clf, image):
    """Las primeras lecturas de un plato siempre van al modelo (la referencia)."""
    items = []
    for _ in range(hybrid.REFERENCE_READINGS):
        items = read(clf, image, items).items
    return items


def test_new_plates_always_go_to_the_model(claude):
    clf = make()
    image = table()
    items = []
    for _ in range(hybrid.REFERENCE_READINGS):
        items = read(clf, image, items).items
    assert len(claude.calls) == hybrid.REFERENCE_READINGS


def test_an_unchanged_table_reuses_the_answer_and_refreshes_every_few(claude):
    clf = make()
    image = table()
    items = warm_up(clf, image)
    calls_after_warm_up = len(claude.calls)
    fills = []
    for _ in range(hybrid.MAX_REUSES + 1):  # 3 reutilizadas y la de refresco
        report = read(clf, image, items)
        items = report.items
        fills.append(report.items[0].fill_ratio)
    assert len(claude.calls) == calls_after_warm_up + 1  # solo la de refresco
    assert fills == [0.9] * (hybrid.MAX_REUSES + 1)


def test_small_noise_is_still_unchanged(claude):
    clf = make()
    items = warm_up(clf, table())
    before = len(claude.calls)
    read(clf, table(noise=3), items)
    assert len(claude.calls) == before


def test_a_visibly_changed_plate_is_asked_again(claude):
    clf = make()
    items = warm_up(clf, table())
    before = len(claude.calls)
    claude.answer = answer(0.4)
    report = read(clf, table(mark=(30, 30, 80, 90)), items)  # se comió buena parte
    assert len(claude.calls) == before + 1
    assert report.items[0].fill_ratio == 0.4


def test_a_new_plate_is_asked_again(claude):
    clf = make()
    items = warm_up(clf, table())
    before = len(claude.calls)
    clf.detector.dets = [PLATE, OTHER]
    claude.answer = answer(0.9, 0.95)
    read(clf, table(), items)
    assert len(claude.calls) == before + 1


def test_people_arriving_or_leaving_is_asked_again(claude):
    people = FakePeople()
    clf = make(people=people)
    items = warm_up(clf, table())
    before = len(claude.calls)
    people.boxes = [(350, 150, 399, 199)]  # alguien en un rincón, lejos del plato
    read(clf, table(), items)
    assert len(claude.calls) == before + 1


def test_a_failed_call_is_not_cached(claude):
    clf = make()
    items = warm_up(clf, table())
    claude.answer = None  # el modelo falla
    changed = table(mark=(30, 30, 80, 90))
    items = read(clf, changed, items).items
    before = len(claude.calls)
    read(clf, changed, items)  # igual que la anterior, pero esa no tuvo respuesta
    assert len(claude.calls) == before + 1


def test_reuse_can_be_turned_off(claude):
    clf = make(reuse=False)
    image = table()
    items = warm_up(clf, image)
    for _ in range(4):
        items = read(clf, image, items).items
    assert len(claude.calls) == hybrid.REFERENCE_READINGS + 4


def test_tables_do_not_share_the_cache(claude):
    clf = make()
    image = table()
    items = warm_up(clf, image)
    before = len(claude.calls)
    clf.classify(image, "T2", [])  # otra mesa: no reutiliza lo de T1
    assert len(claude.calls) == before + 1
    assert items
