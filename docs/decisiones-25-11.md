# Decisiones de producto y negocio para llegar al 25/11

Las decisiones de producto y negocio que el equipo tomó el 8/11/2026 para que lo que queda de las semanas 3 y 4 se haga de corrido, sin frenarse a preguntar. No es un reparto de tareas: la implementación la hacen agentes tarjeta por tarjeta, y lo que necesitan es que estas preguntas tengan respuesta. Cada punto trae el contexto en dos líneas, las opciones, la propuesta que se discutió y la **decisión del equipo** (respuestas del 8/11/2026). Este archivo manda: si una tarjeta choca con algo de acá, gana esto.

Fuentes: [context/01-producto.md](context/01-producto.md), [context/05-reglas-alertas.md](context/05-reglas-alertas.md), [context/12-roles.md](context/12-roles.md), [context/13-metricas.md](context/13-metricas.md), [diseno-front.md](diseno-front.md), [legal-uruguay.md](legal-uruguay.md), [roadmap-producto.md](roadmap-producto.md) y el Trello.

## Resumen en una pantalla

| # | Qué se decidía | Decisión |
|---|---|---|
| **Validar los diseños** | | |
| 1 | F-1, el salón: ¿quién lo mira y dónde? | Pantalla fija en la barra o la cocina, para el encargado; el mozo no la mira |
| 2 | F-1: ¿modo video con caras difuminadas o solo reconstrucción? | Reconstrucción por defecto; video solo con flag, para demo y calibrar |
| 3 | F-2, el dashboard: ¿son estas las 17 métricas? ¿cuál es el titular? | Las 17 quedan; la plata con la carta con precios (la cargamos nosotros, después editable); sin identificar el plato, «X platos volvieron llenos» |
| 4 | F-2: ¿qué se muestra el primer día, sin datos? | Tarjetas visibles con «falta recolectar datos»; selector de período (hoy, día, semana, mes, rango) |
| 5 | F-3: ¿respuesta con una palabra o botones? | Botones «Voy», «Listo», «No hacía falta» + texto libre; la cámara cierra; cada mozo recibe solo sus mesas; «Mesa X» en negrita |
| 6 | F-3: tiempos de recordatorio y escalado | Configurables por local, con los valores de hoy como default |
| **Casos de la vida real** | | |
| 7 | Plato compartido o fuente al medio | (b) comportamiento + (c) el modelo dice si parece fuente o tabla; nada que cargue el restaurante |
| 8 | Pan, guarnición, salsas, lo que se deja a propósito | Por contenido, no por recipiente: pan, canasta, salsera y guarnición no cuentan; un bowl con comida sí |
| 9 | Alguien se levanta (baño, fumar) con el plato lleno | Pausar el reloj de «sigue lleno» mientras no hay nadie |
| 10 | Mesa con platos y sin gente (se fueron sin pagar / ya pagaron) | Configurable X: avisar si pasan X minutos sin gente y con platos |
| 11 | Postre, café, vasos, tazas | Sin estado «sobremesa»; solo mesa vacía del todo |
| 12 | Mesa grande (8 personas, platos que llegan de a tandas) | Estado «terminando» para mesas de N+ personas (default 4; platos si no hay conteo) al 80 %; pasa a «mesa terminó» tras un tiempo; todo configurable |
| 13 | Cliente que rechaza el plato (se lo llevan lleno) | Posible devolución sin preguntar motivo; detectar el acto de comer por los cubiertos |
| 14 | Mesas que se juntan o se mueven | Detección automática de mesas; el admin corrige a mano desde la vista |
| **Opciones técnicas con impacto en el negocio** | | |
| 15 | Detector de platos: seguir con YOLO entrenado, YOLO-World, entrenar más | Benchmark de detectores en paralelo, sin frenar nada; solución agnóstica al detector |
| 16 | Modelo que mira el plato: Haiku, Sonnet, modelo propio | Haiku; Sonnet y modelo propio descartados |
| 17 | Qué va a la nube: solo recortes de platos, o la mesa entera | Mesa entera al modelo con las personas tapadas por un bloque de color; manos visibles (detrás del flag hasta resolver 26) |
| 18 | Cadencia de lectura y costo por mesa por hora | Sin decisión; si se mide el cubierto hacen falta fotos más seguidas |
| **Negocio** | | |
| 19 | Cliente objetivo del primer piloto | No se define cliente ahora: todo es configurable |
| 20 | Qué se vende: el número de cabecera | Para después del 25/11 |
| 21 | Precio y modelo de cobro | Para después del 25/11 |
| 22 | Hardware: celular con DroidCam o cámara IP, quién la pone | Para después del 25/11 |
| 23 | Canal al mozo: Telegram, WhatsApp, app propia | Telegram |
| 24 | Reloj: ¿se vende con reloj o con el celular en el bolsillo? | Reloj y celular, sin limitar |
| 25 | Integración con la comanda (POS) | Sin POS en el piloto; el software preparado para integrar; sin comanda nada rompe |
| 26 | Legal: cartel, inscripción en la URCDP, aviso a los mozos, abogado | Para después del 25/11 |
| 27 | Datos de los mozos: ¿se muestran por nombre al dueño? | El mozo no ve métricas, ninguna; `/metricas` solo en los chats de encargado y dueño (C-7), nunca en el grupo; sin configurar, en ninguno |
| 28 | Piloto: modo sombra primero y cuánto dura | Para después del 25/11 |
| **El 25/11** | | |
| 29 | Qué muestra la demo | Sin decisión puntual: va la propuesta (comida real, 3 minutos) |
| 30 | Qué tiene que decir la hoja de métricas | Sin decisión puntual: lo esperado quedó en 3 y 4 |

