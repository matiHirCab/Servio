"""Dashboard del encargado y del dueño (D-1, diseño F-2 «Pizarra»).

Una sola ruta por local, `/<local_id>/dashboard`, y el rol en la query
(`?rol=encargado` o `?rol=dueno`) hasta que haya login. La página
(`static/dashboard.html`) pide los números a `/<local_id>/dashboard/datos`, que
devuelve JSON con solo las métricas que ese rol ve según `config/servio.toml`
(`roles.py`). El mozo no tiene dashboard: su rol no abre la vista "dashboard".

No es tiempo real: los números se calculan con `metrics.py` y se guardan
`REFRESH_S` (1 h); la página se recarga sola a esa misma cadencia.

Período (D-1b): `?desde=AAAA-MM-DD&hasta=AAAA-MM-DD` (días del salón, los dos
incluidos; sin nada, los últimos `DEFAULT_DAYS` días). Toda métrica se calcula
sobre ese período y solo con lo de este local (`Scope` de `metrics.py`); los
avisos abiertos son de ahora y no cambian con el período. La página arma los
atajos (hoy, un día, semana, mes, a mano) y deja el período en la URL.

`handle()` no depende de ningún servidor: devuelve (status, content-type, bytes)
o None si la ruta no es suya, así lo puede montar el servidor del salón en vivo
(`salon_web.py`) o `tools/dashboard.py` para verlo solo.
"""

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from servio_vision.db import Database
from servio_vision.metrics import (
    FORGOTTEN_LIMIT_S,
    LOCAL_TZ,
    PEAK_SLOT_MIN,
    UNATTENDED_LIMIT_MIN,
    Scope,
    ScopedDatabase,
    _time,
    compute,
)
from servio_vision.roles import Role, Roles

STATIC_DIR = Path(__file__).parent / "static"
PAGE = STATIC_DIR / "dashboard.html"
REFRESH_S = 60 * 60  # se recalcula una vez por hora
DEFAULT_ROLE = "encargado"
DEFAULT_DAYS = 7  # sin ?desde=&hasta=: la última semana, hoy incluido

# Avisos sin cerrar de esta casa (los del historial simulado están todos cerrados),
# con la última respuesta del mozo ("Voy", con C-5).
SQL_OPEN_ALERTS = """
SELECT a.tipo, COALESCE(me.nombre, a.mesa_id) AS mesa,
       COALESCE(m.nombre, '(sin mozo)') AS mozo, a.abierto_at,
       (SELECT r.texto FROM respuestas r
         WHERE r.aviso_id = a.id AND r.local_id = a.local_id
         ORDER BY r.respondido_at DESC LIMIT 1) AS respuesta
  FROM avisos a
  LEFT JOIN mesas me ON me.id = a.mesa_id
  LEFT JOIN mozos m ON m.id = a.mozo_id
 WHERE a.resuelto_at IS NULL AND a.local_id = :local
 ORDER BY a.abierto_at
"""
# Tiempo de respuesta de todo el salón (SQL_ANSWER lo da por mozo).
SQL_ANSWER_ALL = "SELECT AVG(segundos) AS segundos FROM respuestas"
# Platos que vuelven, en total: platos servidos con aviso de plato lleno / servidos.
SQL_RETURNED_ALL = """
SELECT COUNT(DISTINCT a.plato_servido_id) * 1.0 / NULLIF(COUNT(DISTINCT ps.id), 0)
         AS vuelve
  FROM platos_servidos ps
  LEFT JOIN avisos a ON a.plato_servido_id = ps.id AND a.tipo = 'plato_lleno_mucho'
 WHERE ps.plato_id IS NOT NULL
"""

ALERT_TEXT = {
    "plato_lleno_mucho": "Plato lleno",
    "mesa_terminada": "Mesa terminada",
    "plato_vacio": "Plato vacío",
}

# Métricas de M-1 que todavía no se calculan: qué tarea las trae.
PENDING = {
    "historico": "Llega con D-1/D-4: las métricas semana a semana.",
    "antes_despues": "Llega con el modo sombra (D-5) y M-2.",
    "costos_y_configuracion": "El costo del modelo llega con C-2.",
}


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(float(value), digits)


def _open_alerts(db: Database, now: datetime) -> list[dict]:
    local_now = now.astimezone(LOCAL_TZ).replace(tzinfo=None)
    rows = []
    for r in db.query(SQL_OPEN_ALERTS, {"local": db.local_id}):
        minutes = (local_now - _time(r["abierto_at"])).total_seconds() / 60
        rows.append(
            {
                "tipo": ALERT_TEXT.get(r["tipo"], r["tipo"]),
                "mesa": r["mesa"],
                "mozo": r["mozo"],
                "minutos": max(0, round(minutes)),
                "respuesta": r["respuesta"],
            }
        )
    return rows


