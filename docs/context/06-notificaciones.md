# 06 — Notificaciones (Telegram)

Dueño: Matías (`vision/servio_vision/alerts/notify.py` y `feedback.py`). Los avisos se mandan con `sendMessage` y se editan al cerrarse. El mozo responde con **botones** (las respuestas típicas del plato y «✏️ Otra respuesta») y el grupo tiene el comando `/metricas`; no hay webhook: `feedback.py` escucha con *long polling* (`getUpdates`) en un hilo aparte. Ver «Plazo y cola» abajo para lo que pasa cuando Telegram no contesta.

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
- **Plazo y cola.** `send` y `edit` corren en el camino del loop de lecturas, así que no hacen esperar más de `SEND_DEADLINE_S` (8 s): cada intento se corta en lo que queda del plazo y no se espera un `retry_after` que ya no entra. Lo que no llegó (red, 5xx, 429) va a una cola de hasta 50 mensajes que un hilo reintenta con espera creciente, hasta 10 min (un aviso más viejo ya no sirve); un 4xx no se reintenta. Con cola, los envíos nuevos entran detrás sin esperar a una red que no anda y salen en orden cuando vuelve. Un mensaje que sale tarde no le deja su `message_id` al `Alert` (`send` ya devolvió `None`): el mozo lo recibe, pero ese aviso no se edita al cerrarse. `close()` (lo llama `AlertProcessor.close`) para el hilo y avisa cuántos quedaron sin entregar.
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
