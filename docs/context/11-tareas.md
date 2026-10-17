# 11 — Tareas (versión paralela)

Alineado con la rama `cambios-contexto` (reconciliación de ids en el backend, `people_present` por mesa, `schema_version`, cadencias y `tableStatus()` único).

## Reparto

| Persona | Rol | Horas aprox. |
|---|---|---|
| **P1** | **Modelo** (qué tan bien clasifica: dataset, eval, prompt, comparativa YOLO) **+ bot de Telegram** (lo programa mientras corren los evals y el entrenamiento en Colab) | ~10,5 h |
| **P2** | **Backend**: funciones puras, DB, ingest, simulador, APIs, métricas, deploy | ~9 h |
| **P3** | **Conexión de visión** (captura, zonas, loop, suavizado, `.jsonl`, envío) **+ dashboard + mesa física** | ~11 h |

## Cómo está pensado

- **Tres puntos de unión fijos**, definidos en T0. Con ellos nadie espera a nadie hasta la hora 6:
  1. `03-contratos.md` + `fixtures/`: formas de datos y escenarios concretos en JSON.
  2. **`classify(imagen, table_id, items_previos) -> TableReport`**: separa el modelo (P1) de la conexión (P3). P3 arranca con un clasificador falso; P1 enchufa Claude (o YOLO) detrás de la misma función. Se elige con `--classifier fake|claude|yolo`.
  3. **`Notifier` + `alerts/service.ts`**: separa el bot de Telegram (P1) del backend (P2).
- **Un solo formato de intercambio:** el `.jsonl` de observaciones. La visión en `--dry-run` lo escribe, el simulador del backend lo reproduce y los tests de reglas lo leen. Integrar visión → backend es pasar un archivo.
- **El video propio no está en el camino crítico:** se arranca con videos cenitales de YouTube; el de nuestra mesa sirve para el ajuste fino.
- **Funciones puras primero:** reglas, `tableStatus()`, reconciliación y suavizado se escriben y testean sin DB, deploy ni cámara.

Formato de cada tarea: **dueño · estimación · depende de** → qué hacer → **Hecho cuando**.
Al pasarle una tarea a Claude Code: *"leé AGENTS.md, 03-contratos.md, el doc de tu módulo y la tarea PX-N de 11-tareas.md; hacé solo esa tarea"*.

---

## T0 — Arranque (todos, primeros 40 min)

### T0-1 · Congelar contratos
**Todos · 15 min · depende de: nada**
1. Mergear `cambios-contexto` a `main`.
2. Decidir dos propuestas que **no** están en los contratos. Si se aprueban, agregarlas a `03`/`05` en el mismo PR:
   - `visible: boolean` por ítem (false si una mano/cabeza lo tapa). La visión descarta los no visibles antes de suavizar.
   - Alerta `esperando_plato`: mesa con `people_present = true`, sin platos, hace ≥ T4 (prod 8 min / demo ≥ 2 × cadencia) desde que se retiraron los anteriores. Es el diferencial "tiempo entre platos".
3. Arreglar los links rotos a `10-demo-pitch.md` (fila en `AGENTS.md`, línea en `08-plan-24h.md`).

**Hecho cuando:** `main` tiene los contratos finales y nadie los toca más sin avisar.

### T0-2 · Puntos de unión: fixtures, interfaz del clasificador y stubs
**P1 + P2 + P3 en paralelo · 25 min · depende de: T0-1**

**P2 — observaciones de ejemplo** en `fixtures/`:

```
fixtures/
  README.md                          # qué es cada archivo y cómo se usa
  observations/
    mesa_completa.jsonl              # P2
    plato_lleno_mucho.jsonl          # P2
    id_cambiado.jsonl                # P2
  expected/
    mesa_completa.alerts.json        # P2: alertas que tienen que salir y en qué segundo
  api/
    tables.json                      # P3
    alerts.json                      # P3
    metrics.json                     # P3
```

