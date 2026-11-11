"""Tests de los clasificadores `yolo` e `hybrid` con detector y Claude falsos (TOM-5)."""

import dataclasses
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from servio_vision.classifier import ClassifierUnavailable
from servio_vision.classifiers import hybrid
from servio_vision.classifiers.hybrid import HybridClassifier
from servio_vision.classifiers.yolo import YoloClassifier
from servio_vision.detector import Detection
from servio_vision.llm import PlateEstimate, PlatesReport, registry
from servio_vision.tracking import MAX_MISSING
from tests.model_fakes import patch_estimator


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

    def estimate(plates, table_id, **kwargs):
        fake.calls.append(plates)
        fake.references.append(kwargs.get("references"))
        return fake.answer

    patch_estimator(monkeypatch, estimate)
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


def test_only_plate_crops_reach_the_provider(monkeypatch):
    """C-3 (docs/legal-uruguay.md): al proveedor van los recortes de plato y nunca la
    imagen de la mesa. Se mira en la frontera, con el estimador de verdad (`llm.make_estimator`): lo
    que llega al proveedor, de punta a punta de una lectura de `hybrid`."""
    import base64

    sent = []

    def provider(request, model, timeout_s):
        sent.append(request)
        n = len([b for b in request.content if b["type"] == "image"])
        plates = [
            {"index": i, "fill_ratio": 0.5, "confidence": 0.9} for i in range(1, n + 1)
        ]
        return {"plates": plates, "people_present": False}, {}

    monkeypatch.setitem(
        registry.PROVIDERS,
        "cli",
        dataclasses.replace(registry.PROVIDERS["cli"], call=provider),
    )
    table = _jpeg(400, 200)
    HybridClassifier(detector=FakeDetector(PLATES)).classify(table, "T1", [])

    (request,) = sent
    images = [b for b in request.content if b["type"] == "image"]
    assert len(images) == len(PLATES)  # un recorte por plato, ni una imagen de más
    for block in images:
        data = base64.b64decode(block["source"]["data"])
        size = Image.open(io.BytesIO(data)).size
        assert max(size) <= hybrid.CROP_SIDE  # nunca la mesa de 400 px de ancho
        assert size != (400, 200)
    assert request.schema is None  # el camino de los platos, no el de la mesa entera


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
    claude.answer = _answer(False)
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
    # tres lecturas iguales: sin reutilizar la respuesta, para mirar lo que se manda
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]), reuse_unchanged=False)
    claude.answer = _one(0.95)
    previous = clf.classify(_jpeg(), "T1", []).items
    claude.answer = _one(0.9, same_plate=False)
    previous = clf.classify(_jpeg(), "T1", previous).items
    clf.classify(_jpeg(), "T1", previous)
    assert claude.references[1] is not None and claude.references[1][0] is not None
    assert claude.references[2] == [None]


