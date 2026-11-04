# Decisiones para llegar al corte del 25/11

Lo que hay que decidir **en una reunión** para que las semanas 3 (11/11 → 17/11) y 4 (18/11 → 25/11) se puedan trabajar sin frenarse a preguntar. Cada punto trae el contexto en dos líneas, las opciones, una propuesta para discutir y un renglón «Decisión» para completar en la reunión. Después de la reunión, este archivo es la referencia: si una tarjeta choca con algo de acá, manda esto.

Fuentes: [roadmap-producto.md](roadmap-producto.md), el Trello (listas Semana 3, Semana 4 y Después del 25/11), [diseno-front.md](diseno-front.md), [context/12-roles.md](context/12-roles.md), [context/13-metricas.md](context/13-metricas.md) y los PR abiertos.

## Resumen en una pantalla

| # | Decisión | Propuesta | Decide |
|---|---|---|---|
| 1 | Quién toma cada tarjeta de las semanas 3 y 4 | Reparto de abajo, por dueño de área | Todos |
| 2 | Qué se corta si no entra todo | C-2 y D-3 pasan a «Después del 25/11» primero | Todos |
| 3 | Cámara armada en cada casa: fecha | Antes del 13/11, es lo que destraba B-2 y M-2 | Cada uno |
| 4 | Qué aviso se espera en cada escenario de B-2 | Tabla de abajo | Matías + Tomás |
| 5 | Plato compartido: cómo se resuelve | Regla + comportamiento, nada que cargue el restaurante | Tomás |
| 6 | Detector: seguir con YOLO entrenado o probar YOLO-World | Probar YOLO-World 1 día dentro de C-1 | Tomás |
| 7 | Escalado y recordatorios: tiempos y a quién | Los del diseño F-3 | Matías |
| 8 | Dónde vive mozo ↔ Telegram y mesa ↔ mozo | Un solo lugar: `servio.toml` | Matías + Jessica |
| 9 | Canal al reloj: Telegram, y quién lo prueba en un reloj real | Telegram; probar con el reloj que haya antes del 17/11 | Todos |
| 10 | `/metricas` y avisos a encargado y dueño | Solo encargado y dueño; escalados al encargado | Matías |
| 11 | Umbrales por local en `servio.toml` | Sí, los que el dashboard muestra en «configuración» | Matías |
| 12 | F-1b: sobre qué código se hace y modo video | Sobre `salon.html`, no sobre `vista_demo.py`; video solo con `--web-video` | Jessica |
| 13 | PR #65 (D-1): cerrar y aplicar F-2 encima | Mergear #65 como está; F-2 se aplica en un PR aparte | Tomás |
| 14 | PR #29 y #42: qué se hace con cada uno | #29 se cierra o se rebasea en la semana; #42 se mergea el 25/11 | Matías / Tomás |
| 15 | Base compartida: quién usa cuál y `local_id` | Todos contra el Postgres, cada casa con su `local_id` | Todos |
| 16 | Comanda por casa | Cada casa con su `restaurante/` en el toml | Jessica |
| 17 | Sesiones grabadas: carpeta y nombres | Carpeta del equipo, `sesiones/<casa>/<fecha>-<escenario>/` | Matías |
| 18 | M-2: cuántas comidas por casa y en qué modo | 2 en sombra + 2 con avisos por casa, semana 4 | Todos |
| 19 | Demo del 25/11: qué se graba y quién | Una comida de verdad con F-1b + dashboard + avisos | Todos |
| 20 | Reunión fija de la semana | Un día fijo de 30 min | Todos |

---

## A · Equipo y reparto

### 1. Quién toma cada tarjeta

**Contexto.** En el Trello, ninguna tarjeta de las semanas 3 y 4 tiene a nadie asignado. Entre las dos semanas hay unos 28 días de trabajo estimados más F-1b (que arranca en la semana 3 y termina el 24/11), para 3 o 4 personas.

**Propuesta** (sigue los dueños de área del roadmap: Tomás modelo, Matías avisos y datos, Jessica pipeline, app y front):

