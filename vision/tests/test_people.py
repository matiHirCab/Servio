"""C-3, la mitad del modelo: al proveedor van solo recortes de platos, con las
personas y manos pixeladas, y people_present sale del detector local o del modelo."""

import io

import numpy as np
from PIL import Image

from servio_vision import people
from servio_vision.classifiers import claude_plates
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.detector import Detection


def noisy_jpeg(w=400, h=200, seed=0) -> bytes:
    rng = np.random.default_rng(seed)
    img = Image.fromarray(rng.integers(0, 255, (h, w, 3), np.uint8))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=95)
    return buf.getvalue()


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def detect(self, img):
        return self.dets


class FakePeople:
    def __init__(self, boxes):
        self.boxes = boxes
        self.calls = 0

    def detect(self, img):
        self.calls += 1
        return list(self.boxes)


PLATES = [
    Detection((220, 20, 300, 100), 0.8, "food"),
    Detection((20, 20, 100, 100), 0.9, "finished_food"),
]


def answer(n, people_present=True):
    return PlatesReport(
        plates=[
            PlateEstimate(index=i, fill_ratio=0.5, confidence=0.8)
            for i in range(1, n + 1)
        ],
        people_present=people_present,
    )


def test_only_plate_crops_reach_the_provider(monkeypatch):
    """Hecho cuando: un test verifica que al proveedor solo llegan recortes de platos."""
    sent = {}

    def estimate(plates, table_id, **kwargs):
        sent["table"] = None  # no hay imagen de mesa que mandar
        sent["plates"] = plates
        return answer(2)

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    clf = HybridClassifier(FakeDetector(PLATES), person_detector=FakePeople([]))
    clf.classify(noisy_jpeg(), "T1", [])
    assert sent["table"] is None and len(sent["plates"]) == 2
    # y build_content tampoco la agrega aunque se la pasen: una imagen por plato
    jpeg = noisy_jpeg(60, 60)
    content = claude_plates.build_content([jpeg, jpeg], "T1", [None, jpeg])
    images = [b for b in content if b["type"] == "image"]
    assert len(images) == 3  # dos platos y una referencia; la mesa, nunca
    assert content[0]["type"] == "text" and "recortes" in content[0]["text"]


def test_people_in_a_plate_crop_are_pixelated(monkeypatch):
    sent = []
    monkeypatch.setattr(
        claude_plates,
        "estimate_plates",
        lambda plates, *a, **k: sent.append(plates) or answer(2),
    )
    table = noisy_jpeg()
    plain = HybridClassifier(FakeDetector(PLATES), person_detector=FakePeople([]))
    plain.classify(table, "T1", [])
    # una persona cuyo brazo entra en el plato de 220..300 × 20..100, no en el otro
    arm = FakePeople([(280.0, 60.0, 400.0, 200.0)])
    with_person = HybridClassifier(FakeDetector(PLATES), person_detector=arm)
    with_person.classify(table, "T1", [])
    plain_crops, pixelated_crops = sent
    changed = [a != b for a, b in zip(plain_crops, pixelated_crops, strict=True)]
    assert changed.count(True) == 1  # solo el plato donde entra el brazo cambió
    assert arm.calls == 1  # una detección por lectura, no por plato


def test_anonymize_crop_only_touches_the_overlap():
    rng = np.random.default_rng(1)
    crop = Image.fromarray(rng.integers(0, 255, (100, 100, 3), np.uint8))
    before = np.asarray(crop).copy()
    # el recorte empieza en (200, 50) de la mesa; la persona está en 250..400 × 0..80
    out = np.asarray(
        people.anonymize_crop(crop, (200, 50), [(250.0, 0.0, 400.0, 80.0)])
    )
    assert not np.array_equal(out[0:30, 50:100], before[0:30, 50:100])  # tapado
    assert np.array_equal(out[60:100, 0:40], before[60:100, 0:40])  # lejos, igual
    assert people.anonymize_crop(crop, (0, 0), [(500.0, 500.0, 600.0, 600.0)]) is crop


def test_people_present_is_the_local_detector_or_the_model(monkeypatch):
    """Desde arriba, los comensales quedan casi fuera del recorte de la mesa: el
    detector solo diría "nadie" con gente sentada. Vale lo que vea cualquiera."""
    model_says = {"people": False}
    monkeypatch.setattr(
        claude_plates,
        "estimate_plates",
        lambda *a, **k: answer(2, people_present=model_says["people"]),
    )
    nobody = HybridClassifier(FakeDetector(PLATES), person_detector=FakePeople([]))
    assert nobody.classify(noisy_jpeg(), "T1", []).people_present is False
    model_says["people"] = True  # el modelo ve manos en los recortes
    assert nobody.classify(noisy_jpeg(), "T1", []).people_present is True
    model_says["people"] = False
    someone = HybridClassifier(
        FakeDetector(PLATES), person_detector=FakePeople([(0.0, 0.0, 50.0, 50.0)])
    )
    assert someone.classify(noisy_jpeg(), "T1", []).people_present is True
    # si Claude falla, queda lo último que vio el detector
    monkeypatch.setattr(claude_plates, "estimate_plates", lambda *a, **k: None)
    assert someone.classify(noisy_jpeg(), "T1", []).people_present is True


def test_no_person_detection_on_an_empty_table(monkeypatch):
    monkeypatch.setattr(claude_plates, "estimate_plates", lambda *a, **k: answer(0))
    arm = FakePeople([(0.0, 0.0, 50.0, 50.0)])
    clf = HybridClassifier(FakeDetector([]), person_detector=arm)
    assert clf.classify(noisy_jpeg(), "T1", []).items == []
    assert arm.calls == 0


def test_without_a_local_detector_the_model_decides(monkeypatch):
    monkeypatch.setattr(people, "load_detector", lambda: None)
    monkeypatch.setattr(
        claude_plates,
        "estimate_plates",
        lambda *a, **k: answer(2, people_present=False),
    )
    clf = HybridClassifier(FakeDetector(PLATES))
    assert clf.people is None
    assert clf.classify(noisy_jpeg(), "T1", []).people_present is False


def test_load_detector_never_breaks_the_classifier(monkeypatch):
    """Sin ultralytics, sin pesos o sin internet: aviso y seguir sin detector."""

    def broken(*args, **kwargs):
        raise OSError("sin internet para bajar yolo11s.pt")

    monkeypatch.setattr(people, "YoloPersonDetector", broken)
    assert people.load_detector() is None