---

## A · Validar los diseños

Los tres diseños están en [diseno-front.md](diseno-front.md) con un prototipo cada uno en `docs/prototipos/` (F-1 y F-3 además publicados, privados hasta que Jessica los comparta; F-2 se abre desde el repo). Esto es lo que el equipo respondió sobre ellos.

### 1. F-1, el salón: ¿quién lo mira y dónde?

**Contexto.** El diseño F-1 muestra todas las mesas con su estado y los avisos abiertos. [12-roles.md](context/12-roles.md) dice que el mozo lo ve «en la pantalla». Pero un mozo en servicio no mira una pantalla: mira el reloj o el celular. Si la pantalla es para el encargado y para la reunión de venta, cambia lo que importa (que se entienda en 3 segundos desde lejos) y lo que no (que el mozo marque sus mesas).

**Opciones.** (a) Pantalla fija en la barra o la cocina para el encargado. (b) Pantalla que el mozo abre en el celular cuando le llega un aviso. (c) Las dos.

**Propuesta.** (a) más la venta: es «lo que ve Servio» en una reunión con un dueño. En el celular del mozo va solo el aviso (F-3). Si se confirma, F-1b prioriza pantalla grande sobre celular.

**Decisión:** (a) Pantalla fija en la barra o la cocina para el encargado. El mozo no la mira: recibe el aviso en el reloj o el celular.

### 2. F-1: ¿modo video o solo reconstrucción?

**Contexto.** F-1 tiene dos modos: el video real con caras difuminadas, y la reconstrucción (mesas, sillas y platos dibujados). El video es lo que más impresiona en una venta y lo que más riesgo legal tiene (es videovigilancia aunque esté difuminado, [legal-uruguay.md](legal-uruguay.md)).

**Propuesta.** Reconstrucción por defecto en el local. Video solo con un flag explícito (`--web-video`), para la demo y para calibrar la cámara. En la web pública y el pitch, mostrar la reconstrucción como el producto y el video como «así lo ve».

**Decisión:** La propuesta: reconstrucción por defecto; el video solo con el flag, para la demo y para calibrar.

### 3. F-2, el dashboard: ¿son estas las métricas? ¿cuál es el titular?

**Contexto.** [13-metricas.md](context/13-metricas.md) cerró 17 métricas; el prototipo F-2 las ubica todas (10 para el encargado). El héroe del dueño es «antes vs. después» en minutos. Un dashboard con 17 números no vende: vende uno.

**Opciones** para el titular: minutos de mesa terminada sin atender; mesas más por noche (rotación); plata recuperada (ROI, necesita el ticket promedio); platos que vuelven llenos (desperdicio).

**Propuesta.** El titular es «tus mesas esperan X minutos menos después de terminar» (es lo que se mide en M-2 y lo que un dueño entiende sin explicación). Rotación y ROI, debajo, como consecuencia. Las 17 quedan, pero se confirma que ninguna sobra ni falta: ¿propinas? ¿reseñas del local?

**Decisión:** Las 17 quedan. Lo de la plata necesita la carta con precios: al principio la cargamos nosotros y después la edita el local (precios de temporada, platos que salen de la carta). Mientras no se identifique qué plato es, el desperdicio se informa como «X platos volvieron llenos», sin decir cuáles.

### 4. F-2: qué se muestra el primer día

**Contexto.** Un local nuevo arranca sin historia: nada de antes/después, nada de tendencia. El diseño F-2 propone tarjetas punteadas con el porqué («Se muestra a partir del día 14», «Necesita servicios en modo sombra») y nunca un número inventado o un cero que parezca dato.

**Opciones.** Lo de arriba; o esconder las tarjetas sin datos; o mostrar datos de ejemplo marcados como tales.

**Propuesta.** Lo del diseño. Un dashboard que se va llenando le muestra al dueño qué va a tener, y evita vender un cero.

**Decisión:** Las tarjetas se muestran igual, con un texto tipo «falta recolectar datos». Además, el dashboard tiene selector de período: hoy, un día, la última semana, el último mes, o un rango a mano de días o meses hacia atrás.

### 5. F-3: ¿respuesta con una palabra o botones?

**Contexto.** F-3 diseñó que el mozo contesta con una palabra suelta («Voy», «Listo», «No hacía falta») porque en un reloj la notificación se contesta con respuesta rápida o dictado, y no está probado que los botones de Telegram se vean ahí. Los botones siguen en el celular. El riesgo: dos mozos con avisos abiertos y un «Voy» ambiguo; el diseño lo resuelve preguntando «¿Mesa 2 o Mesa 3?».

**Propuesta.** La del diseño. Y una regla de negocio: contestar es opcional, la cámara cierra los avisos sola cuando ve el plato retirado. El mozo que no contesta nunca no rompe nada; solo no tiene métrica de tiempo de respuesta.

