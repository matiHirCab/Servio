# 11 — Tareas

Lista de tareas concretas del MVP. Usa los contratos actuales (reconciliación de ids en el backend, `people_present` por mesa, `schema_version`, cadencias y `tableStatus()` único). Roles y horas: [08-plan-24h.md](08-plan-24h.md). Dueños por carpeta: [09-convenciones.md](09-convenciones.md).

## Reparto

| Persona | Rol | Carpetas | Horas aprox. |
|---|---|---|---|
| **P1** | **Modelo** (dataset, eval, prompt, comparativa YOLO) + `TelegramNotifier` + demo y pitch | `vision/servio_vision/classifiers/`, `vision/servio_vision/prompt.py`, `vision/tools/` (salvo `draw_zones.py`), `web/lib/notify/telegram.ts` | ~9,5 h |
| **P2** | **Backend**: funciones puras, DB, ingest, simulador, APIs, métricas, webhook de Telegram, deploy | `web/app/api/`, `web/lib/` (salvo `notify/telegram.ts`), `web/scripts/` | ~11 h |
| **P3** | **Conexión de visión** (captura, zonas, loop, suavizado, `.jsonl`, envío) + dashboard + mesa física | `vision/` (resto), `web/app/page.tsx`, `web/app/(dashboard)/`, `web/components/` | ~10,5 h |

## Cómo está pensado

- **Tres puntos de unión fijos**, definidos en T0 y documentados en [03-contratos.md](03-contratos.md). Con ellos cada módulo corre solo, con mocks, hasta la integración (regla 4 de `AGENTS.md`):
  1. **`fixtures/`**: escenarios concretos en JSON con la forma exacta de los contratos.
  2. **Interfaz `Classifier`** (`classify(imagen, table_id, items_previos) -> TableReport`): separa el modelo (P1) de la conexión (P3). P3 arranca con un clasificador falso; P1 enchufa Claude (o YOLO) detrás de la misma interfaz. Se elige con `--classifier fake|claude|yolo`.
  3. **Interfaz `Notifier`** (`send` + `update`) y **`web/lib/alerts/service.ts`**: separan el `TelegramNotifier` (P1) del backend (P2).
- **Un solo formato de intercambio:** el `.jsonl` de observaciones. La visión en `--dry-run` lo escribe, el simulador del backend lo reproduce y los tests de reglas lo leen. La primera integración visión → backend es pasar un archivo.
- **El video propio no está en el camino crítico:** se arranca con videos cenitales de YouTube; el de nuestra mesa sirve para el ajuste fino.
- **Funciones puras primero:** reglas, `tableStatus()`, reconciliación y suavizado se escriben y testean sin DB, deploy ni cámara.
- **Las horas siguen el plan de [08](08-plan-24h.md):** checkpoints en las horas 6, 12, 16 y 20.

Formato de cada tarea: **dueño · estimación · horas del plan · depende de** → qué hacer → **Hecho cuando**.
Al pasarle una tarea a Claude Code: *"leé AGENTS.md, 03-contratos.md, el doc de tu módulo y la tarea PX-N de 11-tareas.md; hacé solo esa tarea"*.

---

## T0 — Arranque (hora 0–1, todos)

### T0-1 · Congelar contratos
**Todos · 15 min · depende de: nada**
Decidir dos propuestas que **no** están en los contratos. Si se aprueban, agregarlas a `03-contratos.md` / `05-reglas-alertas.md` y a los fixtures en el mismo PR:
- `visible: boolean` por ítem (false si una mano/cabeza lo tapa). La visión descarta los no visibles antes de suavizar.
- Alerta `esperando_plato`: mesa con `people_present = true`, sin platos, hace ≥ T4 (prod 8 min / demo ≥ 2 × cadencia) desde que se retiraron los anteriores. Es el diferencial "tiempo entre platos".

**Hecho cuando:** la decisión está anotada en `03` (sí o no para cada una) y nadie toca los contratos sin avisar.

