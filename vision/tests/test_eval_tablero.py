"""`tools/eval.py --all` (B-3, parte 2): el tablero de números, guardado con fecha y
comparado con el anterior."""

import json
import os
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import eval as evaluation

from servio_vision.llm import PlateEstimate, PlatesReport
from tests.model_fakes import FakeEstimator


def test_accuracy_by_state_is_the_diagonal_of_the_matrix():
    s = {"matriz": {"lleno": {"lleno": 3, "comiendo": 1}, "vacio": {"lleno": 2}}}
    assert evaluation.accuracy_by_state(s) == {
        "lleno": 0.75,
        "comiendo": None,
        "por_terminar": None,
        "vacio": 0.0,
    }


def make_set(folder: Path, rows):
    folder.mkdir()
    lines = ["archivo,estado,tipo,nota"]
    for name, state in rows:
        Image.new("RGB", (40, 30), (200, 150, 100)).save(folder / name)
        lines.append(f"{name},{state},plato,")
    lines.append("x.jpg,saltado,otro,")  # lo saltado en etiquetar.py no cuenta
    if rows:  # una corrección: la última fila del mismo archivo es la que vale
        lines.append(f"{rows[0][0]},{rows[0][1]},plato,corregido")
    (folder / "labels.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_evaluate_set_sends_crops_in_batches_and_scores_them(tmp_path):
    folder = tmp_path / "set"
    make_set(folder, [("a.jpg", "lleno"), ("b.jpg", "vacio"), ("c.jpg", "comiendo")])
    calls = []

    def answer(plates, table_id, references=None):
        calls.append((len(plates), table_id))
        return PlatesReport(
            plates=[
                PlateEstimate(index=1, fill_ratio=0.95, confidence=0.9),
                PlateEstimate(index=2, fill_ratio=0.0, confidence=0.9),
            ][: len(plates)]
        )

    results, latencies = evaluation.evaluate_set(
        folder, FakeEstimator(answer), per_call=2, log=lambda s: None
    )
    assert calls == [(2, "EVAL1"), (1, "EVAL2")]
    assert [r["predicho"] for r in results] == ["lleno", "vacio", "lleno"]

    def skipping(plates, table_id, references=None):  # el modelo saltea el plato 1
        return PlatesReport(
            plates=[PlateEstimate(index=2, fill_ratio=0.0, confidence=0.9)]
        )

    skipped, _ = evaluation.evaluate_set(
        folder, FakeEstimator(skipping), per_call=3, log=lambda s: None
    )
    # por índice, no por posición: el 1 queda sin respuesta, el 2 es «vacío»
    assert [r["predicho"] for r in skipped] == [None, "vacio", None]
    assert len(latencies) == 2
    summary = evaluation.summarize(results)
    assert summary["acierto"] == round(2 / 3, 3)
    assert evaluation.accuracy_by_state(summary)["comiendo"] == 0.0


class Meter:
    def __init__(self, calls, cost=0.0, tokens=1000):
        self.calls, self.cost_usd = calls, cost
        self.input_tokens, self.output_tokens = tokens, 40


def test_cost_summary_per_table_hour():
    c = evaluation.cost_summary(
        Meter(4, cost=0.02, tokens=4000), calls_per_hour_per_table=120
    )
    assert c["costo_por_llamada_usd"] == 0.005 and c["costo_mesa_hora_usd"] == 0.6
    assert c["tokens_entrada_por_llamada"] == 1000 and c["nota"] is None
    without = evaluation.cost_summary(Meter(4), 120)  # la API no informa costo
    assert without["costo_mesa_hora_usd"] is None and "tokens" in without["nota"]
    assert evaluation.cost_summary(Meter(0), 120)["llamadas"] == 0


def test_board_is_saved_with_a_date_and_compared_with_the_previous_one(tmp_path):
    scenarios = [
        {"escenario": "a", "pasa": True, "diferencias": []},
        {"escenario": "b", "pasa": False, "diferencias": ["esperado y no salió: x"]},
    ]
    first = evaluation.build_board(
        accuracy=None,
        scenarios=scenarios,
        latencies=[],
        cost=None,
        set_dir=None,
        interval=30,
    )
    assert first["acierto"] is None and first["escenarios"]["pasan"] == 1
    assert first["avisos_falsos_por_hora"] is None  # llega con C-5
    first["fecha"] = "2026-11-10T20:00:00+00:00"
    path = evaluation.save_board(first, tmp_path)
    assert path.name == "tablero-20261110-200000.json"
    accuracy = evaluation.summarize(
        [
            {"estado": "lleno", "predicho": "lleno", "fill_ratio": 0.9},
            {"estado": "vacio", "predicho": "vacio", "fill_ratio": 0.0},
        ]
    )
    second = evaluation.build_board(
        accuracy=accuracy,
        scenarios=[dict(s, pasa=True) for s in scenarios],
        latencies=[2.0, 4.0],
        cost=evaluation.cost_summary(Meter(2, cost=0.01), 120),
        set_dir=Path("data/eval"),
        interval=30,
    )
    second["fecha"] = "2026-11-11T20:00:00+00:00"
    evaluation.save_board(second, tmp_path)
    saved = evaluation.saved_boards(tmp_path)
    assert [p.name for p in saved] == [
        "tablero-20261110-200000.json",
        "tablero-20261111-200000.json",
    ]
    lines = evaluation.compare_boards(
        evaluation.load_board(saved[0]), evaluation.load_board(saved[1])
    )
    text = "\n".join(lines)
    assert "acierto: – → 1" in text
    assert "escenarios que pasan: 1 de 2 → 2 de 2  ↑ mejor" in text
    # un escenario nuevo que pasa no es una mejora: se compara la proporción
    same = evaluation.compare_boards(
        {"fecha": "a", "escenarios": {"pasan": 4, "total": 4}},
        {"fecha": "b", "escenarios": {"pasan": 5, "total": 5}},
    )
    assert "escenarios que pasan: 4 de 4 → 5 de 5  igual" in "\n".join(same)
    assert "latencia media: – → 3 s" in text
    assert "costo por mesa por hora: – → 0.6 USD" in text
    evaluation.print_board(evaluation.load_board(saved[1]))  # no rompe


def test_compare_marks_worse_and_equal():
    a = {
        "fecha": "a",
        "acierto": 0.9,
        "escenarios": {"pasan": 5, "total": 5},
        "latencia_media_s": 2.0,
        "costo": {"costo_mesa_hora_usd": 0.5},
    }
    b = dict(a, fecha="b", acierto=0.8, latencia_media_s=3.0)
    text = "\n".join(evaluation.compare_boards(a, b))
    assert "acierto: 0.9 → 0.8  ↓ peor" in text
    assert "escenarios que pasan: 5 de 5 → 5 de 5  igual" in text
    assert "latencia media: 2 s → 3 s  ↓ peor" in text
    assert "costo por mesa por hora: 0.5 USD → 0.5 USD  igual" in text


def test_a_scenario_runs_through_the_whole_pipeline_and_passes(tmp_path):
    """Un escenario de fixtures/observations/ por `main.py` con el fake, comparado
    con su esperado en fixtures/pipeline/ (lo mismo que test_e2e_pipeline, desde la
    herramienta)."""
    before = Path.cwd()
    result = evaluation.run_scenario("plato_lleno_mucho", tmp_path)
    assert (result["escenario"], result["pasa"], result["diferencias"]) == (
        "plato_lleno_mucho",
        True,
        [],
    )
    assert result["avisos"][0]["event"] == "alert_opened"
    assert Path.cwd() == before  # volvió a la carpeta de antes


def test_run_scenario_reports_a_broken_run_instead_of_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        evaluation,
        "alert_events",
        lambda log: (_ for _ in ()).throw(ValueError("vacío")),
    )
    result = evaluation.run_scenario("plato_lleno_mucho", tmp_path)
    assert result["pasa"] is False and result["corrida_ok"] is False
    assert "no dejó avisos" in result["diferencias"][0]


