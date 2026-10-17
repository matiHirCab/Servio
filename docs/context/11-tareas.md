# 11 — Tareas para arrancar

Orden: **fotos primero → video → backend/Telegram**. P2 y P3 no esperan a la visión: trabajan con el simulador.
Cada tarea: rama `pX/<tema>`, PR chico. Al pasarle una tarea a Claude Code: "leé AGENTS.md, 03-contratos.md y el doc de tu módulo, y hacé la tarea PX-N de 11-tareas.md".

---

## T0 — Todos juntos (primeros 30 min)

- [ ] **T0-1 · Actualizar contratos** (`03-contratos.md`) antes de congelar:
  - Por ítem agregar `visible: boolean` (false si una mano/cabeza lo tapa) y `cx`, `cy` (centro aproximado, 0–1 dentro del recorte de la mesa).
  - Nuevo `AlertType`: `"esperando_plato"`.
  - `track_id` lo asigna **el código** (emparejando por cercanía de `cx, cy`), no Claude.
- [ ] **T0-2 · Accesos**: API key de Anthropic (respaldo) + pedir acceso a Claude Haiku en Bedrock. Bot de Telegram en @BotFather + grupo "Mozos". Proyecto en Vercel + Postgres (Neon).
- [ ] **T0-3 · Cortar del plan**: CI con Tenki y S3 (los videos van a una carpeta compartida). WhatsApp queda como extra.

---

## P1 — Visión (`vision/`)

**Etapa A · Fotos (validar que Claude clasifica bien)**

- [ ] **P1-1 · Dataset de prueba.** Bajar de Roboflow *AI Smart Restaurant Surveillance* una versión **a color y sin augmentación** (la v1 está en grises). Abrir 10 imágenes y anotar el ángulo de cámara. Guardar en `vision/data/roboflow/` (no subir al repo, agregar a `.gitignore`).
  *Hecho cuando:* hay imágenes y sus etiquetas (`food`, `finished_food`) en local.
- [ ] **P1-2 · Set de evaluación.** Script `tools/build_eval_set.py`: recorta 60–100 cajas `food` / `finished_food` y escribe `eval/labels.csv` (archivo, estado esperado: `lleno` o `vacio`).
- [ ] **P1-3 · Cliente de Claude.** `servio_vision/claude_client.py` con tool use (`report_table`), `temperature: 0`, validación con Pydantic y 1 reintento. Soporta Bedrock y API de Anthropic por variable de entorno.
  *Hecho cuando:* le pasás una imagen y devuelve JSON válido con `items[]` (`kind`, `state`, `visible`, `cx`, `cy`, `confidence`) y `people_present`.
- [ ] **P1-4 · `tools/eval.py`.** Corre el prompt sobre el set y da % de acierto, matriz de confusión y costo/latencia promedio.
  *Hecho cuando:* hay un número. Meta: ≥ 80 % en lleno vs. terminado. No pulir más que eso con estas fotos.

**Etapa B · Video**

- [ ] **P1-5 · Fuentes de captura.** `capture.py` con 3 fuentes que devuelven `(imagen, timestamp)`: carpeta de fotos, archivo de video (un frame cada N s) y webcam/celular. `--source fotos/ | mesa.mp4 | 0`.
- [ ] **P1-6 · Zonas y recorte.** `tools/draw_zones.py` + `config/zones.json` + recorte por mesa a ~512 px.
- [ ] **P1-7 · Ids estables.** `tracking.py`: empareja ítems nuevos con los anteriores por distancia de `cx, cy` (mismo `kind`); asigna `T1-p1`, `T1-p2`…; si falta en 2 lecturas, retirado.
- [ ] **P1-8 · Suavizado.** Descartar ítems con `visible: false`; cambio de estado solo con 2 lecturas seguidas; estados solo avanzan.
- [ ] **P1-9 · Prueba con video.** Correr sobre un video (propio o cenital de YouTube) con `--dry-run` e imprimir la línea de tiempo de estados por plato. Ajustar prompt acá.
- [ ] **P1-10 · Envío + heartbeat.** `client.py` hace `POST /api/observations` después de cada lectura **y además reenvía el último estado cada 5 s** aunque no haya llamado a Claude (para que las reglas corran a tiempo).

---

## P2 — Backend, reglas y bot (`web/app/api`, `web/lib`)

- [ ] **P2-1 · Scaffold.** Next.js + TypeScript en `web/`, Drizzle, schema de `03-contratos.md` + tabla `visits (id, table_id, started_at, ended_at)`. Deploy en Vercel.
- [ ] **P2-2 · `POST /api/observations`.** Valida `INGEST_TOKEN` y body con Zod, actualiza `items` (`first_seen`, `state_since`, `last_seen`, `removed_at`) y abre/cierra `visits`.
- [ ] **P2-3 · Simulador (prioridad).** `scripts/simulate.ts`: mesa que pasa lleno → comiendo → por_terminar → vacío → retiran → (pausa) → plato nuevo. Pasárselo a P3 apenas ande.
- [ ] **P2-4 · Motor de reglas** como funciones puras + tests Vitest. Reglas de `05-reglas-alertas.md` **más** `esperando_plato`: mesa con `people_present: true`, sin platos, hace ≥ T4 (prod 8 min / demo 30 s) desde que retiraron los anteriores.
- [ ] **P2-5 · Reglas también en lectura.** Correr la evaluación de reglas también en `GET /api/tables` (además de al recibir observaciones), con el cooldown evitando duplicados.
- [ ] **P2-6 · Telegram.** `TelegramNotifier` + webhook: `/start`, `/mesa N`, botones Voy/Listo, editar mensaje, expirar alerta cuando la condición desaparece.
- [ ] **P2-7 · `GET /api/metrics`.** `avg_meal_minutes`, `avg_response_seconds`, `avg_wait_between_courses` (de `esperando_plato`), `alerts_by_type`, `plates_returned_full`.

---

## P3 — Dashboard, datos y demo (`web/app`, `web/components`, `docs/`)

- [ ] **P3-1 · Maqueta con mock.** Una pantalla: mapa de mesas con colores por estado, platos como círculos, feed de alertas, KPIs arriba. Datos de un JSON mock.
- [ ] **P3-2 · Conectar a la API** (`/api/tables`, `/api/alerts`) con SWR cada 2 s, usando el simulador de P2.
- [ ] **P3-3 · Vista de mesa.** Recorte de la mesa con el estado de cada plato como texto encima (no hay cajas, no usamos YOLO).
- [ ] **P3-4 · Mesa física.** Conseguir platos blancos, vasos y comida; armar el soporte del celular arriba (~1,5 m, vista cenital) con luz pareja.
- [ ] **P3-5 · Grabar el video de prueba** (5–10 min): plato lleno → comen de a poco → mano tapando → retiran → pausa → plato nuevo. Pasárselo a P1 para P1-9.
- [ ] **P3-6 · Videos cenitales de YouTube** (2–3, solo uso interno) para que P1 pruebe con otra comida y otra luz.
- [ ] **P3-7 · Pitch.** Guion de `10-demo-pitch.md` con el diferencial: **tiempo entre platos** (`esperando_plato`) además de mesa por terminar.

---

## Checkpoints

| Hito | Qué tiene que andar |
|---|---|
| **H+3** | P1: eval sobre fotos con un %. P2: simulador + observations. P3: maqueta con mock. |
| **H+6** | P1: línea de tiempo de estados sobre un video. P2: reglas + Telegram. P3: dashboard en vivo con el simulador. |
| **H+12** | Demo punta a punta: plato real → Telegram → "Voy" → dashboard. **Grabarla.** |