**Decisión:** ✅ se aprueban las dos (anotadas en `03`, `04` y `05`).

### T0-2 · Puntos de unión: fixtures, `Classifier` y stubs
**P1 + P2 + P3 en paralelo · 25 min · depende de: T0-1**

**P2 — observaciones de ejemplo** en `fixtures/` (raíz del repo; ver [09](09-convenciones.md)):

```
fixtures/
  README.md                          # P2: qué es cada archivo y cómo se usa
  observations/
    mesa_completa.jsonl              # P2
    plato_lleno_mucho.jsonl          # P2
    id_cambiado.jsonl                # P2
  expected/
    mesa_completa.alerts.json        # P2
  api/
    tables.json                      # P3
    alerts.json                      # P3
    metrics.json                     # P3
```

Reglas del `.jsonl`: **una observación completa por línea**, exactamente con la forma de `POST /api/observations` (`schema_version: 1`, `camera_id`, `ts`, `tables[]` con `people_present` e `items[]`). Timestamps cada 10 s desde `2026-10-17T20:00:00Z`.
- `mesa_completa.jsonl` (T1): mesa vacía sin gente → llega gente → 2 platos `lleno` → `comiendo` → `por_terminar` → `vacio` → se retiran → 3 min sin platos con gente → plato nuevo `lleno`.
- `plato_lleno_mucho.jsonl` (T2): un plato `lleno` 12 min con gente; después `people_present: false`.
- `id_cambiado.jsonl` (T1): un plato `comiendo` en `arriba-izquierda` que en una lectura pasa de `p1` a `p4` en la misma posición.
- `expected/mesa_completa.alerts.json`: `[{ "at_seconds": 130, "type": "mesa_por_terminar", "table_id": "T1" }, …]` con umbrales de **producción**.

**P2 — stubs del backend** (tipos de `03` y funciones que tiran `not implemented`):
- `web/lib/notify/types.ts`: interfaz `Notifier` de `03-contratos.md` (`send` y `update`) + `ConsoleNotifier`.
- `web/lib/alerts/service.ts`: `ackAlert(alertId, waiterId)`, `resolveAlert(alertId, waiterId)`, `registerWaiter(chatId, name)`, `assignTables(waiterId, tableIds)`.

**P3 — respuestas de API de ejemplo:** `fixtures/api/tables.json` (3 mesas en estados distintos), `alerts.json` (una abierta, una en camino, una resuelta), `metrics.json` (con los campos de `03`).

**P1 — interfaz del clasificador** (la de `03-contratos.md`, «Visión interna»):
- `vision/servio_vision/schemas.py`: modelos Pydantic de `report_table` (`04-vision.md`): `Item(id, kind, state, fill_ratio?, position, confidence)` y `TableReport(items, people_present, notes?)`. Incluye `visible: bool` (aprobado en T0-1).
- `vision/servio_vision/classifier.py`: `Classifier` (Protocol) y `get_classifier(name)` con registro por nombre (`fake`, `claude`, `yolo`).

**Hecho cuando:** todo está en `main`, `python -c "from servio_vision.classifier import get_classifier"` funciona y `npx tsc --noEmit` pasa con los stubs.

### T0-3 · Accesos (cada uno el suyo, en paralelo)
**15 min · depende de: nada**
- **P1:** `ANTHROPIC_API_KEY` (respaldo) + acceso a Claude Haiku en Bedrock (`BEDROCK_MODEL_ID`, y cuota). Bot en @BotFather (`TELEGRAM_BOT_TOKEN`) y grupo "Mozos" con el bot y el equipo (`TELEGRAM_GROUP_CHAT_ID`).
- **P2:** proyecto en Vercel conectado al repo (`web/`) + Postgres Neon (`DATABASE_URL`). Generar `INGEST_TOKEN` y `TELEGRAM_WEBHOOK_SECRET`.
- **P3:** app de cámara IP en el celular (IP Webcam / DroidCam).

