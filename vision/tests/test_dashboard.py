"""Dashboard del encargado y del dueño (D-1): rutas, qué ve cada rol y refresco."""

import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from servio_vision.dashboard import Dashboard, payload
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant
from servio_vision.roles import Roles

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import dashboard as dashboard_tool

T0 = datetime(2026, 10, 19, 0, 0, tzinfo=UTC)  # 21:00 en Montevideo


@pytest.fixture
def db(tmp_path):
    db = Database(tmp_path / "servio.db", local_id="casa-tomas")
    db.load_restaurant(Restaurant.load())
    yield db
    db.close()


@pytest.fixture
def roles():
    return Roles.load()


def _service(db):
    """Una mesa con un plato que volvió, un aviso abierto y una mesa retirada."""
    risotto = db.served("T2", "risotto", "T2-p1", T0)
    db.alert_opened("a1", "plato_lleno_mucho", "T2", risotto, T0 + timedelta(minutes=5))
    db.feedback(
        {
            "alert_id": "a1",
            "table_id": "T2",
            "answer": "Estaba frío",
            "free_text": False,
            "seconds_to_answer": 40,
            "ts": "2026-10-19T00:06:00Z",
        }
    )
    db.alert_resolved("a1", T0 + timedelta(minutes=6))
    db.alert_opened("a2", "mesa_terminada", "T2", None, T0 + timedelta(minutes=30))
    db.alert_resolved("a2", T0 + timedelta(minutes=37), "retirados")
    db.alert_opened("a3", "mesa_terminada", "T1", None, T0 + timedelta(minutes=50))


def test_encargado_sees_only_his_metrics(db, roles):
    _service(db)
    data = payload(db, roles, "encargado", now=T0 + timedelta(minutes=53))
    m = data["metricas"]
    assert "desperdicio" not in m and "adopcion" not in m
    assert "configuracion" not in data
    assert m["tiempo_respuesta"]["segundos"] == 40
    assert m["mesa_terminada_sin_atender"]["promedio"] == 7
    assert m["mesa_terminada_sin_atender"]["fuera_de_tiempo"] == 1
    assert m["platos_que_vuelven"]["total"] == 1
    assert m["platos_que_vuelven"]["por_plato"][0]["motivo"] == "Estaba frío"
    [alert] = m["avisos_abiertos"]
    assert alert["tipo"] == "Mesa terminada"
    assert alert["minutos"] == 3
    assert alert["respuesta"] is None
    assert data["actualizado"] == "21:53"


def test_dueno_sees_everything_and_what_is_pending(db, roles):
    _service(db)
    data = payload(db, roles, "dueno")
    m = data["metricas"]
    assert m["desperdicio"]["total"] == 690
    assert m["adopcion"] == {"avisos": 1, "respondidos": 1, "no_hacia_falta": 0}
    assert set(data["pendientes"]) == {
        "historico",
        "antes_despues",
        "costos_y_configuracion",
    }
    assert {r["id"] for r in data["configuracion"]["roles"]} == {
        "mozo",
        "encargado",
        "dueno",
    }
    assert data["configuracion"]["umbrales"]["mesa_sin_atender_min"] == 5


def test_empty_base_has_no_numbers_but_does_not_fail(db, roles):
    m = payload(db, roles, "dueno")["metricas"]
    assert m["avisos_abiertos"] == []
    assert m["tiempo_respuesta"] is None
    assert m["mesas_olvidadas"] is None
    assert m["rotacion"] is None
    assert m["carga_por_mozo"] is None


def test_routes(db, roles):
    dash = Dashboard(db, roles)
    page = dash.handle("/casa-tomas/dashboard", "rol=dueno")
    assert page.status == 200 and page.content_type.startswith("text/html")
    assert b"servio" in page.body
    data = dash.handle("/casa-tomas/dashboard/datos/", "rol=dueno")
    assert json.loads(data.body)["rol"] == "dueno"
    # sin ?rol= es el encargado
    assert json.loads(dash.handle("/casa-tomas/dashboard/datos").body)["rol"] == (
        "encargado"
    )
    assert dash.handle("/casa-tomas/dashboard", "rol=mozo").status == 403
    assert dash.handle("/casa-tomas/dashboard", "rol=chef").status == 404
    # lo que no es del dashboard sigue de largo (el salón, otro local)
    assert dash.handle("/casa-tomas/salon") is None
    assert dash.handle("/otra-casa/dashboard") is None
    assert dash.handle("/casa-tomas/dashboard/otra") is None
    assert dash.handle("/") is None


def test_numbers_refresh_once_per_hour(db, roles):
    now = [0.0]
    dash = Dashboard(db, roles, clock=lambda: now[0])
    first = dash.data("encargado")
    _service(db)
    now[0] = 30 * 60
    assert dash.data("encargado") is first  # media hora: los mismos números
    now[0] = 61 * 60
    assert dash.data("encargado")["metricas"]["tiempo_respuesta"]["segundos"] == 40


def test_tool_serves_the_dashboard(db, roles):
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), dashboard_tool.make_handler(Dashboard(db, roles))
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{base}/") as res:  # redirige a /<local>/dashboard
            assert res.url.endswith("/casa-tomas/dashboard")
        with urlopen(f"{base}/casa-tomas/dashboard/datos?rol=dueno") as res:
            assert json.load(res)["nombre_rol"] == "Dueño"
        with pytest.raises(HTTPError) as e:
            urlopen(f"{base}/casa-tomas/nada")
        assert e.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_salon_server_also_serves_the_dashboard(db, roles):
    from servio_vision.app import build_dashboard
    from servio_vision.salon import SalonFeed
    from servio_vision.salon_web import SalonServer

    assert build_dashboard(None) is None  # sin base no hay métricas
    srv = SalonServer(
        SalonFeed("casa-tomas"), "127.0.0.1", 0, build_dashboard(db)
    ).start()
    base = f"http://127.0.0.1:{srv.port}/casa-tomas"
    try:
        with urlopen(f"{base}/dashboard/datos?rol=dueno") as res:
            assert json.load(res)["rol"] == "dueno"
        with urlopen(f"{base}/salon") as res:  # el salón sigue igual
            assert res.status == 200
        with pytest.raises(HTTPError) as e:
            urlopen(f"{base}/dashboard?rol=mozo")
        assert e.value.code == 403
    finally:
        srv.stop()
