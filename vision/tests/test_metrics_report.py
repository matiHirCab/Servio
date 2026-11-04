"""Reporte de métricas en la terminal (`metrics.print_operation` / `print_report`)."""

from servio_vision import metrics

OPS = {
    "unattended": {"mesas": 4, "promedio": 3.5, "peor": 9.0, "fuera_de_tiempo": 0.25},
    "forgotten": [("Ana", 10, 2)],
    "rotation": {
        "visits": 12,
        "services": 3,
        "minutes": 47.0,
        "per_service": 4.0,
        "by_kind": {"almuerzo": (45.0, 3.5)},
    },
    "waste": [("Milanesa", 3, 4500.0)],
    "waste_total": 4500.0,
    "services": 3,
    "load": [("Ana", 4.0, 2.5), ("Beto", 3.0, None)],
    "peaks": [("20:00", 8, 0.5), ("21:00", 2, None)],
    "pace": [("Milanesa", 5, 22.0)],
    "leftover": [("Pasta", 4, 0.3)],
    "adoption": {"respondidos": 8, "avisos": 10, "no_hacia_falta": 1},
    "next_dish": [("Ana", 6, 7.5)],
}
EMPTY_OPS = {
    k: ([] if isinstance(v, list) else {} if isinstance(v, dict) else 0)
    for k, v in OPS.items()
}


def report(ops, **extra):
    return {
        "days": 7,
        "alerts": 5,
        "dishes": [("Milanesa", 0.4, "fría")],
        "attend": [("Ana", 3.2)],
        "answer": [("Ana", 45.0)],
        "problems": {"Milanesa": [("fría", 0.2), ("salada", 0.5)]},
        "ops": ops,
        "live_answers": 0,
        **extra,
    }


def test_operation_report_prints_every_section(capsys):
    metrics.print_operation(OPS, set(metrics.REPORT_METRICS))
    out = capsys.readouterr().out
    for title in (
        "Mesa terminada sin atender",
        "Mesas olvidadas",
        "Tiempo de mesa y rotación",
        "Desperdicio",
        "Carga por mozo",
        "Horas pico",
        "Ritmo por plato",
        "Sobra por plato",
        "Adopción: 8 de 10",
        "Espera del siguiente plato",
    ):
        assert title in out
    assert "$4,500" in out


def test_operation_report_respects_the_role_filter(capsys):
    metrics.print_operation(OPS, {"adopcion"})
    out = capsys.readouterr().out
    assert "Adopción" in out
    assert "Desperdicio" not in out


def test_operation_report_with_no_data_prints_nothing(capsys):
    metrics.print_operation(EMPTY_OPS, set(metrics.REPORT_METRICS))
    assert capsys.readouterr().out == ""


def test_problem_lines_sorted_from_most_to_least_common():
    lines = metrics.problem_lines(report(OPS), "Milanesa")
    assert [text for text, _ in lines] == ["salada", "fría"]
    assert metrics.problem_lines(report(OPS), "Otro") == []


def test_print_report_shows_dishes_waiters_and_problems(capsys):
    metrics.print_report(report(EMPTY_OPS, live_answers=3))
    out = capsys.readouterr().out
    assert "últimos 7 días" in out
    assert "Milanesa" in out and "40.0 %" in out
    assert "Ana" in out and "3.2 min" in out
    assert "salada" in out
    assert "incluye 3 respuestas reales" in out


def test_print_report_filtered_hides_the_other_sections(capsys):
    metrics.print_report(report(OPS), {"adopcion"})
    out = capsys.readouterr().out
    assert "Platos que vuelven" not in out
    assert "Adopción" in out
