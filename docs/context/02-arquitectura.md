# 02 — Arquitectura

## Vista general

```
 [Cámara cenital]
       │ frames (RTSP / webcam / video grabado)
       ▼
 ┌──────────────────────────┐        POST /api/observations   ┌───────────────────────────┐
 │ vision/  (Python, local) │ ─────────────────────────────▶  │ web/  (Next.js en Vercel) │
 │ - captura                │        (cada 15–30 s, JSON)       │ - API routes              │
 │ - recorte por mesa       │                                 │ - motor de reglas         │
 │ - Claude visión (estado) │                                 │ - dashboard               │
 │ - suavizado              │                                 │ - webhook Telegram        │
 └──────────────────────────┘                                 └──────────┬────────────────┘
                                                                         │
                                         ┌───────────────────────────────┼──────────────────┐
                                         ▼                               ▼                  ▼
                                  [Postgres (Neon /             [Telegram Bot API]   [Twilio WhatsApp]
                                   Vercel Postgres)]              → celular mozo      (extra)
```

## Componentes

| Módulo | Carpeta | Tecnología | Dónde corre |
|---|---|---|---|
| Visión | `vision/` | Python 3.11, OpenCV (captura y recorte). Clasificación detrás de la interfaz `Classifier`: Claude multimodal vía AWS Bedrock por defecto; YOLO solo si gana la comparativa (ver [04-vision.md](04-vision.md)). | Laptop con la cámara (edge), sin GPU. Si se usa YOLO, se entrena en Colab con GPU e infiere `yolov8n` en la laptop con CPU. |
| API + reglas + bot | `web/app/api/` | Next.js (App Router, TypeScript) | Vercel |
| Dashboard | `web/app/` | Next.js + Tailwind + shadcn/ui, polling o SSE | Vercel |
| Base de datos | — | Postgres (Neon desde el marketplace de Vercel) + Drizzle o Prisma | Nube |
| Notificaciones | `web/lib/notify/` | Telegram Bot API (MVP), Twilio WhatsApp (extra) | Vercel |

## Por qué así

- **Un solo deploy web (Vercel)** para API, bot y dashboard: menos piezas que se rompen.
- **La captura corre local** porque la cámara está en el local. Al backend solo sube JSON chiquito (estado de cada plato). A Claude solo se mandan recortes de cada mesa cada 15–30 s, no video.
- **Claude multimodal por defecto, YOLO a prueba**: cámara fija y cenital, mesas fijas, estados que cambian en minutos, así que no hace falta detección en tiempo real. Claude funciona sin entrenar; un YOLO entrenado se compara contra Claude con el mismo test set y solo entra si gana antes de la hora 12. Los dos van detrás de la misma interfaz `Classifier`, así que el resto del pipeline no cambia. Detalle en [04-vision.md](04-vision.md).
- **Las reglas viven en el backend**, no en la visión: así se pueden ajustar umbrales sin tocar el modelo, y el dashboard ve lo mismo que el bot.
- **Contrato JSON fijo** entre visión y backend ([03-contratos.md](03-contratos.md)) para que las 3 personas trabajen en paralelo desde la hora 1 con datos falsos.

## Flujo de datos

1. `vision/` lee un frame cada ~1 s.
2. Recorta cada mesa por su zona; si el recorte cambió (y cada 15–30 s), se lo manda a Claude, que devuelve los platos/bebidas con su estado en JSON. Se suaviza.
3. Después de cada lectura manda una **observación** por mesa: lista de ítems con id de seguimiento, tipo, estado y confianza.
4. El backend guarda la observación, **reconcilia ids** (un ítem que cambia de id en la misma posición sigue siendo el mismo; ver [03-contratos.md](03-contratos.md)), actualiza el estado de cada ítem (`first_seen`, `state_since`) y corre las reglas.
5. Si una regla dispara (y no está en cooldown) crea una **alerta** y la manda por Telegram.
6. El mozo toca "Voy" / "Listo" → webhook → se actualiza la alerta → sirve para medir tiempo de respuesta.
7. El dashboard consulta `/api/tables` y `/api/alerts` cada 2 s (o SSE) y muestra el mapa. El polling es más rápido que las lecturas a propósito: así un cambio de alerta (Voy/Listo) se ve enseguida.