| Semana | Tarjeta | Días | Propuesto |
|---|---|---|---|
| 3 | F-1b · Vista de reconocimiento (termina 24/11) | ~5 | Jessica |
| 3 | C-5 · «Voy», «Listo», «No hacía falta» | 2 | Matías |
| 3 | C-7 · Avisos por mozo al reloj | 2 | Matías |
| 3 | B-2 · Escenarios actuados (mínimo 8) | 2 | Matías arma la lista y los esperados; **graban las tres casas** |
| 3 | B-3 · Etiquetar recortes y tablero de números | 4 | Tomás (si hay cuarta persona: con Tomás) |
| 3 | A-7 · Un solo comando `servio` | 2 | Jessica |
| 3 | A-9 · Docs al día | 1 | Cada uno su parte, Tomás junta |
| 4 | C-1 · Menos avisos falsos | 4 | Tomás |
| 4 | C-2 · Menos llamadas al modelo | 3 | Tomás (ver punto 2) |
| 4 | D-4 · Inteligencia de menú | 3 | Tomás o Matías (ver punto 2) |
| 4 | M-2 · Modo sombra y números antes/después | 3 | Matías (la parte de código); miden las tres casas |
| 4 | D-3 · Resumen al cerrar el servicio | 2 | Matías (ver punto 2) |
| 4 | Corte 25/11 | — | Todos |

Con eso Tomás queda con 14 días en dos semanas y Matías con 12. No entra sin cortar algo (punto 2) o sin la cuarta persona.

**Decisión:** ___

### 2. Qué se corta si no entra todo

**Contexto.** Para el corte hacen falta, según la tarjeta «Corte 25/11»: `main` estable, la demo grabada (F-1b + D-1 + avisos) y la hoja de métricas de M-2. Todo lo demás es deseable.

**Opciones.** Definir ahora el orden en que se sacan cosas, para no discutirlo el 20/11.

**Propuesta.** Orden de corte: primero C-2 (ahorra plata, no se ve en la demo), después D-3 (el resumen sale a mano de `/metricas`), después D-4 (se vende con los datos simulados de `generar_historial` que ya están). No se corta: F-1b, C-5, B-2, M-2, C-1.

**Decisión:** ___

### 3. Cámara armada en cada casa

**Contexto.** La tarjeta «Cámara armada en cada casa» de la semana 1 sigue abierta. Sin cámara no hay B-2 (escenarios grabados), ni B-3 (recortes de 3 casas), ni M-2 (comidas medidas en 3 casas). La guía es [montaje-camara.md](montaje-camara.md) y el chequeo `python -m servio_vision.framing --config …`.

**Propuesta.** Fecha límite 13/11 para que las tres casas tengan el pipeline corriendo en vivo con el chequeo de encuadre en verde. Quien no llegue, graba en la casa de otro.

**Decisión:** fecha ___ · Jessica ___ · Matías ___ · Tomás ___

---

## B · Producto y reglas

### 4. Qué aviso se espera en cada escenario de B-2

**Contexto.** B-2 pide grabar 8 escenarios «con sus avisos esperados». Los avisos esperados son una decisión de producto, no de código: hay que acordarlos antes de grabar, si no cada uno graba con una idea distinta. Hoy las reglas ([context/05-reglas-alertas.md](context/05-reglas-alertas.md)) son: plato vacío ≥ 1 min mientras la mesa sigue comiendo → «retirar platos»; todos vacíos → «mesa terminó»; plato lleno ≥ 10 min con gente → «sigue lleno».

**Propuesta** (lo que debería pasar; donde dice «hoy falla» es lo que C-1 tiene que arreglar):

| Escenario | Aviso esperado | Hoy |
|---|---|---|
| Comer todo, dos platos | «Mesa terminó» cuando los dos quedan vacíos; nada antes | Anda |
| Dejar un plato lleno 10 min | «Sigue lleno» a los 10 min, una vez | Anda |
| Plato compartido (fuente al medio + platitos) | Nada mientras alguien tenga comida en su plato; «retirar» por la fuente vacía es aceptable; «mesa terminó» recién sin comida en ningún plato | Hoy falla: ver punto 5 |
| Levantarse un rato (plato con comida, silla vacía) | Nada; «sigue lleno» no sale sin gente | Anda si el detector de personas ve la silla vacía |
| Dejar el pan / la guarnición | No cuenta como plato: nada | Hoy falla: la panera puede detectarse como plato |
| Retirar un plato y no el otro | «Retirar» por el plato vacío que queda si el otro sigue comiendo | Anda |
| Mover la mesa | Los mismos avisos que sin moverla | Anda con `--zones` fijas; a probar con mesas automáticas |
| Poca luz | El chequeo de encuadre avisa; sin avisos falsos | A probar |

