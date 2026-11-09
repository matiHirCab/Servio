# AGENTS.md — Servio

Guía para agentes de IA (Claude Code, Kiro, etc.) y para el equipo. Leé este archivo primero y después **solo** los archivos de contexto que tu tarea necesite.

## Qué es Servio (en una línea)

Una cámara en alto mira el salón; YOLO encuentra los platos de cada mesa, un modelo (Claude por defecto, o OpenAI; `CLAUDE_PROVIDER`) estima cuánta comida queda en cada uno, y cuando una mesa terminó, tiene platos vacíos o un plato sigue lleno mucho rato, le llega un aviso al mozo por Telegram.

Contexto: MVP de hackathon, 24 horas, equipo de 3 personas (Tomás, Matías, Jessica). Todo es un programa en Python dentro de `vision/`: no hay backend web aparte: con `--web` el mismo proceso sirve la vista del salón en vivo (`/<local>/salon`). Lo que pasa (platos servidos, avisos, respuestas de los mozos) queda en una base SQLite (`vision/out/servio.db`) de la que salen las métricas (`tools/metricas.py`).

## Mapa de contexto

| Archivo | Leelo cuando… |
|---|---|
| [docs/context/01-producto.md](docs/context/01-producto.md) | Necesitás entender el problema, los avisos y qué queda afuera. |
| [docs/context/02-arquitectura.md](docs/context/02-arquitectura.md) | Vas a tocar más de una parte o decidir dónde va algo. |
| [docs/context/03-contratos.md](docs/context/03-contratos.md) | Vas a producir o consumir datos entre partes. **Es la fuente de verdad** de `Classifier`, `Item`, la observación de mesa, `Alert` y los fixtures. |
| [docs/context/04-vision.md](docs/context/04-vision.md) | Trabajás en captura, zonas, YOLO, Claude, suavizado o cadencias. |
| [docs/context/05-reglas-alertas.md](docs/context/05-reglas-alertas.md) | Trabajás en el estado de mesa, las reglas, recordatorios o cierre de avisos. |
| [docs/context/06-notificaciones.md](docs/context/06-notificaciones.md) | Trabajás en el envío a Telegram. |
| [docs/context/08-plan-24h.md](docs/context/08-plan-24h.md) | Querés el panorama: roles y fases por hora. |
| [docs/context/09-convenciones.md](docs/context/09-convenciones.md) | Antes de commitear: estructura, dueños por carpeta, ramas, estilo, variables de entorno. |
| [docs/context/11-tareas.md](docs/context/11-tareas.md) | Vas a hacer una tarea: qué hacer, en qué archivo, de qué depende y cuándo está hecha. |
| [docs/context/12-roles.md](docs/context/12-roles.md) | Vas a mostrar algo según quién mira (mozo, encargado, dueño): vistas, métricas y avisos por rol, y dónde se configura. |
| [docs/context/13-metricas.md](docs/context/13-metricas.md) | Vas a calcular o mostrar una métrica: lista cerrada de M-1, fórmula, de qué tabla sale, qué rol la ve y en qué pantalla. |
| [docs/roadmap-producto.md](docs/roadmap-producto.md) | Vas a hacer una tarea de después de la hackathon (fases A–E): arquitectura, banco de pruebas en casa, mejoras y funciones nuevas. |
| [docs/decisiones-25-11.md](docs/decisiones-25-11.md) | **Siempre, antes de tomar una tarjeta:** las decisiones de producto y negocio del equipo (8/11). Si una tarjeta o un doc de contexto dice otra cosa, gana este archivo. Cada tarjeta nombra los números que le aplican. |
| [docs/diseno-front.md](docs/diseno-front.md) | Vas a tocar el salón en vivo, el dashboard o los mensajes de Telegram: qué se decidió de diseño (F-1, F-2, F-3) y los prototipos de `docs/prototipos/`. |
| [docs/montaje-camara.md](docs/montaje-camara.md) | Vas a armar la cámara en una casa: altura, ángulo, luz, cámaras probadas y el chequeo de encuadre. |

## Tablero y trabajo en paralelo

Las tareas viven en el Trello del equipo (tablero «Servio»), y las hacen agentes, varios a la vez. Para no pisarse, **ninguna tarjeta se toma a mano**: se toma con `vision/tools/tablero.py`, que la pasa a «In progress» en el mismo paso y deja un candado (un comentario) que gana el primero que llega.

