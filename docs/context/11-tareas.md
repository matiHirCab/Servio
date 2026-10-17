# 11 — Tareas para arrancar

Alineado con la rama `cambios-contexto` (reconciliación de ids en el backend, `people_present` por mesa, `schema_version`, cadencias y `tableStatus()` único).

Orden: **fotos primero → video → backend/Telegram**. P2 y P3 no esperan a la visión: trabajan con el simulador.
Cada tarea: rama `pX/<tema>`, PR chico. Al pasarle una tarea a Claude Code: "leé AGENTS.md, 03-contratos.md y el doc de tu módulo, y hacé la tarea PX-N de 11-tareas.md".

---

## T0 — Todos juntos (primeros 30 min)

- [ ] **T0-1 · Congelar contratos.** Revisar `03-contratos.md` y `05-reglas-alertas.md` (ya actualizados en `cambios-contexto`) y mergear esa rama a `main`.
- [ ] **T0-2 · Decidir dos propuestas** que todavía **no** están en los contratos. Si se aprueban, agregarlas a `03`/`05` antes de congelar:
  - `visible: boolean` por ítem, para descartar platos tapados por una mano antes de suavizar.
  - Alerta `esperando_plato`: mesa con `people_present = true`, sin platos, hace ≥ T4 (prod 8 min / demo ≥ 2 × cadencia) desde que retiraron los anteriores. Es el diferencial "tiempo entre platos".
- [ ] **T0-3 · Accesos.** API key de Anthropic (respaldo de Bedrock) + pedir acceso a Claude Haiku en Bedrock. Bot de Telegram en @BotFather + grupo "Mozos". Proyecto en Vercel + Postgres (Neon).

---

## P1 — Visión (`vision/`)

**Etapa A · Fotos (validar que Claude clasifica bien)**

- [ ] **P1-1 · Dataset de prueba.** Bajar de Roboflow *AI Smart Restaurant Surveillance* una versión **a color y sin augmentación** (la v1 está en grises). Abrir 10 imágenes y anotar el ángulo de cámara. Guardar en `vision/data/roboflow/` (agregar a `.gitignore`, no subir al repo).
- [ ] **P1-2 · Set de evaluación.** `tools/build_eval_set.py`: recorta 60–100 cajas `food` / `finished_food` y escribe `eval/labels.csv` (archivo, estado esperado: `lleno` o `vacio`).
- [ ] **P1-3 · Cliente de Claude.** `servio_vision/claude_client.py` con la herramienta `report_table` de `04-vision.md`, `temperature: 0`, validación con Pydantic y 1 reintento. Bedrock o API de Anthropic según variable de entorno.
  *Hecho cuando:* le pasás una imagen y devuelve JSON válido (`items[]` + `people_present`).
- [ ] **P1-4 · `tools/eval.py` (parte 1: estado).** % de acierto, matriz de confusión, costo y latencia promedio.
  *Meta:* ≥ 80 % en lleno vs. terminado. No pulir más que eso con estas fotos.

**Etapa B · Video**

- [ ] **P1-5 · Fuentes de captura.** `capture.py` con 3 fuentes que devuelven `(imagen, timestamp)`: carpeta de fotos, archivo de video y webcam/celular. `--source fotos/ | mesa.mp4 | 0`.
- [ ] **P1-6 · Zonas y recorte.** `tools/draw_zones.py` + `config/zones.json` + recorte por mesa a ~512 px.
- [ ] **P1-7 · Loop con cadencias** de la tabla de `04-vision.md`: detector de cambio, llamada por mesa (prod 15–30 s / demo 5–10 s), refresco forzado (60 s / 20 s), pasando el estado anterior a Claude para que mantenga los ids.
- [ ] **P1-8 · Suavizado** según `04-vision.md`: cambio normal con 2 lecturas seguidas; retroceso o salto de más de un estado solo con 3 lecturas y `confidence ≥ 0.8`; ausente 2 lecturas → retirado.
- [ ] **P1-9 · `eval.py` (parte 2: estabilidad de ids)** sobre frames consecutivos de un video. **Pasarle el resultado a P2 antes de la hora 6.**
- [ ] **P1-10 · Envío.** `client.py` hace `POST /api/observations` con `schema_version: 1` y `people_present` por mesa. Modo `--dry-run` que imprime el JSON.

