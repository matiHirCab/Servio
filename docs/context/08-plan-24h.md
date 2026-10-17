# 08 — Plan de 24 horas, paso a paso

Las tareas concretas de cada hora, con su "Hecho cuando", están en [11-tareas.md](11-tareas.md). Este archivo da el panorama: roles, fases y reglas del equipo.

## Roles

| Persona | Rol | Carpetas (detalle en [09](09-convenciones.md)) | Herramienta principal |
|---|---|---|---|
| **P1** | **Modelo de visión** (dataset, eval, prompt, estabilidad de ids, comparativa YOLO vs. Claude) + `TelegramNotifier` + demo y pitch | `vision/servio_vision/classifiers/`, `vision/servio_vision/prompt.py`, `vision/tools/` (salvo `draw_zones.py`), `web/lib/notify/telegram.ts` | **Claude Max** + Claude Code (muchas iteraciones de prompt con imágenes). AWS Bedrock. Colab con GPU para YOLO. |
| **P2** | **Backend**: reglas, reconciliación, DB, API, simulador, webhook de Telegram, deploy | `web/app/api/`, `web/lib/` (salvo `notify/telegram.ts`), `web/scripts/` | Claude Pro + Claude Code. Vercel + Postgres. |
| **P3** | **Conexión de visión** (captura, zonas, loop, suavizado, `.jsonl`, envío) + dashboard + mesa física y videos | `vision/` (resto), `web/app/page.tsx`, `web/app/(dashboard)/`, `web/components/` | Claude Pro + **Kiro** (spec y tareas del dashboard). |

La 3.ª suscripción Pro queda de **reserva**: cuando alguien llega al límite de uso, cambia de cuenta y sigue.

**Tip de uso de Claude:** arrancar cada sesión de Claude Code con "leé AGENTS.md, 03-contratos.md, el doc de tu módulo y la tarea PX-N de 11-tareas.md". Pedir tareas chicas y cerradas (un archivo, una ruta).

## Hora 0–1 · Arranque (los tres juntos) — T0 de [11](11-tareas.md)

1. Clonar el repo, leer `AGENTS.md` y `01-producto.md`, acordar el alcance. No discutir más de 20 min.
2. **Congelar contratos** (T0-1): decidir si entran `visible` por ítem y la alerta `esperando_plato`, y anotarlo en [03-contratos.md](03-contratos.md).
3. **Puntos de unión** (T0-2): `fixtures/` (P2 observaciones, P3 respuestas de API), interfaz `Classifier` (P1) y stubs de `Notifier` y `alerts/service.ts` (P2). Desde acá cada uno trabaja con mocks.
4. **Accesos** (T0-3): P1 Bedrock + API key de Anthropic + bot de Telegram y grupo "Mozos"; P2 Vercel + Postgres; P3 cámara IP en el celular.
5. Pasar las tareas de [11](11-tareas.md) al tablero (Trello o GitHub Projects).

## Hora 1–6 · Cada módulo funciona solo

**P1 — Modelo**
1. Dataset de Roboflow a color y test split (P1-1, P1-2).
2. Clasificador de Claude detrás de `Classifier` (P1-3).
3. `eval.py` sobre fotos: acierto ≥ 80 % en `dev`, número final en `test` (P1-4).
4. Estabilidad de ids con su propio script sobre videos cenitales de YouTube (P1-5). **Pasarle el número a P2 antes de la hora 6.**

**P2 — Backend**
1. Scaffold de `web/` con CI verde (P2-1).
2. Funciones puras con tests contra fixtures: `tableStatus()`, reconciliación de ids, motor de reglas (P2-2 a P2-4).
3. Schema en Neon y `POST /api/observations` en local (P2-5, P2-6).

**P3 — Conexión de visión**
1. Clasificador falso, captura desde 3 fuentes y zonas (P3-1 a P3-3).
2. Suavizado con tests y loop con cadencias (P3-5, P3-4).
3. Salida `.jsonl` en `--dry-run` (P3-6).

**Checkpoint hora 6 (15 min):** cada uno muestra lo suyo andando solo (ver tabla de checkpoints en [11](11-tareas.md)).

## Hora 6–12 · Integración punta a punta

1. **P1:** `TelegramNotifier` (P1-6) y comparativa YOLO vs. Claude con Colab entrenando en paralelo (P1-7). La decisión se anota en [04-vision.md](04-vision.md) antes de la hora 12.
2. **P2:** simulador que reproduce `.jsonl`, `GET /api/tables` y `/api/alerts`, deploy, servicio de alertas, webhook de Telegram y registro en producción, métricas (P2-7 a P2-13).
3. **P3:** dashboard desde fixtures y después contra la API real; envío real al backend; mesa física, zonas definitivas y videos de prueba (P3-7 a P3-13).
4. **Objetivo hora 12: demo completa funcionando una vez.** Plato en la mesa → se va comiendo → llega el Telegram → se toca "Voy" → el dashboard lo refleja. Grabar un video de esto apenas salga (respaldo).

## Hora 12–16 · Precisión y robustez (y dormir por turnos)

- **P1:** ajuste fino del prompt sobre el video de nuestra mesa (P1-8). Si YOLO ganó y se implementó, validarlo en el pipeline.
- **P2:** expiración y agrupado de alertas probados con el simulador, métricas afinadas. WhatsApp con Twilio sandbox solo si todo lo demás está.
- **P3:** KPIs y gráficos, pulir UI, ajustar con P1 el umbral del detector de cambio y la cadencia demo.
- Turnos de descanso de 2–3 h: que nunca duerman a la vez P2 y quien tenga la visión corriendo (P1 o P3).

## Hora 16–20 · Pulido

- Ajustar umbrales "modo demo" (tiempos cortos).
- Probar la demo completa 3 veces seguidas con la mesa real. Anotar lo que falla y arreglar solo eso.
- README con cómo correr cada parte y capturas.
- **P1** arma el guion de demo y los planes B (P1-9).
- **Feature freeze a la hora 20.** Después de esto no se agregan features.

## Hora 20–24 · Demo y pitch

- Ensayar la demo y el pitch con el guion de P1-9 al menos 2 veces con tiempo.
- Tener listos los planes B: video grabado como fuente de la visión, simulador, video de la demo, dashboard con `NEXT_PUBLIC_USE_FIXTURES=true`.
- Dejar `main` estable y deployado. Tag `v0.1-demo`.

## Reglas del equipo

- Cada uno en su rama `p1/...`, `p2/...`, `p3/...`; PRs chicos a `main`; otro revisa rápido (5 min). `main` siempre tiene que andar.
- Si algo te bloquea más de 30 min, avisás y se busca un atajo (mock, simplificar, cortar).
- Sync de 10 min en las horas 6, 12, 16 y 20.
