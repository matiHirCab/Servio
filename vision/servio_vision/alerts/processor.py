"""Une estado, motor y Telegram (MAT-5). Ver docs/context/05-reglas-alertas.md.

`AlertProcessor.process(obs)` actualiza el estado de la mesa (`state.py`), corre el
motor (`engine.py`) y manda, recuerda o edita con el notifier (`notify.py`). Cada
observación y cada aviso quedan en el log `out/eventos.jsonl` (03-contratos.md, «Log»),
después de una línea `run_started` con los umbrales de la corrida.

Agrupado por mesa: los avisos que se abren en la misma evaluación van en un solo
mensaje, y lo mismo los recordatorios. Al cerrar se edita el mensaje en el que se abrió
el aviso: si todos sus avisos están cerrados, con "✅ ya resuelto" al final; si no, solo
las líneas cerradas llevan "✅".
"""

import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from servio_vision import metrics
from servio_vision.alerts import persist
from servio_vision.alerts.config import AlertConfig
from servio_vision.alerts.engine import evaluate, table_name
from servio_vision.alerts.feedback import FeedbackDesk, Question
from servio_vision.alerts.models import Alert, TableObservation
from servio_vision.alerts.notify import Notifier, resolved_text
from servio_vision.alerts.state import (
    TableState,
    empty_table,
    table_status,
    update_table,
)
from servio_vision.db import Database
from servio_vision.restaurant import Restaurant

logger = logging.getLogger(__name__)

RESOLVED_LINE_MARK = " ✅"
# Un estado guardado hace más que esto ya no describe la sala: se arranca de cero.
RESUME_MAX_AGE_S = 600.0
# El estado se guarda cada tanto y no en cada lectura (una vuelta por mesa, con la
# base remota son idas y vueltas del loop); un aviso que se abre o se cierra lo
# guarda al instante y `close()` guarda lo último.
SAVE_STATE_EVERY_S = 5.0
# Una base caída falla en cada lectura: el traceback se loguea una vez por minuto.
DB_LOG_EVERY_S = 60.0


