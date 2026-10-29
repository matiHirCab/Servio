"""Chequeo de encuadre (B-4): oscuro, quemado, desenfocado, mesa cortada o chica."""

import cv2
import numpy as np
import pytest

from servio_vision import framing
from servio_vision.zones import TableZone, Zones


def textured_frame(brightness: int = 120, size=(480, 640)) -> np.ndarray:
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
    assert framing.problems(textured_frame(), GOOD) == []


def test_dark_and_burnt_frames():
    [dark] = framing.problems(textured_frame(brightness=20), GOOD)
    assert dark.startswith("Imagen oscura")
    [burnt] = framing.problems(textured_frame(brightness=250), GOOD)
    assert burnt.startswith("Imagen quemada")


def test_blurry_frame():
    blurry = cv2.GaussianBlur(textured_frame(), (0, 0), sigmaX=12)
    [blur] = framing.problems(blurry, GOOD)
    assert blur.startswith("Imagen desenfocada")


def test_table_touching_the_border_is_cut():
    [cut] = framing.problems(textured_frame(), zones_with(table(0, 90, 400, 480)))
    assert "pegada al borde izquierdo y de abajo" in cut


def test_small_table_and_missing_table():
    [small] = framing.problems(textured_frame(), zones_with(table(300, 200, 360, 250)))
    assert "se ve muy chica" in small
    [none] = framing.problems(textured_frame(), None)
    assert none.startswith("No se ve ninguna mesa")
    assert framing.problems(textured_frame(), zones_with())[0].startswith("No se ve")


def test_sharpness_is_measured_at_a_fixed_width():
    big = cv2.resize(textured_frame(), (1920, 1440), interpolation=cv2.INTER_NEAREST)
    zones = Zones(frame_size=(1920, 1440), tables=[table(400, 300, 1500, 1100)])
    assert framing.problems(big, zones) == []


def test_log_report_says_ok_or_warns(caplog):
    caplog.set_level("INFO")
    assert framing.log_report(textured_frame(), GOOD) == []
    assert "Encuadre OK: 1 mesa(s)" in caplog.text and "Luz OK" in caplog.text
    found = framing.log_report(textured_frame(brightness=20), GOOD)
    assert len(found) == 1 and "Encuadre: Imagen oscura" in caplog.text
    # en dos partes, como app.run: la luz antes de clasificar, las mesas después
    caplog.clear()
    assert framing.log_report(textured_frame(), None, tables=False) == []
    assert "Luz OK" in caplog.text and "Encuadre OK" not in caplog.text
    assert framing.log_report(textured_frame(), None, light=False) == [framing.NO_TABLE]


def test_cli_checks_a_photo(tmp_path, capsys):
    photo = tmp_path / "mesa.png"
    cv2.imwrite(str(photo), textured_frame(brightness=20))
    zones = tmp_path / "zones.json"
    zones.write_text(GOOD.model_dump_json(), encoding="utf-8")
    assert framing.main(["--source", str(photo), "--zones", str(zones)]) == 1
    cv2.imwrite(str(photo), textured_frame())
    assert framing.main(["--source", str(photo), "--zones", str(zones)]) == 0
    assert (
        framing.main(["--source", str(tmp_path / "no.png"), "--zones", str(zones)]) == 2
    )


def test_a_fixed_zone_over_the_whole_frame_is_glued_to_the_borders():
    """Una zona dibujada a todo el cuadro es una mesa sin aire, no "sin mesa"."""
    [glued] = framing.problems(textured_frame(), zones_with(table(0, 0, 639, 479)))
    assert "pegada al borde izquierdo y derecho y de arriba y de abajo" in glued
    assert framing.table_problems(textured_frame(), None) == [framing.NO_TABLE]


def test_small_frames_are_not_upscaled_to_measure_sharpness():
    small = cv2.resize(textured_frame(), (320, 240), interpolation=cv2.INTER_NEAREST)
    zones = Zones(frame_size=(320, 240), tables=[table(60, 45, 260, 195)])
    assert framing.problems(small, zones) == []


def test_cli_uses_the_shared_parser_and_explains_bad_zones(tmp_path, capsys):
    photo = tmp_path / "mesa.png"
    cv2.imwrite(str(photo), textured_frame())
    bad = tmp_path / "zones.json"
    bad.write_text(
        '{"frame_size": [640, 480], "tables": [{"table_id": "T1", "name": "M", "polygon": [[0, 0], [1, 1]]}]}',
        "utf-8",
    )
    assert framing.main(["--source", str(photo), "--zones", str(bad)]) == 2
    assert "Zonas:" in capsys.readouterr().err
    assert framing.main([]) == 2  # sin fuente: lo dice load_settings


def test_load_tables_is_shared_with_the_app(tmp_path):
    from servio_vision.app import load_tables
    from servio_vision.settings import Settings

    zones = tmp_path / "zones.json"
    zones.write_text(GOOD.model_dump_json(), encoding="utf-8")
    fixed, tracker = load_tables(Settings.load(source="0", zones=zones))
    assert tracker is None and [t.name for t in fixed.tables] == ["Mesa 1"]
    with pytest.raises(FileNotFoundError, match="draw_zones"):
        load_tables(Settings.load(source="0", zones=tmp_path / "no.json"))


def test_main_checks_the_light_before_classifying_and_the_tables_after(
    tmp_path, monkeypatch, caplog
):
    import main as loop
    from servio_vision import alerts
    from servio_vision.zones import save_zones

    video = tmp_path / "salon.avi"
    writer = cv2.VideoWriter(
        str(video), cv2.VideoWriter_fourcc(*"MJPG"), 10, (640, 480)
    )
    for _ in range(10 * 5):
        writer.write(textured_frame(brightness=20))
    writer.release()
    zones = tmp_path / "zones.json"
    save_zones(zones_with(table(0, 90, 400, 480)), zones)
    monkeypatch.delenv("DEMO_MODE", raising=False)
    monkeypatch.setattr(alerts, "DEFAULT_LOG", tmp_path / "eventos.jsonl")
    caplog.set_level("INFO")
    assert (
        loop.main(
            [
                "--source",
                str(video),
                "--zones",
                str(zones),
                "--no-telegram",
                "--limit",
                "1",
            ]
        )
        == 0
    )
    alerts._processor.close()
    messages = [
        r.message for r in caplog.records if r.message.startswith(("Encuadre", "Luz"))
    ]
    assert messages[0].startswith("Encuadre: Imagen oscura")
    assert any("pegada al borde izquierdo y de abajo" in m for m in messages[1:])
