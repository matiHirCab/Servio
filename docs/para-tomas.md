# Para Tomás: qué cambió en la rama de Jessica (PR #38)

Resumen de todo lo que entra a `main` con el PR #38 (rama `jessica/restaurante`). La rama `demo` es un respaldo de una versión anterior que ya funcionaba.

**Lo más importante para vos:** hay arreglos de bugs que tocan código tuyo (`tracking.py`, `zones.py`). Están más abajo, en «Bugs encontrados y arreglados». Cada uno tiene su test.

## Qué hicimos

### Demo con video, como si fuera la cámara en vivo
- `tools/vista_demo.py --realtime` reproduce el video a velocidad real. Si la IA está ocupada, el video no la espera y se saltea esa lectura.
- La vista muestra el estado de cada plato y un cartel chico "Mesa N", sin porcentajes ni encabezado.
- Videos con fps falsos (los `.asf` de las cámaras dicen 1000 fps): se usa el tiempo real de cada cuadro.
- Un plato que está en `por_terminar` durante `FINISHED_AFTER` segundos pasa a `vacio`. Así los restos que nunca se ven del todo vacíos no frenan la mesa.

### Mesas detectadas solas (`servio_vision/tables.py`)
- Usa YOLO de COCO (`yolo11s.pt`, clase 60 "dining table"). Con YOLOE no se encontraban mesas de forma estable.
- `TableTracker` mantiene el id de cada mesa entre lecturas, numera las mesas por filas y, si no ve ninguna, usa el cuadro entero.
- Si la mesa se mueve, las cajas de los platos se pasan al recorte nuevo (`Pipeline._follow_moved_tables`).
- `--zones` sigue funcionando para marcar las mesas a mano.

### Avisos nuevos (`docs/context/05-reglas-alertas.md`)
- `mesa_terminada` reemplaza a `mesa_por_terminar`: salta cuando **todos** los platos de la mesa están vacíos.
- `por_terminar` ya no manda mensaje; solo cambia el color de la mesa.
- `plato_vacio` avisa solo si el resto de la mesa sigue comiendo.
- Formato de los mensajes: `emoji Mozo · Mesa N: mensaje`. Por ejemplo, `🍽️ Juan · Mesa 1: terminó.`

### Datos del restaurante, respuestas de los mozos y métricas
- `fixtures/restaurante/` tiene datos simulados de lo que daría el sistema de comandas del local: carta, mozos, pedido de cada mesa y un historial de 30 días.
- `DishMatcher` (Claude) identifica qué plato del pedido es cada plato que ve la cámara. Los platos que no están en el pedido (una caja, una taza) se descartan.
- El aviso de plato lleno trae botones en Telegram con las respuestas típicas de ese plato, más "✏️ Otra respuesta" para escribir.
- Todo queda en una base SQLite relacional (`servio_vision/db.py`, en `vision/out/servio.db`).
- `tools/metricas.py` saca, con consultas SQL, los platos que vuelven llenos y los tiempos de cada mozo, y genera la imagen para el pitch.

### Terminal limpia
- Por defecto se ve solo el arranque, las mesas y platos nuevos, los avisos y los errores. `--verbose` muestra todo.
- Ojo: el SDK nuevo de Anthropic loguea con `httpx2`, no con `httpx`.

## Bugs encontrados y arreglados

### 1. La caja del plato quedaba corrida y el plato no pasaba a vacío
- **Síntoma:** en el video de demo, la caja del plato de donas aparecía corrida, y el plato seguía "lleno" aunque ya habían sacado las donas.
- **Causa, en dos partes:**
  - El detector de mesas a veces incluía las piernas de alguien, y el recorte de la mesa saltaba de unos 300 px a unos 410 px.
  - Las cajas de los platos se pasaban al recorte nuevo, pero la memoria de platos perdidos de `IdCounter` no. Un plato que YOLO perdía volvía con la caja del recorte viejo; Claude miraba ese lugar y decía "lleno". El plato real quedaba con un id nuevo y se descartaba como "no pedido".
- **Arreglo:**
  - `IdCounter.remap()` (en `tracking.py`), que el pipeline llama junto con el del suavizado.
  - `TableTracker` ignora una detección que contiene a la mesa conocida y es más de 1,5 veces más grande.
- **Test:** `tests/test_table_jump.py` corre el pipeline de punta a punta con la mesa saltando de tamaño. Sin el arreglo falla, con la caja corrida 57 px.

### 2. Caja de ancho 0 cuando la mesa se achica (`zones.remap_bbox`)
- Si una caja quedaba fuera del recorte nuevo, se recortaba a ancho 0. Al recortar esa imagen, la lectura entera de la mesa fallaba.
- Ahora se recortan las esquinas de la caja, y si queda fuera de la mesa, `remap_bbox` devuelve `None` y se olvida el plato.

### 3. Un plato que vuelve con otro id se descartaba (`restaurant.py`)
- Pasaba si a un plato lo tapaban un rato o si fallaba el emparejamiento de ids: volvía con un id nuevo y, como el lugar del pedido seguía ocupado por el id viejo, se descartaba.
- Ahora los platos que la cámara deja de seguir liberan su lugar del pedido (`Restaurant.release_missing`).

### 4. Dos platos podían quedar con el mismo plato del pedido (`restaurant.py`)
- Pasaba cuando `DishMatcher` asignaba los platos fuera de orden.
- Ahora a cada plato nuevo se le asigna el plato del pedido que falta.

### 5. Una respuesta tardía borraba el "✅ ya resuelto" (`alerts/feedback.py`)
- Si el mozo respondía justo cuando el aviso se cerraba, la edición de su respuesta pisaba el "ya resuelto".
- Ahora todas las ediciones de un mensaje con botones pasan por `FeedbackDesk.rewrite`, con el lock.
- Un segundo toque casi simultáneo ya no cuenta dos veces.

## Pendientes conocidos (no los arreglamos)
- `DishMatcher` no tiene la opción "ninguno": si una caja o una taza aparece antes que los platos, puede quedarse con un lugar del pedido. Con la demo no pasa, porque la caja de donas no se detecta con la confianza por defecto.
- Al remapear una caja no se recalcula `Item.position` (el texto "arriba-izq", etc.).
- En el video de demo, "plato vacío" (las cookies) sale unos 5 s antes que "Mesa terminó". Las dos cosas son ciertas, pero en la demo se lee como un mensaje de más.
- Claude usa `claude-haiku-5-5` (unos 4 s por lectura). Se puede cambiar con `CLAUDE_MODEL` en el `.env` (Sonnet 5.5 u Opus 5.5 son más capaces, pero más lentos y más caros). No lo comparamos todavía.
- `YOLO_WEIGHTS` (tu `best.pt`) no está en la compu de Jessica: todo se probó con YOLOE `plate`/`bowl`.

## Cómo correrlo
```
cd vision
python tools/vista_demo.py --source "<video>.mp4" --classifier hybrid --demo --interval 5 --realtime
python -m pytest -q        # 333 tests
python tools/metricas.py --image out/metricas.png
```