**Decisión:** Botones («Voy», «Listo», «No hacía falta») más el texto libre de qué pasó con el plato, como hoy. El aviso se cierra solo cuando la cámara ve que el plato se sacó. Cada mozo recibe solo los avisos de **sus** mesas; si no hay mesas definidas por mozo, todo va a un solo chat. No hay pregunta «¿Mesa 2 o Mesa 3?»: «Mesa X» va en negrita al principio del mensaje para que resalte. *Cómo se aplica (completado al revisar, a confirmar):* una respuesta que **cita** el aviso (un botón, o una respuesta rápida del reloj y del celular, que Telegram manda como respuesta a ese mensaje) va a ese aviso. Hoy `feedback.py` solo mira la cita a su propia pregunta «escribí qué pasó»; C-5 extiende eso a la cita del aviso. Que la respuesta rápida del reloj llegue como cita es lo que F-3 asume y hay que probar en un reloj real (punto 24). Una respuesta **suelta**, sin cita: en el chat privado de un mozo (C-7) va a su aviso abierto más reciente; en el grupo compartido se ignora, porque no se sabe de quién ni para qué mesa es y adivinar cobra el tiempo de respuesta al aviso equivocado. Si en el reloj no se ven los botones (lo que F-3 asume hasta probarlo), la respuesta rápida igual cita el mensaje.

### 6. F-3: tiempos de recordatorio y escalado

**Contexto.** Hoy: aviso, recordatorio a los 3 min, otro a los 6, nada más; el intervalo del recordatorio ya se configura (`ALERT_REMINDER`, `--reminder`, el toml). Lo que no existe es el escalado. F-3 agrega: «Voy» frena los recordatorios; «Listo» sin que la cámara lo vea resuelto en 10 min insiste una vez y escala; sin respuesta, escalado al encargado a los 9 min. En un restaurante lleno, 9 minutos de una mesa terminada sin atender ya es mucho; en uno tranquilo, escalar a los 5 puede ser molesto.

**Opciones.** Para el escalado y los 10 min de «Listo», que son lo nuevo: 9 min fijo; 5 min; configurable por local con default.

**Propuesta.** Configurable por local en `servio.toml` (junto con T1, T2 y R de `05-reglas-alertas.md`), default 9 min. El piloto dice si bajarlo.

**Decisión:** Sí, configurables por local (recordatorio, cantidad de recordatorios, escalado y los 10 min de «Listo»), en el mismo lugar que T1, T2 y R, con los valores de hoy (y 9 min para el escalado) como default.

---

## B · Casos de la vida real

Lo que Servio tiene que hacer cuando pasa algo que no es «dos personas, dos platos, comen y se van». Para cada caso: qué debería pasar (decisión de producto) y qué pasa hoy. Las decisiones de acá son los avisos esperados de los escenarios de B-2 y lo que C-1 tiene que arreglar.

### 7. Plato compartido o fuente al medio

**Contexto.** Servio ve platos, no personas, y empareja cada plato con la comanda por orden de aparición. Con una picada o una fuente al medio y platitos, la fuente es el único plato que cuenta: cuando se vacía sale «retirar» (razonable, el mozo retira la tabla vacía) y puede salir «mesa terminó» mientras todos siguen comiendo de su plato (el problema). **Descartado:** marcar en la carta qué platos son para compartir, porque cualquier plato se puede pedir para compartir y si el restaurante no lo carga, anda mal.

**Opciones.** (a) «Mesa terminó» mira todos los platos visibles, estén o no en la comanda. (b) Un plato cuya comida baja de a saltos mientras otros platos de la mesa suben es compartido, y no dispara «retirar» mientras los demás tienen comida (usa números que ya se guardan). (c) El modelo dice en la misma llamada si el recorte parece fuente, tabla o bandeja.

**Propuesta.** (a) siempre; (b) si el escenario grabado sigue fallando; (c) solo si lo anterior no alcanza. Aviso esperado del escenario: nada mientras alguien tenga comida en su plato; «retirar» por la fuente vacía se acepta; «mesa terminó» recién sin comida en ningún plato.

**Decisión:** (b) + (c): el comportamiento (la comida baja de a saltos mientras otros platos suben) y además el modelo dice si el recorte parece fuente o tabla, para darle más seguridad.

### 8. Pan, guarnición, salsas, lo que se deja a propósito

**Contexto.** La panera, el bol de papas, la salsera. Hoy pueden detectarse como «plato» y quedar llenos para siempre («sigue lleno» falso) o vacíos («retirar» falso). Con la comanda, lo que no coincide con un pedido no cuenta, pero si la panera aparece antes que el plato principal, se queda con su lugar (pendiente conocido en [para-tomas.md](para-tomas.md)).

**Opciones.** No contar bowls y canastas (el detector los distingue si se le pide, punto 15); contar solo lo que coincide con la comanda y arreglar el orden; o pedirle al modelo que diga «no es un plato de comida».

**Propuesta.** No cuentan. Aviso esperado del escenario «dejar el pan»: ninguno.

