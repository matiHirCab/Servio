# 06 — Notificaciones

## Telegram (único canal: gratis, inmediato, con botones)

Dueños: **P1** crea el bot y programa `TelegramNotifier` (`web/lib/notify/telegram.ts`); **P2** programa el webhook (`web/app/api/telegram/webhook/route.ts`) y lo registra. La interfaz entre los dos es `Notifier` en [03-contratos.md](03-contratos.md).

1. Crear el bot con **@BotFather** → `/newbot` → guardar `TELEGRAM_BOT_TOKEN`.
2. Cada mozo le escribe `/start` al bot. El webhook guarda su `chat_id` en `waiters`. Comando `/mesa 3` (o `/mesas 1,2,3`) para asignarse mesas.
3. Registrar el webhook una vez que Vercel esté deployado:
   ```
   curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/setWebhook" \
     -d "url=https://<app>.vercel.app/api/telegram/webhook" \
     -d "secret_token=$TELEGRAM_WEBHOOK_SECRET"
   ```
4. Enviar alertas con `sendMessage` y un `inline_keyboard`:
   ```json
   { "inline_keyboard": [[
     { "text": "🏃 Voy",   "callback_data": "ack:a_123" },
     { "text": "✅ Listo", "callback_data": "done:a_123" }
   ]]}
   ```
5. En el webhook: `callback_query` → actualizar la alerta (`en_camino` / `resuelta`) con `alerts/service.ts` y responder con `answerCallbackQuery`. El servicio llama a `Notifier.update` por cada mensaje guardado en `alert_messages`, que hace `editMessageText` para que se vea quién la tomó.
6. Para la demo: crear un **grupo "Mozos"** con el bot adentro y los 3 del equipo; así el jurado ve los mensajes en un celular y todos ven quién toma cada mesa.

Sin librerías pesadas: `fetch` directo a `https://api.telegram.org/bot<token>/<método>` alcanza. (O `grammy` si prefieren.)

## Fuera de alcance: WhatsApp

No se implementa en el MVP. Queda para el roadmap del pitch (WhatsApp Business Cloud API de Meta). La interfaz `Notifier` de [03-contratos.md](03-contratos.md) permite sumar otro canal más adelante sin tocar el motor de reglas.

## Variables de entorno

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_GROUP_CHAT_ID=    # grupo "Mozos" (pruebas y demo)
```
