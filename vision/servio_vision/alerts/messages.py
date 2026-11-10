"""Textos de los avisos y del bot (A-4, C-5). Ver docs/context/05-reglas-alertas.md.

Todo lo que lee un mozo sale de acá: el núcleo (`core.py`), el canal (`outputs.py`)
y el bot (`bot.py`, `feedback.py`) solo eligen qué texto va. Funciones puras: el
mozo y el plato llegan ya resueltos (`Restaurant`), así cambiar un texto o el idioma
no toca las reglas.

Cada aviso empieza con la mesa («Mesa 2 🧹 …») y el mozo va al final («… · Sofía»),
para que en el reloj se lea primero lo que importa. La negrita de «Mesa 2» la pone
el canal (`to_html`, Telegram con `parse_mode=HTML`): acá todo es texto plano, que es
lo que va al log, a los fixtures y a la vista del salón.
"""

import html
import re
from collections.abc import Sequence
from datetime import timedelta

from servio_vision.alerts.models import Alert, AlertType

RESOLVED_SUFFIX = "\n\n✅ ya resuelto"
RESOLVED_LINE_MARK = " ✅"
NO_METRICS = "📊 Todavía no hay datos para las métricas."
DEFAULT_ANSWERS = ("Está todo bien", "No le gustó")

# Botones de todos los avisos (C-5) y sus acciones.
BUTTON_VOY = "Voy"
BUTTON_LISTO = "Listo"
BUTTON_NO = "No hacía falta"
ACTION_LABELS = {"voy": BUTTON_VOY, "listo": BUTTON_LISTO, "no_hacia_falta": BUTTON_NO}
# Palabras que valen como botón cuando el mozo contesta citando el aviso (respuesta
# rápida del reloj o del celular): se comparan normalizadas y enteras (`normalize`).
KEYWORDS = {
    "voy": ("voy", "ya voy", "👍"),
    "listo": ("listo", "hecho", "ya esta", "✅"),
    "no_hacia_falta": ("no hacia falta", "falso", "❌"),
}

# respuestas al tocar un botón (aparecen un momento arriba del chat)
BUTTON_EXPIRED = "Este aviso ya no está activo"
BUTTON_ALREADY_ANSWERED = "Ya respondieron este aviso"
BUTTON_WRITE = "Escribí qué pasó"
BUTTON_THANKS = "¡Gracias! Quedó registrado"
BUTTON_TAKEN = "Ya lo tomó {who}"
BUTTON_ALREADY_DONE = "Ya marcaron «Listo»"


def table_name(table_id: str) -> str:
    """Nombre para el mensaje: `T1` → `Mesa 1`; cualquier otro id queda igual."""
    match = re.fullmatch(r"T(\d+)", table_id)
    return f"Mesa {match.group(1)}" if match else table_id


def minutes(elapsed: timedelta) -> int:
    return max(1, round(elapsed.total_seconds() / 60))


def open_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    if alert_type == "mesa_terminada":
        return f"{name} 🍽️ terminó"
    if alert_type == "plato_vacio":
        return f"{name} 🧹 hay platos vacíos para retirar"
    return (
        f"{name} ❓ un plato sigue lleno hace {minutes(elapsed)} min. ¿Está todo bien?"
    )


def reminder_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    name = table_name(table_id)
    n = minutes(elapsed)
    if alert_type == "mesa_terminada":
        return f"{name} 🍽️ terminó hace {n} min"
    if alert_type == "plato_vacio":
        return f"{name} 🧹 los platos vacíos siguen sin retirar hace {n} min"
    return f"{name} ❓ el plato sigue lleno hace {n} min. ¿Está todo bien?"


def nudge_message(alert_type: AlertType, table_id: str, elapsed: timedelta) -> str:
    """La insistencia después de «Listo»: pasó `done_wait` y la cámara no lo vio."""
    name = table_name(table_id)
    n = minutes(elapsed)
    if alert_type == "mesa_terminada":
        what = "los platos siguen en la mesa"
    elif alert_type == "plato_vacio":
        what = "los platos vacíos siguen sin retirar"
    else:
        what = "el plato sigue lleno"
    return f"{name} 🍽️ dijiste «Listo» hace {n} min y {what}"


ESCALATION_NOBODY = "nadie respondió"


def escalation_message(
    alert_type: AlertType, table_id: str, elapsed: timedelta, unanswered: int
) -> str:
    """El escalado: nadie respondió al aviso ni a sus recordatorios. El mozo lo pone
    el núcleo con `escalation_mention`."""
    plural = "mensaje" if unanswered == 1 else "mensajes"
    return (
        f"{table_name(table_id)} ⚠️ sin atender hace {minutes(elapsed)} min"
        f" · {ESCALATION_NOBODY} a {unanswered} {plural}"
    )


def escalation_mention(message: str, waiter: str | None) -> str:
    """`… · nadie respondió a 3 mensajes` → `… · Sofía no respondió a 3 mensajes`."""
    if not waiter:
        return message
    return message.replace(ESCALATION_NOBODY, f"{waiter} no respondió", 1)


def mention_waiter(message: str, table_id: str, waiter: str | None) -> str:
    """`Mesa 1 🍽️ terminó` → `Mesa 1 🍽️ terminó · Juan`. Si el mensaje sigue con
    otra oración o línea («. ¿Está todo bien?»), el mozo va antes de eso."""
    if not waiter:
        return message
    cut = len(message)
    for mark in (". ", "\n"):
        at = message.find(mark)
        if at != -1:
            cut = min(cut, at)
    return f"{message[:cut]} · {waiter}{message[cut:]}"


def full_plate_question(
    table_id: str, waiter: str | None, dish: str | None, elapsed: timedelta | None
) -> str:
    """El aviso de plato lleno con datos del restaurante: pregunta qué pasó."""
    what = dish or "un plato"
    n = minutes(elapsed) if elapsed is not None else 1
    who = f" · {waiter}" if waiter else ""
    return (
        f"{table_name(table_id)} ❓ {what} sigue lleno hace {n} min{who}."
        " ¿Está todo bien? Pasá a preguntar y contanos qué pasó."
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


def action_line(action: str, who: str) -> str:
    """La línea que se agrega al aviso cuando tocan «Voy», «Listo» o «No hacía falta»."""
    icon = {"voy": "👟", "listo": "✅", "no_hacia_falta": "🚫"}[action]
    return f"{icon} {ACTION_LABELS[action]} · {who}"


def normalize(text: str) -> str:
    """Para comparar una respuesta escrita con `KEYWORDS`: minúsculas, sin tildes ni
    puntuación, espacios simples."""
    import unicodedata

    plain = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in plain if not unicodedata.combining(c))
    plain = re.sub(r"[^\w\s👍✅❌]", " ", plain)
    return " ".join(plain.split())


def keyword_action(text: str) -> str | None:
    """La acción de una respuesta escrita entera («ya voy» → `voy`), o None."""
    plain = normalize(text)
    for action, words in KEYWORDS.items():
        if plain in words:
            return action
    return None


_TABLE_AT_START = re.compile(r"^(Mesa \d+)", re.MULTILINE)


def to_html(text: str) -> str:
    """El texto para Telegram con `parse_mode=HTML`: todo escapado y la mesa en
    negrita al principio de cada línea. Un id de mesa que `table_name` no traduce
    queda sin negrita."""
    return _TABLE_AT_START.sub(r"<b>\1</b>", html.escape(text, quote=False))
