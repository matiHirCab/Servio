"""Chequeo de encuadre (B-4): oscuro, quemado, desenfocado, mesa cortada o chica."""

import cv2
import numpy as np

from servio_vision import framing
from servio_vision.zones import TableZone, Zones


def salon(brightness: int = 120, size=(480, 640)) -> np.ndarray:
    """Un cuadro con textura (no se puede medir nitidez sobre un color plano)."""
    rng = np.random.default_rng(0)
    base = rng.integers(0, 2, (size[0] // 8, size[1] // 8), np.uint8) * 60
    texture = cv2.resize(base, (size[1], size[0]), interpolation=cv2.INTER_NEAREST)
    frame = np.clip(texture.astype(int) + brightness - 30, 0, 255).astype(np.uint8)
    return cv2.cvtColor(frame, cv2.COLOR_GRAY2BGR)


def table(x0, y0, x1, y1, name="Mesa 1") -> TableZone:
    return TableZone(
        table_id="T1", name=name, polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    )


def zones_with(*tables) -> Zones:
    return Zones(frame_size=(640, 480), tables=list(tables))


GOOD = zones_with(table(120, 90, 520, 390))


def test_a_good_frame_has_no_problems():
    assert framing.problems(salon(), GOOD) == []


def test_dark_and_burnt_frames():
    [dark] = framing.problems(salon(brightness=20), GOOD)
    assert dark.startswith("Imagen oscura")
    [burnt] = framing.problems(salon(brightness=250), GOOD)
    assert burnt.startswith("Imagen quemada")


def test_blurry_frame():
    blurry = cv2.GaussianBlur(salon(), (0, 0), sigmaX=12)
    [blur] = framing.problems(blurry, GOOD)
    assert blur.startswith("Imagen desenfocada")


def test_table_touching_the_border_is_cut():
    [cut] = framing.problems(salon(), zones_with(table(0, 90, 400, 480)))
    assert "sale cortada por el borde izquierdo y de abajo" in cut


def test_small_table_and_missing_table():
    [small] = framing.problems(salon(), zones_with(table(300, 200, 360, 250)))
    assert "se ve muy chica" in small
    [none] = framing.problems(salon(), None)
    assert none.startswith("No se ve ninguna mesa")
    assert framing.problems(salon(), zones_with())[0].startswith("No se ve")


def test_sharpness_is_measured_at_a_fixed_width():
    big = cv2.resize(salon(), (1920, 1440), interpolation=cv2.INTER_NEAREST)
    zones = Zones(frame_size=(1920, 1440), tables=[table(400, 300, 1500, 1100)])
    assert framing.problems(big, zones) == []


def test_log_report_says_ok_or_warns(caplog):
    caplog.set_level("INFO")
    assert framing.log_report(salon(), GOOD) == []
    assert "Encuadre OK: 1 mesa(s)" in caplog.text
    found = framing.log_report(salon(brightness=20), GOOD)
    assert len(found) == 1 and "Encuadre: Imagen oscura" in caplog.text


def test_cli_checks_a_photo(tmp_path, capsys):
    photo = tmp_path / "mesa.png"
    cv2.imwrite(str(photo), salon(brightness=20))
    zones = tmp_path / "zones.json"
    zones.write_text(GOOD.model_dump_json(), encoding="utf-8")
    assert framing.main(["--source", str(photo), "--zones", str(zones)]) == 1
    cv2.imwrite(str(photo), salon())
    assert framing.main(["--source", str(photo), "--zones", str(zones)]) == 0
    assert framing.main(["--source", str(tmp_path / "no.png")]) == 2
