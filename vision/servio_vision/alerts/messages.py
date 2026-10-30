"""Textos de los avisos y del bot (A-4). Ver docs/context/05-reglas-alertas.md.

Todo lo que lee un mozo sale de acá: el núcleo (`core.py`), el canal (`outputs.py`)
y el bot (`bot.py`, `feedback.py`) solo eligen qué texto va. Funciones puras: el
mozo y el plato llegan ya resueltos (`Restaurant`), así cambiar un texto o el idioma
no toca las reglas.
"""

import re
from collections.abc import Sequence
from datetime import timedelta

from servio_vision.alerts.models import Alert, AlertType

RESOLVED_SUFFIX = "\n\n✅ ya resuelto"
RESOLVED_LINE_MARK = " ✅"
NO_METRICS = "📊 Todavía no hay datos para las métricas."
DEFAULT_ANSWERS = ("Está todo bien", "No le gustó")

# respuestas al tocar un botón (aparecen un momento arriba del chat)
BUTTON_EXPIRED = "Este aviso ya no está activo"
BUTTON_ALREADY_ANSWERED = "Ya respondieron este aviso"
BUTTON_WRITE = "Escribí qué pasó"
BUTTON_THANKS = "¡Gracias! Quedó registrado"


def table_name(table_id: str) -> str:
    """Nombre para el mensaje: `T1` → `Mesa 1`; cualquier otro id queda igual."""
    match = re.fullmatch(r"T(\d+)", table_id)
    return f"Mesa {match.group(1)}" if match else table_id


def minutes(elapsed: timedelta) -> int:
    return max(1, round(elapsed.total_seconds() / 60))


def open_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    if alert_type == "mesa_terminada":
        return f"🍽️ {name}: terminó."
    if alert_type == "plato_vacio":
        return f"🧹 {name}: hay platos vacíos para retirar."
    return (
        f"❓ {name}: un plato sigue lleno hace {minutes(elapsed)} min. ¿Está todo bien?"
    )


def reminder_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    n = minutes(elapsed)
    if alert_type == "mesa_terminada":
        return f"🍽️ {name}: terminó hace {n} min."
    if alert_type == "plato_vacio":
        return f"🧹 {name}: los platos vacíos siguen sin retirar hace {n} min."
    return f"❓ {name}: el plato sigue lleno hace {n} min. ¿Está todo bien?"


def mention_waiter(message: str, table_id: str, waiter: str | None) -> str:
    """`🍽️ Mesa 1: terminó.` → `🍽️ Juan · Mesa 1: terminó.`"""
    if not waiter:
        return message
    name = table_name(table_id)
    return message.replace(name, f"{waiter} · {name}", 1)


def full_plate_question(
    table_id: str, waiter: str | None, dish: str | None, elapsed: timedelta | None
) -> str:
    """El aviso de plato lleno con datos del restaurante: pregunta qué pasó."""
    who = f"{waiter} · " if waiter else ""
    what = dish or "un plato"
    n = minutes(elapsed) if elapsed is not None else 1
    return (
        f"❓ {who}{table_name(table_id)}: {what} sigue lleno hace "
        f"{n} min. ¿Está todo bien? Pasá a preguntar y contanos qué pasó."
    )


def resolved_text(text: str) -> str:
    """Texto del mensaje original con la marca de resuelto."""
    return text + RESOLVED_SUFFIX


def partial_text(alerts: Sequence[Alert], resolved_ids: set[str]) -> str:
    """Un mensaje con varios avisos, con "✅" en las líneas ya cerradas."""
    return "\n".join(
        a.message + (RESOLVED_LINE_MARK if a.id in resolved_ids else "") for a in alerts
    )


def picked_up_note(waiter: str | None, took: timedelta) -> str:
    """Mesa terminada y platos retirados: reemplaza al "ya resuelto"."""
    who = f"{waiter} retiró" if waiter else "Retiraron"
    return f"✅ {who} los platos {duration(took)} después de que terminó la mesa."


def duration(elapsed: timedelta) -> str:
    """`timedelta` → "1 min 20 s" / "45 s"."""
    seconds = max(0, round(elapsed.total_seconds()))
    mins, seconds = divmod(seconds, 60)
    if not mins:
        return f"{seconds} s"
    return f"{mins} min {seconds} s" if seconds else f"{mins} min"


def reply_prompt(dish: str | None, table_id: str) -> str:
    """Pedido de «escribí qué pasó» tras tocar «Otra respuesta»."""
    prompt = "✏️ Respondé a este mensaje contando qué pasó"
    if dish:
        prompt += f" con {dish}"
    return prompt + f" ({table_name(table_id)})."


def feedback_line(answer: str) -> str:
    return f"📝 {answer}"  # quién respondió queda en la base