**Hecho cuando:** P1 hizo una llamada de texto a Claude, P2 se conecta a Neon desde local, P3 ve la URL de la cámara desde la laptop, y cada variable está en el `.env` de quien la usa (todas ya figuran en `.env.example`).

---

## P1 — Modelo + `TelegramNotifier` + demo

Orden: P1-1 → P1-2 → P1-3 → P1-4 → P1-5 (hora 1–6) · P1-6 → P1-7 (hora 6–12) · P1-8 (hora 12–16) · P1-9 (hora 16–24).

### P1-1 · Dataset de fotos
**P1 · 30 min · hora 1–6 · depende de: nada**
1. Bajar de Roboflow *AI Smart Restaurant Surveillance* (CC BY 4.0) una versión **a color y sin augmentación** (la v1 está en escala de grises y triplicada), formato YOLO.
2. Guardar en `vision/data/roboflow/` (`vision/data/` está en `.gitignore`).
3. Abrir 10 imágenes y anotar en `vision/README.md` el ángulo de cámara y la cita del dataset.

**Hecho cuando:** imágenes y etiquetas en local, ángulo y cita en `vision/README.md`.

### P1-2 · Set de evaluación y test split
**P1 · 45 min · hora 1–6 · depende de: P1-1**
1. Separar un **test set** (15 % de las imágenes) que no se use para iterar el prompt ni para entrenar YOLO → `vision/data/eval/test_split.txt`.
2. `vision/tools/build_eval_set.py`: recorta 60–100 cajas balanceadas `food` / `finished_food` (margen 15 %) del resto → `vision/data/eval/dev/`, y del test set → `vision/data/eval/test/`.
3. `labels.csv` en cada carpeta: `file,expected_state` (`food` → `lleno`, `finished_food` → `vacio`).

**Hecho cuando:** existen `dev/labels.csv` y `test/labels.csv` y todas las imágenes listadas abren.

### P1-3 · Clasificador de Claude
**P1 · 1 h · hora 1–6 · depende de: T0-2, T0-3**
`vision/servio_vision/classifiers/claude.py` implementa `Classifier`:
- Proveedor por `CLAUDE_PROVIDER=bedrock|anthropic`. Bedrock con `boto3` (Converse API con herramienta); Anthropic con el SDK oficial (`ANTHROPIC_API_KEY`).
- Prompt de `04-vision.md` en `vision/servio_vision/prompt.py`, con el estado anterior de la mesa.
- Imagen JPEG base64, `temperature: 0`, herramienta `report_table` forzada.
- Validar con Pydantic; 1 reintento; si vuelve a fallar, devolver `None` y loguear.
- Loguear tokens y latencia por llamada. Registrarlo como `claude`.

**Hecho cuando:** `python -m servio_vision.classifiers.claude foto.jpg` imprime un `TableReport` válido con los dos proveedores.

### P1-4 · Evaluación de estado
**P1 · 1 h · hora 1–6 · depende de: P1-2, P1-3**
1. `vision/tools/eval.py --set vision/data/eval/dev --classifier claude`: corre sobre cada imagen (concurrencia 4) y guarda el detalle en `vision/data/eval/runs/<fecha>.json`.
2. Reporta acierto lleno vs. vacío, matriz de confusión con los 4 estados, latencia media y costo estimado por llamada.
3. Iterar el prompt sobre `dev` hasta **≥ 80 %**, no más (estas fotos no son nuestra cámara). Al final, una corrida sobre `test`.

**Hecho cuando:** el acierto en `test`, la latencia y el costo están en `vision/README.md`.

### P1-5 · Estabilidad de ids sobre video
**P1 · 1 h · hora 1–6 · depende de: P1-3** (no depende del pipeline de P3)
1. Bajar 2–3 videos cenitales de comida de YouTube (`yt-dlp`), solo uso interno, a `vision/data/videos/`. Pasárselos a P3.
2. `vision/tools/id_stability.py --video … --interval 10`: saca un frame cada N s, llama a `classify()` en secuencia pasándole los ítems anteriores y guarda las respuestas.
3. Reporta % de ítems que conservan su id entre lecturas y cuántos cambios de id hubo en la misma `position`.

