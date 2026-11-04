# Diseño del front (F-1, F-2, F-3)

Las tres pantallas de Servio, para que el equipo las revise antes de implementarlas (o de cambiar lo que ya está implementado). En vez de Figma, cada diseño es un **prototipo HTML navegable** en `docs/prototipos/`, publicado como página para verlo desde el celular, más las decisiones escritas acá. Los roles salen de [12-roles.md](context/12-roles.md) y las métricas de [13-metricas.md](context/13-metricas.md).

| Pantalla | Quién la ve | Prototipo | Implementación |
|---|---|---|---|
| F-1 · Reconocimiento (el salón) | Mozo (principal), encargado, dueño | [prototipos/f1-reconocimiento.html](prototipos/f1-reconocimiento.html) · [publicado](https://claude.ai/artifact/ABtN5PdFmwAmZUZnKyWsAw) (es adonde va el botón «Ver el salón» del prototipo F-2 cuando no se abre desde el repo) | `vision/servio_vision/static/salon.html`, `/<local>/salon` (PR #64) |
| F-2 · Dashboard | Encargado, dueño | [prototipos/f2-dashboard.html](prototipos/f2-dashboard.html) | `vision/servio_vision/static/dashboard.html`, `/<local>/dashboard` (rama `tomas/dashboards`, D-1) |
| F-3 · Notificaciones | Mozo (reloj), encargado, dueño | [prototipos/f3-notificaciones.html](prototipos/f3-notificaciones.html) | `vision/servio_vision/alerts/messages.py`, `vision/servio_vision/alerts/bot.py` (Telegram) |

Los prototipos son documentos HTML completos (abrirlos desde el repo anda, también en un celular); para publicarlos como artifact, `vision/tools/publicar_prototipo.sh` les saca el esqueleto (la plataforma pone su propio `charset` y `viewport` con `viewport-fit=cover`, no se pierde el safe-area). **Ninguna imagen real de un local va al repo**: la «cámara» del prototipo F-1 es un SVG dibujado con las mismas coordenadas que los datos de ejemplo (sin imágenes en el repo, regla 6 de AGENTS.md).

## F-1 · Reconocimiento: lo que «ve» Servio

**La idea:** la pantalla de un auto. Una sola superficie oscura, el salón grande y en el centro, y lo que importa (platos, estados, avisos) dibujado encima a medida que la cámara lo detecta. El mozo la mira de reojo con la luz de un restaurante: contraste alto, pocos textos, cuatro colores fijos.

### Dos modos, un mismo salón

- **Reconstrucción** (el default, y el único que hay que mostrar a un cliente o al público): no se muestra el video. Se dibuja la mesa desde arriba con sus sillas, una figura por comensal y un ícono por plato. **Resuelve la privacidad de la pantalla**: ninguna cara, ninguna persona reconocible, y se puede proyectar en el salón. Es lo que ya corre en `/<local>/salon`.
- **Video**: la imagen de la cámara, **ya con las caras difuminadas en la computadora** (C-3), con las capas encima: el recuadro, la etiqueta y el aviso abierto de cada mesa y, por plato con caja detectada, el anillo del estado y una etiqueta con el nombre del plato. Sirve para armar la cámara, para confiar en lo que detecta y para depurar; no para dejarlo puesto. Solo dibuja lo que la cámara ve de verdad.

El cambio de modo es un segmento en la barra de estado. Los dos modos comparten los datos de `/<local>/salon/estado`; el video necesita además el **último frame difuminado**, que hoy no se guarda en ningún lado sin `--record`. La forma simple: el loop de `app.run` ya tiene el frame difuminado en la mano (el mismo que graba `_record`); se lo pasa a un método nuevo `SalonFeed.keep_frame(frame)`, que guarda los bytes de `to_jpeg(frame)` **en memoria** (un solo frame, se pisa en cada lectura), y `salon_web.py` los sirve en `/<local>/salon/frame.jpg`. (`on_reading` no sirve para esto: recibe las observaciones, no el frame.) Así el modo video anda sin grabar a disco, que sigue siendo solo con `--record`. El frame ya viene difuminado con cualquier fuente (`capture.open_source` arma el difuminado también para un video o una carpeta), así que el modo video se puede probar con el video de demo; lo único que lo gobierna es `--web-video`. **El endpoint va apagado por defecto** (`--web-video` para prenderlo): aunque el frame no tenga caras, servirlo en la red del local es, en la práctica, un video en vivo para cualquiera en el wifi; se prende para armar la cámara y se apaga después. Sin el flag, el segmento «Video» no se muestra.

### Los cuatro estados de un plato

| Estado | Color | Cómo se dibuja |
|---|---|---|
| Lleno | verde `#3EE08F` | anillo casi completo, halo verde |
| Comiendo | amarillo `#F2C94C` | anillo a la mitad |
| Por terminar | naranja `#FF8A3D` | anillo corto |
| Vacío | rojo `#FF4D61` | sin anillo, plato con restos, punto rojo arriba |

El **arco del anillo es cuánto queda** (`fill_ratio`), así el estado se lee aunque no se distinga el color. Cuando un plato cambia de estado **late** una vez (`pop`, 1,2 s) y queda quieto; un plato nuevo entra latiendo igual, y uno que se retiró simplemente ya no está (sin animación de salida: el dibujo se rehace entero en cada lectura). Nada parpadea en reposo salvo el punto rojo de «en vivo». Con `prefers-reduced-motion`, nada se anima.

### Mesas y avisos

- Cada mesa lleva su **etiqueta flotante** arriba: nombre, mozo (si no es el que mira) y estado de la mesa (libre, servida, comiendo, por terminar, terminó). La mesa elegida tiene un recuadro punteado azul.
- **Las mesas del mozo que mira van marcadas** (borde azul suave): el mozo ve todas las mesas, pero encuentra las suyas de un vistazo. Quién mira sale del rol/usuario (R-1); hasta que haya login, la pantalla del salón muestra todas sin marca.
- Una mesa con **aviso abierto** lleva una etiqueta de color debajo («MESA 2: PLATO VACÍO · 4 MIN»). El panel de la derecha lista los avisos abiertos, el más viejo arriba, con quién lo tomó («Voy · Juan») o, si nadie respondió, en naranja. Los botones para responder **no están acá**: se responde desde el reloj (F-3); la pantalla es para mirar.
- **Qué pasa cuando no llegan lecturas** (lo que `salon.html` ya hace y se mantiene): pasadas 3 lecturas + 10 s sin novedades, el punto rojo de «en vivo» deja de latir, se pone naranja y dice «Sin lecturas»; sin conexión con el servidor, «Reconectando». En el dock, «Sin lecturas desde hace X». En el celular el dock no muestra ese texto, pero el punto y «Sin lecturas» arriba sí. El prototipo tiene «Simular un corte».
- **La mesa elegida** al entrar es la del aviso abierto **más viejo** (el que hay que atender, el mismo que encabeza el panel; `salon.html` hoy elige el más nuevo); si no hay avisos, la primera ocupada. Si la mesa elegida desaparece (se redibujaron las zonas), se vuelve a elegir con la misma regla. Es lo que ya hace `salon.html` (`pickTable`).
- Panel derecho: la mesa elegida (platos, nombre del pedido, barra de cuánto queda, desde cuándo están sentados), los avisos y «Ahora» (mesas ocupadas, platos en mesa, avisos abiertos, tiempo promedio en mesa; el promedio se calcula en el navegador desde el `since` de cada mesa, no hace falta nada nuevo del servidor).
- Dock abajo: la leyenda de los cuatro estados con el conteo de platos en cada uno, y hace cuánto llegó la última lectura.

### En el celular del mozo

Mismo contenido, otra composición: el salón arriba a todo el ancho, debajo la mesa elegida y los avisos, y el dock de estados **pegado al borde de abajo** (sticky, con `safe-area-inset-bottom`) como barra de mesas activas. Los números de «Ahora» en una fila de cuatro. Tocar una mesa en el salón la elige. En un celular sale así sola (ancho del contenedor hasta 620 px); el botón «Celular» del prototipo es para verlo desde una computadora, con un marco del alto de un celular y scroll propio, así el dock se pega al borde del marco.

### Qué cambia respecto de lo implementado (`salon.html`)

1. El modo **video** (hoy solo hay reconstrucción) y el segmento para cambiar.
2. La **etiqueta de aviso** sobre la mesa (hoy el aviso solo está en el panel).
3. La **marca de las mesas del mozo** (necesita saber quién mira: login de R-1).
4. En el aviso, **quién lo tomó** («Voy · Juan») o que nadie respondió (necesita C-5).
5. El dock sticky en celular.
6. En el panel, los avisos del **más viejo al más nuevo** (hoy `salon.html` pone el más nuevo arriba): el que lleva más tiempo esperando es el que hay que atender.

Lo que necesita del servidor, además de lo que `/estado` ya manda (`fill_ratio`, `since` y `created_at` en ISO, `dish` que puede ser `null`, `message`, `waiter` por mesa):

- `GET /<local>/salon/frame.jpg`: el último frame difuminado (modo video), solo con `--web-video` (ver arriba).
- `answered_by` en cada aviso (el nombre que ya usa `alert_feedback` en el log): quién respondió «Voy» (C-5); `null` si nadie respondió todavía. Con `null` el panel muestra «Nadie respondió · mozo» (el mozo sale de la mesa del aviso; el aviso no lo trae); si el campo no viene (el servidor de hoy), no dice nada: no sabe.
- Las horas (`since`, `created_at`, `ts`) vienen en UTC y la pantalla las muestra en la hora del navegador, que es la del local.
- Quién mira (para marcar sus mesas): el usuario de R-1. Sin login, todas las mesas sin marca.

Lo demás (colores, tipografías Barlow y Barlow Condensed, el dibujo de mesas, sillas, personas y platos, la animación de cambio) es lo que ya está: el prototipo es una **copia** del código de dibujo de `salon.html` al día de hoy (`plate()`, `person()`, `chair()`, `renderFloor()`, colores, `FILL`); si se tocan, `salon.html` manda (este PR le arregla una cosa: `.changed` con `transform-box: fill-box; transform-origin: center`, sin eso el `pop` corría el plato en vez de hacerlo latir). No comparten archivo a propósito: el prototipo se publica solo, sin el servidor; y los datos de ejemplo del prototipo tienen la forma real de `/estado`, así que lo que se implemente a partir de él usa los campos que existen.

**Hecho cuando** (de la tarjeta): diseño con los dos modos, los 4 estados y una mesa con aviso abierto, en pantalla grande y en celular, revisado por el equipo. El prototipo cubre todo eso; falta la revisión.

## F-2 · Dashboard: la pizarra del encargado y del dueño

Prototipo: [prototipos/f2-dashboard.html](prototipos/f2-dashboard.html). Parte de lo que Tomás ya implementó en D-1 (`vision/servio_vision/static/dashboard.html`, servido en `/<local>/dashboard?rol=…` por el mismo servidor que el salón): **mismo estilo y misma forma de datos**, así lo que se decida acá se aplica sobre ese archivo y no en otro. Las métricas son las 17 de [13-metricas.md](context/13-metricas.md); cada tarjeta lleva `data-metric` con el id de la métrica, y la barra de revisión cuenta cuántas tiene cada vista.

### Estilo «pizarra», primero celular

Una sola columna de 480 px como máximo, bloques de color plano, **números enormes** y una línea de texto que dice qué hacer con el número («Martín es el que más tarda y el que más mesas tiene: sacale una»). Sin gráficos decorativos: las dos únicas series (horas pico, semana a semana) son barras planas a escala, con la peor franja o la última semana en color. Tema claro único, a propósito: es la pizarra que el dueño mira en la reunión y manda por WhatsApp como captura. Tipografía Bricolage Grotesque, la de D-1.

### Dos roles, dos pizarras

- **Encargado** (lo que pasa en este turno): tiempo de respuesta como héroe, sin atender > 5 min, mesas olvidadas, avisos abiertos con quién respondió, tabla de mozos (mesas, respuesta, retiro, olvidadas), rotación, siguiente plato, horas pico, platos que vuelven, y el resumen al cerrar (llega por Telegram). Botón «Ver el salón en vivo» arriba: la pizarra y el salón son la misma app.
- **Dueño** (si vale la pena): pestañas **Resultados / Carta / Turno / Config**. Resultados abre con el héroe «**Tus mesas esperan X min menos**» (antes/después del modo sombra), después las cinco cifras del turno (respuesta, sin atender, olvidadas, rotación, platos que vuelven), la serie semana a semana, lo que vuelve a la cocina en pesos y la adopción de los mozos. Carta: tabla por plato (vuelve, ritmo, sobra; ritmo y sobra solo existen con datos en vivo, el historial simulado no los tiene) con una frase de diagnóstico del plato que más vuelve. Turno: lo mismo que ve el encargado. Config: costo de Servio por mes, umbrales del local, roles y usuarios.

Qué ve cada rol sale del servidor (`vistas` y `metricas` en la respuesta de `/datos`: la forma exacta de `dashboard.py`, que el prototipo usa tal cual, con `metricas{…}`, `pendientes{…}` y `configuracion{umbrales, roles}`), no del front: el prototipo tiene el segmento Encargado/Dueño solo para revisar.

### Cuando todavía no hay datos

Toda métrica tiene su lugar desde el primer día, pero **no se inventa**: si el servidor manda `null`, la tarjeta sale punteada con el porqué («Se muestra a partir del día 14», «Necesita servicios en modo sombra», «Ningún plato volvió lleno todavía»). D-1 ya dibuja la tarjeta punteada y manda `pendientes` con un texto por métrica, pero hoy esos textos dicen qué tarea la trae («Llega con D-5 y M-2»); el diseño pide que digan **qué tiene que pasar en el local** («Se muestra a partir del día 14») y que toda métrica en `null` tenga el suyo (punto 4 de «Qué cambia»). El prototipo agrega el botón «Primer día» para ver cómo queda un local recién instalado: avisos en cero, sin histórico ni antes/después, y el resto con sus textos. Ninguna tarjeta divide por cero ni muestra «NaN».

### Qué cambia respecto de lo implementado (`dashboard.html`, D-1)

1. El héroe **antes/después** del dueño con la frase en minutos menos (hoy sale punteado: necesita D-5 y M-2).
2. La serie **semana a semana** y la tarjeta de **costos** dibujadas (hoy punteadas: D-4 y C-2); cuando lleguen los datos, el prototipo es la referencia.
3. La frase de diagnóstico debajo de cada tabla (mozos, carta) sale de los datos (el más lento, el que más vuelve), nunca de un texto fijo; en producción la arma el servidor.
4. Textos de «todavía sin datos» por métrica, no uno genérico.

Lo demás (colores, tipografía, tarjetas, tablas, barras, armado por rol, botón al salón) es lo que ya está en D-1 y el prototipo lo copia tal cual.

**Hecho cuando** (de la tarjeta): diseño con las dos vistas, todas las métricas de M-1 ubicadas y revisado por el equipo. El prototipo cubre las 17 en la vista del dueño (10 en la del encargado); falta la revisión.

## F-3 · Notificaciones: el aviso en la muñeca del mozo

Prototipo: [prototipos/f3-notificaciones.html](prototipos/f3-notificaciones.html): los cuatro avisos como se ven en el reloj y en el celular, cómo se responde y qué le llega a cada rol. Lo que ya corre es C-5 (`vision/servio_vision/alerts/messages.py`, `feedback.py`, `bot.py`): los textos de los avisos y las respuestas con botones en Telegram.

### Canal: Telegram ahora, WhatsApp cuando esté D-6

- **Telegram** es lo que hay y desde junio de 2026 tiene app oficial en Apple Watch y Wear OS (fuentes en el prototipo): la notificación llega al reloj y se contesta desde ahí con las respuestas rápidas del reloj, dictado o teclado. **Los botones del mensaje no aparecen en la notificación del reloj**, solo en el celular: por eso el diseño responde con una palabra.
- **WhatsApp** llega con D-6 (después del 25/11); sus notificaciones en el reloj se comportan igual (texto y respuesta rápida), así que el diseño no cambia de canal a canal.
- **App propia con push**: no por ahora. Costaría una app en dos tiendas para resolver lo mismo que ya resuelven las respuestas rápidas.

**Pendiente, a mano**: probar en un reloj real (Apple Watch o Wear OS) que el aviso llega y que la respuesta rápida vuelve al grupo. No se puede verificar sin el reloj; el diseño asume lo que documentan Telegram y las notas de prensa.

### El aviso en el reloj: una línea y dos o tres respuestas

La primera línea tiene que alcanzar leída de reojo: **mesa · qué** («Mesa 2 · retirar platos», «Mesa 1 · terminó», «Mesa 3 · sorrentinos sigue lleno»). Debajo, hace cuánto y el detalle. Las respuestas rápidas del reloj se configuran con las tres palabras: **Voy**, **Listo**, **No hacía falta** (con «sigue lleno», los motivos del plato). En el celular el mismo aviso trae los botones de hoy; el texto es el de `messages.py` (emoji + mozo + mesa), que ya empieza con lo importante.

| Aviso | Reloj (línea 1 · línea 2) | Respuestas | A quién |
|---|---|---|---|
| Plato vacío | Mesa 2 · retirar platos · hace 4 min, Lomo con papas | Voy · Listo · No hacía falta | mozo de la mesa |
| Mesa terminó | Mesa 1 · terminó · hace 1 min, 2 platos | Voy · Listo · No hacía falta | mozo de la mesa |
| Sigue lleno | Mesa 3 · sorrentinos sigue lleno · hace 14 min, ¿está todo bien? | Voy · motivo (Frío, No le gustó, …) · Otro | mozo de la mesa |
| Escalado | Mesa 2 · sin atender 9 min · Juan no respondió, 2 avisos | Voy · Visto | encargado |

### Responder con una palabra: lo que le pedimos a C-5

Hoy el bot solo toma un texto si es **respuesta a** su pregunta («Otra respuesta»). Para el reloj hace falta que entienda un mensaje suelto en el grupo:

1. Normalizar (minúsculas, sin tildes ni puntuación) y reconocer: `voy | ya voy | dale | ok | 👍` → **Voy**; `listo | hecho | ya esta | ✅` → **Listo**; `no | no hacia falta | falso | ❌` → **No hacía falta**. Cualquier otro texto, solo si hay un «sigue lleno» abierto, es un **motivo**.
2. Buscar el **aviso abierto más reciente de las mesas de ese mozo** (quién escribe ↔ mozo: hoy por nombre de Telegram, con R-1 por usuario). Si tiene más de uno abierto, el bot pregunta «¿Mesa 2 o Mesa 3?» y acepta el número. Si no tiene ninguno, contesta «No tenés avisos abiertos».
3. **Voy** marca el aviso como tomado (`answered_by`, el campo que pide F-1 para el panel) y frena los recordatorios; **Listo** lo cierra y cuenta el tiempo; **No hacía falta** lo cierra como falso aviso (métrica de adopción); el motivo queda en `alert_feedback` como hoy.
4. Responder citando el mensaje sigue andando igual que ahora.

### Qué le llega a cada rol

- **Mozo**: los avisos de **sus mesas** y sus recordatorios; nada de las mesas de otros. Hoy todos van al grupo con mención («🧹 Juan · Mesa 2: …»); con R-1, un chat privado por mozo, que es lo que hace que al reloj le llegue solo lo suyo.
- **Encargado**: los **escalados**: un aviso sin respuesta después del segundo recordatorio, o una mesa que pasó el límite sin atender. Puede tomarlo con «Voy».
- **Dueño**: el **resumen al cerrar** el servicio (lo que ya manda D-1 como `resumen_cierre`) y nada en el momento.

### Qué cambia respecto de lo implementado (C-5)

1. Respuestas con **una palabra suelta** (sin «responder a») y la elección del aviso por mozo (punto anterior).
2. **Voy** como respuesta en todos los avisos, no solo motivos en «sigue lleno»; `answered_by` en el aviso.
3. El **escalado al encargado** como aviso propio (hoy el recordatorio se repite al mismo grupo).
4. Un chat por mozo (necesita R-1); hasta entonces, el grupo con mención.

**Hecho cuando** (de la tarjeta): canal decidido ✔ (Telegram, WhatsApp con D-6), diseño de cada aviso con sus botones ✔, probado en un reloj real: pendiente, a mano.
