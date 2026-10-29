# vision/

Todo Servio en Python: captura, recorte por mesa, clasificación de platos (YOLO + Claude detrás de la interfaz `Classifier`), suavizado, reglas de avisos y envío a Telegram. Contexto: [04-vision.md](../docs/context/04-vision.md), [05-reglas-alertas.md](../docs/context/05-reglas-alertas.md). Contratos: [03-contratos.md](../docs/context/03-contratos.md).

## Arranque

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-yolo.txt          # para --classifier yolo o hybrid
python main.py --source data/videos/demo.mp4 --classifier fake --no-telegram
python main.py --config config/casa-jessica.toml          # todo lo de la casa en un archivo
```

- **Configuración en un solo lugar** (`servio_vision/settings.py`): un `servio.toml` por casa con la cámara, la cadencia, el clasificador, las zonas, el restaurante y los umbrales (`config/servio.example.toml` es el modelo; `config/casa-*.toml` está ignorado por git), más el `.env` para los secretos y lo de la máquina (`.env.example` documenta todo). Prioridad: flags > toml > `.env` > defaults. Los flags solo pisan valores puntuales.
- `--classifier fake|yolo|hybrid|claude`: `fake` no usa modelo; `hybrid` (YOLO + Claude) es el de la demo; `yolo` es el plan B sin internet.
- `--demo` (o `DEMO_MODE=true`, o `demo = true` en el toml): umbrales de demo (la cadencia es siempre `--interval`, 30 s). El flag le gana a la variable.
- `--no-telegram`: imprime los avisos en vez de mandarlos.
- **Privacidad:** las caras se difuminan apenas se lee el frame, con un detector local (YuNet, de OpenCV). La primera vez baja el modelo (~230 KB) a `models/`; sin internet, copialo de la carpeta del equipo o apuntá `FACE_MODEL` a él (una ruta relativa cuenta desde la raíz del repo). Nada de lo que se muestra o se guarda tiene caras. `python -m servio_vision.privacy --source <foto o video>` guarda en `out/privacidad/` cómo quedan.
- `--record DIR`: guarda cada frame procesado (ya difuminado) como JPEG con su hora. **Sin este flag no se guarda ninguna imagen.** Las grabaciones van a la carpeta compartida del equipo, nunca al repo.
- Cada corrida deja `out/eventos.jsonl`; se puede reproducir (plan B de la demo) con `python tools/replay.py --file out/eventos.jsonl --speed 10` (`--no-telegram` para imprimir). Como el log se agrega, el replay lo corta en corridas (`run_started`) y reproduce la última con los umbrales con que se hizo (`--run N` para otra, `--all` para todas). También acepta un fixture: `--file ../fixtures/observations/mesa_completa.jsonl`, con umbrales de producción (`--demo` para los de demo). Escribe su propio log en `out/replay.jsonl` (se pisa en cada replay).
- **Prueba punta a punta de los avisos (MAT-7):** `python main.py --source <video> --classifier fake --demo --speed 10 --limit 20` manda al grupo los tres tipos de aviso de cada mesa en ~1 min. El fake no mira la imagen, así que sirve cualquier video con sus zonas, pero tiene que durar al menos 10 min (`--limit 20` × 30 s); si es más corto, el loop corta antes y faltan avisos. Pensado para 2 mesas: cada mesa siguiente arranca su ciclo 3 lecturas más tarde, así que con más mesas subí `--limit` (+3 por mesa, y el video en proporción). Con Telegram usá siempre `--speed`: sin él el video corre a tope y se pasa el límite de ~20 mensajes por minuto del grupo. Cada mesa suma ~10 mensajes y ediciones por ciclo, así que con 4 mesas o más bajá a `--speed 5` o menos. El mismo recorrido, sin Telegram ni video, está en `tests/test_integration_alerts.py`.

## Cámara del celular

Con [DroidCam](https://www.dev47apps.com/) (Android y iPhone) el celular sirve de cámara por Wi-Fi, sin instalar nada en la PC:

1. Celular y PC en la misma red. Lo más seguro en un evento es la zona Wi-Fi del celular, porque muchas redes públicas no dejan que los dispositivos se vean entre sí.
2. Abrir DroidCam y anotar la `WiFi IP` (puerto 4747). Probar `http://<IP>:4747/video` en el navegador y **cerrar la pestaña**: la versión gratis acepta una sola conexión, así que tampoco se puede correr `main.py` y `vista_demo.py` a la vez.
3. `python main.py --source http://<IP>:4747/video --classifier hybrid --demo --interval 10`

