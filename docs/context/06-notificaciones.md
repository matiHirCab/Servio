# 06 — Notificaciones (Telegram, solo envío)

Dueño: Matías (`vision/servio_vision/alerts/notify.py`). El mozo no responde: no hay botones, comandos ni webhook.

## Setup

1. Crear el bot con **@BotFather** → `/newbot` → guardar `TELEGRAM_BOT_TOKEN`.
2. Crear el grupo **"Mozos"** con el bot y el equipo. Para la demo, el jurado ve los mensajes en un celular.
3. Obtener el `chat_id` del grupo (mandar un mensaje y mirar `https://api.telegram.org/bot<token>/getUpdates`) → `TELEGRAM_GROUP_CHAT_ID`.

## Uso

Con `httpx` directo a `https://api.telegram.org/bot<token>/<método>`, sin librerías de bots:

- Abrir aviso o recordatorio → `sendMessage` (`chat_id`, `text`). Guardar el `message_id` en el `Alert`.
- Cerrar aviso → `editMessageText` del mensaje original agregando "✅ ya resuelto".
- Si Telegram falla: loguear y seguir; el loop no se frena.
- Flag `--no-telegram`: imprime los mensajes en consola en vez de mandarlos.

## `alerts/notify.py`

- `TelegramNotifier.from_env()` lee las dos variables (y da `ValueError` si falta alguna).
- `send(text) -> message_id | None` y `edit(message_id, text) -> bool`. Nunca lanzan: si Telegram falla, logean un warning (sin el token) y devuelven `None` o `False`.
- `resolved_text(text)` agrega `"\n\n✅ ya resuelto"` al texto original, para el `edit` al cerrar.
- `ConsoleNotifier` es el modo `--no-telegram`: imprime `[telegram #n] …` y devuelve ids que suben, así `process()` funciona igual.
- `get_notifier(telegram)`: con `telegram=False`, o si faltan las variables (con un warning), usa la consola.
- Agrupar por mesa y guardar el `message_id` en el `Alert` lo hace `alerts/processor.py` (ver «Tests» en [05-reglas-alertas.md](05-reglas-alertas.md)). `alerts.configure(config, telegram=...)` carga el `.env` de la raíz y elige el notifier con `get_notifier`.

## Prueba

Desde `vision/`, con las variables en el `.env` de la raíz:

```bash
python tools/send_test_alert.py              # manda un aviso al grupo y a los 2 s lo marca resuelto
python tools/send_test_alert.py --no-telegram
```

## Variables de entorno

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_GROUP_CHAT_ID=
```
