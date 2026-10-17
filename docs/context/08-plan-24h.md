# 08 — Plan de 24 horas

Las tareas concretas, con su "Hecho cuando", están en [11-tareas.md](11-tareas.md). Este archivo da el panorama.

## Roles

| Persona | Rol | Carpetas (detalle en [09](09-convenciones.md)) |
|---|---|---|
| **Tomás** | **Modelo**: dataset, YOLO en Colab, clasificadores `yolo` / `hybrid` / `claude`, evaluación y prompt. Guion de demo y pitch. | `servio_vision/classifiers/` (salvo `fake.py`), `tracking.py`, `prompt.py`, `tools/` de modelo |
| **Matías** | **Reglas + Telegram**: estado de mesa, motor de reglas, recordatorios, envío a Telegram, log y replay. | `servio_vision/alerts/`, `tools/replay.py`, `fixtures/` |
| **Jessica** | **Pipeline + video**: captura, zonas, loop cada 30 s, suavizado, clasificador falso, video de la demo y vista de demo. | `main.py`, `servio_vision/{capture,zones,smoothing}.py`, `classifiers/fake.py`, `tools/draw_zones.py`, `config/` |

Los tres programan en Python, así que cualquiera puede cubrir a otro si se traba.

**Tip:** arrancar cada sesión de Claude Code con "leé AGENTS.md, 03-contratos.md, el doc de tu parte y la tarea X de 11-tareas.md; hacé solo esa tarea".

## Hora 0–1 · Arranque (los tres)

1. Leer `AGENTS.md`, `01-producto.md` y `03-contratos.md`. No discutir más de 20 min.
2. T0: Tomás ajusta `schemas.py` (campo `bbox`) y el registro de clasificadores; Matías escribe los fixtures y el modelo `Alert`; Jessica revisa los modelos con Tomás y **arranca a conseguir el video de la demo**.
3. Accesos: Tomás (Claude Code logueado para `claude -p`), Matías (bot de Telegram y grupo "Mozos").

## Hora 1–6 · Cada parte funciona sola

- **Tomás:** dataset y test split, YOLO entrenando en Colab, clasificador de Claude por plato, `yolo` y `hybrid`.
- **Matías:** estado de mesa, motor de reglas con tests contra fixtures, envío a Telegram.
- **Jessica:** clasificador falso, captura, zonas, suavizado y loop cada 30 s sobre un video cualquiera.

**Checkpoint hora 6 (15 min):** cada uno muestra lo suyo andando solo.

## Hora 6–12 · Punta a punta

1. Jessica integra el loop con `alerts` (Matías) y con `hybrid` (Tomás).
2. Tomás prueba el `hybrid` sobre el video de la demo (tamaño mínimo de plato, recorte, cadencia).
3. Matías suma log y replay, y ayuda con la integración.
4. **Objetivo hora 12:** video de la demo → YOLO + Claude → reglas → avisos en Telegram, completo al menos una vez. **Grabarlo.**

## Hora 12–16 · Precisión (y dormir por turnos)

- Tomás: ajuste fino del prompt sobre el video de la demo.
- Jessica: zonas definitivas y vista de demo en pantalla dividida (video con las mesas marcadas + Telegram espejado). Si sobra tiempo, la tabla de lo que pasó (JES-10).
- Matías: umbrales de demo, recordatorios y cierre probados con replay; apoyo con el video.
- Turnos de descanso de 2–3 h: que nunca duerman los tres a la vez.

## Hora 16–20 · Pulido

- Probar la demo completa 3 veces seguidas. Arreglar solo lo que falla.
- Tomás arma el guion y el plan B.
- **Feature freeze a la hora 20.**

## Hora 20–24 · Demo y pitch

- Ensayar la demo y el pitch al menos 2 veces.
- Planes B: `--classifier yolo` (sin internet), `tools/replay.py` con un log grabado, video de la demo funcionando.
- Dejar `main` estable. Tag `v0.1-demo`.

## Reglas del equipo

- Ramas `tomas/...`, `matias/...`, `jessica/...`; PRs chicos a `main`; otro revisa rápido (5 min). `main` siempre tiene que andar.
- Si algo te bloquea más de 30 min, avisás y se busca un atajo.
- Sync de 10 min en las horas 6, 12, 16 y 20.
