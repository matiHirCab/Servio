# 11 — Tareas

Alcance: solo platos de comida, tres avisos por Telegram (mesa por terminar, plato vacío, plato lleno mucho rato), todo en Python, YOLO + Claude. Roles y horas en [08-plan-24h.md](08-plan-24h.md); dueños por carpeta en [09-convenciones.md](09-convenciones.md). Todas las rutas son relativas a `vision/` salvo que digan otra cosa.

| Persona | Rol | Horas aprox. |
|---|---|---|
| **Tomás** | Modelo (YOLO + Claude) + guion y pitch | ~9 h |
| **Matías** | Reglas + Telegram | ~7 h, y apoyo después de la hora 12 |
| **Jessica** | Pipeline + video de la demo | ~8 h |

Formato: **dueño · horas del plan · estimación · depende de** → qué hacer → **Hecho cuando**.
Al pasarle una tarea a Claude Code: *"leé AGENTS.md, 03-contratos.md, el doc de tu parte y la tarea XXX-N de 11-tareas.md; hacé solo esa tarea"*.

---

## T0 — Arranque (hora 0–1)

### T0-1 · Contrato mínimo y fixtures
**Tomás, Matías, Jessica · 30 min · depende de: nada**
- **Tomás:** agregar `bbox` a `Item` en `servio_vision/schemas.py` y sumar `hybrid` al registro de `classifier.py` (contrato en `03`).
- **Matías:** modelo `Alert` en `servio_vision/alerts/models.py` y fixtures en `fixtures/observations/` (mesa que pasa de lleno a vacío y se retira, plato lleno 12 min con gente y después sin gente, id que cambia en la misma posición) con sus avisos esperados en `fixtures/expected/`.
- **Jessica:** revisar los cambios de `schemas.py` con Tomás.

**Hecho cuando:** está en `main` y `pytest` pasa.

### T0-2a · Accesos: Claude
**Tomás · 15 min**
No hay créditos de AWS: Claude va por `claude -p` (Claude Code logueado con la suscripción). Probar `claude -p "hola"` en la laptop de la demo. API key de Anthropic solo si se consigue.

**Hecho cuando:** la llamada responde y las variables están en el `.env` (y documentadas en `.env.example`).

### T0-2b · Accesos: Telegram
**Matías · 15 min**
Bot en @BotFather y grupo "Mozos" con el bot y el equipo. Obtener el `chat_id` del grupo.

**Hecho cuando:** un `curl` a `sendMessage` llega al grupo.

---

## Tomás — Modelo

### TOM-1 · Dataset y test split
**hora 1–6 · 45 min**
Bajar *AI Smart Restaurant Surveillance* de Roboflow a color, sin augmentación y en formato YOLO a `data/roboflow/`. Separar un 15 % como test (`data/eval/test_split.txt`). Con `tools/build_eval_set.py`, recortar cajas `food` / `finished_food` del test en `data/eval/test/` con `labels.csv`. Anotar el ángulo de cámara y la cita del dataset en `README.md`.

**Hecho cuando:** existen el split, los recortes y `labels.csv`, y la cita está en el README.

### TOM-2 · Entrenar YOLO en Colab
**hora 1–6 · 1 h (mientras entrena, avanzar con TOM-3)**
`tools/train_yolo.ipynb` para Colab con GPU: `yolov8n`, clases `food` y `finished_food`, ~30 épocas, imagen 640, sin el test split. Bajar los pesos a `models/best.pt` (ignorado por git) y compartirlos por la carpeta del equipo. Dependencias en `requirements-yolo.txt`.

**Hecho cuando:** `models/best.pt` detecta platos en 5 imágenes del test y el mAP está anotado en el README.

### TOM-3 · Claude por plato
**hora 1–6 · 1 h**
`servio_vision/llm/` (`PlateEstimator`, un archivo por proveedor): una función que recibe el recorte de la mesa y los recortes de cada plato y devuelve, por plato, `fill_ratio`, `confidence` y `visible`, más `people_present`. `claude -p` o API de Anthropic según `CLAUDE_PROVIDER`; salida con el esquema `report_plates`, Pydantic con un reintento, timeout (15 s con `cli`), log de tokens y latencia. Prompt en `prompt.py` (base en `04-vision.md`).