**Hecho cuando:** el número está en `vision/README.md` y P2 lo recibió **antes de la hora 6**.

### P1-6 · `TelegramNotifier`
**P1 · 1 h 30 · hora 6–12 · depende de: T0-2, T0-3**
`web/lib/notify/telegram.ts` implementa `Notifier` (`03-contratos.md`) con `fetch` directo a la Bot API:
- `send`: `sendMessage` con el texto de la alerta e `inline_keyboard` (`ack:<id>` "🏃 Voy", `done:<id>` "✅ Listo"). Devuelve `{ channel: "telegram", chat_id, message_id }` para que P2 lo guarde en `alert_messages`.
- `update`: `editMessageText` según el estado ("🏃 Juan va en camino", "✅ Resuelta", "⌛ Ya no hace falta").
- `web/scripts/send-test-alerts.ts`: manda las alertas de `fixtures/api/alerts.json` a `TELEGRAM_GROUP_CHAT_ID`.

**Hecho cuando:** `npx tsx web/scripts/send-test-alerts.ts` deja en el grupo 3 mensajes con botones, y un segundo modo del script los edita.

### P1-7 · Comparativa YOLO vs. Claude
**P1 · máx. 1 h 30 · hora 6–12 · depende de: P1-2, P1-4**
Spike con tiempo límite. **No traba a nadie**: el plan sigue con Claude. YOLO preentrenado (COCO) no tiene "plato" ni sabe si está lleno, así que se compara **YOLO entrenado** con `food` / `finished_food` contra **Claude sin entrenar**.
1. `vision/tools/yolo_baseline.ipynb` para **Colab con GPU**: entrenar `yolov8n` sobre todo menos el test split, ~30 épocas, imagen 640. Mientras entrena, avanzar con P1-6.
2. Correr YOLO sobre `vision/data/eval/test`.
3. Tabla en `vision/README.md`: acierto, latencia por imagen, costo por 1.000 imágenes, tiempo de armado, estados intermedios (sí/no).
4. Decidir con el criterio de `04-vision.md` («Decisión YOLO vs. Claude») y anotarlo ahí. Si gana YOLO antes de la hora 12: `vision/servio_vision/classifiers/yolo.py` detrás de `Classifier`, pesos en `YOLO_WEIGHTS` (ignorados por git), dependencias en `vision/requirements-yolo.txt`.

**Hecho cuando:** tabla y decisión anotadas **antes de la hora 12**. A la 1 h 30 se corta y se anota lo que haya.

### P1-8 · Ajuste fino del prompt
**P1 · 1 h 15 · hora 12–16 · depende de: P3-6** (y P3-13 si ya está; si no, cualquier video cenital)
Correr el pipeline con `--classifier claude --dry-run` sobre el video de nuestra mesa (o, mientras P3 lo graba, sobre un video de YouTube). Revisar la línea de tiempo de estados y ajustar el prompt. Con P3, ajustar el umbral del detector de cambio y la cadencia demo. Re-correr `eval.py`.

**Hecho cuando:** sobre el video, la mesa pasa por los estados en orden sin saltos raros y el acierto en `test` no bajó.

### P1-9 · Demo y pitch
**P1 · 1 h · hora 16–24 · depende de: checkpoint de la hora 12**
Guion de demo de 3 min (problema → en vivo → métricas → negocio y roadmap, con el diferencial "tiempo entre platos" de `esperando_plato`), lista de planes B (video grabado como fuente, simulador, video de la demo, feed del dashboard) y checklist pre-demo (`DEMO_MODE`, `web/scripts/reset.ts`, celular, cámara).

**Hecho cuando:** el equipo ensayó la demo completa 2 veces con el guion.

---

## P2 — Backend (`web/`)