**Decisión:** El equipo dijo «no contar bowls ni canastas» y en la misma respuesta dudó: «¿una ensalada en un bowl no la detectaría?». Lo que sale de las dos cosas: la regla va **por contenido, no por recipiente**. Lo que el modelo reconoce como pan, canasta, salsera o guarnición para compartir (el bol de papas) no cuenta para ningún aviso; un bowl con un plato de comida (ensalada, pasta, sopa) cuenta como plato. El detector sigue buscando «bowl» (`detector.py`); es el modelo, en la misma llamada que estima la comida, el que dice si lo que hay adentro es un plato o una guarnición (campo `kind` en `Item`, ver también el punto 7).

### 9. Alguien se levanta con el plato lleno

**Contexto.** Baño, fumar, atender el teléfono. Hoy «sigue lleno» no se abre sin gente en la mesa (`people_present`), y se cierra si la gente no está en dos lecturas seguidas. Al volver, el reloj del plato sigue desde que apareció.

**Opciones.** Lo de hoy; o pausar el reloj del plato mientras no hay nadie; o avisar igual porque un plato lleno sin gente también es información.

**Propuesta.** Pausar el reloj mientras no hay nadie: si alguien se fue 8 minutos y vuelve, no le cae «¿está todo bien?» a los 2 minutos de sentarse. Aviso esperado: ninguno mientras no hay nadie; «sigue lleno» solo si el plato sigue lleno 10 minutos **con gente**.

**Decisión:** Pausar el reloj del plato mientras no hay nadie, nada más. *Alcance (completado al revisar):* se pausa el reloj de «sigue lleno» (T2), que es el único que necesita gente; el de «retirar» (T1) sigue corriendo, porque un plato vacío sin gente igual hay que retirarlo (punto 10 y `05-reglas-alertas.md`). Pausar es pausar: el tiempo acumulado con gente se conserva (un plato lleno 9 min con gente, 8 min sin nadie y 1 min más con gente, avisa: estuvo 10 min lleno con gente). Un «sigue lleno» ya abierto **no se cierra** por falta de gente (reemplaza la regla de hoy de cerrarlo a las 2 lecturas sin gente, que lo cerraba y lo volvía a abrir); se cierra cuando el plato se va o la mesa queda libre.

### 10. Mesa con platos y sin gente

**Contexto.** Se fueron (pagaron en la barra, o se fueron sin pagar). Hoy «retirar» sigue saliendo aunque no haya gente (está decidido así en [05](context/05-reglas-alertas.md)); «mesa terminó» sale solo si todos los platos están vacíos, así que una mesa abandonada con comida no avisa nunca.

**Propuesta.** Mesa sin gente dos lecturas seguidas y con platos: aviso de «retirar» a los 2 minutos, haya o no comida. Es la rotación: esa mesa no se puede volver a ocupar hasta que la limpien.

**Decisión:** Configurable un X: avisar si después de X minutos la mesa sigue sin gente y con platos. *Alcance (completado al revisar):* es un aviso nuevo, distinto de «retirar»; si la mesa ya tiene abierto un «retirar» o un «mesa terminó» (que siguen saliendo sin gente, punto 9), no se abre otro: el mozo ya tiene que ir. Sirve para la mesa que se fue dejando platos **con comida**, que hoy no avisa nunca.

### 11. Postre, café, vasos, tazas

**Contexto.** El MVP mira solo platos de comida. Vasos y tazas son D-7 (después del 25/11). Un postre es un plato más y entra en la comanda; un café es una taza y hoy no existe. «Mesa terminó» se abre cuando todos los platos están vacíos y se cierra al retirar o al servir algo nuevo: con el café después, la mesa queda «libre» aunque sigan sentados.

**Opciones.** Dejarlo así hasta D-7; o que la mesa con gente y sin platos sea un estado propio («sobremesa») que no avisa pero cuenta para el tiempo de mesa.

**Propuesta.** Estado «sobremesa» solo para la métrica de tiempo de mesa, sin aviso. Vasos vacíos es el primer aviso nuevo de D-7 (es el que más vende: una bebida más por mesa).

**Decisión:** Sin estado «sobremesa»: traería conflictos (una mesa tomando tragos no clasifica). Se identifica la mesa vacía del todo (sin gente y sin platos) y nada más.

### 12. Mesa grande, platos que llegan de a tandas

**Contexto.** Ocho personas, los platos llegan en dos tandas, dos terminan rápido. Las reglas son por plato, así que «retirar» por los dos vacíos sale bien. «Mesa terminó» exige todos vacíos: con uno que come lento, la mesa espera. Un plato que se cambia por otro en el mismo lugar (principal → postre) se reconoce como plato nuevo.

**Propuesta.** Confirmar que así está bien. Un posible cambio: «mesa terminó» con el 80 % de los platos vacíos en mesas de 6 o más, para que el mozo vaya con la cuenta antes. Para el piloto, no.

