"""Roles: el default de config/servio.toml y que todo salga de la config (R-1)."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from servio_vision.roles import METRICS, Roles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import metricas

OWNER_ONLY = {
    "desperdicio",
    "historico",
    "antes_despues",
    "costos_y_configuracion",
    "ritmo_por_plato",
    "sobra_por_plato",
    "adopcion",
}


def test_default_matches_the_decided_table():
    roles = Roles.load()
    waiter = roles.get("mozo")
    assert waiter.visible_metrics() == []
    assert waiter.visible_views() == ["reconocimiento"]
    assert (waiter.mesas, waiter.avisos) == ("todas", "suyas")

    manager = roles.get("encargado")
    assert set(manager.visible_metrics()) == set(METRICS) - OWNER_ONLY
    assert not roles.can_open("encargado", "configuracion")

    assert roles.get("dueno").visible_metrics() == list(METRICS)
    assert roles.can_open("dueno", "configuracion")


def test_changing_the_file_changes_what_a_role_sees(tmp_path):
    config = tmp_path / "servio.toml"
    config.write_text(
        '[roles.encargado]\nnombre = "Encargado"\nmetricas = ["desperdicio"]\n',
        encoding="utf-8",
    )
    roles = Roles.load(config)
    assert roles.can_see("encargado", "desperdicio")
    assert not roles.can_see("encargado", "platos_que_vuelven")


def test_admin_sees_everything_even_if_not_listed(tmp_path):
    config = tmp_path / "servio.toml"
    config.write_text(
        '[roles.dueno]\nnombre = "Dueño"\nadmin = true\n', encoding="utf-8"
    )
    roles = Roles.load(config)
    assert roles.get("dueno").visible_metrics() == list(METRICS)
    assert roles.can_open("dueno", "dashboard")


def test_typos_in_the_config_fail_loudly(tmp_path):
    config = tmp_path / "servio.toml"
    config.write_text(
        '[roles.encargado]\nnombre = "Encargado"\nmetricas = ["desperdico"]\n',
        encoding="utf-8",
    )
    with pytest.raises(ValidationError, match="desperdico"):
        Roles.load(config)
    with pytest.raises(KeyError, match="rol desconocido"):
        Roles.load().get("cocinero")


def test_metricas_tool_respects_the_role(tmp_path, capsys):
    db = tmp_path / "servio.db"
    assert metricas.main(["--db", str(db), "--rol", "mozo"]) == 3
    assert "no ve" in capsys.readouterr().out

    config = tmp_path / "servio.toml"
    config.write_text(
        '[roles.encargado]\nnombre = "Encargado"\nmetricas = ["platos_que_vuelven"]\n',
        encoding="utf-8",
    )
    assert (
        metricas.main(["--db", str(db), "--rol", "encargado", "--config", str(config)])
        == 0
    )
    out = capsys.readouterr().out
    assert "Platos que vuelven llenos" in out
    assert "Segundos en responder" not in out