Orden: P2-1 → P2-2 → P2-3 → P2-4 → P2-5 → P2-6 (hora 1–6) · P2-7 → P2-8 → P2-11 → P2-9 → P2-12 → P2-13 → P2-10 (hora 6–12).

### P2-1 · Scaffold mínimo
**P2 · 20 min · hora 1–6 · depende de: T0-3**
`create-next-app` en `web/` (App Router, TypeScript strict, Tailwind, ESLint), Vitest y Zod.

**Hecho cuando:** el CI existente (`lint`, `tsc --noEmit`, `vitest`) pasa en un PR.

### P2-2 · `tableStatus()`
**P2 · 45 min · hora 1–6 · depende de: P2-1, T0-2**
`web/lib/rules/tableStatus.ts`, función pura según `05-reglas-alertas.md` (solo platos, orden 1–5). Tests en `web/lib/rules/tableStatus.test.ts` con casos borde (sin platos, todo vacío, empate 50 %).

**Hecho cuando:** tests de Vitest verdes.

### P2-3 · Reconciliación de ids
**P2 · 1 h · hora 1–6 · depende de: P2-1, T0-2**
`web/lib/items/reconcile.ts`, función pura `(previos, nuevos, ts) → { items, reconciliations }` según `03-contratos.md`.

**Hecho cuando:** un test que lee `fixtures/observations/id_cambiado.jsonl` verifica que el plato conserva `track_id`, `first_seen` y `state_since`, y otro verifica `removed_at` tras 2 lecturas ausente.

### P2-4 · Motor de reglas
**P2 · 1 h 30 · hora 1–6 · depende de: P2-2, P2-3**
`web/lib/rules/engine.ts` con la firma de `05-reglas-alertas.md`: `(estadoMesa, ahora, config, alertasAbiertas) → { nuevas, expiradas }`.
- Reglas de `05` (incluye `esperando_plato`).
- `web/lib/rules/config.ts` con umbrales prod/demo según `DEMO_MODE` y validación umbral ≥ 2 × cadencia.
- Cooldown por `(mesa, tipo)`, agrupado, expiración.

**Hecho cuando:** un test que reproduce `fixtures/observations/mesa_completa.jsonl` con tiempo simulado produce exactamente `fixtures/expected/mesa_completa.alerts.json`.

### P2-5 · Base de datos
**P2 · 45 min · hora 1–6 · depende de: P2-1, T0-3**
Drizzle en `web/lib/db/schema.ts` con las tablas de `03-contratos.md` (incluye `table_waiters` y `alert_messages`). Migración en Neon. `web/scripts/seed.ts` (mesas T1–T4) y `web/scripts/reset.ts`.

**Hecho cuando:** `npm run db:reset && npm run db:seed` deja T1–T4 en Neon y las tablas vacías.

### P2-6 · `POST /api/observations`
**P2 · 1 h · hora 1–6 · depende de: P2-3, P2-4, P2-5**
`web/app/api/observations/route.ts`: valida `INGEST_TOKEN`, body con Zod, `schema_version` (400 si no es 1). Guarda la observación cruda, actualiza `people_present`, reconcilia ítems y corre el motor. Por cada alerta **nueva**: la guarda y llama a `Notifier.send` por cada mozo de la mesa, guardando el resultado en `alert_messages`. Por cada alerta **expirada**: la pasa a `expirada` y llama a `Notifier.update` por cada fila de `alert_messages`. Usa `ConsoleNotifier` hasta que P1-6 esté. Responde `202`.

**Hecho cuando:** un `curl` con una línea del fixture devuelve 202 y crea filas en `observations` e `items`.

### P2-7 · Simulador que reproduce `.jsonl`
**P2 · 45 min · hora 6–12 · depende de: P2-6**
`web/scripts/simulate.ts --file <archivo.jsonl> --url <api> --speed 10`: reescribe cada `ts` relativo a "ahora" respetando los intervalos (÷ `speed`) y hace POST. Funciona con los fixtures **y** con el `.jsonl` de la visión. Avisarle a P3.