```
cd vision
python tools/tablero.py listar                      # 🟢 libre · 🔴 bloqueada (espera otra) · 🟠 choca (toca archivos de una en curso) · 🟡 humano
python tools/tablero.py ver <enlace>                # la descripción completa
python tools/tablero.py tomar <enlace> --agente <quién> --rama <prefijo>   # la reclama; si otro llegó antes, sale con 2
python tools/tablero.py terminar <enlace> --pr <N>  # a Done, y libera las que dependían de ella
python tools/tablero.py soltar <enlace> --motivo "…" # si no la vas a terminar: vuelve a su lista
```

El `<enlace>` es lo que va en `trello.com/c/XXXXXXXX`. Hacen falta `TRELLO_KEY` y `TRELLO_TOKEN` en el `.env` (de la cuenta de la persona que corre el agente; ver `.env.example`).

El ciclo de una tarjeta:

1. `listar` y elegir una 🟢 **libre**. Las 🔴 esperan otra tarjeta (el encabezado dice cuál); las 🟠 tocan los mismos archivos que una en curso (esperar a que termine, o `--forzar` si estás seguro de que no chocan); las 🟡 las hace una persona.
2. `tomar`. Rama **desde `develop`** con el nombre que imprime (`<prefijo>/<código>-<tema>`). Leer el encabezado de la tarjeta: **Toca** (qué carpetas; no salirse sin motivo), **Decisiones** (los números de [docs/decisiones-25-11.md](docs/decisiones-25-11.md) que mandan sobre esa tarjeta) y el «Hecho cuando».
3. Programar con tests. PR a `develop` (nunca a `main`), con el enlace de la tarjeta en la descripción. Pasar el code review (`/code-review <PR>`) e iterar hasta el ok; recién ahí mergear.
4. `terminar --pr <N>`: comenta el PR en la tarjeta, la pasa a Done y pone 🟢 a las que esperaban por ella. Después, `listar` de nuevo y seguir con otra.

Reglas del tablero:

- Una tarjeta por agente a la vez. Si no la vas a terminar, `soltar`: una tarjeta en curso que nadie trabaja bloquea a las demás.
- El encabezado de cada tarjeta (las primeras líneas de la descripción) es lo que lee el programa: `**Área:**`, `**Toca:**` (rutas entre acentos graves), `**Depende de:**` (otras tarjetas con su enlace entre paréntesis), `**Decisiones:**`. Si creás una tarjeta, ponéselo; si partís una en dos, las dos lo llevan.
- Si al trabajar una tarjeta hace falta tocar archivos de otra que está en curso, no lo hagas en esa rama: terminá la tuya y abrí otra tarjeta, o esperá.
- Lo que una tarjeta decide y no está en `docs/decisiones-25-11.md` ni en los docs de contexto, se escribe en el PR y, si cambia reglas, en el doc de contexto que corresponde (ver el mapa de arriba).

## Reglas para agentes

1. **Los contratos mandan.** Si cambiás `Classifier`, `Item`, la observación de mesa o `Alert`, actualizá primero `docs/context/03-contratos.md` y los fixtures en el mismo PR, y avisá en la descripción.
2. **Trabajá solo en tus carpetas** según la tabla «Dueños por carpeta» de [09-convenciones.md](docs/context/09-convenciones.md), salvo que la tarea diga otra cosa.
3. **Primero que funcione, después que sea lindo.** Es una hackathon: preferí lo simple y demostrable.
4. **Siempre con mock.** Cada parte tiene que poder correr sola: el pipeline con `--classifier fake`, las reglas con `fixtures/` y `--no-telegram`, el clasificador con fotos sueltas.
5. **Nada de secretos en el repo.** Tokens y claves van en `.env` (ignorado) y se documentan en `.env.example`. Toda la configuración se lee en un solo lugar, `servio_vision/settings.py` (ver «Configuración» en [09-convenciones.md](docs/context/09-convenciones.md)); no agregues `os.getenv` en otro archivo.
6. **No subas imágenes, videos, datasets ni pesos al repo.** Ver «Privacidad y datos» en [09-convenciones.md](docs/context/09-convenciones.md).
7. Escribí docs y comentarios en español; código (nombres de variables, funciones) en inglés.