**Decisión:** Estado «terminando» solo para mesas grandes, de N o más (default 4): si terminó el 80 %, la mesa pasa a «terminando»; si está mucho tiempo así, pasa a «mesa terminó». El porcentaje, el N y el tiempo son configurables por el admin. *Para implementar (completado al revisar):* el detector local de personas (`people.py`) ya devuelve una caja por persona, pero al contrato solo llega `people_present` (sí/no). C-1b propaga el **conteo** (`people_count` en la observación, `03-contratos.md`) y **N se cuenta en personas**; si no hay conteo (clasificador sin detector local), se cuenta en platos de comida. «Terminando» es un estado de **mesa**; el `FINISHED_AFTER` de hoy, que pasa un plato de `por_terminar` a `vacio` a los 6 min, es por **plato** y sigue igual. *Para implementar (completado al revisar):* default del tiempo 5 min; «terminando» es un estado de mesa nuevo al lado de `por_terminar` (que sigue siendo solo el color) y cambia cuándo se abre `mesa_terminada` en mesas grandes, así que el PR actualiza `03-contratos.md`, `05-reglas-alertas.md` y los fixtures en el mismo paso (regla 1 de `AGENTS.md`).

### 13. Cliente que rechaza el plato

**Contexto.** El plato llega, no le gusta, el mozo se lo lleva lleno. «Sigue lleno» se abre a los 10 minutos si nadie lo retiró; si el mozo lo retira antes, no hay aviso y no queda registro de que ese plato volvió lleno. Y esa es la métrica de D-4 («platos que vuelven con comida»), la más diferencial para vender.

**Opciones.** Registrar como «vuelve lleno» todo plato que desaparece estando `lleno` antes de 10 minutos (puede ser un cambio de plato, no un rechazo); o que el mozo lo marque desde el celular; o las dos (automático, y el mozo pone el motivo si quiere).

**Propuesta.** Las dos. Un plato lleno que desaparece queda como «posible devolución» y el mozo recibe la pregunta de motivo (los mismos botones de hoy). Sin respuesta, cuenta igual pero sin motivo.

**Decisión:** Se marca como posible devolución y **no** se pregunta el motivo. *Alcance (completado al revisar):* lo que no se pregunta es por el plato que **desaparece lleno**; la pregunta de motivo del aviso «sigue lleno» (A-4, con sus botones) sigue como está, y de ahí salen `desperdicio`, la mitad «por qué» de `platos_que_vuelven` y los motivos de D-4. La posible devolución suma al conteo de platos que vuelven, sin motivo. Reconocer el acto de comer por el movimiento de los cubiertos (el utensilio entra y sale más de X veces): si la persona comió, un plato que se va aunque parezca lleno es retiro y no devolución, y el plato lleno que llega después es uno nuevo de otra cosa.

### 14. Mesas que se juntan o se mueven

**Contexto.** Hoy las mesas se detectan solas o se dibujan a mano con `--zones`. Si la mesa se mueve, los platos se siguen; si dos mesas se juntan, son dos zonas con una sola comanda.

**Propuesta.** En el piloto, zonas fijas dibujadas al instalar (más estable que la detección automática) y las mesas que se juntan se tratan como lo que son, dos mesas con el mismo mozo. Juntar y separar desde la web es parte de D-2.

**Decisión:** Detección automática de mesas en el piloto. Si falla, el admin lo ve desde la vista en la computadora y lo arregla a mano.

---

## C · Opciones técnicas con impacto en el negocio

### 15. Detector de platos

**Contexto.** El YOLO del repo (`best.pt`, entrenado en Colab con datasets públicos) sabe dos clases, comida y comida terminada: ubica los platos y, si Claude falla o tarda, su clase es el plan B para el estado (`04-vision.md`). Sin esos pesos, el pipeline ya usa YOLOE con las palabras «plate» y «bowl» (detección por texto, sin entrenar). Ninguno de los dos sabe decir «fuente», «tabla» o «canasta», que es lo que piden los casos 7 y 8. Opciones en la industria hoy:

| Opción | Qué da | Qué cuesta | Licencia |
|---|---|---|---|
| Seguir con `best.pt` y entrenarle más clases | Lo que etiquetemos, y conserva el plan B de estado | Etiquetar cientos de fotos por clase (B-3) | AGPL (ultralytics) |
| **YOLO-World** o el YOLOE que ya está, con más palabras | Detecta lo que se le pide por texto: plato, bowl, fuente, tabla, canasta, vaso | Un día de prueba; **pierde el plan B de estado** si reemplaza a `best.pt` en vez de sumarse | GPL (YOLO-World) / AGPL (YOLOE, y `ultralytics` en los dos casos) |
| RF-DETR (Roboflow) | Más preciso que YOLO en datasets propios | Etiquetar igual que la primera | Apache 2.0 |
| Claude mirando la mesa entera | Entiende la escena («picada al medio y tres platitos») | Más caro por lectura y manda la mesa con gente a la nube (punto 17) | — |

**Propuesta.** Probar YOLO-World (o YOLOE con más palabras) sobre las sesiones grabadas y adoptarlo si no empeora el acierto por estado, sumado a `best.pt` para no perder el plan B. Entrenar solo con fotos de nuestras cámaras cuando B-3 tenga etiquetas. La licencia GPL/AGPL se revisa antes de cobrar (hoy ya es así con `ultralytics`).

