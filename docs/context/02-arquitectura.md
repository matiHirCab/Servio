# 02 — Arquitectura

## Vista general

```
 [Cámara cenital]
       │ frames (RTSP / webcam / video grabado)
       ▼
 ┌──────────────────────────┐        POST /api/observations   ┌───────────────────────────┐
 │ vision/  (Python, local) │ ─────────────────────────────▶  │ web/  (Next.js en Vercel) │
 │ - captura                │        (cada 2–5 s, JSON)       │ - API routes              │
 │ - detección (YOLO)       │                                 │ - motor de reglas         │
 │ - estado del plato       │                                 │ - dashboard               │
 │ - asignación a mesa      │                                 │ - webhook Telegram        │
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
| Visión | `vision/` | Python 3.11, OpenCV, Ultralytics YOLO, (opcional) Claude visión vía AWS Bedrock | Laptop con la cámara (edge). GPU en AWS solo para entrenar. |
| API + reglas + bot | `web/app/api/` | Next.js (App Router, TypeScript) | Vercel |
| Dashboard | `web/app/` | Next.js + Tailwind + shadcn/ui, polling o SSE | Vercel |
| Base de datos | — | Postgres (Neon desde el marketplace de Vercel) + Drizzle o Prisma | Nube |
| Notificaciones | `web/lib/notify/` | Telegram Bot API (MVP), Twilio WhatsApp (extra) | Vercel |

## Por qué así

- **Un solo deploy web (Vercel)** para API, bot y dashboard: menos piezas que se rompen.
- **La visión corre local** porque la cámara está en el local y no queremos subir video a la nube. Solo sube JSON chiquito (estado de cada plato), no imágenes. Bueno para privacidad y para la demo (anda con el wifi del evento).
- **Las reglas viven en el backend**, no en la visión: así se pueden ajustar umbrales sin tocar el modelo, y el dashboard ve lo mismo que el bot.
- **Contrato JSON fijo** entre visión y backend ([03-contratos.md](03-contratos.md)) para que las 3 personas trabajen en paralelo desde la hora 1 con datos falsos.

## Flujo de datos

1. `vision/` lee un frame cada ~1 s.
2. Detecta platos/vasos, los asigna a una mesa por zona, estima el estado y lo suaviza en el tiempo.
3. Cada 2–5 s manda una **observación** por mesa: lista de ítems con id de seguimiento, tipo, estado y confianza.
4. El backend guarda la observación, actualiza el estado de cada ítem (`first_seen`, `state_since`) y corre las reglas.
5. Si una regla dispara (y no está en cooldown) crea una **alerta** y la manda por Telegram.
6. El mozo toca "Voy" / "Listo" → webhook → se actualiza la alerta → sirve para medir tiempo de respuesta.
7. El dashboard consulta `/api/tables` y `/api/alerts` cada 2 s (o SSE) y muestra el mapa.

## Uso de los recursos que tenemos

| Recurso | Para qué |
|---|---|
| AWS (créditos) | Bedrock para Claude visión (plan A de clasificación), EC2 con GPU (`g5.xlarge` o similar) si hay que fine-tunear YOLO, S3 para el dataset. |
| Vercel | Deploy de `web/` (API + dashboard + webhook), Postgres desde el marketplace. |
| GitHub | Repo, PRs, issues como tablero de tareas. |
| Tenki (USD 250) | Runners de CI rápidos para GitHub Actions (lint + tests). Si su plan incluye cómputo con GPU, alternativa a AWS para entrenar; verificarlo antes de contar con eso. |
| Claude (3 Pro + 1 Max) | Claude Code para programar cada módulo. Ver reparto en [08-plan-24h.md](08-plan-24h.md). |
| Kiro | Specs y tareas del dashboard/API (modo spec-driven), en paralelo a Claude Code. |
