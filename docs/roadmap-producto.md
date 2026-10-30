# Roadmap — de MVP de hackathon a producto

Qué hacer después de la hackathon para que Servio quede prolijo, bien armado y con más funciones. Mismo formato que [11-tareas.md](context/11-tareas.md): **dueño · fase · estimación · depende de** → qué hacer → **Hecho cuando**. Rutas relativas a `vision/` salvo que digan otra cosa.

## Punto de partida

- **No hay restaurante todavía.** Se prueba en nuestras casas, con una cámara (celular con DroidCam o cámara IP) mirando una mesa desde arriba, como en la hackathon. Por eso el plan se apoya en un **banco de pruebas en casa** (fase B) que reemplaza los datos que daría un restaurante.
- **El código creció rápido.** Son ~7.200 líneas que funcionan y tienen buenos tests, pero hay partes que hacen demasiado:
  - `alerts/processor.py` junta estado, reglas, log, base, textos de Telegram, botones y comandos (ya partido: A-4).
  - `main.py` tiene a la vez el `Pipeline`, el armado de las piezas y los flags.
  - La configuración está repartida entre flags, `.env` y `os.getenv` sueltos en 7 archivos.
- **Los docs de contexto quedaron viejos.** `01` y `02` dicen que el mozo no responde y que no hay base, y ya hay botones, `/metricas` y `servio.db`.

## Cómo se organiza

| Fase | Qué es | Cuándo |
|---|---|---|
| **A · Ordenar la base** | Arquitectura y deuda técnica, sin cambiar lo que hace el producto | Primero: 2–3 semanas |
| **B · Banco de pruebas en casa** | Grabar, etiquetar y repetir escenarios para medir sin restaurante | En paralelo con A |
| **C · Mejorar lo que hay** | Precisión, costo, privacidad y avisos | Después de A, con los números de B |
| **D · Funciones nuevas** | Dashboard, alta del local, inteligencia de menú, nuevos avisos | Después de A; algunas en paralelo con C |
| **E · Cuando haya restaurante** | Lo que necesita un cliente real | Queda en espera |

Reglas para estas fases:
- **A no cambia comportamiento.** Cada tarea de A deja `pytest` en verde y los mismos avisos sobre `fixtures/` y sobre una sesión grabada (B-1). Si algo cambia, es un bug.
- **Los contratos siguen mandando:** si una tarea cambia `Alert`, `Item` o la observación de mesa, primero se actualiza [03-contratos.md](context/03-contratos.md) con sus fixtures.
- **Dueños** por área, igual que en [09-convenciones.md](context/09-convenciones.md): Tomás → modelo, Matías → avisos y datos, Jessica → pipeline, app y dashboard. Ahora el equipo es de 4: se propone que la persona nueva tome el banco de pruebas en casa (B-1 a B-5) junto con su dueño original y después el dashboard (D-1). Se confirma entre todos.
- **Estimaciones** en días de una persona (d).

---

## A — Ordenar la base

### A-1 · Configuración en un solo lugar
**Jessica · A · 2 d · depende de: nada**
Crear `servio_vision/settings.py` con `pydantic-settings`: un objeto `Settings` que lee `.env` y un archivo `servio.toml` del local (cámara, mesas, umbrales, proveedor del modelo, Telegram). Reemplazar los `os.getenv` sueltos (`main.py`, `llm/` (los proveedores), `detector.py`, `dish_match.py`, `prompt.py`, `smoothing.py`, `alerts/config.py`, `notify.py`) por parámetros que reciben de `Settings`. Los flags quedan solo para pisar valores puntuales.

**Hecho cuando:** `grep -r getenv servio_vision` solo encuentra `settings.py`, `.env.example` documenta todo, y `python main.py --config casa-jessica.toml` arranca sin otros flags.

### A-2 · Armado de la app separado del pipeline
**Jessica · A · 2 d · depende de: A-1**
Mover `Pipeline` a `servio_vision/pipeline.py` y el armado de las piezas (`build_pipeline`, `load_restaurant`, `classifier_kwargs`) a `servio_vision/app.py`. `main.py` queda solo con los flags y la llamada a la app.

**Hecho cuando:** `main.py` tiene menos de 100 líneas y `test_main.py` pasa sin cambios de comportamiento.