**Decisión:** Benchmark de detectores en paralelo, sin frenar nada (no es showstopper). La solución tiene que ser agnóstica al modelo de detección, para cambiarlo y escalar como queramos. *Para implementar (completado al revisar):* el detector queda detrás de una interfaz (cajas + tipo de recipiente + clase de estado si la tiene) y el benchmark se corre contra esa interfaz; el plan B de estado de `best.pt` no se pierde al cambiar de detector.

### 16. Modelo que estima cuánta comida queda

**Contexto.** Hoy Claude Haiku mira cada recorte de plato (1 a 3 s por llamada, `04-vision.md`). Sonnet y Opus son más precisos y más caros; no se compararon. D-8 propone un modelo propio chico entrenado con las etiquetas de B-3, dejando Claude solo para los casos dudosos (corta el 80 % de las llamadas).

**Propuesta.** Haiku hasta el 25/11; comparar con Sonnet en el tablero de números (B-3) solo si el acierto por estado queda abajo del 85 %. Modelo propio cuando haya 1.000 recortes etiquetados, no antes.

**Decisión:** Haiku. Sonnet queda descartado por costo (analizamos imágenes todo el rato). El modelo propio también queda descartado (el que estima cuánta comida queda, D-8; un detector local chico de cubiertos, punto 18, es otra cosa y sigue abierto).

### 17. Qué va a la nube

**Contexto.** Desde C-3, a Anthropic van solo recortes de platos con las personas pixeladas; las caras se difuminan apenas se lee el frame. Mandar la mesa entera existe como modo opt-in. [legal-uruguay.md](legal-uruguay.md): el mayor riesgo jurídico es mandar frames con personas a EE. UU.

**Propuesta.** Solo recortes de platos, siempre, en producción. El modo mesa entera se queda para experimentar en casa y no se vende. Esto es un argumento de venta y va en el cartel: «ninguna imagen de personas sale del local».

**Decisión:** Mandar la mesa entera, pero sacando a las personas: se tapan con un bloque de un color que el modelo sepa que es censura de persona. Las manos no se tapan. *Cómo se aplica (completado al revisar):* esto cambia lo que C-3 dejó integrado y lo que `legal-uruguay.md` da por única base para que el dato deje de ser personal (solo el plato). Hasta que el punto 26 se resuelva, el modo mesa entera se implementa con el bloque de color **detrás del flag que ya existe** (`CLAUDE_ALLOW_TABLE_IMAGES`), y en producción el default siguen siendo los recortes de platos.

### 18. Cadencia y costo por mesa por hora

**Contexto.** Una lectura cada 30 segundos por mesa. Hoy se saltea la llamada al modelo cuando la mesa entera no cambió (`CLAUDE_REUSE_UNCHANGED`, con refresco cada 4 lecturas); si cambió algo, va toda la mesa. Mandar solo los platos que cambiaron, con caché por plato, es C-2. El costo por mesa por hora no está medido todavía (sale del tablero de B-3) y es lo que define el precio mínimo. C-2 lo baja a la mitad (caché por plato, no volver a preguntar por un plato vacío).

**Opciones.** 30 s; 60 s (la mitad del costo, avisos un minuto más tarde); adaptativo (rápido cuando la mesa cambia, lento cuando no).

**Propuesta.** 30 s y C-2. Decidir ahora el tope de costo que aceptamos por mesa por hora (por ejemplo, 2 centavos de dólar), para que el precio del punto 21 cierre.

**Decisión:** Sin decisión por ahora: si se mide el cubierto (punto 13) hay que sacar fotos más rápido; para eso podría usarse un modelo local chico que solo detecte el cubierto entrando y saliendo.

---

## D · Negocio

### 19. Cliente objetivo del primer piloto

**Contexto.** El problema que se vende sale de 297 reseñas de 14 restaurantes de Montevideo (`context/13-metricas.md`, sección «Externas»; las reseñas, en la carpeta compartida `servio-resenas/`): el 18,5 % se queja del servicio y el 57 % de las de 1 y 2 estrellas. Pero el producto no sirve igual en todos lados: un bar de tapas con platos que van y vienen es el caso 7 todo el tiempo; un restaurante de menú de pasos tiene mesas que se quedan dos horas.

**Opciones.** Restaurante de 15 a 40 mesas con encargado y platos individuales; cadena chica con 2 o 3 locales (más mesas con una sola venta); bar o cervecería (rotación alta, el aviso de vasos vacíos vende más que el de platos).

**Propuesta.** Restaurante de 15 a 40 mesas en Montevideo, con encargado en el salón (es quien mira F-1 y recibe los escalados) y platos individuales. Es donde las tres alertas de hoy aciertan más.

**Decisión:** No se define el cliente ahora: casi todo es configurable, así que da igual si la mesa se queda 1 h o 2 h.

### 20. Qué se vende: el número de cabecera

**Contexto.** Hay tres candidatos medibles en M-2: minutos de mesa terminada sin atender (antes vs. después), mesas más por noche, platos que vuelven llenos. Y uno que no se puede medir sin restaurante: plata recuperada.

**Propuesta.** «Tus mesas esperan X minutos menos» como número de cabecera, con «eso son Y mesas más por mes» calculado con un ticket promedio que el dueño carga. Platos que vuelven llenos es el segundo argumento, el que ningún competidor tiene.

**Decisión:** Para después del 25/11.

### 21. Precio y modelo de cobro