**Hecho cuando:** reproducir `mesa_completa.jsonl` contra local genera en la DB las alertas de `expected/`.

### P2-8 · `GET /api/tables` y `GET /api/alerts`
**P2 · 1 h · hora 6–12 · depende de: P2-6**
`web/app/api/tables/route.ts` y `web/app/api/alerts/route.ts`, con la forma exacta de `03` y de `fixtures/api/*.json`. Solo lectura: las reglas corren únicamente al recibir observaciones (`05`).

**Hecho cuando:** un test compara las claves de cada respuesta contra `fixtures/api/tables.json` y `alerts.json`.

### P2-9 · Servicio de alertas + rutas ack/resolve
**P2 · 45 min · hora 6–12 · depende de: P2-5, T0-2**
Implementar `web/lib/alerts/service.ts` (`ackAlert`, `resolveAlert`, `registerWaiter`, `assignTables`); al cambiar de estado, llamar a `Notifier.update` por cada fila de `alert_messages`. Rutas `POST /api/alerts/:id/ack` y `POST /api/alerts/:id/resolve` (`03`). Cambiar `ConsoleNotifier` por `TelegramNotifier` cuando P1-6 esté.

**Hecho cuando:** tests de Vitest verdes para los cuatro métodos y un `curl` a `/ack` cambia la alerta a `en_camino` con `acked_at`.

### P2-10 · `GET /api/metrics`
**P2 · 1 h · hora 6–12 · depende de: P2-6**
`web/app/api/metrics/route.ts` con los campos de `03`: `avg_meal_minutes`, `avg_response_seconds`, `alerts_by_type`, `plates_returned_full`, `id_reconciliations` y `avg_wait_between_courses`. Filtros `from`/`to`.

**Hecho cuando:** después de reproducir `mesa_completa.jsonl`, la respuesta tiene las claves de `fixtures/api/metrics.json` con valores distintos de cero.

### P2-11 · Deploy
**P2 · 30 min · hora 6–12 · depende de: P2-8**
Deploy en Vercel con las variables de entorno. Compartir `INGEST_TOKEN` con P3 y la URL pública con P1 y P3.

**Hecho cuando:** `web/scripts/simulate.ts` funciona contra la URL de Vercel.

### P2-12 · Webhook de Telegram
**P2 · 1 h 30 · hora 6–12 · depende de: P2-9**
`web/app/api/telegram/webhook/route.ts`:
- Valida `X-Telegram-Bot-Api-Secret-Token` contra `TELEGRAM_WEBHOOK_SECRET`.
- `/start` → `registerWaiter`. `/mesa 1,3` → `assignTables`.
- `callback_query` → `ackAlert` / `resolveAlert` y `answerCallbackQuery` (la edición del mensaje la hace el servicio vía `Notifier.update`).

**Hecho cuando:** tests de Vitest verdes con updates de Telegram de ejemplo (`/start`, `/mesa`, `ack:`, `done:`, secret inválido → 401).

### P2-13 · Registrar el webhook en producción
**P2 · 15 min · hora 6–12 · depende de: P2-11, P2-12**
`setWebhook` con la URL de Vercel y `secret_token` (comando en `06-notificaciones.md`). Cada mozo hace `/start` y `/mesa`.

**Hecho cuando:** tocar "Voy" en un mensaje real del grupo cambia la alerta a `en_camino` en la DB.

---

## P3 — Conexión de visión + Dashboard

Orden: P3-1 → P3-2 → P3-3 → P3-5 → P3-4 → P3-6 (hora 1–6) · P3-8 → P3-9 → P3-10 → P3-7 → P3-11 → P3-12 → P3-13 (hora 6–12).

### P3-1 · Clasificador falso
**P3 · 30 min · hora 1–6 · depende de: T0-2**
`vision/servio_vision/classifiers/fake.py` implementa `Classifier` sin llamar a ningún modelo:
- Modo `script`: avanza los platos lleno → comiendo → por_terminar → vacío según el tiempo transcurrido (configurable) y los retira al final.
- Modo `replay`: devuelve, en orden, los ítems de una mesa leídos de `fixtures/observations/mesa_completa.jsonl`.
- Registrado como `fake`.