### A-3 · Un proveedor de modelo por archivo
**Tomás · A · 2 d · hecho** (no dependía de A-1: los proveedores siguen leyendo `settings.current()`; con A-1 se les pasa `Settings`)
Se partió el antiguo `claude_plates.py` en el paquete `servio_vision/llm/`: la interfaz `PlateEstimator` (`estimate(request) -> PlatesReport`) en `base.py`, un archivo por proveedor (`claude_cli.py`, `anthropic_api.py`, `openai_api.py`), el registro en `registry.py`, el parseo y la validación en `parsing.py` y lo común de llamar (cortacircuito, tope, reintento) en `runtime.py`. `hybrid.py` recibe un `PlateEstimator` y no sabe cuál es.

**Hecho cuando:** sumar un proveedor nuevo es un archivo nuevo más una línea en el registro, y `test_claude_plates.py` y `test_hybrid.py` pasan.

### A-4 · Avisos: estado y reglas por un lado, salidas por el otro
**Matías · A · 4 d · hecho** (junto con A-5, como P1)
Hecho así: el núcleo es `alerts/core.py` (`AlertCore.apply(obs)` devuelve eventos: `TableUpdated`, `AlertsOpened`, `AlertsReminded`, `AlertsResolved`); los suscriptores, `alerts/outputs.py` (`TelegramOutput`, `EventLogOutput`, `DatabaseOutput`); los textos, `alerts/messages.py`; el bot, `alerts/bot.py` (escucha y comandos, y del canal solo usa `BotChannel`). `AlertProcessor` (`processor.py`) quedó como la fachada que los arma, con la misma firma.

Partir `AlertProcessor` en:
- **núcleo:** estado de mesas, reglas y avisos abiertos, sin entrada/salida;
- **suscriptores** de los eventos del núcleo (aviso abierto, recordado, cerrado, plato servido, respuesta): `EventLog`, `Database` y el notificador;
- **textos** de los mensajes en un módulo propio (`alerts/messages.py`);
- **bot de Telegram** (botones, texto libre, `/metricas`) separado del notificador, para poder cambiar de canal después (C-6).

**Hecho cuando:** el núcleo se prueba con fixtures sin log, sin base y sin notificador, y `test_integration_alerts.py` produce los mismos avisos.

### A-5 · Estado que sobrevive a un reinicio
**Matías · A · 2 d · hecho** (junto con A-4, como P1)
Hecho así: la foto (`alerts/persist.py`) va a la tabla `estado_servicio` y se recupera con `resume` (solo cámara en vivo) si tiene menos de `RESUME_MAX_AGE_S`. Además de al abrir o cerrar un aviso, se guarda cuando cambia algo que usan las reglas, a lo sumo una vez por segundo (`SAVE_RULES_EVERY_S`, para no escribir en cada lectura con Postgres remoto), en la primera lectura que llega pasado ese tiempo: un corte brusco pierde como mucho los cambios que esperaban esa lectura. El test es `tests/test_alert_crash.py` (corta antes de cada lectura de cada fixture, con ese tope en 0).

Guardar en `servio.db` los platos abiertos, desde cuándo están en cada estado y los avisos abiertos (con su `message_id`), y recuperarlos al arrancar. Si el último guardado es muy viejo, se arranca de cero.

**Hecho cuando:** un test corta el proceso a mitad de un fixture, lo relanza y los avisos son los mismos que sin corte.

### A-6 · Base con migraciones
**Matías · A · 1 d · depende de: nada**
Versionar el esquema de `db.py` (tabla `schema_version` y migraciones numeradas) para poder agregar columnas sin borrar `servio.db`.

**Hecho cuando:** una base de la versión anterior se actualiza sola al arrancar, con un test.

### A-6b · Base en Postgres gestionado
**Matías + Jessica · A · 3 d · depende de: A-1, A-6**
Decidido: la base se despliega en un Postgres gestionado (Neon o Supabase, plan gratis) para que las tres casas escriban en la misma base y el dashboard (D-1) lea de ahí. Pasar `db.py` de `sqlite3` a SQLAlchemy Core con migraciones (Alembic). La URL va en `DATABASE_URL` (`.env`, A-1). Sin `DATABASE_URL` se sigue usando el SQLite local, para tests y para trabajar sin internet. Cada fila lleva `local_id` (cada casa es un local) para no mezclar datos.