---

## P2 — Backend, reglas y bot (`web/app/api`, `web/lib`)

- [ ] **P2-1 · Scaffold.** Next.js + TypeScript en `web/`, Drizzle, schema de `03-contratos.md` (incluye `table_waiters`). Deploy en Vercel. Que pase el CI que ya existe (`lint`, `tsc`, `vitest`).
- [ ] **P2-2 · `POST /api/observations`.** Valida `INGEST_TOKEN`, body con Zod y `schema_version` (400 si no es 1). Guarda `people_present` en `tables` y actualiza `items`.
- [ ] **P2-3 · Simulador (prioridad).** `scripts/simulate.ts`: mesa que pasa lleno → comiendo → por_terminar → vacío → retiran → pausa → plato nuevo. Incluir casos de id que cambia en la misma `position` para probar la reconciliación. Pasárselo a P3 apenas ande.
- [ ] **P2-4 · Reconciliación de ids** (función pura + tests) según `03-contratos.md`, con contador de reconciliaciones como métrica de calidad.
- [ ] **P2-5 · `tableStatus()` y motor de reglas** como funciones puras + tests Vitest, según `05-reglas-alertas.md` (incluye `people_present` y la validación de umbrales ≥ 2 × cadencia en `config.ts`).
- [ ] **P2-6 · Reglas también en lectura.** Correr la evaluación también en `GET /api/tables` (no solo al recibir observaciones), con el cooldown evitando duplicados. Así las alertas no esperan al refresco forzado de la visión.
- [ ] **P2-7 · Telegram.** `TelegramNotifier` + webhook: `/start`, `/mesa N` (escribe en `table_waiters`), botones Voy/Listo, editar mensaje, expirar alerta cuando la condición desaparece.
- [ ] **P2-8 · `GET /api/metrics`.** `avg_meal_minutes`, `avg_response_seconds`, `alerts_by_type`, `plates_returned_full`, `id_reconciliations`.

---

## P3 — Dashboard y datos de prueba (`web/app`, `web/components`)

- [ ] **P3-1 · Maqueta con mock.** Una pantalla: mapa de mesas con color según `status`, platos como círculos, feed de alertas, KPIs arriba, indicador de `people_present`. Datos de un JSON mock con la forma de `GET /api/tables`.
- [ ] **P3-2 · Conectar a la API** (`/api/tables`, `/api/alerts`) con SWR cada 2 s, usando el simulador de P2.
- [ ] **P3-3 · Vista de mesa.** Recorte de la mesa con el estado de cada plato como texto encima (no hay cajas: no usamos YOLO).
- [ ] **P3-4 · Mesa física.** Platos blancos, vasos y comida; soporte del celular arriba (~1,5 m, vista cenital) con luz pareja.
- [ ] **P3-5 · Grabar el video de prueba** (5–10 min): plato lleno → comen de a poco → mano tapando → retiran → pausa → plato nuevo. Pasárselo a P1 para la etapa B.
- [ ] **P3-6 · Videos cenitales de YouTube** (2–3, solo uso interno) para que P1 pruebe con otra comida y otra luz.

---

## Checkpoints

| Hito | Qué tiene que andar |
|---|---|
| **H+3** | P1: eval sobre fotos con un %. P2: simulador + observations. P3: maqueta con mock. |
| **H+6** | P1: estados y estabilidad de ids sobre un video (resultado a P2). P2: reconciliación + reglas + Telegram. P3: dashboard en vivo con el simulador. |
| **H+12** | Punta a punta: plato real → Telegram → "Voy" → dashboard. **Grabarlo.** |