def test_scenarios_ignore_the_house_env(tmp_path, monkeypatch):
    """El .env de la máquina (o lo que `load_dotenv` ya dejó en el entorno) no
    cambia los escenarios: corren con los umbrales de producción."""
    monkeypatch.setenv("FINISHED_AFTER", "1")  # haría otros avisos
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("SERVIO_INTERVAL", "5")
    monkeypatch.setenv("SERVIO_DEMO", "true")  # el alias que gana sobre DEMO_MODE
    result = evaluation.run_scenario("plato_lleno_mucho", tmp_path)
    assert result["pasa"], result["diferencias"]
    assert os.environ["FINISHED_AFTER"] == "1"  # y el entorno queda como estaba


def test_sin_modelo_needs_all_and_interval_is_positive(tmp_path):
    make_set(tmp_path / "set", [("a.jpg", "lleno")])
    with pytest.raises(SystemExit):
        evaluation.main(["--set", str(tmp_path / "set"), "--sin-modelo"])
    with pytest.raises(SystemExit):
        evaluation.main(["--all", "--sin-modelo", "--interval", "0"])


def test_scenario_names_come_from_fixtures():
    names = evaluation.scenario_names()
    assert "mesa_completa" in names and "plato_vacio" in names


def test_main_compares_two_saved_boards(tmp_path, capsys):
    for day, acierto in (("10", 0.5), ("11", 0.7)):
        board = {
            "fecha": f"2026-11-{day}T20:00:00+00:00",
            "acierto": acierto,
            "escenarios": {"pasan": 1, "total": 1},
        }
        (tmp_path / f"t{day}.json").write_text(json.dumps(board), encoding="utf-8")
    code = evaluation.main(
        ["--comparar", str(tmp_path / "t10.json"), str(tmp_path / "t11.json")]
    )
    assert code == 0
    assert "acierto: 0.5 → 0.7  ↑ mejor" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        evaluation.main([])  # falta --set, --all o --comparar
