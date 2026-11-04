# Servio en un contenedor, para dejarlo corriendo en un servidor chico (CPU, sin GPU).
#
#   docker compose up -d --build        (ver compose.yaml)
#
# Adentro no hay `claude` CLI: compose pone CLAUDE_PROVIDER=anthropic (o el de
# SERVIO_CLAUDE_PROVIDER) y hace falta su API key en el .env.
FROM python:3.11-slim

# libgl/glib: los pide opencv-python (lo trae ultralytics aunque no haya pantalla).
# git: ultralytics instala el tokenizador de CLIP desde GitHub la primera vez que usa YOLOE.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 git \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YOLO_CONFIG_DIR=/tmp/ultralytics \
    YOLO_DEVICE=cpu

WORKDIR /app/vision

# torch de CPU primero: si no, pip baja la build con CUDA (varios GB que no se usan).
# Versiones fijas (las que andan en el equipo) para que un rebuild dé lo mismo.
RUN pip install torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/cpu

COPY vision/requirements.txt vision/requirements-yolo.txt ./
# python-dotenv[cli]: el entrypoint carga /app/.env antes de arrancar (ver abajo).
# anthropic/openai: los SDKs de los proveedores por API (servio_vision/llm/ los importa al usarlos).
RUN pip install -r requirements.txt -r requirements-yolo.txt "ultralytics==8.4.181" \
    "python-dotenv[cli]" anthropic "openai>=3.26.0"

# Se bajan en el build los pesos de YOLOE y MobileCLIP (y se instala CLIP), así el
# contenedor arranca sin descargar nada cuando no hay YOLO_WEIGHTS entrenado.
RUN python -c "from ultralytics import YOLOE; m = YOLOE('yoloe-11s-seg.pt'); m.get_text_pe(['plate', 'bowl'])"

COPY vision/ ./

# .env, config/ (zones.json), models/ (best.pt) y out/ (eventos.jsonl) se montan como volúmenes.
# El .env lo carga dotenv y no `env_file` de compose: compose toma el comentario como valor
# en líneas como `CLAUDE_MODEL=   # ...`, y el clasificador lee las variables antes que alerts.
# (Cargarlo al principio de main.py evitaría este wrapper; main.py es de Jessica, queda propuesto.)
# --no-override: las variables que pone compose (YOLO_WEIGHTS, CLAUDE_PROVIDER, DEMO_MODE) le ganan al .env.
ENTRYPOINT ["dotenv", "-f", "/app/.env", "run", "--no-override", "python", "main.py"]
CMD ["--help"]
