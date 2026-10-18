"""Tests de los clasificadores `yolo` e `hybrid` con detector y Claude falsos (TOM-5)."""

import io
from types import SimpleNamespace

import pytest
from PIL import Image

from servio_vision.classifiers import claude_plates, hybrid
from servio_vision.classifiers.claude_plates import PlateEstimate, PlatesReport
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.classifiers.yolo import YoloClassifier
from servio_vision.detector import Detection
from servio_vision.tracking import MAX_MISSING


def _jpeg(w=400, h=200):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "JPEG")
    return buf.getvalue()


class FakeDetector:
    def __init__(self, dets):
        self.dets = dets

    def detect(self, img):
        return self.dets


PLATES = [
    Detection((220, 20, 300, 100), 0.8, "food"),
    Detection((20, 20, 100, 100), 0.9, "finished_food"),
]


@pytest.fixture
def claude(monkeypatch):
    """Claude falso: devuelve `fake.answer` (None = Claude falló) y guarda los recortes."""
    fake = SimpleNamespace(answer=None, calls=[], references=[])

    def estimate(table, plates, table_id, **kwargs):
        fake.calls.append(plates)
        fake.references.append(kwargs.get("references"))
        return fake.answer

    monkeypatch.setattr(claude_plates, "estimate_plates", estimate)
    return fake


def test_hybrid_uses_claude_fill_ratio(claude):
    claude.answer = PlatesReport(
        plates=[
            PlateEstimate(index=1, fill_ratio=0.02, confidence=0.9, visible=True),
            PlateEstimate(index=2, fill_ratio=0.5, confidence=0.8, visible=True),
        ],
        people_present=False,
    )
    report = HybridClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    # ordenados de izquierda a derecha: el de x=20 es p2 (segundo detectado)
    assert [(i.id, i.state) for i in report.items] == [
        ("p2", "vacio"),
        ("p1", "comiendo"),
    ]
    assert report.people_present is False
    assert report.items[0].bbox == pytest.approx((0.05, 0.1, 0.2, 0.4))
    assert len(claude.calls[0]) == 2


def test_hybrid_hidden_plate_keeps_its_last_reading(claude):
    """Un plato que ya se vio nunca queda «tapado»: conserva su última lectura,
    con visible=False para que el suavizado no la cuente."""
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    clf = HybridClassifier(detector=det)
    claude.answer = PlatesReport(
        plates=[PlateEstimate(index=1, fill_ratio=0.9, confidence=0.8, visible=True)]
    )
    first = clf.classify(_jpeg(), "T1", [])
    claude.answer = PlatesReport(
        plates=[PlateEstimate(index=1, fill_ratio=0.2, confidence=0.3, visible=False)]
    )
    second = clf.classify(_jpeg(), "T1", first.items)
    item = second.items[0]
    assert (item.id, item.visible, item.state, item.fill_ratio) == (
        "p1",
        False,
        "lleno",
        0.9,
    )
    # sigue así en el próximo frame tapado
    third = clf.classify(_jpeg(), "T1", second.items)
    assert third.items[0].state == "lleno"
    # a la tercera lectura seguida sin verlo, queda tapado
    fourth = clf.classify(_jpeg(), "T1", third.items)
    assert fourth.items[0].state is None


def test_hybrid_hidden_plate_has_no_state(claude):
    claude.answer = PlatesReport(
        plates=[PlateEstimate(index=1, fill_ratio=0.9, confidence=0.3, visible=False)]
    )
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    report = HybridClassifier(detector=det).classify(_jpeg(), "T1", [])
    assert report.items[0].visible is False
    assert report.items[0].state is None


def test_hybrid_falls_back_to_yolo_labels(claude):
    report = HybridClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    assert [i.state for i in report.items] == ["vacio", "lleno"]
    assert report.items[0].confidence == pytest.approx(0.45)


def test_hybrid_without_claude_or_trained_yolo_is_discarded(claude):
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    assert HybridClassifier(detector=det).classify(_jpeg(), "T1", []) is None


def test_hybrid_no_plates_skips_claude(claude):
    report = HybridClassifier(detector=FakeDetector([])).classify(_jpeg(), "T1", [])
    assert report.items == []
    assert claude.calls == []