**Decisión:** ___ (marcar los que cambian)

### 5. Plato compartido

**Contexto.** Hoy el sistema ve platos, no personas, y empareja cada plato con la comanda por orden de aparición. Con una fuente al medio y platitos, la fuente es el único plato que «cuenta»: cuando se vacía, sale «retirar» (aceptable) y puede salir «mesa terminó» mientras todos siguen comiendo (eso es el problema). Marcar en la carta qué platos son para compartir **queda descartado**: cualquier plato se puede pedir para compartir y si el restaurante no lo carga, anda mal.

**Opciones.**
- (a) Regla: «mesa terminó» mira **todos** los platos visibles, contados o no en la comanda. Barato, sin visión nueva.
- (b) Comportamiento: un plato cuya comida baja de a saltos mientras otros platos de la mesa suben es compartido; no dispara «retirar» mientras los demás tienen comida. Usa números que ya se guardan (`fill_ratio` por lectura).
- (c) Que el modelo diga en la misma llamada si el recorte parece fuente, tabla o bandeja. Ayuda a acertar desde el primer segundo.

**Propuesta.** (a) ya en C-1, (b) si el escenario de B-2 sigue fallando con (a), (c) solo si lo anterior no alcanza. Nada que tenga que cargar el restaurante.

**Decisión:** ___

### 6. Detector: YOLO entrenado o YOLO-World

**Contexto.** El YOLO del repo (`best.pt`) conoce una sola clase, «plato». No sabemos si detecta una tabla de madera, una fuente o un bowl. YOLO-World viene en la misma librería `ultralytics`, no se entrena, y detecta lo que se le pida por texto («plato, bowl, fuente, tabla, canasta de pan, vaso»). Es GPL, como ya lo es `ultralytics`. Alternativas más precisas (RF-DETR, licencia Apache) necesitan etiquetar fotos de cada tipo.

**Propuesta.** Un día dentro de C-1: correr YOLO-World sobre las sesiones de B-2 y comparar con `best.pt` en el tablero de números. Se adopta solo si no empeora el acierto por estado.

**Decisión:** ___

### 7. Escalado y recordatorios

**Contexto.** El diseño F-3 ([diseno-front.md](diseno-front.md), sección F-3) propone: «Voy» toma el aviso y frena recordatorios; «Listo» lo anota y, si la cámara no lo ve resuelto en 10 min, insiste una vez y escala; «No hacía falta» es la única que cierra a mano (`motivo_cierre = no_hacia_falta`); sin respuesta, escalado al encargado a los 9 min (cuando saldría el tercer recordatorio); el encargado se configura con `encargado_telegram` en `servio.toml`.

**Opciones.** Confirmar esos tiempos, o cambiarlos (por ejemplo escalar a los 5 min).

**Propuesta.** Los del diseño. Son los que C-5 implementa.

**Decisión:** ___

### 8. Dónde vive mozo ↔ Telegram y mesa ↔ mozo

**Contexto.** Hay dos textos que se contradicen. La tarjeta C-7 dice que la asignación mesa → mozo va en `servio.toml`. El diseño F-3 propone un campo `telegram` por mozo en `salon.json` (la comanda). Hay que elegir uno antes de C-5 y C-7.

**Propuesta.** Todo en `servio.toml`: `[mozos.juan] nombre = "Juan", telegram = "@juan", mesas = ["T1", "T2"]`. `salon.json` queda solo con los pedidos (es lo que en un local real viene del sistema de comandas, y ahí no hay usuarios de Telegram). Quien implemente C-7 corrige el doc F-3.

**Decisión:** ___

### 9. Canal al reloj y la prueba en un reloj real

**Contexto.** F-3 eligió Telegram (app de reloj desde junio 2026) condicionado a dos cosas que solo se prueban con un reloj: que la notificación del grupo se contesta sin abrir la app, y si se ven los botones del mensaje. Si falla, el plan es WhatsApp con D-6 (después del 25/11) con la misma mecánica de responder con una palabra.