**Hecho cuando:** `pytest vision/tests/test_fake_classifier.py` pasa (los dos modos devuelven `TableReport` válidos en el orden esperado).

### P3-2 · Captura desde 3 fuentes
**P3 · 45 min · hora 1–6 · depende de: nada**
`vision/servio_vision/capture.py`, iterador común que devuelve `(frame, ts)`:
- **Carpeta** (`--source fotos/`): ts artificial cada `--interval` s.
- **Video** (`--source mesa.mp4`): salta con `CAP_PROP_POS_MSEC` cada `--interval` s. **El ts es el tiempo del video**, no el reloj. `--speed` opcional.
- **Cámara** (`--source 0` o URL de IP Webcam): reloj real.

**Hecho cuando:** `python -m servio_vision.capture --source <x>` imprime ts y tamaño de frame para las 3 fuentes.

### P3-3 · Zonas y recorte por mesa
**P3 · 45 min · hora 1–6 · depende de: P3-2**
`vision/tools/draw_zones.py` (clics para el rectángulo de cada mesa → `vision/config/zones.json`, formato de `03`) + `vision/servio_vision/zones.py` (recorte a ~512 px, JPEG 80).

**Hecho cuando:** sobre un video cenital se guardan recortes por mesa en `vision/out/crops/` y se ven bien.

### P3-4 · Loop principal con cadencias
**P3 · 1 h 30 · hora 1–6 · depende de: P3-1, P3-3, P3-5**
`vision/main.py --source … --classifier fake|claude|yolo [--demo]` (`--demo` le gana a `DEMO_MODE`):
1. Frame cada 1 s → recorte por mesa.
2. Detector de cambio (`vision/servio_vision/change.py`).
3. Llama a `classify()` por mesa según la tabla de cadencias de `04` (prod 15–30 s / demo 5–10 s; refresco 60 s / 20 s), de forma asíncrona, pasando los ítems anteriores.
4. Aplica el suavizado (P3-5).

**Hecho cuando:** con `--classifier fake` sobre un video imprime una línea por lectura (mesa, ts, ítems suavizados) y respeta las cadencias.

### P3-5 · Suavizado
**P3 · 1 h · hora 1–6 · depende de: T0-2 (función pura)**
`vision/servio_vision/smoothing.py` según `04-vision.md`:
- Vecino: 2 lecturas seguidas. Retroceso o salto: 3 lecturas con `confidence ≥ 0.8`.
- Ausente 1 lectura: se mantiene. Ausente 2: retirado.
- Ítems con `visible: false` no cuentan como lectura.

**Hecho cuando:** `pytest vision/tests/test_smoothing.py` pasa con un caso por regla.

### P3-6 · Salida `.jsonl` (dry-run)
**P3 · 30 min · hora 1–6 · depende de: P3-4**
`vision/servio_vision/client.py` con `--dry-run --out vision/out/obs.jsonl`: una observación por línea con el formato exacto de los fixtures (`schema_version: 1`, `people_present` por mesa, `track_id = {table_id}-{id}`).

**Hecho cuando:** un test compara las claves contra `fixtures/observations/mesa_completa.jsonl`, y P2 reproduce el archivo con `web/scripts/simulate.ts` sin tocar nada.

### P3-7 · Envío real al backend
**P3 · 30 min · hora 6–12 · depende de: P3-6, P2-11**
Sin `--dry-run`: `POST /api/observations` a `SERVIO_API_URL` con `Authorization: Bearer $INGEST_TOKEN`, timeout 5 s; si falla, reintenta en la próxima lectura sin bloquear el loop.

**Hecho cuando:** corriendo sobre un video, las observaciones aparecen en la DB de Vercel.