Reglas del `.jsonl`: **una observación completa por línea**, exactamente con la forma de `POST /api/observations` (`schema_version: 1`, `camera_id`, `ts`, `tables[]` con `people_present` e `items[]`). Timestamps cada 10 s desde `2026-10-17T20:00:00Z`.
- `mesa_completa.jsonl` (T1): mesa vacía sin gente → llega gente → 2 platos `lleno` → `comiendo` → `por_terminar` → `vacio` → se retiran → 3 min sin platos con gente → plato nuevo `lleno`. Una bebida pasa a `vacia` en el medio.
- `plato_lleno_mucho.jsonl` (T2): un plato `lleno` 12 min con gente; después `people_present: false`.
- `id_cambiado.jsonl` (T1): un plato `comiendo` en `arriba-izquierda` que en una lectura pasa de `p1` a `p4` en la misma posición.
- `expected/mesa_completa.alerts.json`: `[{ "at_seconds": 130, "type": "mesa_por_terminar", "table_id": "T1" }, …]` con umbrales de **producción**.

**P2 — stubs del backend** (solo tipos y funciones que tiran `not implemented`), para que P1 programe Telegram sin esperar:
- `web/lib/notify/types.ts` → `interface Notifier { send(alert: Alert, waiter: Waiter): Promise<{ messageId: string }>; update(alert: Alert, messageId: string): Promise<void> }`
- `web/lib/alerts/service.ts` → `ackAlert(alertId, waiterId)`, `resolveAlert(alertId, waiterId)`, `registerWaiter(chatId, name)`, `assignTables(waiterId, tableIds)`.

**P3 — respuestas de API de ejemplo:** `fixtures/api/tables.json` (3 mesas en estados distintos), `alerts.json` (una abierta, una en camino, una resuelta), `metrics.json`.

**P1 — interfaz del clasificador** en `vision/servio_vision/`:
- `schemas.py`: modelos Pydantic de la herramienta `report_table` (`04-vision.md`): `Item(id, kind, state, fill_ratio?, position, confidence, visible?)` y `TableReport(items, people_present, notes?)`.
- `classifier.py`: `class Classifier(Protocol): def classify(self, image: bytes, table_id: str, previous_items: list[Item]) -> TableReport | None` y `get_classifier(name: str) -> Classifier` (registro por nombre: `fake`, `claude`, `yolo`).

**Hecho cuando:** todo está en `main` y los tres lo pueden importar o leer.

### T0-3 · Accesos (cada uno el suyo, en paralelo)
**15 min · depende de: nada**
- **P1:** API key de Anthropic (respaldo) + acceso a Claude Haiku en Bedrock (y cuota). Probar una llamada con texto. **Bot en @BotFather** (`TELEGRAM_BOT_TOKEN`) y grupo "Mozos" con el bot y el equipo; anotar el `chat_id`.
- **P2:** proyecto en Vercel conectado al repo (`web/`) + Postgres (Neon). `DATABASE_URL` en Vercel y en `.env` local.
- **P3:** app de cámara IP en el celular (IP Webcam / DroidCam) y verificar que la laptop ve la URL.

**Hecho cuando:** cada secreto está en el `.env` de quien lo usa y documentado en `.env.example`.

---

## P1 — Modelo + Telegram

### P1-1 · Dataset de fotos
**P1 · 30 min · depende de: nada**
1. Bajar de Roboflow *AI Smart Restaurant Surveillance* una versión **a color y sin augmentación** (la v1 está en escala de grises y triplicada). Formato COCO o YOLO (sirve para C-1).
2. Guardar en `vision/data/roboflow/` y agregar `vision/data/` a `.gitignore`.
3. Abrir 10 imágenes al azar y anotar en `vision/README.md` el ángulo de cámara.

**Hecho cuando:** imágenes y etiquetas en local, ángulo anotado.

### P1-2 · Set de evaluación y test split
**P1 · 45 min · depende de: P1-1**
1. Separar un **test set** (15 % de las imágenes) que no se use para iterar el prompt ni para entrenar YOLO → `vision/eval/test_split.txt`.
2. `tools/build_eval_set.py`: del resto, recortar 60–100 cajas balanceadas `food` / `finished_food` con margen 15 % → `vision/eval/dev/`; lo mismo para el test set → `vision/eval/test/`.
3. `labels.csv` en cada carpeta: `file,expected_state` (`food` → `lleno`, `finished_food` → `vacio`).

**Hecho cuando:** existen los dos sets con sus CSV.