def test_hybrid_keeps_ids_between_readings(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    second = clf.classify(_jpeg(), "T1", first.items)
    assert [i.id for i in second.items] == ["p1"]


def test_crop_has_margin():
    img = Image.new("RGB", (400, 200))
    crop = hybrid._crop(img, (0.25, 0.25, 0.5, 0.5))
    assert crop.size == (240, 120)


def test_yolo_classifier_states_from_labels():
    report = YoloClassifier(detector=FakeDetector(PLATES)).classify(_jpeg(), "T1", [])
    assert [i.state for i in report.items] == ["vacio", "lleno"]


def test_yolo_classifier_without_trained_weights_returns_none():
    det = FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    assert YoloClassifier(detector=det).classify(_jpeg(), "T1", []) is None


def test_hybrid_fallback_keeps_last_people_present(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = PlatesReport(
        plates=[
            PlateEstimate(index=1, fill_ratio=0.5, confidence=0.8),
            PlateEstimate(index=2, fill_ratio=0.5, confidence=0.8),
        ],
        people_present=False,
    )
    first = clf.classify(_jpeg(), "T1", [])
    claude.answer = None  # Claude falla: usa YOLO, pero la mesa sigue sin gente
    second = clf.classify(_jpeg(), "T1", first.items)
    assert second.people_present is False


def test_hybrid_does_not_reuse_ids_of_missing_plates(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    first = clf.classify(_jpeg(), "T1", [])  # p1 y p2
    clf.detector = FakeDetector([PLATES[0]])
    second = clf.classify(_jpeg(), "T1", first.items)  # p2 no se ve
    clf.detector = FakeDetector(
        [PLATES[0], Detection((300, 100, 380, 180), 0.8, "food")]
    )
    third = clf.classify(_jpeg(), "T1", second.items)
    # el plato nuevo no hereda el p2 que faltó
    assert sorted(i.id for i in third.items) == ["p1", "p3"]


def test_yolo_skips_unknown_labels():
    det = FakeDetector([*PLATES, Detection((300, 100, 380, 180), 0.8, "glass")])
    report = YoloClassifier(detector=det).classify(_jpeg(), "T1", [])
    assert len(report.items) == 2


def _answer(*visible, fill=0.5):
    return PlatesReport(
        plates=[
            PlateEstimate(index=n, fill_ratio=fill, confidence=0.8, visible=v)
            for n, v in enumerate(visible, 1)
        ]
    )


def test_hybrid_asks_claude_about_a_plate_yolo_lost(claude):
    """YOLO deja de ver un plato: igual se le manda a Claude su último recorte, y si
    Claude lo ve sigue con su id y su lectura."""
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])  # YOLO pierde p2 (x=20)
    claude.answer = _answer(True, True, fill=0.2)
    second = clf.classify(_jpeg(), "T1", first.items)
    assert len(claude.calls[1]) == 2
    assert sorted((i.id, i.state) for i in second.items) == [
        ("p1", "por_terminar"),
        ("p2", "por_terminar"),
    ]
    p2 = next(i for i in second.items if i.id == "p2")
    assert p2.bbox == pytest.approx((0.05, 0.1, 0.2, 0.4))


def test_hybrid_lost_plate_that_claude_does_not_see_keeps_last_reading(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True, fill=0.9)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    claude.answer = _answer(True, False)
    second = clf.classify(_jpeg(), "T1", first.items)
    p2 = next(i for i in second.items if i.id == "p2")
    assert (p2.state, p2.visible) == ("lleno", False)


def test_hybrid_lost_plate_nobody_sees_is_dropped(claude):
    """Perdido por YOLO y tapado para Claude tres veces: no se muestra «tapado»,
    se deja de mostrar (lo más probable es que se lo hayan llevado)."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[1]]))
    claude.answer = _answer(True)
    items = clf.classify(_jpeg(), "T1", []).items
    clf.detector = FakeDetector([])
    claude.answer = _answer(False)
    for _ in range(2):
        items = clf.classify(_jpeg(), "T1", items).items
        assert [i.id for i in items] == ["p1"]
    assert clf.classify(_jpeg(), "T1", items).items == []


def test_hybrid_stops_asking_about_a_lost_plate(claude):
    clf = HybridClassifier(detector=FakeDetector([PLATES[1]]))
    claude.answer = _answer(True)
    items = clf.classify(_jpeg(), "T1", []).items
    clf.detector = FakeDetector([])
    for _ in range(MAX_MISSING):
        items = clf.classify(_jpeg(), "T1", items).items
    calls = len(claude.calls)
    assert clf.classify(_jpeg(), "T1", items).items == []
    assert len(claude.calls) == calls  # sin platos que leer, no llama a Claude


def test_hybrid_lost_plate_found_again_keeps_its_id(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    second = clf.classify(_jpeg(), "T1", first.items)
    clf.detector = FakeDetector(PLATES)
    third = clf.classify(_jpeg(), "T1", second.items)
    assert sorted(i.id for i in third.items) == ["p1", "p2"]


def test_prepare_does_not_wait_for_claude(claude):
    """La lectura siguiente se puede preparar antes de terminar la anterior."""
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    a = clf.prepare(_jpeg(), "T1", [])
    b = clf.prepare(_jpeg(), "T1", a.seen_items)
    assert [p.id for p in a.plates] == [p.id for p in b.plates]
    assert claude.calls == []


def test_hybrid_lost_plate_read_as_empty_is_not_taken(claude):
    """Se llevaron el plato: Claude mira el hueco y dice vacío. No se toma como vista
    (si no, saldría un aviso de plato vacío por un plato que ya no está)."""
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True, fill=0.6)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    claude.answer = PlatesReport(
        plates=[
            PlateEstimate(index=1, fill_ratio=0.6, confidence=0.9),
            PlateEstimate(index=2, fill_ratio=0.0, confidence=0.9),
        ]
    )
    second = clf.classify(_jpeg(), "T1", first.items)
    p2 = next(i for i in second.items if i.id == "p2")
    assert (p2.state, p2.visible) == ("comiendo", False)


def test_hybrid_lost_plate_sharp_drop_is_not_taken(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True, fill=0.9)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])
    claude.answer = _answer(True, True, fill=0.2)  # de 0.9 a 0.2 sin que YOLO lo vea
    second = clf.classify(_jpeg(), "T1", first.items)
    p2 = next(i for i in second.items if i.id == "p2")
    assert (p2.state, p2.visible) == ("lleno", False)


def test_hybrid_plates_seen_last_win_over_lost_ones(claude):
    """Una caja entre un plato visto en la lectura anterior y uno perdido se queda
    con el id del visto, aunque esté un poco más cerca del perdido."""
    a = Detection((100, 50, 180, 130), 0.9, "plate")
    b = Detection((150, 50, 230, 130), 0.9, "plate")
    clf = HybridClassifier(detector=FakeDetector([a, b]))
    claude.answer = _answer(True, True)
    first = clf.classify(_jpeg(), "T1", [])
    id_a = next(i.id for i in first.items if i.bbox[0] < 0.3)
    id_b = next(i.id for i in first.items if i.bbox[0] >= 0.3)
    clf.detector = FakeDetector([a])  # b se pierde
    second = clf.classify(_jpeg(), "T1", first.items)
    clf.detector = FakeDetector([Detection((130, 50, 210, 130), 0.9, "plate")])
    third = clf.classify(_jpeg(), "T1", second.items)
    found = next(i for i in third.items if i.bbox[0] == pytest.approx(130 / 400))
    assert found.id == id_a
    assert id_b != id_a


def _one(fill, same_plate=True, visible=True):
    return PlatesReport(
        plates=[
            PlateEstimate(
                index=1,
                fill_ratio=fill,
                confidence=0.8,
                visible=visible,
                same_plate=same_plate,
            )
        ]
    )


def test_hybrid_sends_the_served_plate_as_reference(claude):
    """La primera lectura llena queda de referencia y viaja en las siguientes."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]))
    claude.answer = _one(0.95)
    first = clf.classify(_jpeg(), "T1", [])
    assert claude.references[0] == [None]
    claude.answer = _one(0.33)
    clf.classify(_jpeg(), "T1", first.items)
    assert claude.references[1] == claude.calls[0]


def test_hybrid_no_reference_from_a_half_eaten_or_hidden_plate(claude):
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]))
    previous = []
    for answer in (_one(0.95, visible=False), _one(0.5)):
        claude.answer = answer
        previous = clf.classify(_jpeg(), "T1", previous).items
    clf.classify(_jpeg(), "T1", previous)
    assert claude.references[-1] == [None]


