# Montaje de la cámara (B-4)

Para que Servio lea bien los platos, la cámara tiene que ver **la mesa entera, desde arriba, con luz pareja y nítida**. Esta guía es lo que probamos en las tres casas; el chequeo de encuadre (`python -m servio_vision.framing --source …`) dice si falta algo antes de arrancar.

## Dónde va la cámara

| | Recomendado | Por qué |
|---|---|---|
| **Altura** | 2,2 a 2,8 m sobre el piso (1,4 a 2 m sobre la mesa) | A menos, la mesa no entra entera; a más, los platos quedan de menos de 60 px y YOLO deja de encontrarlos. |
| **Ángulo** | Cenital, o hasta 30° de inclinación desde la vertical | De costado, un plato tapa al de atrás y las manos tapan la comida. Si no se puede colgar del techo, un estante alto o la punta de un mueble, inclinada hacia la mesa. |
| **Encuadre** | La mesa entera con un 10 % de aire alrededor | Si la mesa toca el borde del cuadro, un plato que se corre queda afuera. Si hay dos mesas, las dos enteras. |
| **Tamaño de los platos** | ≥ 60 px de diámetro en el cuadro (a 1080p, una mesa de 1,2 m a ~2 m de altura da ~90 px) | Debajo de eso YOLO no los ve y Claude no distingue cuánto queda. `probar_video` muestra desde qué tamaño falla. |
| **Fijación** | Soporte de celular con pinza o trípode chico, atado o encintado; el cable enchufado | Una cámara que se mueve cambia el encuadre a mitad de la comida y los ids de los platos saltan. |

## Luz

- **Pareja y de arriba**: la luz del comedor encendida, mejor dos fuentes que una. El chequeo pide brillo medio entre 60 y 215 (de 0 a 255).
- **Sin ventana ni lámpara dentro del cuadro**: quema la imagen y la cámara baja la exposición del resto; la comida queda negra.
- **Sin contraluz**: la ventana atrás de la mesa, no enfrente de la cámara. De noche, con luz artificial, anda mejor que de día con sol de costado.
- **Nada que parpadee**: una lámpara LED barata a 50 Hz hace franjas en el celular; cambiarla o bajar la resolución de cuadros.

## Cámaras probadas

| Cámara | Resolución | Cómo | Resultado |
|---|---|---|---|
| Celular con **DroidCam** (Android/iPhone) por Wi-Fi | 720p (gratis) / 1080p (pago) | `--source http://<IP>:4747/video` | Lo que usamos. 720p alcanza para una mesa; para dos, 1080p. Dejar la app en primer plano y el celular enchufado. |
| Celular con **IP Webcam** (Android) | hasta 1080p | `--source http://<IP>:8080/video` | Anda igual; permite fijar foco y exposición desde la app, que ayuda de noche. |
| Webcam de la laptop | 640×480 | `--source 0` | Solo para probar el pipeline: apunta de costado y los platos salen chicos. |
| Cámara de seguridad (`.asf`, 640×480) | 640×480 | video grabado | Se detectan los platos pero las caras y los bordes salen borrosos; sirve para probar, no para medir. |

Con el celular: **foco fijo en la mesa** (tocar la mesa en la app y bloquear), exposición bloqueada, sin HDR ni "belleza". Una conexión por vez: DroidCam gratis no deja correr `main.py` y `vista_demo.py` a la vez.

## Checklist antes de arrancar

1. Cámara fija a 2,2–2,8 m, mirando hacia abajo, cable enchufado.
2. Luz del comedor prendida; ninguna ventana ni lámpara en el cuadro.
3. Foco y exposición bloqueados en la app.
4. `python -m servio_vision.framing --config config/casa-<quien>.toml` (acepta los mismos flags que `main.py`: `--source`, `--zones`…) → `Encuadre OK`. Si avisa algo, está escrito qué mover.
5. `python main.py --config config/casa-<quien>.toml`: la primera lectura repite el chequeo en el log.

## Qué revisa el chequeo (`servio_vision/framing.py`)

- **Oscura** (brillo medio < 60) o **quemada** (> 215).
- **Desenfocada**: varianza del laplaciano < 40, con la imagen achicada a 640 px de ancho si es más grande (nunca agrandada).
- **Mesa cortada o pegada al borde**: el recuadro de una mesa a menos del 1 % del lado del borde del cuadro. Las mesas detectadas solas ya traen un 10 % de margen, así que tocar el borde es no tener el aire que pide esta guía, aunque la mesa entre justo.
- **Mesa muy chica**: menos del 4 % del área del cuadro.
- **Sin mesa**: no se detectó ninguna (con detección automática, "una mesa" que ocupa casi todo el cuadro es el comodín del detector: no encontró nada), o la zona fija queda fuera del cuadro.

Los umbrales están arriba del archivo; si una casa los necesita distintos, es una señal de que algo del montaje se puede mejorar antes de tocarlos.
