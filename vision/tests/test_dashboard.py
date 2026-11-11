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
    data = payload(db, roles, "dueno", now=T0 + timedelta(minutes=53))
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
    day = ("encargado", "2026-10-18", "2026-10-18")  # 21:00 del 18 en Montevideo
    first = dash.data(*day)
    _service(db)
    now[0] = 30 * 60
    assert dash.data(*day) is first  # media hora: los mismos números
    now[0] = 61 * 60
    assert dash.data(*day)["metricas"]["tiempo_respuesta"]["segundos"] == 40


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


# --- D-1b: período y local ---

import generar_historial  # tools/, en el sys.path de arriba

from servio_vision.db import HISTORY_LOCAL
from servio_vision.metrics import Scope


@pytest.fixture
def history(db, tmp_path):
    """10 días de historial simulado (generar_historial): del día -10 al -1."""
    restaurant = Restaurant.load()
    rows = generar_historial.generate(restaurant, days=10, seed=7)
    path = tmp_path / "historial.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    db.import_history(path, restaurant)
    days = sorted({r["fecha"] for r in rows})
    return days, rows


def _numbers(db, roles, desde, hasta):
    return payload(
        db, roles, "dueno", desde=desde, hasta=hasta, locales=(HISTORY_LOCAL,)
    )["metricas"]


def test_same_meals_give_other_numbers_per_period(db, roles, history):
    days, rows = history
    all_days = _numbers(db, roles, days[0], days[-1])
    first = _numbers(db, roles, days[0], days[0])
    week = _numbers(db, roles, days[-7], days[-1])
    # un día: solo las mesas de ese día (dos servicios × 4 mesas, cada comida
    # termina con una mesa terminada)
    ended = sum(
        1 for r in rows if r["tipo"] == "mesa_terminada" and r["fecha"] == days[0]
    )
    assert first["mesa_terminada_sin_atender"]["mesas"] == ended
    total = sum(1 for r in rows if r["tipo"] == "mesa_terminada")
    assert all_days["mesa_terminada_sin_atender"]["mesas"] == total
    assert first["rotacion"]["servicios"] == 2
    assert week["rotacion"]["servicios"] == 14
    assert all_days["rotacion"]["servicios"] == 20
    assert first["desperdicio"]["total"] != all_days["desperdicio"]["total"]
    # un período sin comidas: sin números, nunca un cero que parezca dato
    empty = _numbers(db, roles, "2020-01-01", "2020-01-31")
    assert empty["tiempo_respuesta"] is None
    assert empty["mesa_terminada_sin_atender"] is None
    assert empty["platos_que_vuelven"] is None


def test_only_this_local_counts(db, roles, history, tmp_path):
    days, _ = history
    # el dashboard de una casa no ve el historial simulado
    m = payload(db, roles, "dueno", desde=days[0], hasta=days[-1])["metricas"]
    assert m["tiempo_respuesta"] is None and m["rotacion"] is None
    # ni lo de otra casa que escribe en la misma base
    other = Database(tmp_path / "servio.db", local_id="otra-casa")
    try:
        _service(other)
        _service(db)
        m = payload(db, roles, "dueno", desde="2026-10-18", hasta="2026-10-18")
        assert m["metricas"]["mesa_terminada_sin_atender"]["mesas"] == 1
        assert m["metricas"]["adopcion"]["avisos"] == 1
    finally:
        other.close()


def test_camera_utc_counts_on_the_local_day(db, roles):
    # T0 es 00:00 UTC del 19/10 = 21:00 del 18/10 en Montevideo
    _service(db)
    on_18 = payload(db, roles, "encargado", desde="2026-10-18", hasta="2026-10-18")
    on_19 = payload(db, roles, "encargado", desde="2026-10-19", hasta="2026-10-19")
    assert on_18["metricas"]["tiempo_respuesta"]["segundos"] == 40
    assert on_19["metricas"]["tiempo_respuesta"] is None
    # los avisos abiertos son de ahora: no dependen del período
    assert len(on_19["metricas"]["avisos_abiertos"]) == 1


def test_default_period_is_last_week_and_travels_in_payload(db, roles):
    now = datetime(2026, 11, 11, 2, 0, tzinfo=UTC)  # 23:00 del 10/11 en Montevideo
    data = payload(db, roles, "encargado", now=now)
    assert data["periodo"] == {
        "desde": "2026-11-04",
        "hasta": "2026-11-10",
        "hoy": "2026-11-10",
    }
    one = payload(db, roles, "encargado", now=now, desde="2026-11-01")
    assert one["periodo"]["desde"] == one["periodo"]["hasta"] == "2026-11-01"


def test_period_in_the_url(db, roles):
    _service(db)
    dash = Dashboard(db, roles)
    res = dash.handle(
        "/casa-tomas/dashboard/datos", "rol=dueno&desde=2026-10-18&hasta=2026-10-18"
    )
    data = json.loads(res.body)
    assert data["periodo"]["desde"] == "2026-10-18"
    assert data["metricas"]["tiempo_respuesta"]["segundos"] == 40
    bad = dash.handle("/casa-tomas/dashboard/datos", "desde=ayer")
    assert bad.status == 400 and "AAAA-MM-DD" in json.loads(bad.body)["error"]
    backwards = dash.handle(
        "/casa-tomas/dashboard/datos", "desde=2026-10-19&hasta=2026-10-18"
    )
    assert backwards.status == 400


def test_scope_rewrites_fact_tables_only():
    scope = Scope(("casa",), None, None)
    sql, params = scope.apply(
        "SELECT * FROM avisos a JOIN respuestas r ON r.aviso_id = a.id"
        " JOIN mozos m ON m.id = a.mozo_id"
        " WHERE EXISTS (SELECT 1 FROM respuestas WHERE x = 1)"
    )
    assert "FROM (SELECT * FROM avisos WHERE local_id IN (:_local0)) a" in sql
    assert "JOIN (SELECT * FROM respuestas WHERE local_id IN (:_local0)) r ON" in sql
    assert (
        "(SELECT * FROM respuestas WHERE local_id IN (:_local0)) respuestas WHERE"
        in sql
    )
    assert "JOIN mozos m" in sql
    assert params == {"_local0": "casa"}
    # sin scope, la consulta queda igual (tools/metricas.py y /metricas)
    assert Scope().apply("SELECT 1 FROM avisos a") == ("SELECT 1 FROM avisos a", {})