**Hecho cuando:** con 3 recortes de plato devuelve un resultado válido, y un test con la API mockeada pasa.

### TOM-4 · Clasificador `yolo` y emparejado de ids
**hora 1–6 · 1 h · depende de: TOM-2**
`servio_vision/tracking.py`: empareja cajas nuevas con `previous_items` por distancia entre centros y asigna `p1`, `p2`… a los nuevos. `classifiers/yolo.py`: detecta platos en el recorte de la mesa y devuelve `TableReport` con `id`, `bbox`, `position` y estado `lleno` (`food`) o `vacio` (`finished_food`).

**Hecho cuando:** tests de `tracking.py` (plato que se mueve poco conserva el id, plato nuevo recibe id nuevo) y `--classifier yolo` anda en el pipeline de Jessica.

### TOM-5 · Clasificador `hybrid`
**hora 1–6 · 1 h · depende de: TOM-3, TOM-4**
`classifiers/hybrid.py`: YOLO encuentra los platos y empareja ids → recorta cada plato con 10 % de margen → Claude estima `fill_ratio` de todos en una llamada → arma el `TableReport`. Si Claude falla o tarda más de 10 s, usa el estado de YOLO con confianza baja.

**Hecho cuando:** sobre 5 recortes de mesa devuelve platos con estados intermedios, y con Claude apagado sigue devolviendo lo de YOLO.

### TOM-6 · Evaluación y prompt
**hora 6–12 · 1 h · depende de: TOM-1, TOM-5**
`tools/eval.py --set data/eval/test --classifier hybrid`: acierto lleno vs. vacío, latencia y costo por llamada. Ajustar el prompt hasta ≥ 80 %.

**Hecho cuando:** acierto, latencia y costo en el README.

### TOM-7 · Prueba con el video de la demo
**hora 6–12 · 1 h · depende de: TOM-5, JES-1**
Con el video de la demo, ver desde qué tamaño de plato YOLO deja de detectar y Claude deja de distinguir `comiendo` de `por_terminar`. Definir con Jessica el recorte y la cadencia.

**Hecho cuando:** el tamaño mínimo y la configuración recomendada están en el README y Jessica los aplicó.

### TOM-8 · Ajuste fino sobre el video de la demo
**hora 12–16 · 1 h 15 · depende de: JES-8**
Correr todo sobre el video final y revisar en `out/eventos.jsonl` la línea de tiempo de cada mesa. Ajustar el prompt; con Jessica, la cadencia (`--interval`) y el `--speed` de la demo.

**Hecho cuando:** cada mesa del video pasa por los estados en orden, sin saltos raros ni ids que cambien.

### TOM-9 · Guion de demo y pitch
**hora 16–20 · 1 h**
Guion de 3 minutos (problema → demo en vivo → cómo funciona YOLO + Claude → negocio y roadmap), plan B (`--classifier yolo`, replay de un log, video de la demo) y checklist previo.

**Hecho cuando:** el equipo ensayó 2 veces con el guion.

---

## Matías — Reglas + Telegram

### MAT-1 · Paquete `alerts` y CI
**hora 1–6 · 30 min**
`servio_vision/alerts/` con `config.py` (umbrales de producción y demo de `05`). Ajustar `.github/workflows/ci.yml` para que solo corra Python (ruff + pytest, sin `continue-on-error`).

**Hecho cuando:** el CI corre en verde en un PR.

### MAT-2 · Estado de mesa
**hora 1–6 · 1 h**
`alerts/state.py`: por cada `track_id`, `first_seen`, `state`, `state_since`, `last_seen`; si un plato deja de venir y un id nuevo apareció en su `position` en esa lectura o la anterior, el nuevo hereda sus tiempos (ver «Estado por plato» en `05`); uno que no viene ya está retirado. Función pura `table_status(items)` según `05`.

**Hecho cuando:** tests con el fixture del id cambiado (no reinicia tiempos) y con los 5 casos de `table_status`.