- Dejá la app en primer plano, el celular enchufado y sin ahorro de batería para DroidCam.
- La cámara se lee en un hilo aparte (si no, DroidCam corta la conexión mientras Claude clasifica) y, si se corta, se reconecta sola: se da por vencida si pasan 60 s sin un frame bueno. Una lectura por red que se cuelga (celular sin Wi-Fi) corta a los 5 s.
- **Mesas:** se detectan solas en cada lectura (YOLO de COCO, clase "dining table"; necesita `requirements-yolo.txt`) y se llaman "Mesa 1", "Mesa 2"… Si se mueven, el recuadro las sigue. Para zonas fijas, `--zones config/zones.json` (con `tools/draw_zones.py`).
- **Montaje:** altura, ángulo, luz y cámaras probadas en [docs/montaje-camara.md](../docs/montaje-camara.md). Antes de arrancar, `python -m servio_vision.framing --config config/casa-<quien>.toml` (mismos flags que `main.py`) dice si la mesa sale cortada, oscura o desenfocada; `main.py` repite el chequeo en la primera lectura.

## Tests

```bash
pytest -q                              # sin Telegram: los avisos salen por consola
SERVIO_TEST_TELEGRAM=1 pytest -q       # con el Telegram del .env: manda mensajes reales al grupo
```

Los tests nunca escriben en el `out/eventos.jsonl` real: cada uno usa una carpeta temporal (`tests/conftest.py`).

## Vista de la demo (pantalla dividida)