**Hecho cuando:** dos casas corriendo a la vez escriben en la misma base sin pisarse, `servio metricas` lee de Postgres, y los tests siguen corriendo sobre SQLite.

### A-7 · Un solo comando `servio`
**Jessica · A · 2 d · depende de: A-2**
Entry point en `pyproject.toml` con subcomandos: `servio run`, `servio replay`, `servio eval`, `servio metricas`, `servio zonas`, `servio probar-video`. Los scripts de `tools/` pasan a ser esos subcomandos. Las herramientas solo para la demo (`vista_demo`, `generar_historial`) quedan aparte, marcadas como demo.

**Hecho cuando:** `pip install -e .` deja el comando `servio` y el README usa solo esos subcomandos.

### A-8 · Calidad en el CI
**Tomás · A · 1 d · depende de: nada**
Sumar al CI un chequeo de tipos gradual (`pyright` en modo básico, empezando por `schemas.py`, `alerts/` y `llm/`) y el reporte de cobertura. Un test de punta a punta que corre `servio run` con `--classifier fake` sobre una sesión grabada corta (B-1) y compara los avisos.

**Hecho cuando:** el CI falla si se rompe un tipo en esos módulos o si cambian los avisos de la sesión grabada.

### A-9 · Docs al día
**Todos · A · 1 d · depende de: A-1 a A-4**
Actualizar `01-producto.md` (respuestas de los mozos, métricas), `02-arquitectura.md` (diagrama nuevo, base, eventos y suscriptores), `09-convenciones.md` (dueños de las carpetas nuevas) y `AGENTS.md`.

**Hecho cuando:** un agente que solo lee los docs describe bien cómo está armado Servio hoy.

---

## B — Banco de pruebas en casa

### B-1 · Grabar sesiones
**Jessica · B · 2 d · depende de: nada**
Agregar `servio run --record DIR`: guarda los frames que se procesan (a la cadencia real, no el video entero) con su hora, más el log de eventos. `servio replay DIR` reproduce la sesión con cualquier clasificador. Las sesiones van a una carpeta compartida del equipo, **nunca al repo**.

**Hecho cuando:** se graba una comida en casa y al reproducirla con `--classifier hybrid` salen los mismos avisos que en vivo.

### B-2 · Escenarios guionados
**Matías · B · 2 d · depende de: B-1**
Una lista de escenarios para actuar en casa, cada uno con sus avisos esperados: comer todo, dejar un plato lleno 10 minutos, plato compartido, levantarse un rato, dejar el pan, retirar un plato y no el otro, mover la mesa, poca luz. Grabar cada uno (B-1) y guardar los avisos esperados como en `fixtures/expected/`.

**Hecho cuando:** hay al menos 8 escenarios grabados y `servio eval --sessions` dice cuáles pasan.

### B-3 · Etiquetar recortes
**Tomás · B · 3 d · depende de: B-1**
Herramienta simple (web local o ventana de OpenCV) que muestra los recortes de platos de las sesiones y deja marcar el estado real (`lleno`, `comiendo`, `por_terminar`, `vacio`). Las etiquetas forman el set de evaluación propio, en el ángulo y la luz de nuestras cámaras.

**Hecho cuando:** hay al menos 300 recortes etiquetados de 3 casas distintas y `servio eval` reporta el acierto por estado sobre ese set.

### B-4 · Montaje de cámara repetible
**Jessica · B · 1 d · depende de: nada**
Guía con altura, ángulo, luz y cámaras probadas. Al arrancar, un chequeo de encuadre avisa si la mesa sale cortada, oscura o desenfocada.

**Hecho cuando:** los tres armamos la cámara siguiendo la guía y el chequeo de encuadre pasa en las tres casas.

### B-5 · Tablero de números
**Tomás · B · 1 d · depende de: B-2, B-3**
Un solo reporte (`servio eval --all`) con el acierto por estado, los escenarios que pasan, los avisos falsos por hora, la latencia y el costo por mesa por hora. Se corre antes de cada cambio grande para comparar.

**Hecho cuando:** el reporte se guarda con fecha y sirve para comparar dos versiones del prompt.

