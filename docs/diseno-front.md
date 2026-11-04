# Diseño del front (F-1, F-2, F-3)

Las tres pantallas de Servio, para que el equipo las revise antes de implementarlas (o de cambiar lo que ya está implementado). En vez de Figma, cada diseño es un **prototipo HTML navegable** en `docs/prototipos/`, publicado como página para verlo desde el celular, más las decisiones escritas acá. Los roles salen de [12-roles.md](context/12-roles.md) y las métricas de [13-metricas.md](context/13-metricas.md).

| Pantalla | Quién la ve | Prototipo | Implementación |
|---|---|---|---|
| F-1 · Reconocimiento (el salón) | Mozo (principal), encargado, dueño | [prototipos/f1-reconocimiento.html](prototipos/f1-reconocimiento.html) | `vision/servio_vision/static/salon.html`, `/<local>/salon` (PR #64) |
| F-2 · Dashboard | Encargado, dueño | [prototipos/f2-dashboard.html](prototipos/f2-dashboard.html) | `vision/servio_vision/static/dashboard.html`, `/<local>/dashboard` (rama `tomas/dashboards`, D-1) |
| F-3 · Notificaciones | Mozo (reloj), encargado, dueño | pendiente | `vision/servio_vision/alerts/messages.py`, `vision/servio_vision/alerts/bot.py` (Telegram) |

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
- **Dueño** (si vale la pena): pestañas **Resultados / Carta / Turno / Config**. Resultados abre con el héroe «**Tus mesas esperan X min menos**» (antes/después del modo sombra), después las cuatro cifras, la serie semana a semana, lo que vuelve a la cocina en pesos y la adopción de los mozos. Carta: tabla por plato (vuelve, ritmo, sobra) con una frase de diagnóstico por plato. Turno: lo mismo que ve el encargado. Config: costo de Servio por mes, umbrales del local, roles y usuarios.

Qué ve cada rol sale del servidor (`vistas` en la respuesta de `/datos`), no del front: el prototipo tiene el segmento Encargado/Dueño solo para revisar.

### Cuando todavía no hay datos

Toda métrica tiene su lugar desde el primer día, pero **no se inventa**: si el servidor manda `null`, la tarjeta sale punteada con el porqué («Se muestra a partir del día 7», «Necesita servicios en modo sombra», «Ningún plato volvió lleno todavía»). D-1 ya manda esos textos (`PENDING` en `dashboard.py`) y dibuja la tarjeta punteada; el prototipo agrega el botón «Primer día» para ver cómo queda un local recién instalado: avisos en cero, sin histórico ni antes/después, y el resto con sus textos. Ninguna tarjeta divide por cero ni muestra «NaN».

### Qué cambia respecto de lo implementado (`dashboard.html`, D-1)

1. El héroe **antes/después** del dueño con la frase en minutos menos (hoy sale punteado: necesita D-5 y M-2).
2. La serie **semana a semana** y la tarjeta de **costos** dibujadas (hoy punteadas: D-4 y C-2); cuando lleguen los datos, el prototipo es la referencia.
3. La frase de diagnóstico debajo de cada tabla (mozos, carta) la escribe el servidor con los datos, no está fija en el HTML.
4. Textos de «todavía sin datos» por métrica, no uno genérico.

Lo demás (colores, tipografía, tarjetas, tablas, barras, armado por rol, botón al salón) es lo que ya está en D-1 y el prototipo lo copia tal cual.

**Hecho cuando** (de la tarjeta): diseño con las dos vistas, todas las métricas de M-1 ubicadas y revisado por el equipo. El prototipo cubre las 17 en la vista del dueño (10 en la del encargado); falta la revisión.