def today(now: datetime) -> date:
    """El día del salón (hora de Uruguay)."""
    return now.astimezone(LOCAL_TZ).date()


def period(desde: str | None, hasta: str | None, now: datetime) -> tuple[date, date]:
    """El período de la URL. Sin ninguno de los dos: los últimos DEFAULT_DAYS días;
    con uno solo, ese día. ValueError (con el texto para el usuario) si una fecha
    no es AAAA-MM-DD o si `hasta` es antes que `desde`."""
    if not desde and not hasta:
        end = today(now)
        return end - timedelta(days=DEFAULT_DAYS - 1), end
    try:
        start = date.fromisoformat(desde or hasta or "")
        end = date.fromisoformat(hasta or desde or "")
    except ValueError:
        raise ValueError("Las fechas van como AAAA-MM-DD (?desde=&hasta=).") from None
    if end < start:
        raise ValueError("«hasta» es antes que «desde».")
    return start, end


def _metrics(db: Database, now: datetime, scope: Scope) -> dict[str, Any]:
    """Todas las métricas que se calculan, ya en la forma que lee la página."""
    m = compute(db, scope=scope)
    ops = m["ops"] or {}
    scoped = ScopedDatabase(db, scope)
    answer = scoped.query(SQL_ANSWER_ALL)[0]["segundos"]
    returned = scoped.query(SQL_RETURNED_ALL)[0]["vuelve"]
    forgotten = ops.get("forgotten") or []
    alerts = sum(a for _, a, _ in forgotten)
    rot = ops.get("rotation")
    services = ops.get("services") or 0
    out: dict[str, Any] = {
        "avisos_abiertos": _open_alerts(db, now),
        "tiempo_respuesta": None
        if answer is None
        else {
            "segundos": _round(answer, 0),
            "por_mozo": [{"mozo": w, "segundos": _round(s, 0)} for w, s in m["answer"]],
        },
        "mesa_terminada_sin_atender": None
        if not ops.get("unattended")
        else {
            "mesas": ops["unattended"]["mesas"],
            "promedio": _round(ops["unattended"]["promedio"]),
            "peor": _round(ops["unattended"]["peor"], 0),
            "fuera_de_tiempo": _round(ops["unattended"]["fuera_de_tiempo"], 3),
            "limite_min": UNATTENDED_LIMIT_MIN,
        },
        "mesas_olvidadas": None
        if not alerts
        else {
            "avisos": alerts,
            "olvidadas": sum(f for _, _, f in forgotten),
            "limite_s": FORGOTTEN_LIMIT_S,
            "por_mozo": [
                {"mozo": w, "avisos": a, "olvidadas": f} for w, a, f in forgotten
            ],
        },
        "rotacion": None
        if not rot
        else {
            "mesas_por_servicio": _round(rot["per_service"]),
            "minutos": _round(rot["minutes"], 0),
            "servicios": rot["services"],
        },
        "resumen_cierre": {"canal": "Telegram"},
        "platos_que_vuelven": None
        if not m["dishes"]
        else {
            "total": _round(returned, 3),
            "por_plato": [
                {"plato": d, "vuelve": _round(p, 3), "motivo": why}
                for d, p, why in m["dishes"]
            ],
        },
        "desperdicio": None
        if not ops.get("waste")
        else {
            "total": _round(ops["waste_total"], 0),
            "por_servicio": _round(ops["waste_total"] / services, 0)
            if services
            else None,
            "por_plato": [
                {"plato": d, "platos": n, "pesos": _round(p, 0)}
                for d, n, p in ops["waste"]
            ],
        },
        "carga_por_mozo": [
            {"mozo": w, "mesas_por_servicio": _round(n), "minutos_retiro": _round(a)}
            for w, n, a in ops.get("load") or []
        ]
        or None,
        "horas_pico": [
            {"franja": s, "avisos": n, "tarde": _round(late, 3)}
            for s, n, late in ops.get("peaks") or []
        ]
        or None,
        "ritmo_por_plato": [
            {"plato": d, "platos": n, "minutos": _round(x)}
            for d, n, x in ops.get("pace") or []
        ]
        or None,
        "sobra_por_plato": [
            {"plato": d, "platos": n, "sobra": _round(x, 3)}
            for d, n, x in ops.get("leftover") or []
        ]
        or None,
        "adopcion": None
        if not ops.get("adoption")
        else {
            "avisos": ops["adoption"]["avisos"],
            "respondidos": ops["adoption"]["respondidos"],
            "no_hacia_falta": ops["adoption"]["no_hacia_falta"],
        },
        "espera_siguiente_plato": [
            {"mozo": w, "veces": n, "minutos": _round(x)}
            for w, n, x in ops.get("next_dish") or []
        ]
        or None,
    }
    return out


