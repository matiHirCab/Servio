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

## Variables de entorno

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_GROUP_CHAT_ID=
```