### P1-3 · Clasificador de Claude
**P1 · 1 h · depende de: T0-2, T0-3**
`servio_vision/classifiers/claude.py` implementa `Classifier`:
- Proveedor por `CLAUDE_PROVIDER=bedrock|anthropic`. Bedrock con `boto3` (Converse API con herramienta); Anthropic con el SDK oficial.
- Prompt de `04-vision.md` en `prompt.py`, con el estado anterior de la mesa.
- Imagen JPEG base64, `temperature: 0`, herramienta forzada.
- Validar con Pydantic; 1 reintento; si vuelve a fallar, devolver `None` y loguear.
- Loguear tokens y latencia por llamada.
- Registrarlo como `claude` en `get_classifier`.

**Hecho cuando:** `python -m servio_vision.classifiers.claude foto.jpg` imprime un `TableReport` válido.

### P1-4 · Evaluación de estado
**P1 · 1 h · depende de: P1-2, P1-3**
1. `tools/eval.py --set eval/dev --classifier claude`: corre sobre cada imagen (concurrencia 4) y guarda en `eval/runs/<fecha>.json`.
2. Reporta acierto lleno vs. vacío, matriz de confusión con los 4 estados, latencia media y costo estimado por llamada.
3. Iterar el prompt sobre `dev` hasta **≥ 80 %**, no más (estas fotos no son nuestra cámara). Al final, una corrida sobre `test`.

**Hecho cuando:** número de acierto en `test` anotado en `vision/README.md`.

### P1-5 · Comparativa YOLO vs. Claude (C-1)
**P1 · máx. 1 h 30 · depende de: P1-2, P1-4**
Spike con tiempo límite. **No traba a nadie**: el plan sigue con Claude mientras corre. YOLO preentrenado (COCO) no tiene "plato" ni sabe si está lleno, así que se compara **YOLO entrenado** con `food` / `finished_food` contra **Claude sin entrenar**.
1. Notebook `vision/tools/yolo_baseline.ipynb` para **Colab con GPU**: entrenar `yolov8n` sobre todo menos el test split, ~30 épocas, imagen 640. Mientras entrena, avanzar con Telegram (P1-7).
2. Correr YOLO sobre `eval/test` y Claude sobre el mismo set (ya hecho en P1-4).
3. Tabla en `vision/README.md`: acierto, latencia por imagen, costo por 1.000 imágenes, tiempo de armado, estados intermedios (sí/no).
4. Decisión en `04-vision.md`:
   - Claude ≥ YOLO o diferencia chica → solo Claude.
   - YOLO claramente mejor y **antes de la hora 12** → implementar `classifiers/yolo.py` (o híbrido: YOLO detecta, Claude da estados intermedios) detrás de `Classifier`. P3 no toca nada.
   - Si ya pasó la hora 12 → queda como roadmap del pitch.

**Hecho cuando:** tabla y decisión anotadas. A la 1 h 30 se corta y se anota lo que haya.

### P1-6 · Estabilidad de ids sobre video
**P1 · 1 h · depende de: P3-6 (pipeline con `.jsonl`)**
1. Bajar 2–3 videos cenitales de comida de YouTube (`yt-dlp`), solo uso interno, a `vision/data/videos/` (ignorado). Compartirlos con P3 apenas estén.
2. Correr el pipeline de P3 con `--classifier claude --dry-run` sobre esos videos.
3. `tools/eval.py --ids out/obs.jsonl`: % de ítems que conservan su id entre lecturas y cuántos cambios de id hubo en la misma `position`.
4. **Mandarle el número a P2 antes de la hora 6.**

**Hecho cuando:** número compartido con P2 y anotado en `vision/README.md`.

### P1-7 · `TelegramNotifier`
**P1 · 1 h 30 · depende de: T0-2, T0-3**
`web/lib/notify/telegram.ts` implementa `Notifier` con `fetch` directo a la Bot API:
- `send`: `sendMessage` al grupo/mozo con texto e `inline_keyboard` (`ack:<id>` "🏃 Voy", `done:<id>` "✅ Listo").
- `update`: `editMessageText` ("🏃 Juan va en camino", "✅ Resuelta", "⌛ Ya no hace falta").
- `scripts/send-test-alerts.ts` que manda las alertas de `fixtures/api/alerts.json` al grupo.

**Hecho cuando:** los mensajes de prueba llegan al grupo con botones.

