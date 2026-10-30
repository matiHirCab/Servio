"""La foto del estado de los avisos, para seguir después de un reinicio (A-5).

`Snapshot` junta lo que el núcleo y sus salidas guardan en memoria y que un
reinicio perdería: los tiempos de cada plato (`TableState`) y los avisos abiertos
(`core.py`), los mensajes de cada aviso con su `message_id` (`TelegramOutput`), y lo
que lo conecta con la base, qué fila de `platos_servidos` es cada plato
(`DatabaseOutput`). Lo arma `processor.py`. `dumps`/`loads` lo pasan a JSON para la tabla
`estado_servicio` (`db.py`).

Una foto vieja puede traer `alert_plate` (el plato de cada aviso, antes de A-4): se
ignora, porque ahora ese plato viaja en el evento del aviso y no queda en memoria.

No se guardan las preguntas de «¿qué pasó?» de `feedback.py`: tras un reinicio los
botones de un mensaje viejo ya no responden, pero el aviso se edita y se cierra igual.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from servio_vision.alerts.models import Alert
from servio_vision.alerts.state import PlateTrack, TableState

VERSION = 1
_PLATE_TIMES = ("first_seen", "state_since", "last_seen", "id_since")


@dataclass
class Snapshot:
    tables: dict[str, TableState] = field(default_factory=dict)
    open_alerts: dict[str, Alert] = field(default_factory=dict)
    messages: dict[int, list[Alert]] = field(default_factory=dict)
    resolved_ids: set[str] = field(default_factory=set)
    close_notes: dict[str, str] = field(default_factory=dict)
    served_rows: dict[str, int] = field(default_factory=dict)
    plate_states: dict[str, str] = field(default_factory=dict)
    on_table: dict[str, set[str]] = field(default_factory=dict)


def dumps(snapshot: Snapshot) -> str:
    return json.dumps(
        {
            "version": VERSION,
            "tables": {k: _table_out(t) for k, t in snapshot.tables.items()},
            "open_alerts": {
                k: a.model_dump(mode="json") for k, a in snapshot.open_alerts.items()
            },
            "messages": {
                str(m): [a.model_dump(mode="json") for a in alerts]
                for m, alerts in snapshot.messages.items()
            },
            "resolved_ids": sorted(snapshot.resolved_ids),
            "close_notes": snapshot.close_notes,
            "served_rows": snapshot.served_rows,
            "plate_states": snapshot.plate_states,
            "on_table": {k: sorted(v) for k, v in snapshot.on_table.items()},
        },
        ensure_ascii=False,
    )


def loads(text: str) -> Snapshot:
    """La foto guardada; `ValueError` si no se puede leer (otra versión, JSON roto)."""
    try:
        data = json.loads(text)
        if data["version"] != VERSION:
            raise ValueError(f"versión {data['version']} de la foto")
        return Snapshot(
            tables={k: _table_in(t) for k, t in data["tables"].items()},
            open_alerts={k: _alert_in(a) for k, a in data["open_alerts"].items()},
            messages={
                int(m): [_alert_in(a) for a in alerts]
                for m, alerts in data["messages"].items()
            },
            resolved_ids=set(data["resolved_ids"]),
            close_notes=dict(data["close_notes"]),
            served_rows={k: int(v) for k, v in data["served_rows"].items()},
            plate_states=dict(data["plate_states"]),
            on_table={k: set(v) for k, v in data["on_table"].items()},
        )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError(f"foto ilegible: {error!r}") from error


def _alert_in(data: dict[str, Any]) -> Alert:
    # Alert es estricto: un ts como texto solo lo acepta en modo JSON.
    return Alert.model_validate_json(json.dumps(data))


def _table_out(table: TableState) -> dict[str, Any]:
    return {
        "table_id": table.table_id,
        "last_ts": table.last_ts.isoformat() if table.last_ts else None,
        "people_present": table.people_present,
        "people_absent_readings": table.people_absent_readings,
        "plates": {k: _plate_out(p) for k, p in table.plates.items()},
    }


def _plate_out(plate: PlateTrack) -> dict[str, Any]:
    data = asdict(plate)
    for name in _PLATE_TIMES:
        data[name] = data[name].isoformat()
    return data


def _table_in(data: dict[str, Any]) -> TableState:
    last_ts = data["last_ts"]
    return TableState(
        table_id=data["table_id"],
        plates={k: _plate_in(p) for k, p in data["plates"].items()},
        last_ts=datetime.fromisoformat(last_ts) if last_ts else None,
        people_present=data["people_present"],
        people_absent_readings=data["people_absent_readings"],
    )


def _plate_in(data: dict[str, Any]) -> PlateTrack:
    fields = dict(data)
    for name in _PLATE_TIMES:
        fields[name] = datetime.fromisoformat(fields[name])
    return PlateTrack(**fields)