**Contexto.** No hay precio definido. El costo variable es el modelo (punto 18) más el hardware (punto 22). Lo que se compara: un mozo más cuesta 30.000 a 50.000 pesos por mes.

**Opciones.** Por mes por local escalonado por mesas; por mesa por mes; un fijo chico más variable por aviso; gratis el primer mes.

**Propuesta.** Por mes por local, tres escalones por cantidad de mesas (hasta 15, hasta 40, más), piloto gratis 4 semanas a cambio del caso de éxito con nombre. Precio objetivo: menos que un día de un mozo por semana.

**Decisión:** Para después del 25/11.

### 22. Hardware: qué cámara y quién la pone

**Contexto.** Se probó con celular y DroidCam (720p alcanza para una mesa, 1080p para dos) y con cámaras IP por RTSP. Un restaurante de 30 mesas necesita 2 o 3 cámaras en alto y una computadora que corra Servio en el local (o un servidor nuestro que reciba el stream).

**Opciones.** Cámara IP que ponemos nosotros (instalación incluida); el local pone cámara propia y nosotros la conectamos; usar las cámaras de seguridad que ya tiene (ángulo y calidad no controlados).

**Propuesta.** Cámara IP que ponemos nosotros, con la guía de montaje de B-4, y una mini PC en el local. Las cámaras de seguridad existentes se evalúan caso por caso, nunca se promete.

**Decisión:** Para después del 25/11.

### 23. Canal al mozo

**Contexto.** F-3 eligió Telegram (tiene app en Apple Watch y Wear OS desde junio 2026, y es gratis) y WhatsApp con D-6 (API paga por conversación, más conocido por los mozos). App propia: no, costaría dos tiendas para resolver lo mismo.

**Propuesta.** Telegram para el piloto. WhatsApp cuando un cliente lo ponga como condición; su costo por conversación se suma al precio.

**Decisión:** Sí, Telegram.

### 24. Reloj o celular

**Contexto.** El diseño F-3 está pensado para reloj (el mozo no saca el celular en el salón). Nadie del equipo probó todavía en un reloj real si la notificación se contesta sin abrir la app. Un reloj por mozo son unos 200 dólares cada uno.

**Opciones.** Vender con reloj incluido; vender para el celular y el reloj como opcional; exigir reloj.

**Propuesta.** El celular en el bolsillo con vibración alcanza para el piloto y para vender. El reloj es el «premium» y se prueba cuando alguien consiga uno. Si nadie del equipo tiene reloj antes del 17/11, la demo del 25/11 va con celular.

**Decisión:** Lo ideal es el reloj, pero no se limita: tiene que servir para reloj y para celular.

### 25. Integración con la comanda (POS)

**Contexto.** Hoy la comanda es un archivo (`fixtures/restaurante/`): mozos, mesas, pedidos, carta. Sirve para nombrar el plato en el aviso y para la métrica por plato de la carta. En un local real la comanda vive en el POS (Fudo, Toteat u otros), y no está verificado qué API exponen.

**Opciones.** Sin POS: el encargado carga la carta una vez y los pedidos no se cargan (los avisos dicen «un plato» en vez de «Risotto»); con POS desde el piloto; o una carga mínima por turno desde la web (D-2).

**Propuesta.** Sin POS en el piloto. La carta y los mozos se cargan una vez; el aviso de «sigue lleno» nombra el plato solo si el POS lo da. La métrica por plato de la carta (D-4) se vende como «con tu POS conectado».

**Decisión:** Sin integración en el piloto, pero con el software preparado para integrar: si no hay comanda, nada se rompe; las métricas pierden detalle y un par faltan; con la integración se enriquece todo.

### 26. Legal antes del piloto

**Contexto.** [legal-uruguay.md](legal-uruguay.md): viable con condiciones. Cartel con logo de la URCDP y datos del responsable, inscripción de la base, aviso a los mozos de que se los mide (lo admite la URCDP si se informa), y el envío a EE. UU. solo con recortes sin personas. No es asesoramiento legal.

**Propuesta.** Paquete mínimo antes del primer piloto: cartel, texto de aviso a los mozos firmado, inscripción de la base. Abogado antes de cobrar el primer peso. Esto es un costo y un tiempo que entra en el plan de venta.

**Decisión:** Para después del 25/11.

### 27. Datos de los mozos

**Contexto.** Las métricas por mozo (tiempo de respuesta, retiros, mesas olvidadas) son el producto para el encargado y a la vez un dato laboral. Hoy `/metricas` sale en el grupo donde están todos los mozos.

**Propuesta.** Métricas por nombre solo para encargado y dueño; el mozo ve las suyas si las pide; nunca comparativas en el grupo. En el pitch: «para que el encargado sepa a quién ayudar», no «para controlar».

**Decisión:** El mozo no ve métricas, ninguna, ni aunque las pida. *Para implementar (completado al revisar):* `/metricas` deja de responderse en el grupo donde están los mozos; se responde solo en los chats de encargado y dueño. Esos chats **no existen hoy**: los define C-7 en `servio.toml` (`encargado_telegram`, `dueno_telegram`), igual que el chat de cada mozo; el bot, que hoy solo escucha el grupo, pasa a escuchar también esos chats. Sin configurar, `/metricas` **no responde en ningún chat** (las métricas quedan en `tools/metricas.py` y el dashboard); en el grupo, nunca. `TELEGRAM_ADMIN_CHAT_ID` sigue siendo el chat de operaciones (salud del sistema), no se mezcla. El escalado del punto 6 va a `encargado_telegram`; sin configurar, al grupo, como un recordatorio más.