---

## C — Mejorar lo que hay

### C-1 · Menos avisos falsos
**Tomás · C · 4 d · depende de: B-2**
Atacar los escenarios de B-2 que fallan: plato compartido o de centro, pan o guarnición que se deja, persona que se levantó, mesa sin gente con platos. Ajustar el prompt o las reglas según el caso, con un fixture por cada caso.

**Hecho cuando:** todos los escenarios de B-2 pasan y bajan los avisos falsos por hora del tablero de B-5.

### C-2 · Menos llamadas al modelo
**Tomás · C · 3 d · depende de: A-3, B-5**
Mandar al modelo solo los platos cuyo recorte cambió (no la mesa entera), con un caché por plato, y no volver a preguntar por un plato `vacio` estable. Medir antes y después con B-5.

**Hecho cuando:** el costo por mesa por hora baja al menos a la mitad sin perder acierto.

### C-3 · Privacidad: personas y caras (se adelanta a la semana 1)
**Tomás + Jessica · C · 4 d · depende de: nada**
Decidido hacer las dos cosas que pide [legal-uruguay.md](legal-uruguay.md):
- **Al modelo de la nube solo van recortes de platos**, con las personas y manos que entren en el recorte difuminadas en el equipo (Tomás, en `hybrid.py`).
- **Caras difuminadas en todo lo que se muestra o se guarda**: vista de demo, grabaciones (B-1), hojas de `probar_video` y cualquier imagen de `out/` (Jessica). Se hace en un solo lugar, apenas se lee el frame (`capture.py`), con un detector de caras local, así nada de lo que viene después ve una cara.
- No se guardan frames salvo con `--record`, y esos también salen difuminados.

**Hecho cuando:** un test verifica que al proveedor solo llegan recortes de platos, otro que un frame con una cara sale difuminado de la captura, y sin `--record` no quedan imágenes en `out/`.

### C-4 · Salud del sistema
**Jessica · C · 2 d · depende de: A-1**
Un latido cada minuto con el estado de la cámara, del modelo y de la conexión. Si algo falla más de N minutos, mandar un aviso por un canal distinto al de los mozos. Log rotado.

**Hecho cuando:** al tapar la cámara, cortar internet o romper la clave del modelo llega un aviso de salud en menos de 5 minutos.

### C-5 · "Voy" / "Listo" y escalado
**Matías · C · 2 d · depende de: A-4**
Botones "Voy" y "Listo" en todos los avisos, y "No hacía falta" para marcar falsos positivos (alimenta B-5). Si nadie dice "Voy" en N minutos, recordar con mención al mozo.

**Hecho cuando:** `servio metricas` muestra el tiempo de respuesta por mozo y la cantidad de avisos marcados "No hacía falta".

### C-6 · Canal de aviso intercambiable
**Matías · C · 3 d · depende de: A-4**
Interfaz `Notifier` (`send`, `edit`, `on_reply`) con Telegram como primera implementación y la consola para tests. Deja lista la puerta para WhatsApp (D-6).

**Hecho cuando:** un notificador nuevo se suma con un archivo, y los tests de avisos corren contra la consola y contra un Telegram falso.

### C-7 · Avisos por mozo y por sector
**Matías · C · 2 d · depende de: C-6, D-2**
Cada mozo tiene sus mesas; el aviso le llega a él (mención o chat privado) y no a todo el grupo. Cambia `Alert` (destinatario) → actualizar `03-contratos.md`.

**Hecho cuando:** con dos mozos configurados, cada uno recibe solo los avisos de sus mesas.

---

## D — Funciones nuevas

### D-1 · Dashboard local
**Jessica · D · 5 d · depende de: A-4, A-6**
Web chica (FastAPI + HTML simple o HTMX) que corre en la misma máquina y lee `servio.db`: mesas en vivo con el estado de cada plato, avisos abiertos, y las métricas que hoy salen por `/metricas` (tiempos de retiro, platos con problemas, mozos). Sin servidor central por ahora.

**Hecho cuando:** desde el celular, en la misma red, se ve la mesa de casa en vivo y las métricas del día.