### MAT-3 · Motor de reglas
**hora 1–6 · 1 h 30 · depende de: MAT-2**
`alerts/engine.py`: función pura `(estado_mesa, ahora, config, avisos_abiertos) → { abrir, recordar, cerrar }` para los tres avisos, con un aviso abierto por `(mesa, tipo)`, recordatorio cada R (máximo 2), cierre cuando la condición desaparece y agrupado por mesa. `config.py` valida umbrales ≥ 2 × cadencia.

**Hecho cuando:** reproducir cada fixture con tiempo simulado produce exactamente los avisos de `fixtures/expected/`.

### MAT-4 · Envío a Telegram
**hora 1–6 · 1 h**
`alerts/notify.py` con `httpx`: `sendMessage` al grupo (guarda `message_id`) y `editMessageText` con "✅ ya resuelto" al cerrar. Si Telegram falla, loguea y sigue. Modo `--no-telegram` que imprime. Script `tools/send_test_alert.py`.

**Hecho cuando:** el script manda un aviso de ejemplo al grupo y después lo edita.

### MAT-5 · `alerts.process()` y log
**hora 6–12 · 45 min · depende de: MAT-3, MAT-4**
`alerts/__init__.py`: `process(observation)` actualiza el estado, corre el motor y manda, recuerda o edita. Escribe cada observación y cada aviso en `out/eventos.jsonl` (formato en `03`).

**Hecho cuando:** pasar a mano las líneas de un fixture por `process()` deja el log esperado y los mensajes en el grupo.

### MAT-6 · Replay
**hora 6–12 · 45 min · depende de: MAT-5**
`tools/replay.py --file <jsonl> --speed 10 [--no-telegram]`: reproduce un fixture o el log de una corrida real por `process()`, respetando los tiempos divididos por `speed`. Es el plan B de la demo.

**Hecho cuando:** `replay.py` con el fixture de la mesa completa manda los avisos esperados al grupo en menos de 1 minuto.

### MAT-7 · Integración con el loop
**hora 6–12 · 1 h · depende de: MAT-5, JES-6**
Con Jessica, conectar `alerts.process()` al loop de `main.py` y probar con `--classifier fake` sobre un video.

**Hecho cuando:** `python main.py --source <video> --classifier fake --demo` manda los tres tipos de aviso al grupo.

### MAT-8 · Apoyo con video y plan B
**hora 12–20**
Ayudar a Jessica a conseguir o grabar el video, y a Tomás con el plan B y los umbrales de demo.

---

## Jessica — Pipeline + video

### JES-1 · Conseguir el video de la demo
**arranca ya · 1 h (en paralelo)**
Video desde arriba (cenital o en alto), con varias mesas y platos que se van vaciando, 1080p o más, luz pareja. Ideal grabarlo el equipo, con permiso. Los de YouTube solo para probar, no para mostrar. Guardarlo en `data/videos/` (ignorado) y compartirlo por la carpeta del equipo.

**Hecho cuando:** Tomás y Matías tienen una primera versión del video y la pueden abrir con `--source`.

### JES-2 · Clasificador falso
**hora 1–6 · 30 min**
`classifiers/fake.py`: modo `script` (avanza los platos de lleno a vacío por tiempo y los retira) y modo `replay` (devuelve los ítems de un fixture en orden).

**Hecho cuando:** `pytest tests/test_fake_classifier.py` pasa con los dos modos.

### JES-3 · Captura
**hora 1–6 · 45 min**
`servio_vision/capture.py`: iterador `(frame, ts)` para video (salta cada `--interval` s, el ts es el tiempo del video, `--speed` opcional), carpeta de fotos y cámara o URL del celular.

**Hecho cuando:** `python -m servio_vision.capture --source <x>` imprime ts y tamaño para las 3 fuentes.

### JES-4 · Zonas para muchas mesas
**hora 1–6 · 45 min · depende de: JES-3**
`tools/draw_zones.py`: clics para marcar cada mesa → `config/zones.json`. `servio_vision/zones.py`: recorte de cada mesa desde el frame original, JPEG 85.