def test_hybrid_no_reference_after_the_first_readings(claude):
    """Un plato que recién se ve lleno tarde (¿id cambiado a mitad de comida?) no
    toma referencia."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]))
    previous = []
    for fill in [0.5] * hybrid.REFERENCE_READINGS + [0.95]:
        claude.answer = _one(fill)
        previous = clf.classify(_jpeg(), "T1", previous).items
    clf.classify(_jpeg(), "T1", previous)
    assert claude.references[-1] == [None]


def test_hybrid_drops_a_reference_of_other_food(claude):
    """Si Claude dice que la referencia es de otra comida, no se manda más."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]))
    claude.answer = _one(0.95)
    previous = clf.classify(_jpeg(), "T1", []).items
    claude.answer = _one(0.9, same_plate=False)
    previous = clf.classify(_jpeg(), "T1", previous).items
    clf.classify(_jpeg(), "T1", previous)
    assert claude.references[1] is not None and claude.references[1][0] is not None
    assert claude.references[2] == [None]


def _one_plate(fill):
    return PlatesReport(
        plates=[PlateEstimate(index=1, fill_ratio=fill, confidence=0.8, visible=True)]
    )


def test_hybrid_reading_that_rises_is_not_taken_until_it_repeats(claude):
    """La comida no vuelve: una subida grande no cuenta; si se repite
    HIDDEN_READINGS veces es otro plato y se acepta."""
    clf = HybridClassifier(
        detector=FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    )
    claude.answer = _one_plate(0.3)
    items = clf.classify(_jpeg(), "T1", []).items
    claude.answer = _one_plate(0.8)
    seen = []
    for _ in range(hybrid.HIDDEN_READINGS):
        items = clf.classify(_jpeg(), "T1", items).items
        seen.append((items[0].fill_ratio, items[0].visible))
    assert seen[:-1] == [(0.3, False)] * (hybrid.HIDDEN_READINGS - 1)
    assert seen[-1] == (0.8, True)


def test_hybrid_small_rise_is_taken(claude):
    clf = HybridClassifier(
        detector=FakeDetector([Detection((20, 20, 100, 100), 0.9, "plate")])
    )
    claude.answer = _one_plate(0.4)
    items = clf.classify(_jpeg(), "T1", []).items
    claude.answer = _one_plate(0.55)
    item = clf.classify(_jpeg(), "T1", items).items[0]
    assert (item.fill_ratio, item.visible) == (0.55, True)
