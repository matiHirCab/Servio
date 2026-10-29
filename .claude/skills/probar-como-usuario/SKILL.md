---
name: probar-como-usuario
description: Levanta Servio entero en local (video de prueba, avisos, base, métricas) y lo revisa como lo usarían el mozo, el encargado y el dueño. Usar cuando pidan probar Servio de punta a punta, "como usuario", o antes de mostrar una demo.
---

# Probar Servio como usuario

Un solo comando levanta todo y deja lo que ve cada rol en `vision/out/prueba_usuario/`. Funciona en la nube (sin Claude, sin Telegram, sin YOLO) y en la PC de Windows.

## 1. Preparar (una vez por máquina)

```bash
cd vision
pip install -r requirements.txt          # alcanza para el clasificador fake
```

- **Detector de caras:** la primera corrida baja `models/face_detection_yunet_2023mar.onnx` (~230 KB). Si no hay internet, copialo de la carpeta del equipo (en el proyecto de Claude: `/mnt/project-files/servio-modelos/`) a `vision/models/`, o apuntá `FACE_MODEL` a él.
- Para `--classifier hybrid` además: `pip install -r requirements-yolo.txt` y Claude (`claude -p` o `ANTHROPIC_API_KEY`). En la nube no hay `claude -p`: usá `fake`.

## 2. Levantar

```bash
python tools/probar_usuario.py                    # fake, 3 mesas, ~40 s
python tools/probar_usuario.py --mesas 4          # las 4 mesas de salon.json
python tools/probar_usuario.py --source "..\video demo test.mp4" --classifier hybrid   # PC de Tomás
```

Arma un video de prueba (platos que se llenan, se comen y se retiran), aparta el log y la base anteriores a `out/respaldo/`, corre la vista de reconocimiento sin ventana con `--demo` y sin Telegram, y al final imprime los chequeos. Sale con 1 si alguno falla.

## 3. Revisar como cada rol

Leé `out/prueba_usuario/INFORME.md` y después, rol por rol (qué ve cada uno está en `docs/context/12-roles.md` y `vision/config/servio.toml`):

- **Mozo** (Juan, Sofía, Martín, Lucía según `fixtures/restaurante/salon.json`):
  - Mirá 2 o 3 `vista_*.jpg` con Read: ¿cada mesa tiene el color de su estado y cada plato su estado? ¿se entiende sin explicación?
  - Leé `mozo_<nombre>.txt`: ¿le llegan solo avisos de sus mesas?, ¿el texto dice qué hacer?, ¿los recordatorios son demasiados?, ¿el aviso se cierra cuando corresponde?
  - `metricas_mozo.txt` tiene que decir que no ve métricas.
- **Encargado:** `metricas_encargado.txt`, `resumen.txt` y `resumen.png`. ¿Le sirve para operar el turno? ¿Falta algo de su lista de métricas que todavía no se calcula?
- **Dueño:** `metricas_dueno.txt`, `metricas.png`. Tiene que ver todo lo del encargado más plata e historia (las que todavía no existen, anotarlas como faltantes).

## 4. Informar

Respondé con: los chequeos (✅/❌), y por rol lo que funciona, lo que confunde y lo que falta, citando el archivo y la línea o la imagen. Separá bugs (algo que no hace lo que dice `docs/context/`) de sugerencias. No cambies código en la misma pasada salvo que lo pidan.

Notas:
- El clasificador `fake` no mira la imagen: el video de prueba dibuja los platos con el mismo guion, así que lo que se ve coincide con lo que se "lee". Para probar la visión de verdad hace falta `hybrid` con un video real.
- `--conservar` no aparta el log ni la base; `-- <flags>` pasa flags extra a `tools/vista_demo.py`.
- Todo queda en `vision/out/` (ignorado por git): nunca lo subas.