### 28. Piloto: modo sombra y duración

**Contexto.** M-2 hace modo sombra (observa y guarda los avisos sin mandarlos) y mide antes/después en nuestras casas. En un restaurante, la misma secuencia es el piloto y da el número de venta.

**Propuesta.** 1 semana en sombra (línea de base sin que nadie cambie nada), 3 semanas con avisos, una hoja de una página al final con el antes/después. Gratis, a cambio de poder contarlo.

**Decisión:** Para después del 25/11.

---

## E · El 25/11

### 29. Qué muestra la demo

**Contexto.** La tarjeta del corte pide la demo grabada con el salón (F-1b), el dashboard (D-1) y los avisos. Se puede grabar sobre el video de siempre o sobre una comida real en una casa.

**Propuesta.** Una comida real en una casa, grabada en pantalla y celular: el salón en reconstrucción, una mesa que termina, el aviso que llega y se contesta «Voy», el «✅ ya resuelto» cuando se retiran los platos, y el dashboard del encargado al final. Tres minutos.

**Decisión:** Sin decisión puntual (el equipo lo dejó para después, pero la demo es el 25/11): va la propuesta, una comida real de tres minutos, salvo que el equipo diga otra cosa antes.

### 30. Qué tiene que decir la hoja de métricas

**Contexto.** M-2 termina con una hoja de una página. Sin restaurante, es con comidas en casa.

**Propuesta.** Tres números y nada más: minutos de mesa terminada sin atender antes vs. después (al menos 3 casas), costo por mesa por hora, acierto por estado. Con la aclaración de que es en casas, no en un local.

**Decisión:** Sin decisión puntual: lo que se espera de las métricas quedó dicho en los puntos 3 y 4.

---

## Agregado en la reunión

- **RAG sobre las métricas y los datos del negocio:** poder consultar en lenguaje natural lo que Servio recolecta (avisos, tiempos, platos, respuestas), sin depender de las pantallas fijas del dashboard. Va al Trello como tarjeta nueva.
- **Detectar el acto de comer** (punto 13) es una capacidad nueva de visión: cubiertos que entran y salen del plato. Pide fotos más seguidas que cada 30 s (punto 18); se investiga aparte antes de prometerlo.
- **La implementación no se reparte entre personas:** la hacen agentes tarjeta por tarjeta, y el tablero marca cuáles se pueden tomar (ver «Tablero y trabajo en paralelo» en `AGENTS.md`, que llega con `vision/tools/tablero.py` en la rama `jessica/tablero-agentes`).

## Qué doc de contexto cambia con cada decisión

Mientras no se actualicen, los docs de contexto dicen lo viejo; este archivo gana. La tarjeta que implementa cada decisión actualiza el doc en el mismo PR.

| Decisión | Doc que hoy dice otra cosa | Qué cambia |
|---|---|---|
| 1 | `context/12-roles.md` («el mozo ve la vista»), `diseno-front.md` F-1 («Mozo (principal)») | La pantalla es del encargado; el mozo solo recibe avisos |
| 5, 6 | `diseno-front.md` F-3 (palabra suelta, `telegram` en `salon.json`), `context/05-reglas-alertas.md` | Botones, tiempos configurables, mozo ↔ mesas en `servio.toml` |
| 9, 10, 12 | `context/05-reglas-alertas.md`, `context/03-contratos.md`, fixtures | Pausa de T2, aviso de mesa sin gente, estado «terminando» |
| 7, 8 | `context/03-contratos.md` (`Item`), `context/05-reglas-alertas.md`, prompt | `kind` por plato; qué cuenta y qué no |
| 13 | `context/05-reglas-alertas.md`, `context/13-metricas.md`, `roadmap-producto.md` (D-4) | Posible devolución sin motivo; el motivo sigue en «sigue lleno» |
| 14 | `diseno-front.md` F-1, `context/04-vision.md` | Mesas automáticas corregibles a mano |
| 17 | `context/04-vision.md`, `legal-uruguay.md`, `.env.example` | Bloque de color en vez de pixelado, detrás del flag |
| 27 | `context/12-roles.md` («Pendiente»), `context/06-notificaciones.md` | `/metricas` nunca en el grupo |

## Lo que Claude no puede hacer solo

No son decisiones, pero sin esto las tarjetas no cierran:

- Cámara andando en cada casa (B-2, B-3 y M-2 necesitan grabaciones de 3 casas).
- Comidas en modo sombra y con avisos en la semana 4 (M-2).
- Probar un aviso en un reloj real, si alguien consigue uno (punto 24).
- Compartir los prototipos publicados de F-1 y F-3 (son privados en claude.ai) para que el equipo los abra.
- Pasar `DATABASE_URL` por mensaje directo a cada uno (nunca en el grupo ni en el repo).
- Hablar con un abogado y con el primer restaurante.
