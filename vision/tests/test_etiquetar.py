"""`tools/etiquetar.py` (B-3): recortar una sesión grabada y etiquetar por la web local."""

import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import etiquetar
import eval as evaluation

from servio_vision import recording
from servio_vision.zones import TableZone, Zones

T0 = datetime(2026, 11, 10, 20, 0, tzinfo=UTC)
ZONES = Zones(
    frame_size=(200, 100),
    tables=[
        TableZone(
            table_id="T1", name="Mesa 1", polygon=[(0, 0), (99, 0), (99, 99), (0, 99)]
        ),
        TableZone(
            table_id="T2",
            name="Mesa 2",
            polygon=[(100, 0), (199, 0), (199, 99), (100, 99)],
        ),
    ],
)


def make_session(folder: Path, readings: int = 3, zones: Zones | None = ZONES) -> Path:
    folder.mkdir()
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[:, :100] = (40, 80, 200)  # una mesa de cada color, para distinguir recortes
    frame[:, 100:] = (200, 80, 40)
    for n in range(readings):
        cv2.imwrite(
            str(folder / recording.frame_name(T0 + timedelta(seconds=30 * n))), frame
        )
    info = {
        "started_at": T0.isoformat(),
        "rules": {"interval": 30},
        "zones": zones.model_dump(mode="json") if zones else None,
        "anonymized": True,
    }
    (folder / recording.SESSION_FILE).write_text(json.dumps(info), encoding="utf-8")
    return folder


class Box:
    def __init__(self, box):
        self.box = box