### P1-8 · Webhook de Telegram
**P1 · 1 h 30 · depende de: P1-7 (usa los stubs de `alerts/service.ts`)**
`app/api/telegram/webhook/route.ts`:
- Valida `X-Telegram-Bot-Api-Secret-Token`.
- `/start` → `registerWaiter`. `/mesa 1,3` → `assignTables`.
- `callback_query` → `ackAlert` / `resolveAlert`, `answerCallbackQuery` y `update` del mensaje.
- Tests Vitest con updates de Telegram de ejemplo y los stubs mockeados (hasta que P2-9 esté).

**Hecho cuando:** tests verdes.

### P1-9 · Registrar el webhook en producción
**P1 · 15 min · depende de: P2-11, P1-8**
`setWebhook` con la URL de Vercel y `secret_token` (comando en `06-notificaciones.md`). Cada mozo hace `/start` y `/mesa`.

### P1-10 · Ajuste fino con el video propio
**P1 · 1–1 h 30 · depende de: P3-13**
Correr el pipeline sobre el video de nuestra mesa, revisar la línea de tiempo de estados y ajustar el prompt. Con P3, ajustar umbral del detector de cambio y cadencia demo. Re-correr `eval.py`.

**Hecho cuando:** sobre el video propio la mesa pasa por los estados en orden sin saltos raros.

---

## P2 — Backend (`web/`)

### P2-1 · Scaffold mínimo
**P2 · 20 min · depende de: T0-3**
`create-next-app` en `web/` (App Router, TypeScript strict, Tailwind, ESLint), Vitest y Zod. Que pase el CI existente (`lint`, `tsc --noEmit`, `vitest`).

**Hecho cuando:** CI verde en un PR vacío.

### P2-2 · `tableStatus()`
**P2 · 45 min · depende de: P2-1, T0-2**
`web/lib/rules/tableStatus.ts`, función pura según `05-reglas-alertas.md` (solo platos, orden 1–5). Tests con casos borde (sin platos, todo vacío, empate 50 %).

### P2-3 · Reconciliación de ids
**P2 · 1 h · depende de: P2-1, T0-2**
`web/lib/items/reconcile.ts`, función pura `(previos, nuevos, ts) → { items, reconciliations }` según `03-contratos.md`: mismo `kind` + misma `position` que desaparece y aparece en la misma lectura = mismo objeto (conserva `track_id`, `first_seen`, `state_since`); ausente 2 lecturas → `removed_at`. Test con `fixtures/observations/id_cambiado.jsonl`.

### P2-4 · Motor de reglas
**P2 · 1 h 30 · depende de: P2-2, P2-3**
`web/lib/rules/engine.ts`: `(estadoMesa, ahora, config, alertasAbiertas) → { nuevas, expiradas }`.
- Reglas de `05-reglas-alertas.md` (+ `esperando_plato` si se aprobó).
- `config.ts` con umbrales prod/demo según `DEMO_MODE` y validación umbral ≥ 2 × cadencia.
- Cooldown por `(mesa, tipo)`, agrupado, expiración cuando la condición desaparece.
- Test que reproduce `mesa_completa.jsonl` con tiempo simulado y compara contra `expected/mesa_completa.alerts.json`.

**Hecho cuando:** el test del escenario completo pasa.

### P2-5 · Base de datos
**P2 · 45 min · depende de: P2-1, T0-3**
Drizzle con el schema de `03-contratos.md` (`tables`, `table_waiters`, `items`, `observations`, `alerts`, `waiters`). Migración en Neon. `scripts/seed.ts` (mesas T1–T4) y `scripts/reset.ts`.

### P2-6 · `POST /api/observations`
**P2 · 1 h · depende de: P2-3, P2-4, P2-5**
Valida `INGEST_TOKEN`, body con Zod, `schema_version` (400 si no es 1). Guarda la observación cruda, actualiza `people_present`, reconcilia ítems, corre reglas, guarda alertas y llama al `Notifier` (`ConsoleNotifier` hasta que P1-7 esté). Responde `202`.

**Hecho cuando:** `curl` con una línea del fixture devuelve 202 y crea filas.

### P2-7 · Simulador que reproduce `.jsonl`
**P2 · 45 min · depende de: P2-6**
`scripts/simulate.ts --file <archivo.jsonl> --url <api> --speed 10`: reescribe cada `ts` relativo a "ahora" respetando intervalos (÷ `speed`) y hace POST. Funciona con los fixtures **y** con el `out/obs.jsonl` de la visión. **Avisarle a P3.**

