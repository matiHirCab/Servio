# Diseño del front (F-1, F-2, F-3)

Las tres pantallas de Servio, para que el equipo las revise antes de implementarlas (o de cambiar lo que ya está implementado). En vez de Figma, cada diseño es un **prototipo HTML navegable** en `docs/prototipos/`, publicado como página para verlo desde el celular, más las decisiones escritas acá. Los roles salen de [12-roles.md](context/12-roles.md) y las métricas de [13-metricas.md](context/13-metricas.md).

| Pantalla | Quién la ve | Prototipo | Implementación |
|---|---|---|---|
| F-1 · Reconocimiento (el salón) | Mozo (principal), encargado, dueño | [prototipos/f1-reconocimiento.html](prototipos/f1-reconocimiento.html) · [publicado](https://claude.ai/artifact/ABtN5PdFmwAmZUZnKyWsAw) (privado hasta que Jessica lo comparta; es adonde va el botón «Ver el salón» del prototipo F-2 cuando no se abre desde el repo) | `vision/servio_vision/static/salon.html`, `/<local>/salon` (PR #64) |
| F-2 · Dashboard | Encargado, dueño | [prototipos/f2-dashboard.html](prototipos/f2-dashboard.html) | `vision/servio_vision/static/dashboard.html`, `/<local>/dashboard` (rama `tomas/dashboards`, D-1) |
| F-3 · Notificaciones | Mozo (reloj), encargado, dueño | [prototipos/f3-notificaciones.html](prototipos/f3-notificaciones.html) · [publicado](https://claude.ai/artifact/W4VtBM51QoVNMTYTnidENe) (privado hasta que Jessica lo comparta) | `vision/servio_vision/alerts/messages.py`, `vision/servio_vision/alerts/bot.py` (Telegram) |

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

Hecho en F-1b (sobre `salon.html` y `salon_web.py`; `vista_demo.py` queda como herramienta de demo con video):

1. ✅ El modo **video** y el segmento para cambiar. El segmento solo aparece si el servidor ofrece el frame (`"video": true` en `/estado`, que sale de `--web-video`); el modo elegido se recuerda en el navegador. Una cámara vertical no se dibuja más alta que la pantalla (el video se acota al 72 % del alto y se centra); las etiquetas de dos platos vecinos no se pisan (la segunda baja debajo del anillo). En la reconstrucción, el salón tampoco queda más alto que ancho: se acota y se centra.
2. ✅ La **etiqueta de aviso** debajo de la mesa (el aviso más viejo de esa mesa), en los dos modos.
3. ✖ La **marca de las mesas del mozo**: no va. La pantalla es del encargado (decisión 1), el mozo no la mira; todas las mesas sin marca, y la etiqueta de la mesa lleva el mozo siempre («Mesa 1 · Juan»).
4. ✅ En el aviso, **quién lo tomó** («Voy · Juan») o «Nadie respondió · mozo»: la página lo muestra si `answered_by` viene y no dice nada si no viene. El servidor lo agrega C-5.
5. ✅ El dock sticky en celular; los números de «Ahora» en una fila de cuatro.
6. ✅ En el panel, los avisos del **más viejo al más nuevo**, y la mesa elegida al entrar es la del aviso más viejo (`pickTable`). Sin avisos: «Nada pendiente: todas las mesas atendidas.»

Lo que el servidor manda, además de lo que `/estado` ya mandaba (`fill_ratio`, `since` y `created_at` en ISO, `dish` que puede ser `null`, `message`, `waiter` por mesa):

- `"video"` en la foto: si la página puede pedir `frame.jpg`.
- `GET /<local>/salon/frame.jpg`: el último frame difuminado (modo video), solo con `--web-video` (ver arriba); sin el flag, 404. El loop de `app.run` se lo deja a `SalonFeed.keep_frame` antes de procesar la lectura y `on_reading` lo publica junto con la foto de esa lectura: el frame que sirve `frame.jpg` y las capas que la página dibuja encima son siempre del mismo cuadro. Se codifica a JPEG recién cuando alguien lo pide (una vez por lectura; con un solo navegador en reconstrucción no se codifica nada). La página lo pide con `?v=<version>` para que el navegador no reuse el anterior.
- `answered_by` en cada aviso (el nombre que ya usa `alert_feedback` en el log): quién respondió «Voy» (C-5); `null` si nadie respondió todavía. Con `null` el panel muestra «Nadie respondió · mozo» (el mozo sale de la mesa del aviso; el aviso no lo trae); si el campo no viene (el servidor de hoy), no dice nada: no sabe. **Pendiente de C-5.**
- Las horas (`since`, `created_at`, `ts`) vienen en UTC y la pantalla las muestra en la hora del navegador, que es la del local.

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

Prototipo: [prototipos/f3-notificaciones.html](prototipos/f3-notificaciones.html): los cuatro avisos como se ven en el reloj y en el celular, cómo se responde y qué le llega a cada rol. Lo que ya corre es **A-4** (`vision/servio_vision/alerts/messages.py`, `feedback.py`, `bot.py`): los textos de los avisos y las respuestas con botones en Telegram. **C-5** es la tarjeta pendiente que trae «Voy / Listo / No hacía falta»: lo que esta sección le pide va ahí.

### Canal: Telegram ahora, WhatsApp cuando esté D-6

- **Telegram** es lo que hay y desde junio de 2026 tiene app oficial en Apple Watch y Wear OS (fuentes en el prototipo): las notas dicen que la app del reloj muestra los chats y contesta con texto o voz. Lo que **no** dicen, y el diseño asume hasta probarlo: que la notificación se puede contestar sin abrir la app (respuestas rápidas del sistema o dictado) y que **los botones del mensaje no aparecen en la notificación del reloj**. Con cualquiera de las dos, responder con una palabra es lo que anda seguro: por eso el diseño responde así, y los botones quedan para el celular.
- **WhatsApp** llega con D-6 (después del 25/11); sus notificaciones en el reloj se comportan igual (texto y respuesta rápida), así que el diseño no cambia de canal a canal.
- **App propia con push**: no por ahora. Costaría una app en dos tiendas para resolver lo mismo que ya resuelven las respuestas rápidas.

**Pendiente, a mano**, exactamente las dos suposiciones de arriba: en un reloj real (Apple Watch o Wear OS), (1) ¿la notificación del grupo se contesta desde el reloj sin abrir la app, y con qué (respuesta rápida, dictado, teclado)? (2) ¿se ven o no los botones del mensaje? Si se ven y se pueden tocar, el diseño mantiene la palabra suelta igual (sirve para dictado) y suma el botón.

### El aviso en el reloj: una línea y dos o tres respuestas

La primera línea tiene que alcanzar leída de reojo: **mesa · qué** («Mesa 2 · retirar platos», «Mesa 1 · terminó», «Mesa 3 · sin tocar»). Debajo, hace cuánto y el detalle. Telegram manda **el mismo texto** al reloj y al celular, así que esto es un cambio de `messages.py` (hoy: «🧹 Juan · Mesa 2: hay platos vacíos para retirar.», sin hace cuánto ni plato): la primera línea pasa a ser «🧹 Mesa 2 · retirar platos» y la segunda «hace 1 min · Lomo con papas · Sofía», con la mención del mozo al final para que no tape lo importante (`mention_waiter` hoy lo pone adelante; `partial_text` pone el ✅ al final de cada aviso: con dos líneas va al final de la primera). El «hace cuánto» queda fijo en el mensaje: al abrirse vale el umbral de cada tipo (plato vacío ≈1 min, sigue lleno 10 min: `config.py`); «terminó» no tiene umbral y abre al instante, así que su segunda línea dice «recién»; los recordatorios traen el suyo. Lo que se muestra en el reloj y lo que lee el celular es **el mismo mensaje**, palabra por palabra; el prototipo usa ese texto en los dos. La primera línea tiene como mucho **24 caracteres** después del emoji: «Mesa 2 · retirar platos», «Mesa 1 · terminó», «Mesa 3 · sin tocar», «Mesa 2 · sin atender»; el resto va a la segunda. En un reloj de 45 mm entra en una línea; en uno de 41 mm las más largas se parten en dos, y por eso la mesa va primero y el qué después: lo que se corta es lo menos importante. Si una mesa abre dos avisos en la misma evaluación (solo puede ser «platos vacíos» + «sigue lleno» de otro plato; `engine.py` no abre «platos vacíos» en una mesa que termina), van en un solo mensaje con las dos líneas de cada aviso, uno debajo del otro, y el reloj muestra el primero: por eso «Listo» en esa mesa pregunta «¿los platos vacíos o el plato lleno?». Las respuestas rápidas del reloj (una lista fija por usuario, no por mensaje) se configuran con las tres palabras: **Voy**, **Listo**, **No hacía falta**; el motivo de un «sigue lleno» se dicta o se escribe. En el celular el aviso trae botones: hoy solo los tiene «sigue lleno» (los motivos del plato); plato vacío y mesa terminó ganan **Voy / Listo / No hacía falta** con C-5.

| Aviso | Reloj (línea 1 · línea 2) | Respuestas | A quién |
|---|---|---|---|
| Plato vacío | Mesa 2 · retirar platos · hace 1 min, Lomo con papas, Sofía | Voy · Listo · No hacía falta | mozo de la mesa |
| Mesa terminó | Mesa 1 · terminó · recién, 2 platos, Juan | Voy · Listo · No hacía falta | mozo de la mesa |
| Sigue lleno | Mesa 3 · sin tocar · hace 10 min, Milanesa napolitana, Martín, ¿está todo bien? | Voy · Listo (el bot pregunta «¿Qué pasó?» y el motivo se dicta o se toca en el celular) · No hacía falta. En el celular: Voy, los motivos del plato (incluido «Está todo bien») y No hacía falta | mozo de la mesa |
| Escalado | Mesa 2 · sin atender · hace 9 min, Sofía no respondió a 3 mensajes | Voy · Visto (lo vio, no lo toma: frena los recordatorios al encargado) | encargado, reloj y celular |

### Responder con una palabra: lo que le pedimos a C-5

Hoy el bot solo toma un texto si es **respuesta a** su pregunta («Otra respuesta»). Para el reloj hace falta que entienda un mensaje suelto en el grupo:

1. Normalizar (minúsculas, sin tildes ni puntuación) y comparar **el mensaje entero** con la lista, nunca por prefijo: `voy | ya voy | 👍` → **Voy**; `listo | hecho | ya esta | ✅` → **Listo**; `no hacia falta | falso | ❌` → **No hacía falta**; `visto` → **Visto** (solo escalados). Mientras los avisos vayan al grupo compartido, **no** se aceptan palabras de charla («ok», «dale», «no» solo): un «no» a un compañero cerraría un aviso. «No le gustaron» no es «no»: es un motivo. Un texto libre es un **motivo** solo en dos casos: citando el aviso o la pregunta del bot (como hoy), o como **el siguiente mensaje del mozo** después de que el bot le preguntó «¿Qué pasó?» (tras un «Listo» a un «sigue lleno», durante 2 min). Cualquier otro texto se ignora en silencio: el grupo también se usa para charlar y una pregunta a un compañero no puede quedar como motivo del plato. Un mensaje de alguien que no es un mozo mapeado: ver el punto 4.
2. Buscar **las mesas con aviso abierto de ese mozo** (quién escribe ↔ mozo: **hoy no existe**, `feedback.py` solo guarda el nombre de Telegram junto a la respuesta; C-5 lo arma con un campo `telegram` por mozo en `salon.json` (nombre o usuario, comparado normalizado) y C-7 lo pasa a usuario). Si es una sola, la respuesta es para esa; si son varias (en `salon.json` cada mozo tiene una mesa, en un local real tiene varias), el bot pregunta «¿Mesa 2 o Mesa 3?» y acepta el número (nunca elige solo). Para el encargado, «sus avisos» son los escalados abiertos, con la misma regla; el escalado no es un aviso aparte en la base: es un mensaje al encargado atado al aviso de la mesa, se cierra cuando se cierra ese aviso, y un «Voy» del mozo de la mesa también lo toma (frena los recordatorios al encargado). **Voy** vale para todos los avisos abiertos de esa mesa (los toma todos). **Listo** y **No hacía falta** valen para **un** aviso: si la mesa tiene uno solo abierto, ese; si tiene más de uno de distinto tipo (pasa cuando «platos vacíos» ya estaba abierto y después la mesa termina, o con un «sigue lleno» de otro plato), el bot pregunta «Mesa 2: ¿los platos o la mesa?» y acepta «platos» / «mesa» / «plato lleno». Si no tiene ninguna mesa con avisos, contesta «No tenés avisos abiertos».
3. Ninguna respuesta cierra un aviso por su cuenta: **el aviso lo cierra la cámara**, como hoy (`05-reglas-alertas.md`), así las métricas de retiro y rotación (`motivo_cierre = 'retirados'`) no cambian. Lo que cambia es qué se anota y cuándo se insiste: **Voy** marca el aviso como tomado (`answered_by`, el campo que pide F-1 para el panel) y frena los recordatorios. **Listo** marca que el mozo dice que ya lo hizo (`done_at`): si la cámara lo confirma, cierra como siempre; si a los 10 min sigue igual, le vuelve a avisar una vez («Mesa 1 sigue sin retirar») y después escala; el **tiempo de respuesta** corre hasta el «Voy» (`13-metricas.md`). En un «sigue lleno», «Listo» hace que el bot pregunte «¿Qué pasó?» y el motivo queda en `alert_feedback` como hoy (si la pregunta vence o se cancela sin motivo, el «Listo» vale como en cualquier aviso: a los 10 min insiste una vez y escala); «Está todo bien» (o cualquier motivo de la carta) es un aviso real contestado, y después de un motivo el aviso **no se repite** aunque el plato siga lleno: queda esperando a que la cámara lo vea retirado (hoy el tope son dos recordatorios; con motivo, cero más). **No hacía falta** es lo único que cierra a mano: cierre con un valor nuevo en `motivo_cierre` (la base) y `close_reason` (el log), `no_hacia_falta`, al lado de `retirados` y `servido` (`05-reglas-alertas.md`); `SQL_ADOPTION` lo cuenta, cuenta como falso aviso (`adopcion`), no entra en los tiempos, y silencia **ese plato** (por `track_id`) en plato vacío y sigue lleno, o **esa mesa hasta que se libere** en mesa terminó: lo que la cámara ve mal lo va a seguir viendo, pero otro plato de la misma mesa sí tiene que avisar. Esto cambia reglas escritas: `05-reglas-alertas.md` (los botones ahora frenan recordatorios y uno cierra; hay escalado), `03-contratos.md` (`answered_by`, `done_at`, el `close_reason` nuevo). Hay que actualizarlos al implementarlo.
4. **Precedencia**: si el bot le hizo una pregunta al mozo («¿Mesa 2 o Mesa 3?», «¿los platos o la mesa?», «¿Qué pasó?»), su **siguiente mensaje dentro de 2 min** responde esa pregunta si encaja (un número, «platos» / «mesa», o un motivo); si es una palabra clave que no encaja («Voy» cuando preguntó «¿Qué pasó?»), la pregunta se cancela (el «sigue lleno» queda sin motivo) y la palabra se procesa como siempre. Pasados los 2 min la pregunta vence: un «2» suelto una hora después no elige nada. Responder citando el mensaje sigue andando igual que ahora. Alguien que no es un mozo mapeado y escribe una palabra clave recibe, una vez por hora, «No te reconozco como mozo» (si no, no se distingue de un bot caído); dos mozos con el mismo nombre o un nombre distinto en Telegram se resuelven cargando el usuario de Telegram en el campo `telegram` del mozo; C-7 lo hace obligatorio.

### Qué le llega a cada rol

- **Mozo**: los avisos de **sus mesas** y sus recordatorios; nada de las mesas de otros. Hoy todos van al grupo con mención («🧹 Juan · Mesa 2: …»); con **C-7** (avisos por mozo: mención o chat privado; toma el mozo de cada mesa de `servio.toml`, cambia `Alert` y `03-contratos.md`), un chat privado por mozo, que es lo que hace que al reloj le llegue solo lo suyo. Quién escribe ↔ mozo sigue siendo el nombre de Telegram (`feedback.py`, `_name`) hasta que C-7 asocie usuario de Telegram y mozo.
- **Encargado**: los **escalados**: un aviso sin respuesta después del segundo recordatorio (aviso + dos recordatorios = tres mensajes sin respuesta; el escalado sale cuando habría salido el tercer recordatorio, a los 9 min en producción con `PROD_REMINDER = 180`), o una mesa que pasó el límite sin atender. Le llegan al reloj y al celular igual que al mozo; puede tomarlo con «Voy» o darse por enterado con «Visto». Hasta C-7 hay un solo grupo: el escalado va al grupo y «Visto» vale solo si lo escribe quien figura como encargado (`encargado_telegram = "Nombre"` en `servio.toml`, un dato nuevo; no confundir con `[roles.encargado] nombre`, que es la etiqueta del rol); un «visto» de un mozo se ignora. Hoy `12-roles.md` solo tiene `avisos: suyas | todas | ninguno`: hace falta el valor **`escalados`** para el encargado (y actualizar esa tabla y `roles.py` al implementarlo).
- **Dueño y encargado**: el **resumen al cerrar** el servicio (`resumen_cierre`, tarea D-3, todavía no implementada; `12-roles.md` se lo da a los dos) y, el dueño, nada en el momento.

### Qué cambia respecto de lo implementado (A-4): lo que va en C-5

1. Respuestas con **una palabra suelta** (sin «responder a») y la elección del aviso por mozo (punto anterior).
2. Botones **Voy / Listo / No hacía falta** en plato vacío y mesa terminó (hoy solo «sigue lleno» tiene botones, los motivos); `answered_by` y `done_at` en el aviso; «No hacía falta» como único cierre a mano, con silencio por plato o por mesa.
3. El **escalado al encargado** como aviso propio (hoy el recordatorio se repite al mismo grupo) y el valor `escalados` en la config de roles.
4. Un chat por mozo (es C-7, que según el roadmap depende de C-6 y D-2); hasta entonces, el grupo con mención.
5. Los textos de `messages.py` en dos líneas: «emoji Mesa · qué» y «hace cuánto · plato · mozo», para que la primera línea se lea en el reloj; `mention_waiter` y `partial_text` (las dos en `messages.py`) se adaptan a eso; en el escalado la segunda línea nombra al mozo que no respondió, no al que lo recibe.

**Hecho cuando** (de la tarjeta): canal decidido (Telegram, WhatsApp con D-6) **condicionado a la prueba en el reloj**: si la app de reloj no existe o la notificación no se contesta desde ahí, la alternativa es WhatsApp con D-6 con la misma mecánica de texto; diseño de cada aviso con sus botones ✔; probado en un reloj real: pendiente, a mano (las dos fuentes son notas de prensa, no se verificaron desde el repo).