class TwoPlates:
    """Detector falso: dos platos en cada mesa."""

    def detect(self, image):
        h, w = image.shape[:2]
        return [Box((5, 5, w // 2 - 5, h - 5)), Box((w // 2 + 5, 5, w - 5, h - 5))]


def test_crop_session_without_detector_saves_one_crop_per_table(tmp_path):
    session = make_session(tmp_path / "sesion", readings=4)
    out = tmp_path / "set"
    written = etiquetar.crop_session(session, out, every=2)
    assert len(written) == 4  # 2 frames (uno de cada 2) × 2 mesas
    names = sorted(p.name for p in out.glob("*.jpg"))
    assert names[0].endswith("_T1.jpg") and names[1].endswith("_T2.jpg")
    crop = cv2.imread(str(out / names[0]))
    assert crop.shape[:2] == (100, 100)  # la mesa entera
    assert etiquetar.crop_session(session, out, every=2) == []  # ya estaban


def test_crop_session_with_detector_saves_one_crop_per_plate(tmp_path):
    session = make_session(tmp_path / "sesion", readings=1)
    out = tmp_path / "set"
    written = etiquetar.crop_session(session, out, detector=TwoPlates())
    assert len(written) == 4  # 2 mesas × 2 platos
    assert {p.name.split("_", 1)[1] for p in written} == {
        "T1_1.jpg",
        "T1_2.jpg",
        "T2_1.jpg",
        "T2_2.jpg",
    }
    plate = cv2.imread(str(written[0]))
    assert plate.shape[1] < 100  # un pedazo de la mesa, con algo de aire


def test_crop_session_without_zones_uses_the_whole_frame(tmp_path):
    session = make_session(tmp_path / "sesion", readings=2, zones=None)
    written = etiquetar.crop_session(session, tmp_path / "set")
    assert len(written) == 2 and all(p.name.endswith("_frame.jpg") for p in written)


def test_pending_accepts_png_and_serves_it_with_its_type(tmp_path):
    folder = tmp_path / "set"
    folder.mkdir()
    cv2.imwrite(str(folder / "b.PNG"), np.zeros((10, 10, 3), np.uint8))
    cv2.imwrite(str(folder / "a.jpg"), np.zeros((10, 10, 3), np.uint8))
    (folder / "labels.csv").write_text("archivo,estado,tipo,nota\n", encoding="utf-8")
    assert etiquetar.pending(folder) == ["a.jpg", "b.PNG"]  # el csv no es un recorte
    srv = etiquetar.LabelServer(folder, "127.0.0.1", 0).start()
    try:
        base = srv.url.rstrip("/")
        assert get(f"{base}/img/b.PNG")[1] == "image/png"
        with pytest.raises(HTTPError) as e:
            get(f"{base}/img/labels.csv")  # no es una imagen
        assert e.value.code == 404
    finally:
        srv.stop()


def test_crop_session_rejects_a_bad_step(tmp_path):
    session = make_session(tmp_path / "sesion")
    with pytest.raises(ValueError, match="--cada"):
        etiquetar.crop_session(session, tmp_path / "set", every=0)


def test_labels_are_written_once_per_crop_and_read_by_eval(tmp_path):
    session = make_session(tmp_path / "sesion", readings=2)
    out = tmp_path / "set"
    etiquetar.crop_session(session, out)
    todo = etiquetar.pending(out)
    assert len(todo) == 4
    etiquetar.write_label(out, todo[0], "lleno", "plato", "manos encima")
    etiquetar.write_label(out, todo[1], etiquetar.SKIP, "otro", "")
    assert etiquetar.pending(out) == todo[2:]  # también lo saltado deja de aparecer
    rows = evaluation.load_labels(out)  # lo que lee tools/eval.py: solo estados reales
    assert [(r["archivo"], r["estado"], r["tipo"], r["nota"]) for r in rows] == [
        (todo[0], "lleno", "plato", "manos encima")
    ]
    with pytest.raises(ValueError, match="estado"):
        etiquetar.write_label(out, todo[2], "casi", "plato", "")
    with pytest.raises(ValueError, match="tipo"):
        etiquetar.write_label(out, todo[2], "vacio", "postre", "")
    with pytest.raises(ValueError, match="recorte"):
        etiquetar.write_label(out, "../otro.jpg", "vacio", "plato", "")


@pytest.fixture
def server(tmp_path):
    session = make_session(tmp_path / "sesion", readings=1)
    out = tmp_path / "set"
    etiquetar.crop_session(session, out)
    srv = etiquetar.LabelServer(out, "127.0.0.1", 0).start()
    yield srv
    srv.stop()


def get(url: str):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.headers.get("Content-Type"), r.read()


def post(url: str, data: dict):
    req = urllib.request.Request(
        url, json.dumps(data).encode(), {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status


def test_web_labels_a_set_end_to_end(server):
    base = server.url.rstrip("/")
    status, kind, body = get(f"{base}/")
    assert status == 200 and kind.startswith("text/html")
    assert b"Por terminar" in body and b"Pan o guarnici" in body
    first = json.loads(get(f"{base}/siguiente")[2])
    assert first["pendientes"] == 2 and first["etiquetados"] == 0
    assert first["archivo"].endswith("_T1.jpg")
    status, kind, body = get(f"{base}/img/{first['archivo']}")
    assert status == 200 and kind == "image/jpeg" and body[:2] == bytes([0xFF, 0xD8])
    assert (
        post(
            f"{base}/etiqueta",
            {
                "archivo": first["archivo"],
                "estado": "comiendo",
                "tipo": "plato",
                "nota": "ok",
            },
        )
        == 200
    )
    second = json.loads(get(f"{base}/siguiente")[2])
    assert second["archivo"].endswith("_T2.jpg") and second["etiquetados"] == 1
    assert (
        post(
            f"{base}/etiqueta",
            {"archivo": second["archivo"], "estado": "saltado", "tipo": "otro"},
        )
        == 200
    )
    last = json.loads(get(f"{base}/siguiente")[2])
    assert last["archivo"] is None and last["pendientes"] == 0
    rows = list(etiquetar.read_labels(server.folder).values())
    assert [r["estado"] for r in rows] == ["comiendo", "saltado"]


def test_web_rejects_bad_labels_and_unknown_paths(server):
    base = server.url.rstrip("/")
    first = json.loads(get(f"{base}/siguiente")[2])["archivo"]
    with pytest.raises(HTTPError) as e:
        post(f"{base}/etiqueta", {"archivo": first, "estado": "casi", "tipo": "plato"})
    assert e.value.code == 400 and "estado" in e.value.read().decode()
    with pytest.raises(HTTPError) as e:
        get(f"{base}/img/..%2Fsesion.json")
    assert e.value.code == 404
    with pytest.raises(HTTPError) as e:
        get(f"{base}/otra")
    assert e.value.code == 404


def test_main_checks_its_arguments(tmp_path):
    with pytest.raises(SystemExit) as e:  # no es una sesión grabada
        etiquetar.main(["recortar", str(tmp_path), "--set", str(tmp_path / "s")])
    assert e.value.code == 2
    with pytest.raises(
        SystemExit
    ) as e:  # etiquetar (sin subcomando) una carpeta que no existe
        etiquetar.main([str(tmp_path / "nada")])
    assert e.value.code == 2


def test_main_recorta_and_tells_how_to_label(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(etiquetar, "load_detector", lambda: None)
    session = make_session(tmp_path / "sesion", readings=2)
    out = tmp_path / "set"
    assert (
        etiquetar.main(["recortar", str(session), "--set", str(out), "--cada", "2"])
        == 0
    )
    assert len(list(out.glob("*.jpg"))) == 2
    assert "tools/etiquetar.py" in capsys.readouterr().out