### P2-8 · `GET /api/tables` y `GET /api/alerts` (+ reglas en lectura)
**P2 · 1 h · depende de: P2-6**
Forma exacta de `fixtures/api/*.json`. `GET /api/tables` también corre la evaluación de reglas (con cooldown) para que las alertas por tiempo no esperen a la próxima observación.

### P2-9 · Servicio de alertas
**P2 · 45 min · depende de: P2-5, T0-2**
Implementar `ackAlert`, `resolveAlert`, `registerWaiter`, `assignTables` + `POST /api/alerts/:id/ack` y `/resolve` para el dashboard. Guardar `acked_by`, `acked_at`, `resolved_at`. Cambiar `ConsoleNotifier` por `TelegramNotifier` cuando P1-7 esté.

### P2-10 · `GET /api/metrics`
**P2 · 1 h · depende de: P2-6**
`avg_meal_minutes`, `avg_response_seconds`, `alerts_by_type`, `plates_returned_full`, `id_reconciliations` y `avg_wait_between_courses` si existe `esperando_plato`. Filtros `from`/`to`. Forma de `fixtures/api/metrics.json`.

### P2-11 · Deploy
**P2 · 30 min · depende de: P2-8**
Deploy en Vercel con variables de entorno. `INGEST_TOKEN` compartido con P3 (envío de la visión). URL pública compartida con P1 (webhook) y P3 (dashboard).

**Hecho cuando:** el simulador funciona contra la URL de Vercel.

---

## P3 — Conexión de visión + Dashboard

Orden sugerido: P3-1 → P3-2 → P3-3 → P3-5 → P3-8 (hora 0–3) · P3-4 → P3-6 → P3-9 → P3-10 (hora 3–6) · resto después.

### P3-1 · Clasificador falso
**P3 · 30 min · depende de: T0-2**
`servio_vision/classifiers/fake.py` implementa `Classifier` sin llamar a ningún modelo:
- Modo `script`: avanza los platos lleno → comiendo → por_terminar → vacío según el tiempo transcurrido (configurable), y los retira al final.
- Modo `replay`: devuelve, en orden, los ítems de una mesa leídos de `fixtures/observations/mesa_completa.jsonl`.
- Registrado como `fake` en `get_classifier`.

**Hecho cuando:** el pipeline puede correr de punta a punta con `--classifier fake`.

### P3-2 · Captura desde 3 fuentes
**P3 · 45 min · depende de: nada**
`servio_vision/capture.py`, iterador común que devuelve `(frame, ts)`:
- **Carpeta** (`--source fotos/`): ts artificial cada `--interval` s.
- **Video** (`--source mesa.mp4`): salta con `CAP_PROP_POS_MSEC` cada `--interval` s. **El ts es el tiempo del video**, no el reloj. `--speed` opcional.
- **Cámara** (`--source 0` o URL de IP Webcam): reloj real.

### P3-3 · Zonas y recorte por mesa
**P3 · 45 min · depende de: P3-2**
`tools/draw_zones.py` (clics para el rectángulo de cada mesa → `config/zones.json`) + `servio_vision/zones.py` (recorte a ~512 px, JPEG 80). Probar con un video de YouTube que pase P1, o cualquier video cenital mientras tanto.

### P3-4 · Loop principal con cadencias
**P3 · 1 h 30 · depende de: P3-1, P3-3**
`main.py --source … --classifier fake|claude|yolo [--demo]`:
1. Frame cada 1 s → recorte por mesa.
2. Detector de cambio (`change.py`): diferencia media de píxeles contra el último recorte enviado.
3. Llama a `classifier.classify()` por mesa si cambió y pasó la cadencia mínima (prod 15–30 s / demo 5–10 s), o si pasó el refresco forzado (60 s / 20 s). Llamadas asíncronas: el loop no espera.
4. Pasa los ítems anteriores de la mesa.

**Hecho cuando:** con `--classifier fake` imprime una línea por llamada con mesa, ts y cantidad de ítems.

### P3-5 · Suavizado
**P3 · 1 h · depende de: T0-2 (función pura)**
`servio_vision/smoothing.py` según `04-vision.md`, con tests `pytest`:
- Vecino: 2 lecturas seguidas. Retroceso o salto: 3 lecturas con `confidence ≥ 0.8`.
- Ausente 1 lectura: se mantiene. Ausente 2: retirado.
- No visibles no cuentan como lectura (si se aprobó `visible`).