- **Izquierda:** `tools/vista_demo.py`, con los mismos flags que `main.py`. Muestra el video con cada mesa coloreada por estado y la caja de cada plato.
- **Derecha:** el grupo de Telegram: Telegram Desktop / web.telegram.org, o el celular espejado con [`scrcpy`](https://github.com/Genymobile/scrcpy).

```bash
# en vivo con la cámara del celular (DroidCam): siempre en tiempo real
python tools/vista_demo.py --source http://<IP>:4747/video --classifier hybrid --demo --interval 5
# simulación con el video de 3 min de la demo, como si fuera la cámara
python tools/vista_demo.py --source "<video>.mp4" --classifier hybrid --demo --interval 5 --realtime
# grabar la vista sin ventana (plan B)
python tools/vista_demo.py --source "<video>.mp4" --classifier hybrid --demo --interval 5 --realtime --no-window --record out/vista.mp4
```

**`.env` para la demo** (además del token y el grupo de Telegram):

```
CLAUDE_PROVIDER=anthropic   # API de Anthropic: ~5 s por lectura (con claude -p, ~11 s)
ANTHROPIC_API_KEY=...
DEMO_MODE=true
ALERT_T2=60                 # plato lleno a los 60 s (2 × interval lo dispararía en cada plato recién servido)
ALERT_REMINDER=180          # recordatorios cada 3 min, para no llenar el grupo
FINISHED_AFTER=20           # 20 s en "por terminar" → "vacío"
```

- **`--realtime`:** con un video, la vista se comporta como la cámara: el video nunca espera a la IA y, si está ocupada, esa lectura se saltea. Con la cámara es siempre así. Sin `--realtime`, el video espera cada lectura (todas las lecturas, pero se ve a los tirones).
- **Sobre cada plato** se muestra solo el estado (sin id, % ni confianza), sin encabezado.
- **`--yolo-conf`:** umbral del detector (por defecto 0,25). Para videos de baja calidad (cámaras de seguridad) YOLOE da confianzas de ~0,08: probar con 0,07.

- **Teclas:** `q` sale, espacio pausa. Con `--no-window --record` graba sin abrir la ventana.
- **`--speed`:** a diferencia de `main.py`, sin `--speed` la vista reproduce en tiempo real (es para mirar). Después de una pausa o de esperar al modelo, el video retoma su ritmo sin adelantarse.
- **Grabar en vivo:** con la cámara, la grabación puede quedar más rápida que la realidad si el loop va más lento que los fps de la cámara. Para el plan B, grabar sobre el video.
- **Colores de mesa:** gris libre · azul servida · verde comiendo · amarillo por terminar · rojo terminada. Los platos usan los mismos colores según su estado.

## Datos del restaurante y respuestas de los mozos

Con la comanda del restaurante, Servio sabe **qué plato es cada uno** y **qué mozo atiende cada mesa**. Para la demo esos datos son simulados (`fixtures/restaurante/carta.json` y `salon.json`): es lo que daría el sistema del restaurante si estuviéramos conectados.

- **Qué plato es:** cuando aparece un plato en cámara, Claude mira su recorte y elige cuál de los platos del pedido de esa mesa es (una llamada chica por plato; sin `ANTHROPIC_API_KEY`, se usa el orden del pedido). Un "plato" que no corresponde a nada del pedido (una caja, una taza) no se tiene en cuenta.
- **Los avisos** empiezan con el emoji, el mozo y la mesa ("🍽️ Juan · Mesa 1: terminó."). El de plato lleno nombra el plato y trae **botones** con las respuestas típicas de ese plato, más "✏️ Otra respuesta" (el bot pide que escriban y toma lo que respondan).
- **Las respuestas** se escuchan en un hilo aparte (long polling: sin servidor ni webhook), se agregan al mensaje ("📝 Ana: Muy dulces") y quedan en el log (`alert_feedback`).

## Base de datos (SQLite local o Postgres compartido)

Con datos del restaurante, todo queda también en una base relacional (`servio_vision/db.py`, SQLAlchemy Core). Tablas: `mozos`, `mesas`, `platos`, `respuestas_tipicas`, `platos_servidos`, `avisos` y `respuestas`. En vivo se guardan los platos que aparecen (con el plato del pedido que son), los avisos que se abren y cierran, y las respuestas de los mozos (de botón o escritas).

- **Sin `DATABASE_URL`**: `vision/out/servio.db`, SQLite, un archivo sin servidor (tests, sin internet). Se abre con cualquier cliente de SQLite.
- **Con `DATABASE_URL`** en el `.env` (Postgres en Neon o Supabase; la URL va por un canal privado, nunca por el repo): todas las casas escriben en la misma base y `tools/metricas.py` lee de ahí. Driver: `pip install -r requirements-postgres.txt`. Cada casa escribe con su **`local_id`** (`local_id = "casa-jessica"` en su toml): los avisos van con clave `(local_id, id)`, así dos casas con una "Mesa 1" no se pisan. La comanda (`mozos`, `mesas`, `platos`) es compartida: todas las casas cargan el mismo `fixtures/restaurante/`. `tools/metricas.py --config casa.toml` muestra lo que pasó en vivo en esa casa.
- **Migraciones** (Alembic, `servio_vision/migrations/`): al abrir la base se aplican las que falten, así se agregan columnas sin borrar datos; una base de antes de Alembic se reconoce y se actualiza sola. Para una migración nueva: `python -m alembic -c servio_vision/migrations/alembic.ini revision -m "qué cambia"` y escribir el `upgrade()` a mano (SQLite y Postgres tienen que poder correrla).

## Métricas de platos y mozos

```bash
python tools/generar_historial.py              # historial simulado de 30 días (fixtures/restaurante/historial.jsonl)
python tools/metricas.py --image out/metricas.png
```

En la demo, escribí **`/metricas`** en el grupo de Telegram y el bot responde con la imagen y un resumen (con el programa corriendo). Platos que vuelven llenos (% y motivo más común según los mozos), minutos hasta atender una mesa que terminó y segundos en responder el aviso, por mozo. Salen de consultas SQL a la base (`--sql` las muestra), que junta el historial simulado (se importa la primera vez) con lo que pasó en las demos.

## Tabla de lo que pasó (cierre de la demo)

`tools/resumen.py` lee `out/eventos.jsonl` y arma una tabla por mesa: comidas y su duración media, avisos de cada tipo, recordatorios y cuánto tardaron los avisos en resolverse, con los totales al final. Con `--image`, la tabla sale también como imagen para la última slide.

```bash
python tools/resumen.py                              # out/eventos.jsonl de la última corrida
python tools/resumen.py --image out/resumen.png
```

## Claude

No hay créditos de AWS, así que Claude va por el Claude Code logueado (`claude -p`, suscripción del equipo). Probar con un recorte de mesa y los de sus platos:

```bash
python -m servio_vision.classifiers.claude_plates mesa.jpg plato1.jpg plato2.jpg
```

- `CLAUDE_PROVIDER=cli` (por defecto), `anthropic` (necesita `ANTHROPIC_API_KEY` y `pip install anthropic`) u `openai` (Decisions API de OpenAI, modelo `gpt-6-luna` o el de `OPENAI_MODEL`, timeout de 14 s o el de `OPENAI_TIMEOUT_S`; necesita `OPENAI_API_KEY` y `pip install "openai>=3.26.0"`). Con `openai` no hay JSON libre: se hacen preguntas tipadas por plato (`prompt.decision_questions`) y se arma el mismo `PlatesReport`.
- **Prompts:** están en `vision/prompts/` (`claude.txt` para Claude, `openai.json` para la Decisions API: instrucciones, preguntas con `{i}` = número de plato y los 5 niveles de comida). Para probar otro sin pisar el default, poné la ruta en `CLAUDE_PROMPT_FILE` / `OPENAI_PROMPT_FILE` del `.env`. Se leen en cada lectura y se validan al arrancar.
- En Windows llama a `claude.exe` directo; si no está en el PATH, poner la ruta en `CLAUDE_CLI`.
- Primera prueba (8 platos, video de IA): 6–8 s por mesa con `haiku`.

## YOLO + Claude (`hybrid`)

`--classifier hybrid` es el de la demo: YOLO encuentra los platos, se mantienen los ids por posición y Claude estima cuánto queda en cada uno.

- Sin `YOLO_WEIGHTS` usa YOLOE preentrenado con los prompts `plate` y `bowl` (`imgsz=1280`, lo que funcionó en las pruebas: 11/11 platos en el video de IA). Encuentra los platos pero no su estado, así que sin Claude la lectura se descarta.
- Con `YOLO_WEIGHTS` (el `yolov8n` entrenado con `food` / `finished_food`), si Claude falla usa el estado de YOLO con confianza más baja. Es también lo que usa `--classifier yolo`.
- Si YOLO deja de encontrar un plato que ya conocía, el `hybrid` igual le manda a Claude el recorte de su última caja, hasta 3 lecturas seguidas. Si Claude lo ve, sigue con su id y su lectura; si tampoco lo ve, muestra la última lectura y después deja de mostrarlo.
- Probar sobre un video (un frame cada 0,5 s, el frame entero como una mesa):

```bash
pip install -r requirements.txt -r requirements-yolo.txt
python tools/probar_video.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4" --max-frames 5
```

Con `hybrid` las llamadas a Claude de varios frames van en paralelo (`--paralelo 4` por defecto; `--paralelo 1` para una a la vez). Cada corrida va a su carpeta (`out/video/run1`, `run2`, ...) con cada frame dibujado (`f_NNN.jpg`), la hoja con el recorte de cada plato y su clasificación (`f_NNN_platos.jpg`) y `linea_de_tiempo.json`.

Para medir qué tan estables son los ids (TOM: estabilidad de ids), solo con YOLO y sin gastar Claude:

```
python tools/id_stability.py "C:/Users/tomas/Downloads/video mesa platos ia.mp4" --cada 0.5
python tools/id_stability.py out/video/run1/linea_de_tiempo.json
```

Imprime los ids de cada frame y `conservan`: qué fracción de los platos de un frame sigue con el mismo id en el siguiente. Un plato que YOLO deja de ver hasta 3 lecturas recupera su id si reaparece en su lugar (`MAX_MISSING` en `tracking.py`).

## Resultados

_(Tomás: mAP de YOLO, acierto del `hybrid`, latencia, costo y tamaño mínimo de plato; cita del dataset de Roboflow.)_

## Evaluación de estado (TOM-6)

```
python tools/eval.py --set data/eval
```

`data/eval/` tiene `labels.csv` (`archivo,estado,nota`) y las imágenes de cada plato; no va al repo. El set actual está en la carpeta compartida del proyecto (`servio-eval/`): 119 platos, 110 `lleno` (recortes del video de IA y platos de más de 300 g de Nutrition5k), 8 `por_terminar` y 1 `vacio` (Nutrition5k, etiquetados a ojo). Falta sumar platos a medio comer y vacíos (dataset de Roboflow).

Manda los platos de a 8 por llamada, como una mesa. Resultado con `claude -p` y Haiku (2026-10-17):

| Prompt | Acierto | A un estado | `fill_ratio` medio de los llenos | Latencia por llamada |
|---|---|---|---|---|
| Original | 19,5 % | 100 % | 0,67 | 6,9 s |
| Porción servida + manos/cubiertos no cuentan | 67–69 % | 98–99 % | 0,83–0,84 | 6,6 s |

Casi todos los errores que quedan son platos llenos que Claude puntúa 0,8 justo en el corte (`lleno` es > 0,80) o 0,5–0,7 con brazos encima.