### D-2 · Alta del local desde la web
**Jessica · D · 4 d · depende de: D-1, A-1**
Desde el dashboard: ver la foto de la cámara, confirmar o dibujar las mesas y numerarlas, y cargar mozos, sectores y la carta con sus respuestas típicas. Escribe el `servio.toml` (A-1) y reemplaza `draw_zones.py` y `fixtures/restaurante/` a mano.

**Hecho cuando:** alguien de afuera del equipo deja su casa configurada en menos de 15 minutos sin tocar archivos.

### D-3 · Resumen al cerrar el servicio
**Matías · D · 2 d · depende de: A-4**
Al cortar Servio (o a una hora configurada), mandar un resumen del servicio: mesas atendidas, tiempos promedio, avisos, respuestas y platos con problemas. Reusar la imagen de `/metricas`.

**Hecho cuando:** al terminar una comida en casa llega el resumen al grupo.

### D-4 · Inteligencia de menú
**Tomás · D · 3 d · depende de: D-1, C-5**
Por plato de la carta: % que vuelve lleno, motivos más comunes (los textos libres de los mozos agrupados con el modelo) y tendencia. Es lo más diferencial para vender.

**Hecho cuando:** el dashboard muestra los platos con más devoluciones y su motivo principal (con datos de `generar_historial` mientras no haya datos reales).

### D-5 · Modo sombra
**Matías · D · 1 d · depende de: A-4**
`servio run --shadow`: observa y guarda los avisos que habría mandado, sin mandarlos. Sirve para probar cambios grandes sin molestar y, el día de mañana, para medir la línea de base en un restaurante.

**Hecho cuando:** una sesión en sombra deja en la base los mismos avisos que en modo normal, marcados como no enviados.

### D-6 · WhatsApp
**Matías · D · 3 d · depende de: C-6**
Un notificador de WhatsApp Business API con plantillas y botones. Antes, verificar las condiciones y el costo por conversación.

**Hecho cuando:** el mismo escenario de B-2 produce los mismos avisos por Telegram y por WhatsApp.

### D-7 · Avisos nuevos
**Tomás + Matías · D · 4 d cada uno · depende de: C-1**
En orden de valor: vasos vacíos, mesa esperando el siguiente plato y persona que levanta la mano. Cada uno con su escenario en B-2, su fixture y su entrada en `05-reglas-alertas.md`.

**Hecho cuando:** cada aviso pasa sus escenarios en el tablero de B-5.

### D-8 · Modelo propio de cuánta comida queda
**Tomás · D · 8 d · depende de: B-3, C-2**
Entrenar un modelo chico sobre el recorte del plato con las etiquetas de B-3 y las respuestas guardadas del modelo grande. El modelo grande queda solo para los casos dudosos. Se suma como un `Classifier` más.

**Hecho cuando:** iguala el acierto de `hybrid` en el set de B-3 y corta al menos 80 % de las llamadas al modelo grande.

---

## E — Cuando haya restaurante (en espera)

Esto no se hace ahora, pero A, C y D ya dejan la puerta abierta:
- **Equipo en el local** que arranque solo (Docker o systemd, cámara IP por RTSP). Se puede adelantar en casa si alguien quiere dejar Servio corriendo días seguidos.
- **Servidor central** con varios locales, cuentas y roles; el dashboard local (D-1) pasa a leer de ahí.
- **Integración con el POS** (Fudo, Toteat u otro; verificar qué API exponen).
- **Paquete legal:** contrato de encargado, cartel, aviso a los mozos y evaluación de impacto (ver [legal-uruguay.md](legal-uruguay.md)).
- **Piloto:** una semana en modo sombra (D-5) como línea de base, después con avisos, y un reporte de antes vs. después en plata.

## Orden sugerido

1. **Semanas 1–2:** A-1, A-3, A-4 y A-6 en paralelo (cada uno en su área), C-3 (caras y personas) antes de grabar nada, después B-1 y B-4 para empezar a grabar en casa, y A-6b (Postgres) en cuanto estén A-1 y A-6.
2. **Semanas 2–3:** A-2, A-5, A-7, A-8 y A-9, más B-2 y B-3. Al final, B-5 da los números de partida.
3. **Semanas 4–6:** C-1, C-2 y C-5, y D-1 en paralelo.
4. **Después:** D-2, C-6 y C-7, D-3, D-4 y el resto según lo que muestre el tablero de B-5.
