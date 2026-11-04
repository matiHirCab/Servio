# Diseño del front (F-1, F-2, F-3)

Las tres pantallas de Servio, para que el equipo las revise antes de implementarlas (o de cambiar lo que ya está implementado). En vez de Figma, cada diseño es un **prototipo HTML navegable** en `docs/prototipos/`, publicado como página para verlo desde el celular, más las decisiones escritas acá. Los roles salen de [12-roles.md](context/12-roles.md) y las métricas de [13-metricas.md](context/13-metricas.md).

| Pantalla | Quién la ve | Prototipo | Implementación |
|---|---|---|---|
| F-1 · Reconocimiento (el salón) | Mozo (principal), encargado, dueño | [prototipos/f1-reconocimiento.html](prototipos/f1-reconocimiento.html) | `vision/servio_vision/static/salon.html`, `/<local>/salon` (PR #64) |
| F-2 · Dashboard | Encargado, dueño | pendiente | `vision/servio_vision/static/dashboard.html`, `/<local>/dashboard` (rama `tomas/dashboards`, D-1) |
| F-3 · Notificaciones | Mozo (reloj), encargado, dueño | pendiente | `vision/servio_vision/alerts/messages.py`, `vision/servio_vision/alerts/bot.py` (Telegram) |

Los prototipos son documentos HTML completos (abrirlos desde el repo anda, también en un celular); para publicarlos como artifact, `tools/publicar_prototipo.sh` les saca el esqueleto. **Ninguna imagen real de un local va al repo**: la «cámara» del prototipo F-1 es un frame dibujado con las mismas coordenadas que los datos de ejemplo.

## F-1 · Reconocimiento: lo que «ve» Servio

**La idea:** la pantalla de un auto. Una sola superficie oscura, el salón grande y en el centro, y lo que importa (platos, estados, avisos) dibujado encima a medida que la cámara lo detecta. El mozo la mira de reojo con la luz de un restaurante: contraste alto, pocos textos, cuatro colores fijos.

### Dos modos, un mismo salón

- **Reconstrucción** (el default, y el único que hay que mostrar a un cliente o al público): no se muestra el video. Se dibuja la mesa desde arriba con sus sillas, una figura por comensal y un ícono por plato. **Resuelve la privacidad de la pantalla**: ninguna cara, ninguna persona reconocible, y se puede proyectar en el salón. Es lo que ya corre en `/<local>/salon`.
- **Video**: la imagen de la cámara, **ya con las caras difuminadas en la computadora** (C-3), con las capas encima: el recuadro, la etiqueta y el aviso abierto de cada mesa y, por plato con caja detectada, el anillo del estado y una etiqueta con el nombre del plato. Sirve para armar la cámara, para confiar en lo que detecta y para depurar; no para dejarlo puesto. Solo dibuja lo que la cámara ve de verdad.

El cambio de modo es un segmento en la barra de estado. Los dos modos comparten los datos de `/<local>/salon/estado`; el video necesita además el **último frame difuminado**, que hoy no se guarda en ningún lado sin `--record`. La forma simple: el loop de `app.run` ya tiene el frame difuminado en la mano (el mismo que graba `_record`); se lo pasa a un método nuevo `SalonFeed.keep_frame(frame)`, que guarda los bytes de `to_jpeg(frame)` **en memoria** (un solo frame, se pisa en cada lectura), y `salon_web.py` los sirve en `/<local>/salon/frame.jpg`. (`on_reading` no sirve para esto: recibe las observaciones, no el frame.) Así el modo video anda sin grabar a disco, que sigue siendo solo con `--record`. **El endpoint va apagado por defecto** (`--web-video` para prenderlo): aunque el frame no tenga caras, servirlo en la red del local es, en la práctica, un video en vivo para cualquiera en el wifi; se prende para armar la cámara y se apaga después. Sin el flag, el segmento «Video» no se muestra.

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
- Panel derecho: la mesa elegida (platos, nombre del pedido, barra de cuánto queda, desde cuándo están sentados), los avisos y «Ahora» (mesas ocupadas, platos en mesa, avisos abiertos, tiempo promedio en mesa; el promedio se calcula en el navegador desde el `since` de cada mesa, no hace falta nada nuevo del servidor).
- Dock abajo: la leyenda de los cuatro estados con el conteo de platos en cada uno, y hace cuánto llegó la última lectura.

### En el celular del mozo

Mismo contenido, otra composición: el salón arriba a todo el ancho, debajo la mesa elegida y los avisos, y el dock de estados **pegado al borde de abajo** (sticky, con `safe-area-inset-bottom`) como barra de mesas activas. Los números de «Ahora» en una fila de cuatro. Tocar una mesa en el salón la elige. En un celular sale así sola (`max-width: 620px`); el botón «Celular» del prototipo es para verlo desde una computadora.

### Qué cambia respecto de lo implementado (`salon.html`)

1. El modo **video** (hoy solo hay reconstrucción) y el segmento para cambiar.
2. La **etiqueta de aviso** sobre la mesa (hoy el aviso solo está en el panel).
3. La **marca de las mesas del mozo** (necesita saber quién mira: login de R-1).
4. En el aviso, **quién lo tomó** («Voy · Juan») o que nadie respondió (necesita C-5).
5. El dock sticky en celular.
6. Un arreglo chico para pasar a `salon.html`: `.changed` necesita `transform-box: fill-box; transform-origin: center`, si no el `pop` escala desde el origen del SVG y el plato se corre en vez de latir.

Lo que necesita del servidor, además de lo que `/estado` ya manda (`fill_ratio`, `since` y `created_at` en ISO, `dish` que puede ser `null`, `message`, `waiter` por mesa):

- `GET /<local>/salon/frame.jpg`: el último frame difuminado (modo video), solo con `--web-video` (ver arriba).
- `answered_by` en cada aviso (el nombre que ya usa `alert_feedback` en el log): quién respondió «Voy» (C-5); `null` si nadie respondió todavía. Con `null` el panel muestra «Nadie respondió · mozo» (el mozo sale de la mesa del aviso; el aviso no lo trae); si el campo no viene (el servidor de hoy), no dice nada: no sabe.
- Las horas (`since`, `created_at`, `ts`) vienen en UTC y la pantalla las muestra en la hora del navegador, que es la del local.
- Quién mira (para marcar sus mesas): el usuario de R-1. Sin login, todas las mesas sin marca.

Lo demás (colores, tipografías Barlow y Barlow Condensed, el dibujo de mesas, sillas, personas y platos, la animación de cambio) es lo que ya está: `plate()` y el `pop` están copiados tal cual de `salon.html` (si se tocan, `salon.html` manda, salvo el punto 6), y los datos de ejemplo del prototipo tienen la forma real de `/estado`, así que lo que se implemente a partir de él usa los campos que existen.

**Hecho cuando** (de la tarjeta): diseño con los dos modos, los 4 estados y una mesa con aviso abierto, en pantalla grande y en celular, revisado por el equipo. El prototipo cubre todo eso; falta la revisión.