class EventLog:
    """Una línea JSON por evento, agregada al final del archivo (no se pisa)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        # Abierto una sola vez; buffering=1 baja cada línea al disco al escribirla.
        self._file = path.open("a", encoding="utf-8", buffering=1)
        self._lock = threading.Lock()  # también escriben las respuestas de los mozos

    def write(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._file.write(json.dumps(event, ensure_ascii=False) + "\n")

    def close(self) -> None:
        self._file.close()


class AlertProcessor:
    """Estado en memoria de todas las mesas y de los avisos abiertos."""

    def __init__(
        self,
        config: AlertConfig,
        notifier: Notifier,
        log_path: Path,
        restaurant: Restaurant | None = None,
        db: Database | None = None,
        resume: bool = False,
        resume_max_age_s: float = RESUME_MAX_AGE_S,
    ) -> None:
        """`resume`: seguir con el estado que dejó la corrida anterior en `db` (si es
        de hace menos de `resume_max_age_s`). Solo para una cámara en vivo: un video
        o un fixture empiezan siempre de cero."""
        self.config = config
        self.notifier = notifier
        self.log = EventLog(log_path)
        # Con los datos del restaurante, los avisos nombran el plato y el mozo, y el de
        # plato lleno pregunta qué pasó (botones en Telegram, ver feedback.py).
        self.restaurant = restaurant
        # base relacional (db.py): platos servidos, avisos y respuestas
        self.db = db
        self._served_rows: dict[str, int] = {}  # track_id → platos_servidos.id
        self._plate_states: dict[str, str] = {}  # track_id → último estado guardado
        self._on_table: dict[str, set[str]] = {}  # mesa → track_ids en la base
        self._alert_plate: dict[str, str] = {}  # aviso → track_id del plato
        if db is not None and restaurant is not None:
            db.load_restaurant(restaurant)
        self.feedback: FeedbackDesk | None = None
        if restaurant is not None:
            commands = {"metricas": self._send_metrics} if db is not None else {}
            self.feedback = FeedbackDesk(notifier, self._on_feedback, commands)
            self.feedback.start()
        # Marca dónde empieza la corrida (el log se agrega) y con qué umbrales: replay.py
        # corta el archivo acá y reproduce con la misma config.
        self.log.write(
            {
                "event": "run_started",
                "started_at": _iso(datetime.now(UTC)),
                "config": dataclasses.asdict(config),
            }
        )
        self.tables: dict[str, TableState] = {}
        self.open_alerts: dict[str, Alert] = {}
        # message_id → avisos de ese mensaje (en orden) y cuáles ya se cerraron.
        self._messages: dict[int, list[Alert]] = {}
        self._resolved_ids: set[str] = set()
        # aviso de mesa terminada cerrado porque retiraron los platos → la línea
        # que reemplaza al "ya resuelto" (quién y cuánto tardó)
        self._close_notes: dict[str, str] = {}
        self._state_dirty = False  # hay estado sin guardar
        self._saved_at = float("-inf")  # time.monotonic() del último intento
        # (escritura, tipo de error) → cuándo se logueó, y cuántos fallos se callaron
        self._db_logged: dict[tuple[str, str], float] = {}
        self._db_muted: dict[tuple[str, str], int] = {}
        self._save_now = False  # pasó algo que no puede esperar al próximo guardado
        if resume and db is not None:
            self._resume(db, resume_max_age_s)

    def _resume(self, db: Database, max_age_s: float) -> None:
        """Sigue con las mesas y los avisos abiertos de la corrida anterior: los
        tiempos de los platos no se reinician, un aviso abierto no se repite y su
        mensaje de Telegram se sigue pudiendo editar."""
        try:
            saved = db.load_state()
            if saved is None:
                return
            text, saved_at = saved
            age = (datetime.now(UTC) - saved_at).total_seconds()
            if age > max_age_s:
                logger.info("El estado guardado es de hace %d s: arranco de cero", age)
                return
            snapshot = persist.loads(text)
        except Exception:
            # ilegible o la base caída: arrancar de cero es lo que pasaba antes
            logger.exception("No se pudo recuperar el estado guardado: arranco de cero")
            return
        self.tables = snapshot.tables
        self.open_alerts = snapshot.open_alerts
        self._messages = snapshot.messages
        self._resolved_ids = snapshot.resolved_ids
        self._close_notes = snapshot.close_notes
        self._alert_plate = snapshot.alert_plate
        self._served_rows = snapshot.served_rows
        self._plate_states = snapshot.plate_states
        self._on_table = snapshot.on_table
        logger.info(
            "Sigo donde quedé hace %d s: %d mesas, %d avisos abiertos",
            age,
            len(self.tables),
            len(self.open_alerts),
        )

    def _save_state(self) -> None:
        assert self.db is not None
        snapshot = persist.Snapshot(
            self.tables,
            self.open_alerts,
            self._messages,
            self._resolved_ids,
            self._close_notes,
            self._alert_plate,
            self._served_rows,
            self._plate_states,
            self._on_table,
        )
        self.db.save_state(persist.dumps(snapshot), datetime.now(UTC))
        self._state_dirty = False

    def _flush_state(self) -> None:
        self._saved_at = time.monotonic()  # también si falla: no insistir cada lectura
        self._save_now = False
        self._db_write("el estado", self._save_state)

    def _record_plates(
        self, obs: TableObservation, table: TableState, ts: datetime
    ) -> None:
        """Cada plato nuevo de la mesa, a la base (con el plato del pedido que es),
        y la lectura de cada plato: estado, cuánto le queda y si se fue de la
        mesa (métricas de M-1). Un plato que heredó los tiempos de otro (cambió
        de id) sigue siendo el mismo plato servido."""
        if self.db is None or self.restaurant is None:
            return
        active = table.active_plates()
        for plate in active:
            if plate.track_id in self._served_rows:
                continue
            # el id de la fila tiene que llegar al estado guardado ya: si no, tras
            # un reinicio el plato se insertaría de nuevo
            self._save_now = True
            dish = self.restaurant.dish(obs.table_id, plate.track_id)
            same = self._served_rows.get(plate.inherited_from or "")
            if same is not None:
                self._served_rows[plate.track_id] = same
                last = self._plate_states.pop(plate.inherited_from, None)
                if last is not None:
                    self._plate_states[plate.track_id] = last
                continue
            self._served_rows[plate.track_id] = self.db.served(
                obs.table_id, dish.dish_id if dish else None, plate.track_id, ts
            )
        readings = []
        states: dict[str, str] = {}
        for item in obs.items:
            served_id = self._served_rows.get(item.track_id)
            if served_id is None:
                continue
            changed = self._plate_states.get(item.track_id) != item.state
            states[item.track_id] = item.state
            readings.append((served_id, item.state, item.fill_ratio, changed))
        # los que estaban y ya no están (salvo que otro id haya heredado el plato)
        inherited = {p.inherited_from for p in table.plates.values()}
        before = self._on_table.get(obs.table_id, set())
        gone = before - set(table.plates) - inherited
        self.db.plate_readings(
            readings, [self._served_rows[t] for t in sorted(gone)], ts
        )
        # Recién con la base al día: si la escritura falló, la próxima lectura
        # repite estos cambios de estado y estos platos retirados en vez de perderlos.
        self._plate_states.update(states)
        for track_id in gone:
            self._plate_states.pop(track_id, None)
        self._on_table[obs.table_id] = {p.track_id for p in active}

    def _record_opened(self, alert: Alert, ts: datetime) -> None:
        if self.db is None:
            return
        track_id = self._alert_plate.get(alert.id)
        served_id = self._served_rows.get(track_id) if track_id else None
        self._db_write(
            "el aviso abierto",
            self.db.alert_opened,
            alert.id,
            alert.type,
            alert.table_id,
            served_id,
            ts,
        )

    def _db_write(self, what: str, write: Callable[..., Any], *args: Any) -> None:
        """Una escritura a la base. La base es auxiliar (métricas e historial): si
        falla se loguea y se sigue, así el aviso al mozo sale igual. Con la base
        caída falla en cada lectura: el traceback sale una vez por `DB_LOG_EVERY_S`
        y los demás se cuentan."""
        try:
            write(*args)
        except Exception as error:
            # por escritura y por tipo de error: una causa nueva no se calla
            key = (what, type(error).__name__)
            now = time.monotonic()
            last = self._db_logged.get(key)
            if last is not None and now - last < DB_LOG_EVERY_S:
                self._db_muted[key] = self._db_muted.get(key, 0) + 1
                return
            self._db_logged[key] = now
            muted = self._db_muted.pop(key, 0)
            more = f" (y {muted} fallos más desde el último aviso)" if muted else ""
            logger.exception("No se pudo guardar %s en la base%s", what, more)

    def _send_metrics(self) -> None:
        """`/metricas` en el grupo: la imagen de métricas con un resumen corto.
        Junta el historial simulado (se importa una vez) con lo de hoy."""
        if self.db is None or self.restaurant is None:
            return
        self.db.import_history(metrics.HISTORY, self.restaurant)
        m = metrics.compute(self.db, operation=False)  # la imagen no las usa
        if not m["dishes"]:
            self.notifier.send("📊 Todavía no hay datos para las métricas.")
            return
        image = self.db.path.parent / "metricas.png"
        metrics.render(m, image)
        self.notifier.send_photo(image, metrics.summary(m))
        problems = metrics.problems_text(m)  # aparte: en el pie de foto no entra
        if problems:
            self.notifier.send(problems)

    def _on_feedback(self, event: dict[str, Any]) -> None:
        """Respuesta de un mozo: al log y a la base."""
        self.log.write(event)
        if self.db is not None:
            try:
                self.db.feedback(event)
            except Exception:
                logger.exception("No se pudo guardar la respuesta en la base")

    def process(self, obs: TableObservation) -> list[dict[str, Any]]:
        """Procesa una observación de mesa; devuelve los eventos que escribió al log.

        Cada evento se escribe apenas pasa: la observación antes de aplicarla (queda
        aunque `update_table` la rechace) y cada aviso apenas se manda o edita.
        """
        events: list[dict[str, Any]] = []

        def log(event: dict[str, Any]) -> None:
            self.log.write(event)
            events.append(event)

        log({"event": "observation", **obs.model_dump(mode="json")})
        prev = self.tables.get(obs.table_id) or empty_table(obs.table_id)
        table = update_table(prev, obs)  # ValueError si el ts va hacia atrás
        self.tables[obs.table_id] = table

        if self.restaurant is not None:
            self.restaurant.see_plates(
                obs.table_id, [p.track_id for p in table.active_plates()]
            )
        self._db_write("las lecturas", self._record_plates, obs, table, obs.ts)

        result = evaluate(table, obs.ts, self.config, self.open_alerts.values())
        opened_all = [self._with_context(a, table, obs.ts) for a in result.opened]
        grouped = [a for a, question in opened_all if question is None]

        if grouped:
            text = "\n".join(a.message for a in grouped)
            message_id = self.notifier.send(text)
            opened = [
                a.model_copy(update={"telegram_message_id": message_id})
                for a in grouped
            ]
            if message_id is not None:
                self._messages[message_id] = opened
            for alert in opened:
                self.open_alerts[alert.id] = alert
                log(_alert_event("alert_opened", obs.ts, alert, text))
                self._record_opened(alert, obs.ts)

        for alert, question in opened_all:
            if question is None:
                continue
            # plato lleno: mensaje propio, con los botones de "¿qué pasó?"
            message_id = self.feedback.ask(question)
            alert = alert.model_copy(update={"telegram_message_id": message_id})
            if message_id is not None:
                self._messages[message_id] = [alert]
            self.open_alerts[alert.id] = alert
            log(_alert_event("alert_opened", obs.ts, alert, alert.message))
            self._record_opened(alert, obs.ts)

        if result.reminded:
            text = "\n".join(
                self._mention_waiter(r.message, obs.table_id) for r in result.reminded
            )
            self.notifier.send(text)  # si falla, ya se logueó; el aviso sigue igual
            for reminder in result.reminded:
                self.open_alerts[reminder.alert.id] = reminder.alert
                log(_alert_event("alert_reminder", obs.ts, reminder.alert, text))

        # Primero se marcan todos los cerrados, así un mensaje se edita una sola vez.
        message_ids: list[int] = []
        reasons = {a.id: self._close_reason(a, table, obs.ts) for a in result.resolved}
        for alert in result.resolved:
            del self.open_alerts[alert.id]
            message_id = alert.telegram_message_id
            if message_id is not None:
                self._resolved_ids.add(alert.id)
                if message_id not in message_ids:
                    message_ids.append(message_id)
        edited = {message_id: self._edit(message_id) for message_id in message_ids}
        for alert in result.resolved:
            # Sin mensaje (Telegram falló al abrir) no hubo nada que editar: text None.
            text = edited.get(alert.telegram_message_id)
            event = _alert_event("alert_resolved", obs.ts, alert, text)
            if reasons[alert.id]:
                event["close_reason"] = reasons[alert.id]
            log(event)
            if self.db is not None:
                self._db_write(
                    "el aviso cerrado",
                    self.db.alert_resolved,
                    alert.id,
                    obs.ts,
                    reasons[alert.id],
                )

        if self.db is not None:
            self._state_dirty = True
            alerts_changed = any(e["event"] != "observation" for e in events)
            due = time.monotonic() - self._saved_at >= SAVE_STATE_EVERY_S
            if alerts_changed or self._save_now or due:
                self._flush_state()
        return events

    def close(self) -> None:
        if self.db is not None and self._state_dirty:
            self._flush_state()
        if self.feedback is not None:
            self.feedback.stop()
        self.log.close()
        if self.db is not None:
            self.db.close()

    def _with_context(
        self, alert: Alert, table: TableState, now: datetime
    ) -> tuple[Alert, Question | None]:
        """Aviso con el plato y el mozo (si hay datos del restaurante). El de plato
        lleno vuelve con la pregunta para el mozo."""
        if self.restaurant is None:
            return alert, None
        waiter = self.restaurant.waiter(alert.table_id)
        if alert.type != "plato_lleno_mucho" or self.feedback is None:
            message = self._mention_waiter(alert.message, alert.table_id)
            return alert.model_copy(update={"message": message}), None
        full = sorted(
            (p for p in table.active_plates() if p.state == "lleno"),
            key=lambda p: p.first_seen,
        )
        plate = full[0] if full else None
        if plate is not None:
            self._alert_plate[alert.id] = plate.track_id
        dish = self.restaurant.dish(alert.table_id, plate.track_id) if plate else None
        minutes = (
            max(1, round((now - plate.first_seen).total_seconds() / 60)) if plate else 1
        )
        who = f"{waiter} · " if waiter else ""
        what = dish.name if dish else "un plato"
        message = (
            f"❓ {who}{table_name(alert.table_id)}: {what} sigue lleno hace "
            f"{minutes} min. ¿Está todo bien? Pasá a preguntar y contanos qué pasó."
        )
        alert = alert.model_copy(update={"message": message})
        question = Question(
            alert_id=alert.id,
            table_id=alert.table_id,
            alert_type=alert.type,
            dish=dish.name if dish else None,
            waiter=waiter,
            text=message,
            answers=dish.answers if dish else ("Está todo bien", "No le gustó"),
            asked_at=datetime.now(UTC),
        )
        return alert, question

    def _mention_waiter(self, message: str, table_id: str) -> str:
        """`🍽️ Mesa 1: terminó.` → `🍽️ Juan · Mesa 1: terminó.`"""
        waiter = self.restaurant.waiter(table_id) if self.restaurant else None
        if not waiter:
            return message
        name = table_name(table_id)
        return message.replace(name, f"{waiter} · {name}", 1)

    def _close_reason(
        self, alert: Alert, table: TableState, now: datetime
    ) -> str | None:
        """Por qué se cerró un aviso de mesa terminada: "retirados" (la mesa quedó
        libre) o "servido" (llegó algo nuevo). Con retirados, deja la línea que va en
        el mensaje: quién retiró y cuánto después de que terminó la mesa."""
        if alert.type != "mesa_terminada":
            return None
        if table_status(p.state for p in table.active_plates()) != "libre":
            return "servido"
        waiter = self.restaurant.waiter(alert.table_id) if self.restaurant else None
        who = f"{waiter} retiró" if waiter else "Retiraron"
        took = _duration(now - alert.created_at)
        self._close_notes[alert.id] = (
            f"✅ {who} los platos {took} después de que terminó la mesa."
        )
        return "retirados"

    def _edit(self, message_id: int) -> str:
        """Reescribe un mensaje con sus avisos cerrados marcados; devuelve el texto."""
        alerts = self._messages[message_id]
        done = all(a.id in self._resolved_ids for a in alerts)
        resolved_ids = set(self._resolved_ids)
        notes = [
            self._close_notes.pop(a.id) for a in alerts if a.id in self._close_notes
        ]

        def build(answers: str) -> str:
            # las respuestas del mozo quedan en el mensaje (antes del "ya resuelto")
            text = "\n".join(a.message for a in alerts) + answers
            if done and notes:
                # mesa terminada y platos retirados: quién y cuánto tardó
                return text + "\n\n" + "\n".join(notes)
            if done:
                return resolved_text(text)
            return _partial_text(alerts, resolved_ids) + answers

        if done:
            del self._messages[message_id]
            self._resolved_ids -= {a.id for a in alerts}
        if self.feedback is not None:
            # con el lock de las respuestas: una que llega a la vez no lo pisa
            return self.feedback.rewrite(message_id, build, resolved=done)
        text = build("")
        self.notifier.edit(message_id, text)  # si falla, ya se logueó
        return text


def _duration(elapsed: timedelta) -> str:
    """`timedelta` → "1 min 20 s" / "45 s"."""
    seconds = max(0, round(elapsed.total_seconds()))
    minutes, seconds = divmod(seconds, 60)
    if not minutes:
        return f"{seconds} s"
    return f"{minutes} min {seconds} s" if seconds else f"{minutes} min"


def _partial_text(alerts: Sequence[Alert], resolved_ids: set[str]) -> str:
    return "\n".join(
        a.message + (RESOLVED_LINE_MARK if a.id in resolved_ids else "") for a in alerts
    )


def _alert_event(
    name: str, ts: datetime, alert: Alert, text: str | None
) -> dict[str, Any]:
    return {
        "event": name,
        "ts": _iso(ts),
        **alert.model_dump(mode="json"),
        "text": text,
    }


def _iso(ts: datetime) -> str:
    # Mismo formato que pydantic usa para los demás ts del log (UTC con "Z").
    return ts.isoformat().replace("+00:00", "Z")