### P3-8 · Dashboard desde fixtures
**P3 · 1 h 30 · hora 6–12 · depende de: T0-2** (se puede escribir la spec en Kiro antes)
Una pantalla para proyectar en `web/app/page.tsx`, leyendo `fixtures/api/*.json`:
- **KPIs arriba:** tiempo medio de comida, tiempo de respuesta, alertas abiertas, platos devueltos llenos.
- **Mapa de mesas:** tarjeta por mesa con color por `status` (`libre` gris, `servida` azul, `comiendo` verde, `por_terminar` amarillo, `terminada` rojo), minutos en la mesa, ícono de `people_present`, platos como círculos con color por estado.
- **Feed de alertas** a la derecha: tipo, mesa, hace cuánto, quién la tomó.

Componentes en `web/components/`: `KpiBar`, `TableCard`, `ItemDot`, `AlertFeed`.

**Hecho cuando:** el dashboard muestra las 3 mesas de `fixtures/api/tables.json` con sus colores, las 3 alertas de `alerts.json` y los KPIs de `metrics.json`.

### P3-9 · Botones Voy / Listo y vista de mesa
**P3 · 1 h · hora 6–12 · depende de: P3-8**
- En el feed, botones que llaman a `POST /api/alerts/:id/ack` y `/resolve` (mockeados hasta P2-9).
- Al tocar una mesa: detalle con cada plato, su estado, desde cuándo y la línea de tiempo de sus alertas.

**Hecho cuando:** con fixtures, tocar "Voy" cambia la alerta a "en camino" en pantalla y la vista de mesa abre desde cada tarjeta.

### P3-10 · Conectar a la API real
**P3 · 45 min · hora 6–12 · depende de: P2-8**
SWR a `/api/tables`, `/api/alerts`, `/api/metrics` cada 2 s, con `NEXT_PUBLIC_USE_FIXTURES=true` para volver a los fixtures (plan B).

**Hecho cuando:** el dashboard se mueve solo mientras corre el simulador, y con el flag en `true` vuelve a los fixtures.

### P3-11 · Mesa física
**P3 · 1 h · hora 6–12 · cualquier hueco**
Platos blancos con comida (sin vasos ni botellas en la mesa); celular arriba a ~1,5 m con vista cenital, luz pareja sin reflejos.

**Hecho cuando:** `python -m servio_vision.capture --source <url>` muestra la mesa entera, enfocada y sin reflejos.

### P3-12 · Zonas sobre la cámara real
**P3 · 15 min · hora 6–12 · depende de: P3-3, P3-11**
Correr `vision/tools/draw_zones.py` sobre un frame de la cámara de la mesa.

**Hecho cuando:** `vision/config/zones.json` definitivo commiteado y los recortes muestran cada mesa completa.

### P3-13 · Grabar el video de prueba
**P3 · 30 min · hora 6–12 · depende de: P3-11**
5–10 min con la cámara fija: plato lleno → comen de a poco → mano tapando → retiran → pausa con gente → plato nuevo. Otro corto con 2–3 platos. Guardar en `vision/data/videos/` (ignorado) y compartirlos por la carpeta del equipo.

**Hecho cuando:** P1 tiene los dos videos y los puede abrir con `--source`.

---

## Checkpoints (los de [08](08-plan-24h.md))

| Hora | Qué tiene que andar | Integración |
|---|---|---|
| **6** | Cada módulo solo. P1: acierto en `test` + estabilidad de ids (ya en manos de P2). P2: funciones puras verdes contra fixtures + `POST /api/observations` en local. P3: pipeline con `--classifier fake` que escribe `vision/out/obs.jsonl`. | Ninguna obligatoria. |
| **12** | Punta a punta: plato real → visión (`--classifier claude`) → backend en Vercel → Telegram → "Voy" → dashboard. Decisión YOLO vs. Claude anotada. **Grabarlo.** | P3-7, P3-10, P2-13, P1-6. |
| **16** | Ajuste fino (P1-8), métricas en el dashboard, umbrales demo. | — |
| **20** | Feature freeze. Demo ensayada (P1-9). | — |