**Hecho cuando:** sobre un video con varias mesas se guardan los recortes en `out/crops/` y cada uno muestra una mesa completa.

### JES-5 · Suavizado
**hora 1–6 · 1 h**
`servio_vision/smoothing.py` según `04`: no visibles no cuentan, vecino con 2 lecturas, retroceso o salto con 3 lecturas y confianza ≥ 0,8, ausente 2 lecturas = retirado.

**Hecho cuando:** `pytest tests/test_smoothing.py` pasa con un caso por regla.

### JES-6 · Loop cada 30 s
**hora 1–6 · 1 h 30 · depende de: JES-2, JES-4, JES-5**
`main.py --source … --classifier fake|yolo|hybrid|claude [--demo] [--no-telegram]`: un frame cada `--interval` s (30 por defecto, `04`), recorte y clasificación de cada mesa, suavizado y armado de la observación de mesa (`03`). Cada observación va a `alerts.process()` (MAT-5), que la escribe en `out/eventos.jsonl`.

**Hecho cuando:** con `--classifier fake` sobre un video imprime una observación por mesa cada 30 s de video, con el formato de `03`.

### JES-7 · Integración
**hora 6–12 · 30 min · depende de: JES-6, MAT-5, TOM-5**
Llamar a `alerts.process()` con cada observación y probar con `--classifier hybrid`. Aplicar el recorte y la cadencia que defina Tomás (TOM-7).

**Hecho cuando:** el video de la demo con `--classifier hybrid` manda avisos al grupo.

### JES-8 · Zonas sobre el video final
**hora 6–12 · 30 min · depende de: JES-1, JES-4**
Marcar las mesas del video final y commitear `config/zones.json`.

**Hecho cuando:** los recortes del video final muestran cada mesa completa.

### JES-9 · Vista de demo (pantalla dividida)
**hora 12–16 · 1 h 30 · depende de: JES-6, JES-8, TOM-4 (`bbox`)**
Es lo que ve el jurado. La pantalla se divide en dos:
- **Izquierda:** `tools/vista_demo.py`, una ventana de OpenCV con el video y, encima:
  - el contorno de cada mesa con el color de su estado (`libre` gris, `comiendo` verde, `por_terminar` amarillo, `terminada` rojo);
  - la caja de cada plato (`bbox`) con id, estado y % de comida (`p1 · comiendo · 45 %`);
  - un cartel por mesa con el estado y los minutos ("Mesa 2: por terminar · 4 min").
- **Derecha:** el celular con el grupo de Telegram, espejado en la laptop (`scrcpy` en Android).

La vista lee lo mismo que manda el loop (observaciones y avisos), así lo que se ve coincide con lo que llega a Telegram.

**Hecho cuando:** en la corrida de la demo, la vista se ve fluida y cada aviso aparece en Telegram justo después de que la mesa cambia de color.

### JES-10 · Tabla de lo que pasó (si sobra tiempo)
**hora 16–20 · 45 min · depende de: MAT-5 (`out/eventos.jsonl`)**
`tools/resumen.py`: lee `out/eventos.jsonl` de una corrida y muestra una tabla para el cierre de la demo: por mesa, cuánto duró la comida, cuántos avisos de cada tipo hubo y cuánto tardó cada aviso en resolverse; al final, los totales. Puede ser en la terminal o como imagen para poner en la última slide.

**Hecho cuando:** sobre el log de la corrida de la demo imprime la tabla con números que coinciden con lo que se vio en Telegram.

---

## Checkpoints

| Hora | Qué tiene que andar |
|---|---|
| **6** | Tomás: YOLO entrenado y `hybrid` respondiendo sobre recortes. Matías: reglas verdes contra fixtures y mensajes de prueba en el grupo. Jessica: loop con `--classifier fake` sobre un video. |
| **12** | Video de la demo → `hybrid` → reglas → avisos en Telegram, completo al menos una vez. **Grabarlo.** |
| **16** | Ajuste fino sobre el video final y umbrales de demo definidos. |
| **20** | Feature freeze. Demo ensayada con el guion y plan B listo. |