### P3-6 · Salida `.jsonl` (dry-run)
**P3 · 30 min · depende de: P3-4, P3-5**
`--dry-run --out out/obs.jsonl`: una observación por línea con el formato **exacto** de los fixtures (`schema_version: 1`, `people_present` por mesa, `track_id = {table_id}-{id}`). Test que compara claves contra `fixtures/observations/mesa_completa.jsonl`.

**Hecho cuando:** P2 reproduce `out/obs.jsonl` con su simulador sin tocar nada, y P1 puede correrlo con `--classifier claude`.

### P3-7 · Envío real al backend
**P3 · 30 min · depende de: P3-6, P2-11**
Sin `--dry-run`: `POST /api/observations` con `Authorization: Bearer $INGEST_TOKEN`, timeout 5 s; si falla, reintenta en la próxima lectura sin bloquear el loop.

**Hecho cuando:** las observaciones aparecen en la DB de Vercel.

### P3-8 · Dashboard desde fixtures
**P3 · 1 h 30 · depende de: T0-2**
Una pantalla para proyectar, leyendo `fixtures/api/*.json`. Mantenerlo simple:
- **KPIs arriba:** tiempo medio de comida, tiempo de respuesta, alertas abiertas, platos devueltos llenos.
- **Mapa de mesas:** tarjeta por mesa con color por `status` (`libre` gris, `servida` azul, `comiendo` verde, `por_terminar` amarillo, `terminada` rojo), minutos en la mesa, ícono de `people_present`, platos como círculos con color por estado.
- **Feed de alertas** a la derecha: tipo, mesa, hace cuánto, quién la tomó.

Componentes: `KpiBar`, `TableCard`, `ItemDot`, `AlertFeed`.

### P3-9 · Botones Voy / Listo y vista de mesa
**P3 · 1 h · depende de: P3-8**
- En el feed, botones que llaman a `POST /api/alerts/:id/ack` y `/resolve` (mockeados hasta P2-9).
- Al tocar una mesa: detalle con cada plato, su estado, desde cuándo y la línea de tiempo de sus alertas.

### P3-10 · Conectar a la API real
**P3 · 45 min · depende de: P2-8**
SWR a `/api/tables`, `/api/alerts`, `/api/metrics` cada 2 s, con `NEXT_PUBLIC_USE_FIXTURES=true` para volver a los fixtures (plan B).

**Hecho cuando:** el dashboard se mueve solo mientras corre el simulador.

### P3-11 · Mesa física
**P3 · 1 h · cualquier hueco**
Platos blancos, vasos, comida; celular arriba a ~1,5 m con vista cenital, luz pareja sin reflejos; URL de la cámara probada con `--source <url>`.

### P3-12 · Zonas sobre la cámara real
**P3 · 15 min · depende de: P3-3, P3-11**
Correr `draw_zones.py` sobre un frame de la cámara de la mesa y guardar `config/zones.json` definitivo.

### P3-13 · Grabar el video de prueba
**P3 · 30 min · depende de: P3-11**
5–10 min con la cámara fija: plato lleno → comen de a poco → mano tapando → retiran → pausa con gente → plato nuevo. Otro corto con 2–3 platos y una bebida. Pasárselos a P1 (P1-10).

---

## Integración y checkpoints

| Hito | Qué tiene que andar | Integración |
|---|---|---|
| **H+3** | P1: eval de Claude sobre fotos con un % + mensajes de prueba en Telegram. P2: `tableStatus`, reconciliación y reglas verdes contra fixtures. P3: pipeline con `--classifier fake` + dashboard con fixtures. | Ninguna. |
| **H+6** | P1: estabilidad de ids + tabla YOLO vs. Claude y decisión + webhook con tests. P2: observations, simulador y GETs deployados. P3: `out/obs.jsonl` desde video + dashboard con botones. | Pipeline de P3 con el clasificador de P1 (`--classifier claude`) → `out/obs.jsonl` → simulador de P2 → dashboard de P3 y Telegram de P1. |
| **H+12** | Punta a punta en vivo: plato real → visión → backend → Telegram → "Voy" → dashboard. **Grabarlo.** | P3-7, P3-10, P1-9. |