**Opciones.** Alguien del equipo tiene Apple Watch o Wear OS; se consigue uno prestado; o se asume Telegram sin probar y la demo se hace con el celular.

**Propuesta.** Definir quién tiene reloj y probar antes del 17/11. Si nadie tiene, la demo del 25/11 se hace con el celular y la prueba en reloj pasa a «Después del 25/11» explícitamente.

**Decisión:** reloj: ___ · fecha: ___

### 10. `/metricas` y avisos a encargado y dueño

**Contexto.** [context/12-roles.md](context/12-roles.md) deja pendiente: `/metricas` hoy se responde en el grupo donde están los mozos, y encargado y dueño no reciben ningún aviso. F-3 propone escalados al encargado y D-3 el resumen al encargado y al dueño.

**Propuesta.** Con C-7: `/metricas` se responde solo a encargado y dueño; el mozo recibe solo sus avisos; el encargado recibe los escalados; el resumen de cierre (D-3) va a encargado y dueño.

**Decisión:** ___

### 11. Umbrales por local

**Contexto.** [context/13-metricas.md](context/13-metricas.md) deja pendiente si los umbrales (5 min de mesa sin atender, 2 min, etc.) van a `servio.toml`. El prototipo F-2 ya los muestra en «configuración» del dashboard como si vinieran del servidor.

**Propuesta.** Sí: van a `servio.toml` (A-1 ya lee el toml) con los valores de hoy como default. Editarlos desde la web es D-2, después del 25/11.

**Decisión:** ___

---

## C · Front

### 12. F-1b: sobre qué código y el modo video

**Contexto.** La tarjeta dice «rehacer `vista_demo.py`». Pero desde el PR #64 existe `servio_vision/static/salon.html` servido por `--web`, y el prototipo F-1 es una copia de ese código con los cambios listados en «Qué cambia respecto de lo implementado». Además, el diseño pide del servidor `frame.jpg` (el último frame difuminado) solo con `--web-video`, `answered_by` en cada aviso, y horas en UTC.

**Propuesta.** F-1b se hace sobre `salon.html` y `salon_web.py`, no sobre `vista_demo.py` (que queda como herramienta de demo con video). El modo video se sirve solo con `--web-video` y solo frames ya difuminados (lo que C-3 garantiza). `answered_by` lo agrega C-5; hasta entonces el panel no muestra quién respondió.

**Decisión:** ___

### 13. PR #65 (D-1, dashboard) y los cambios de F-2

**Contexto.** La tarjeta D-1 está en Done pero el PR #65 (`tomas/dashboards`) sigue abierto. El diseño F-2 se hizo sobre ese código y lista 4 cambios (héroe antes/después, serie semanal y costos dibujadas, frases de diagnóstico desde los datos, textos de «sin datos» por métrica).

**Opciones.** Mergear #65 como está y aplicar F-2 en un PR aparte; o aplicar F-2 en la rama antes de mergear.

**Propuesta.** Mergear #65 ya (después de su code review) para que F-1b y D-4 tengan el dashboard en `develop`. Los 4 cambios de F-2 van en un PR chico después, de quien tenga lugar (Jessica, con F-1b).

**Decisión:** ___

### 14. PR #29 y PR #42

**Contexto.** PR #29 (`matias/yolo-imgsz`, YOLO no agranda recortes chicos) está abierto desde el 17/10 sin movimiento. PR #42 es `develop → main`, la regla de que a `main` solo se mergea desde `develop`; `develop` le lleva 135 commits a `main`.

**Propuesta.** #29: Matías decide en la semana si lo rebasea sobre `develop` o lo cierra. #42: se mergea el 25/11 como el acto del corte (`main` congelado), con el CI en verde.

**Decisión:** #29 ___ · #42 ___

---

## D · Datos e infraestructura

### 15. Base compartida y `local_id`

**Contexto.** El Postgres de Supabase existe desde A-6. La URL la tiene Jessica en su `.env` y se pasa **por mensaje directo**, nunca en el grupo ni en el repo. Cada casa tiene que poner su `local_id` en su `servio.toml` para no mezclar datos. Sin `DATABASE_URL` se usa el SQLite local.

