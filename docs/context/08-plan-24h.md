# 08 — Plan de 24 horas, paso a paso

## Roles

| Persona | Rol | Módulo | Herramienta principal |
|---|---|---|---|
| **P1** | Visión computacional | `vision/` | **Claude Max** + Claude Code (es lo más pesado: muchas iteraciones de prompt con imágenes). AWS Bedrock. |
| **P2** | Backend, reglas y bot | `web/app/api`, `web/lib` | Claude Pro + Claude Code. Vercel + Postgres + Telegram. |
| **P3** | Dashboard, datos de prueba, demo y pitch | `web/app/page.tsx`, `web/components`, `docs/` | Claude Pro + **Kiro** (specs y tareas del dashboard). Graba los videos de prueba y arma la mesa. |

La 3.ª suscripción Pro queda de **reserva**: cuando alguien llega al límite de uso, cambia de cuenta y sigue. Rotarla según quién la necesite.

**Tip de uso de Claude:** arrancar cada sesión de Claude Code con "leé AGENTS.md y docs/context/0X-….md de tu módulo". Pedir tareas chicas y cerradas (un archivo, una ruta), no "hacé todo el backend".

## Hora 0–1 · Arranque (los tres juntos)

1. Clonar el repo, leer `AGENTS.md` y `01-producto.md`, acordar el alcance. No discutir más de 20 min.
2. Revisar y congelar [03-contratos.md](03-contratos.md). Antes de congelar, confirmar los puntos que más pegan después: reconciliación de ids, `people_present` como campo de mesa, `table_waiters`, `schema_version`, cadencia 15–30 s y la regla única de `tableStatus()` en [05-reglas-alertas.md](05-reglas-alertas.md). Desde acá cada uno trabaja con mocks.
3. Crear en GitHub los issues por persona (pueden salir de este archivo) y un Project board.
4. **P2**: crear el proyecto en Vercel conectado al repo (`web/`), agregar Postgres, crear el bot de Telegram en @BotFather, crear el grupo "Mozos".
5. **P1**: activar Bedrock en AWS (pedir acceso al modelo Claude Haiku en la región), pedir aumento de cuota de requests si hace falta, crear un bucket S3 para videos de prueba.
6. **P3**: conseguir platos, vasos, comida (o comida falsa), armar la mesa y el soporte del celular en el techo/trípode. Grabar los primeros videos: un plato de lleno a vacío (puede ser acelerado), 2–3 platos, con manos que tapan.
7. Configurar CI básico (lint + tests) en GitHub Actions con runners de **Tenki**.

## Hora 1–6 · Cada módulo funciona solo

**P1 — Visión**
1. `vision/` con `capture.py` leyendo video grabado y webcam/celular.
2. `draw_zones.py` + `zones.json` + recorte por mesa.
3. Recorte → Claude en Bedrock con tool use → JSON validado con Pydantic.
4. Etiquetar ~40 frames a mano y armar `tools/eval.py` para medir el % de acierto del prompt **y la estabilidad de ids** entre lecturas consecutivas. Compartir el resultado con P2 antes de la hora 6: define cuánto trabajo hace la reconciliación del backend.
5. `client.py` con `--dry-run` imprimiendo el JSON del contrato.

**P2 — Backend**
1. Scaffold Next.js en `web/`, schema de DB, migración.
2. `POST /api/observations` guardando ítems.
3. **Simulador** (`scripts/simulate.ts`) → se lo pasa a P3 apenas esté.
4. Motor de reglas como funciones puras (incluida `tableStatus()` y la reconciliación de ids) + tests con Vitest.
5. `TelegramNotifier` mandando un mensaje de prueba al grupo.

**P3 — Dashboard**
1. En Kiro: escribir la spec del dashboard (pantalla única, mapa + feed + KPIs) y generar tareas.
2. Maqueta con datos hardcodeados (mapa de mesas con colores, feed de alertas).
3. Conectarlo a `GET /api/tables` y `/api/alerts` cuando P2 los tenga (o a un JSON mock mientras tanto).

**Checkpoint hora 6 (15 min):** cada uno muestra lo suyo andando solo.

## Hora 6–12 · Integración punta a punta

1. **P1 + P2**: visión manda observaciones reales al backend deployado en Vercel.
2. **P2**: webhook de Telegram con botones Voy/Listo, cooldowns, edición de mensajes.
3. **P3**: dashboard en vivo contra Vercel; vista de detalle de mesa.
4. **Objetivo hora 12: demo completa funcionando una vez.** Plato en la mesa → se va comiendo → llega el Telegram → se toca "Voy" → el dashboard lo refleja. Grabar un video de esto apenas salga (respaldo).

## Hora 12–16 · Precisión y robustez (y dormir por turnos)

- **P1**: iterar el prompt con `eval.py`, suavizado, detector de cambio para ahorrar llamadas, agrupar mesas en una sola llamada, medir costo real por hora. Bebidas si da.
- **P2**: endpoint de métricas, expiración de alertas, agrupado de mensajes. WhatsApp con Twilio sandbox solo si todo lo demás está.
- **P3**: KPIs y gráficos, pulir UI, empezar el guion del pitch.
- Turnos de descanso de 2–3 h: que nunca duerman P1 y P2 a la vez.

## Hora 16–20 · Pulido

- Ajustar umbrales "modo demo" (tiempos cortos).
- Probar la demo completa 3 veces seguidas con la mesa real. Anotar lo que falla y arreglar solo eso.
- README con cómo correr cada parte y capturas.
- **Feature freeze a la hora 20.** Después de esto no se agregan features.

## Hora 20–24 · Demo y pitch

- Ensayar el pitch (ver [10-demo-pitch.md](10-demo-pitch.md)) al menos 2 veces con tiempo.
- Tener listos los planes B: video grabado como fuente de la visión, simulador, video de la demo.
- Dejar `main` estable y deployado. Tag `v0.1-demo`.

## Reglas del equipo

- Cada uno en su rama `p1/...`, `p2/...`, `p3/...`; PRs chicos a `main`; el otro revisa rápido (5 min). `main` siempre tiene que andar.
- Si algo te bloquea más de 30 min, avisás y se busca un atajo (mock, simplificar, cortar).
- Sync de 10 min en las horas 6, 12, 16 y 20.