def _settings(roles: Roles) -> dict:
    """Lo que el dueño ve en Configuración: umbrales y qué ve cada rol."""
    return {
        "umbrales": {
            "mesa_sin_atender_min": UNATTENDED_LIMIT_MIN,
            "plato_lleno_sin_respuesta_s": FORGOTTEN_LIMIT_S,
            "franja_horas_pico_min": PEAK_SLOT_MIN,
        },
        "roles": [
            {
                "id": rid,
                "nombre": role.nombre,
                "vistas": role.visible_views(),
                "metricas": len(role.visible_metrics()),
            }
            for rid, role in roles.roles.items()
        ],
    }


def payload(
    db: Database,
    roles: Roles,
    role_id: str,
    now: datetime | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    locales: tuple[str, ...] | None = None,
) -> dict:
    """El JSON de un rol: solo las métricas que ve, y cuáles le faltan todavía,
    sobre el período `desde`–`hasta` (ver `period()`) y solo con las filas de
    `locales` (por defecto, este local)."""
    role: Role = roles.get(role_id)
    now = now or datetime.now(UTC)
    start, end = period(desde, hasta, now)
    scope = Scope(locales or (db.local_id,), start, end)
    everything = _metrics(db, now, scope)
    visible = role.visible_metrics()
    data = {
        "rol": role_id,
        "nombre_rol": role.nombre,
        "local": db.local_id,
        "periodo": {
            "desde": start.isoformat(),
            "hasta": end.isoformat(),
            "hoy": today(now).isoformat(),
        },
        "actualizado": now.astimezone(LOCAL_TZ).strftime("%H:%M"),
        "refresco_s": REFRESH_S,
        "vistas": role.visible_views(),
        "metricas": {k: everything[k] for k in visible if k in everything},
        "pendientes": {k: PENDING[k] for k in visible if k in PENDING},
    }
    if roles.can_open(role_id, "configuracion"):
        data["configuracion"] = _settings(roles)
    return data


@dataclass
class Response:
    status: int
    content_type: str
    body: bytes


def _json(status: int, data: dict) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(status, "application/json; charset=utf-8", body)


class Dashboard:
    """Rutas del dashboard de un local, con los números guardados `refresh_s`
    (por rol y período). `locales`: de qué locales son las filas que cuentan; por
    defecto solo este. `tools/dashboard.py --demo` suma el historial simulado."""

    def __init__(
        self,
        db: Database,
        roles: Roles,
        refresh_s: int = REFRESH_S,
        clock: Callable[[], float] = time.monotonic,
        locales: tuple[str, ...] | None = None,
    ) -> None:
        self.db = db
        self.roles = roles
        self.refresh_s = refresh_s
        self.locales = locales or (db.local_id,)
        self._clock = clock
        self._cache: dict[tuple, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def data(
        self, role_id: str, desde: str | None = None, hasta: str | None = None
    ) -> dict:
        key = (role_id, desde, hasta)
        with self._lock:
            hit = self._cache.get(key)
            if hit and self._clock() - hit[0] < self.refresh_s:
                return hit[1]
            fresh = payload(
                self.db,
                self.roles,
                role_id,
                desde=desde,
                hasta=hasta,
                locales=self.locales,
            )
            self._cache[key] = (self._clock(), fresh)
            return fresh

    def handle(self, path: str, query: str = "") -> Response | None:
        """`/<local_id>/dashboard` (la página) y `/<local_id>/dashboard/datos`
        (JSON). Cualquier otra ruta, u otro local: None, para que siga el
        servidor que lo monta."""
        parts = [p for p in path.split("/") if p]
        if len(parts) not in (2, 3) or parts[0] != self.db.local_id:
            return None
        if parts[1] != "dashboard" or (len(parts) == 3 and parts[2] != "datos"):
            return None
        qs = parse_qs(query)
        role_id = (qs.get("rol") or [DEFAULT_ROLE])[0]
        try:
            allowed = self.roles.can_open(role_id, "dashboard")
        except KeyError:
            return _json(404, {"error": f"No existe el rol «{role_id}»."})
        if not allowed:
            return _json(403, {"error": "Este rol no tiene dashboard."})
        if len(parts) == 2:
            return Response(200, "text/html; charset=utf-8", PAGE.read_bytes())
        desde = qs["desde"][0] if qs.get("desde") else None
        hasta = qs["hasta"][0] if qs.get("hasta") else None
        try:
            period(desde, hasta, datetime.now(UTC))
        except ValueError as e:
            return _json(400, {"error": str(e)})
        return _json(200, self.data(role_id, desde, hasta))
