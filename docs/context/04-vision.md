# 04 — Visión computacional

## Setup de cámara (desde el techo)

- Vista **cenital** (mirando recto hacia abajo): los platos se ven como círculos/elipses y casi no hay oclusión entre mesas. Esto simplifica todo.
- Para la demo: celular o webcam en un trípode/brazo a ~1,5–2 m arriba de una mesa armada. Celular como cámara IP (app tipo "IP Webcam" / DroidCam) → OpenCV lee la URL.
- **Grabar videos de prueba desde la hora 1** (plato lleno → comiendo → vacío). Sirven para desarrollar sin la mesa armada y como respaldo en la demo si falla la cámara en vivo.
- Iluminación pareja; evitar reflejos fuertes sobre platos blancos.

## Pipeline

```
frame → detección (plato/vaso) → tracking → asignar a mesa → recorte → estado → suavizado → JSON
```

### 1. Detección

COCO **no tiene la clase "plato"** (tiene `bowl`, `cup`, `wine glass`, `bottle`). Opciones, en orden:

1. **Open-vocabulary (arranque rápido):** YOLOE o YOLO-World de Ultralytics con prompts de texto `["plate", "bowl", "glass", "cup", "bottle"]`. Funciona sin entrenar. Probarlo primero en los videos grabados.
2. **Fine-tune rápido:** si (1) falla con la vista cenital, etiquetar 150–300 frames propios (Roboflow o Label Studio, o auto-etiquetar con (1) y corregir) y fine-tunear un YOLO chico (`n`/`s`) ~30–50 épocas. En GPU de AWS (`g5.xlarge`) son minutos.
3. Fallback geométrico: `cv2.HoughCircles` sobre la zona de la mesa (los platos desde arriba son círculos). Feo pero funciona.

### 2. Tracking

`model.track(..., persist=True)` (ByteTrack integrado en Ultralytics) da un id por objeto. Armar `track_id = f"{table_id}-{p|d}-{id}"`.

### 3. Asignación a mesa

Centro del bbox dentro del polígono de la mesa (`cv2.pointPolygonTest`). Zonas en `vision/config/zones.json` ([03-contratos.md](03-contratos.md)). Hacer un script `vision/tools/draw_zones.py` que muestre un frame y deje clickear los vértices.

### 4. Estado del plato (lo más importante)

Dos caminos en paralelo; el que ande mejor gana:

**Plan A — Claude visión (rápido de tener andando).**
Cada 10–15 s por mesa, mandar el recorte de la mesa (no el frame entero) a Claude vía AWS Bedrock (usar créditos) con un prompt que devuelva JSON:
`[{ "kind": "plate", "state": "comiendo", "fill_ratio": 0.5 }]`. Pocas líneas de código y muy preciso. Contra: latencia ~1–3 s y costo por llamada; por eso se llama poco y solo con recortes. Usar el modelo Haiku más reciente disponible en Bedrock.

**Plan B — local, sin nube.**
- *Heurística de píxeles:* dentro del círculo del plato, medir qué fracción de píxeles no es "color de plato" (segmentar el color del plato vacío con HSV / k-means) → `fill_ratio`. Umbrales: `>0.75 lleno`, `0.30–0.75 comiendo`, `0.05–0.30 por_terminar`, `<0.05 vacio`.
- *Clasificador:* con los recortes que etiquetó el Plan A (¡Claude como etiquetador!), entrenar un `yolo11n-cls` o similar con 4 clases. Corre en tiempo real en la laptop.

Recomendación: arrancar con A para tener la demo punta a punta temprano; usar A para etiquetar datos y pasar a B si llega.

Bebidas: mismo esquema con 3 estados (`llena`, `media`, `vacia`); si no da el tiempo, dejar solo platos.

### 5. Suavizado temporal

La clasificación por frame "tiembla". Por cada `track_id`:
- Ventana de los últimos N=8 estados → **voto mayoritario**.
- Solo cambiar de estado si el nuevo gana en ≥ 3 lecturas seguidas (histéresis).
- Los estados solo deberían avanzar (`lleno → comiendo → por_terminar → vacio`); un retroceso se ignora salvo que se mantenga mucho (plato nuevo en el mismo lugar → nuevo track).
- Si el objeto se tapa (mano, brazo) y desaparece < 30 s, mantener el último estado.

### 6. Salida

Cada 2–5 s, `POST /api/observations` con el formato del contrato. Si el backend no responde, loguear y seguir (no frenar el loop de cámara).

## Estructura sugerida

```
vision/
  main.py               # loop: captura → pipeline → envío
  config/zones.json
  servio_vision/
    capture.py          # webcam / RTSP / archivo de video
    detector.py         # YOLO-E / YOLO fine-tuneado
    zones.py
    state_llm.py        # Plan A (Bedrock)
    state_local.py      # Plan B (heurística / clasificador)
    smoothing.py
    client.py           # POST al backend (con modo --dry-run que imprime)
  tools/
    draw_zones.py
    record.py           # grabar videos de prueba
    export_crops.py     # guardar recortes para dataset
  requirements.txt
```

`python main.py --source videos/mesa1.mp4 --dry-run` tiene que funcionar sin backend.