def test_hybrid_lost_plate_under_a_new_box_is_not_sent(claude):
    """YOLO vuelve a ver el plato pero con un id nuevo (se corrió más de MAX_SHIFT):
    el perdido se pisa con la caja nueva y no se manda, así no hay dos platos."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[1]]))
    claude.answer = _answer(True)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([Detection((20, 61, 100, 141), 0.9, "plate")])
    second = clf.classify(_jpeg(), "T1", first.items)
    assert [i.id for i in second.items] == ["p2"]
    assert len(claude.calls[1]) == 1


def test_hybrid_lost_plate_drop_is_measured_from_claude_not_the_smoothed(claude):
    """En main.py `previous_items` son los suavizados (todavía 0.9): un plato que se
    está comiendo (0.55 en la lectura anterior de Claude) no se toma como retirado."""
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True, fill=0.9)
    smoothed = clf.classify(_jpeg(), "T1", []).items
    claude.answer = _answer(True, True, fill=0.55)
    clf.classify(_jpeg(), "T1", smoothed)
    clf.detector = FakeDetector([PLATES[0]])  # YOLO pierde p2
    third = clf.classify(_jpeg(), "T1", smoothed)
    p2 = next(i for i in third.items if i.id == "p2")
    assert (p2.fill_ratio, p2.visible) == (0.55, True)


def test_hybrid_lost_plate_that_claude_keeps_seeing_keeps_its_id(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True)
    items = clf.classify(_jpeg(), "T1", []).items
    clf.detector = FakeDetector([PLATES[0]])
    for _ in range(MAX_MISSING + 2):
        items = clf.classify(_jpeg(), "T1", items).items
        assert sorted(i.id for i in items) == ["p1", "p2"]
    clf.detector = FakeDetector(PLATES)
    items = clf.classify(_jpeg(), "T1", items).items
    assert sorted(i.id for i in items) == ["p1", "p2"]


def test_hybrid_lost_plate_is_not_kept_forever(claude):
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True)
    items = clf.classify(_jpeg(), "T1", []).items
    clf.detector = FakeDetector([PLATES[0]])
    for _ in range(hybrid.LOST_KEEP_MAX + MAX_MISSING + 1):
        items = clf.classify(_jpeg(), "T1", items).items
    assert [i.id for i in items] == ["p1"]


def test_hybrid_reference_saved_after_prepare_is_not_dropped(claude):
    """Lecturas en paralelo: la referencia se guarda después de preparar la siguiente,
    que no la llevó. Su `same_plate` no dice nada de esa referencia."""
    clf = HybridClassifier(detector=FakeDetector([PLATES[0]]))
    a = clf.prepare(_jpeg(), "T1", [])
    b = clf.prepare(_jpeg(), "T1", a.seen_items)
    clf.finish(a, _one(0.95), [])
    clf.finish(b, _one(0.9, same_plate=False), [])
    c = clf.prepare(_jpeg(), "T1", b.seen_items)
    assert c.references[0] is not None


def test_hybrid_empty_plate_that_yolo_stops_seeing_is_gone(claude):
    """Plato vacío que YOLO deja de ver: lo retiraron. No se le pregunta a Claude
    (leería el hueco como "plato vacío") y deja de venir enseguida."""
    clf = HybridClassifier(detector=FakeDetector(PLATES))
    claude.answer = _answer(True, True, fill=0.0)
    first = clf.classify(_jpeg(), "T1", [])
    clf.detector = FakeDetector([PLATES[0]])  # sacaron p2
    claude.answer = _answer(True, fill=0.0)
    second = clf.classify(_jpeg(), "T1", first.items)
    assert len(claude.calls[1]) == 1  # solo el plato que se ve
    assert [i.id for i in second.items] == ["p1"]


def test_hybrid_takes_any_plate_estimator():
    """`hybrid` recibe un `PlateEstimator` y no sabe cuál es: con uno propio no hace
    falta parchear nada."""
    asked = []

    class Estimator:
        def estimate(self, request):
            asked.append(request)
            return PlatesReport(
                plates=[
                    PlateEstimate(index=i, fill_ratio=0.5, confidence=0.9)
                    for i in range(1, len(request.plate_jpegs) + 1)
                ],
                people_present=False,
            )

    clf = HybridClassifier(detector=FakeDetector(PLATES), estimator=Estimator())
    report = clf.classify(_jpeg(), "T1", [])
    assert [i.state for i in report.items] == ["comiendo", "comiendo"]
    assert asked[0].table_id == "T1" and len(asked[0].plate_jpegs) == 2


def test_hybrid_with_an_unknown_provider_is_classifier_unavailable(monkeypatch):
    """Igual que `claude`: un CLAUDE_PROVIDER que no existe no es un `ValueError`
    suelto, también si se arma el `hybrid` sin pasar por `build_pipeline`."""
    monkeypatch.delitem(registry.PROVIDERS, "cli")  # el de la configuración por defecto
    with pytest.raises(ClassifierUnavailable, match="CLAUDE_PROVIDER desconocido"):
        HybridClassifier(detector=FakeDetector(PLATES))
