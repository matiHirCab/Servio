# 06 — Notificaciones

## MVP: Telegram (gratis, inmediato, con botones)

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

## Extra: WhatsApp

- Más fricción: **Twilio WhatsApp Sandbox** (cada número de prueba tiene que mandar "join <código>" al sandbox). Sirve para la demo, no para producción.
- Producción real: WhatsApp Business Cloud API de Meta (requiere verificación del negocio y plantillas aprobadas; no entra en 24 h). Mencionarlo en el roadmap.
- Implementar detrás de una interfaz común:
  la interfaz `Notifier` de [03-contratos.md](03-contratos.md) (`send` + `update`).
  `TelegramNotifier` primero, `TwilioWhatsAppNotifier` si sobra tiempo.

## Variables de entorno

```
TELEGRAM_BOT_TOKEN=
TELEGRAM_WEBHOOK_SECRET=
TELEGRAM_GROUP_CHAT_ID=    # grupo "Mozos" (pruebas y demo)
TWILIO_ACCOUNT_SID=        # opcional
TWILIO_AUTH_TOKEN=         # opcional
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
```