**Propuesta.** En la reunión Jessica pasa la URL por DM a cada uno. `local_id`: `casa-jessica`, `casa-matias`, `casa-tomas`. Las comidas de M-2 van todas al Postgres; los tests y las pruebas sueltas, al SQLite.

**Decisión:** ___

### 16. Comanda por casa

**Contexto.** La comanda (`fixtures/restaurante/`: mozos, mesas, pedidos, carta) es la misma para todas las casas. Con la base compartida, las tres casas escriben «Juan · Mesa 1». Para M-2 alcanza (el `local_id` separa), pero cada casa come platos distintos y el aviso de «sigue lleno» nombra el plato de la comanda.

**Propuesta.** Cada casa tiene su carpeta de restaurante (copia de `fixtures/restaurante/` con sus platos y sus nombres) y la apunta desde su `servio.toml`. Lo que es «configurar el local desde la web» sigue siendo D-2, después del 25/11.

**Decisión:** ___

### 17. Sesiones grabadas: carpeta y nombres

**Contexto.** B-1 graba sesiones (`--record DIR`: frames difuminados + `sesion.json` + `eventos.jsonl`). Van a la carpeta compartida del equipo, nunca al repo (AGENTS.md). Falta acordar dónde y cómo se nombran para que B-2, B-3 y `servio eval --sessions` las encuentren.

**Propuesta.** En la carpeta del equipo: `sesiones/<casa>/<AAAA-MM-DD>-<escenario>/`, con `escenario` uno de los 8 nombres de B-2 en minúsculas (`compartido`, `pan`, `poca-luz`…). Los avisos esperados, en el repo en `fixtures/expected/sesiones/<escenario>.alerts.json`.

**Decisión:** carpeta ___ · nombres ___

---

## E · Corte del 25/11

### 18. M-2: cuántas comidas por casa

**Contexto.** La hoja de métricas antes/después necesita comidas en modo sombra (sin avisos) y comidas con avisos, en al menos 3 casas, en la semana 4. Es tiempo de calendario de cada uno, no de código.

**Propuesta.** Cada casa: 2 comidas en sombra y 2 con avisos entre el 18/11 y el 23/11, de al menos 2 personas, y cada una anotada en una tabla compartida (fecha, casa, modo, cuántos, qué pasó).

**Decisión:** Jessica ___ · Matías ___ · Tomás ___

### 19. Demo del 25/11

**Contexto.** La tarjeta del corte pide una demo grabada con la vista de reconocimiento (F-1b), el dashboard (D-1) y los avisos.

**Propuesta.** Una comida real en una casa, grabada con pantalla + celular: el salón en vivo en los dos modos, un aviso que llega al celular (o al reloj, punto 9) y se contesta con «Voy», y el dashboard del encargado al final. Quién la graba y qué día se define en la reunión.

**Decisión:** casa ___ · día ___

### 20. Reunión fija

**Contexto.** Las semanas 3 y 4 tienen dependencias cruzadas (C-5 → D-4, B-2 → C-1, B-3 → C-2, F-1b ↔ #65). Sin un punto de sincronización, lo que se traba se entera tarde.

**Propuesta.** 30 minutos un día fijo por semana, con el Trello abierto: qué se mergeó, qué está trabado, qué se corta.

**Decisión:** día ___ · hora ___

---

## Al salir de la reunión

- [ ] Cada tarjeta de las semanas 3 y 4 tiene una persona asignada en el Trello (punto 1).
- [ ] Lo que se cortó está en «Después del 25/11» (punto 2).
- [ ] Este archivo tiene todos los «Decisión» completos y se mergea a `develop`.
- [ ] Las decisiones que cambian docs se anotan en la tarjeta que corresponde: 4 y 5 en C-1 y B-2, 7 y 8 en C-5 y C-7, 10 en C-7 y D-3, 11 en A-1/A-9, 12 en F-1b, 13 en D-1.
- [ ] Jessica pasó `DATABASE_URL` por DM a cada uno (punto 15).
- [ ] Los tres prototipos (F-1, F-2, F-3, links en [diseno-front.md](diseno-front.md)) están compartidos para que el equipo los abra.
