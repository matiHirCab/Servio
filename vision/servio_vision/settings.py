"""Configuración en un solo lugar (A-1).

Un objeto `Settings` con todo lo que antes se leía con `os.getenv` por ahí: el
proveedor del modelo, el suavizado, YOLO, Telegram, los umbrales de avisos, el
detector de caras y la base. Más lo que describe **la casa** (`servio.toml`): la
cámara, la cadencia, el clasificador, las zonas de las mesas y los datos del
restaurante. Este módulo es el **único** que mira el entorno.

Prioridad, de más a menos:
1. **flags** de la línea de comandos (`Settings.load(..., **flags)`), para pisar algo
   puntual;
2. **`servio.toml`** de la casa (`--config casa.toml`), claves planas con el mismo
   nombre que los campos de abajo;
3. **entorno y `.env`** del repo (`.env.example` documenta cada variable; se cargan
   en `os.environ`, así los SDK de Anthropic/OpenAI y `claude -p` también los ven);
4. los **defaults** de este archivo.

Los módulos reciben los valores por parámetro desde `app.py`. Cuando se los
construye sin parámetros (herramientas, tests) toman `current()`: la configuración
que `cli.load_settings` dejó activa al arrancar o, si no hay, la del entorno.

    python main.py --config config/casa.toml            # todo del toml y el .env
    python main.py --config config/casa.toml --demo     # el flag pisa al toml
"""

import tomllib
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from servio_vision import ENV_FILE as DEFAULT_ENV_FILE
from servio_vision import VISION_DIR

REPO_DIR = VISION_DIR.parent
PROMPTS_DIR = VISION_DIR / "prompts"

# Los tests lo ponen en None: el `.env` de la máquina no cambia sus resultados.
ENV_FILE: Path | None = DEFAULT_ENV_FILE

Provider = Literal["cli", "anthropic", "openai"]
ClassifierName = Literal["fake", "yolo", "hybrid", "claude"]

DEFAULT_INTERVAL = 30  # segundos entre lecturas (04-vision.md)
DEFAULT_MODELS: dict[str, str] = {
    "cli": "haiku",
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-6-luna",
}
# `claude -p` tarda ~6-8 s con 8 platos (arranque del CLI incluido). openai: unas 25
# preguntas tipadas y ~17 imágenes en una mesa de 8 platos con referencias. Con el
# reintento, el peor caso es 2 × timeout + la espera: 14 s deja 30 s para la lectura.
DEFAULT_TIMEOUTS: dict[str, float] = {"cli": 15.0, "anthropic": 10.0, "openai": 14.0}

# Rutas relativas de archivos del repo cuentan desde la raíz del repo (como en
# `.env.example`: `YOLO_WEIGHTS=vision/models/best.pt`), no desde donde se corre.
REPO_RELATIVE = (
    "claude_prompt_file",
    "openai_prompt_file",
    "yolo_weights",
    "face_model",
)

# Claves de la casa: en el toml y los flags van sin prefijo; en el entorno, con
# `SERVIO_` (su alias), así un `SOURCE` o `RECORD` cualquiera de la máquina no se cuela.
HOUSE_KEYS = (
    "source",
    "classifier",
    "demo",
    "zones",
    "interval",
    "speed",
    "telegram",
    "restaurante",
    "yolo_conf",
    "record",
    "fake_mode",
    "fixture",
)

# Rutas del toml que no son archivos del repo: relativas a la carpeta del toml, así
# `zones = "zones.json"` al lado de `casa.toml` anda desde cualquier carpeta.
TOML_PATH_KEYS = ("zones", "record", "restaurante", "fixture", "source")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        extra="ignore",  # el entorno tiene de todo (el toml se valida aparte)
        case_sensitive=False,
        env_ignore_empty=True,  # `CLAUDE_MODEL=` en el .env es "sin valor"
    )

    # --- la casa: cámara, cadencia, mesas, restaurante (servio.toml o flags) ---
    source: str | None = Field(None, validation_alias="servio_source")
    """video, carpeta de fotos, foto, 0 o URL de la cámara"""
    classifier: ClassifierName = Field("fake", validation_alias="servio_classifier")
    zones: Path | None = Field(None, validation_alias="servio_zones")
    """zonas fijas (zones.json); sin esto se detectan solas"""
    interval: float = Field(DEFAULT_INTERVAL, ge=1, validation_alias="servio_interval")
    """segundos entre lecturas"""
    speed: float | None = Field(None, gt=0, validation_alias="servio_speed")
    """reproducir un video a esta velocidad"""
    demo: bool = Field(False, validation_alias=AliasChoices("servio_demo", "demo_mode"))
    """umbrales de demo (DEMO_MODE en el .env)"""
    telegram: bool = Field(True, validation_alias="servio_telegram")
    """False: los avisos salen por consola"""
    restaurante: Path | None = Field(None, validation_alias="servio_restaurante")
    """carta.json y salon.json (por defecto fixtures/restaurante)"""
    yolo_conf: float | None = Field(
        None, ge=0, le=1, validation_alias="servio_yolo_conf"
    )
    finished_after: float | None = Field(None, ge=0)
    """por_terminar → vacio a los tantos segundos (FINISHED_AFTER)"""
    record: Path | None = Field(None, validation_alias="servio_record")
    """guardar los frames procesados acá (B-1)"""
    fake_mode: Literal["script", "replay"] = Field(
        "script", validation_alias="servio_fake_mode"
    )
    fixture: Path | None = Field(None, validation_alias="servio_fixture")
    """jsonl para fake_mode = replay"""
    database_url: str | None = None  # Postgres compartido (A-6); sin esto, SQLite

    # --- modelo de platos ---
    claude_provider: Provider = "cli"
    claude_model: str | None = None  # cli y anthropic; por defecto DEFAULT_MODELS
    openai_model: str | None = None
    claude_timeout_s: float | None = Field(None, gt=0)
    openai_timeout_s: float | None = Field(None, gt=0)
    claude_cli: str | None = None  # ruta a claude.exe si no está en el PATH
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    claude_prompt_file: Path | None = None  # otro prompt (vision/prompts/claude.txt)
    openai_prompt_file: Path | None = None  # (vision/prompts/openai.json)

    # --- suavizado (04-vision.md, «Suavizado») ---
    smooth_readings: int = Field(2, ge=1, le=10)
    smooth_hard_readings: int = Field(3, ge=1, le=10)
    smooth_min_confidence: float = Field(0.8, ge=0, le=1)

    # --- YOLO ---
    yolo_weights: Path | None = None  # entrenados en Colab; sin esto, YOLOE
    yolo_device: str = "cpu"  # cpu o 0 (GPU)

    # --- caras (privacy.py, C-3) ---
    face_model: Path | None = None  # YuNet .onnx; por defecto se baja a models/

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_group_chat_id: str = ""

    # --- umbrales de avisos en segundos (05-reglas-alertas.md); None = default ---
    alert_t1: int | None = Field(None, ge=1)
    alert_t2: int | None = Field(None, ge=1)
    alert_reminder: int | None = Field(None, ge=1)

    @field_validator(*REPO_RELATIVE, mode="after")
    @classmethod
    def _from_repo_root(cls, path: Path | None) -> Path | None:
        if path is None or path.is_absolute():
            return path
        return REPO_DIR / path

    # --- derivados, para que los módulos no repitan la regla ---

    def model_for(self, provider: str | None = None) -> str:
        provider = provider or self.claude_provider
        configured = self.openai_model if provider == "openai" else self.claude_model
        return configured or DEFAULT_MODELS[provider]

    def timeout_for(self, provider: str | None = None) -> float:
        provider = provider or self.claude_provider
        configured = (
            self.openai_timeout_s if provider == "openai" else self.claude_timeout_s
        )
        return configured if configured is not None else DEFAULT_TIMEOUTS[provider]

    def prompt_file_for(self, provider: str | None = None) -> Path:
        provider = provider or self.claude_provider
        if provider == "openai":
            return self.openai_prompt_file or PROMPTS_DIR / "openai.json"
        return self.claude_prompt_file or PROMPTS_DIR / "claude.txt"

    @classmethod
    def load(
        cls,
        config: Path | str | None = None,
        env_file: Path | None | Literal["default"] = "default",
        **flags: Any,
    ) -> "Settings":
        """Arma la configuración: `flags` (solo los que no son None) pisan al toml
        `config`, que pisa al entorno (`env_file` cargado en `os.environ`)."""
        if env_file == "default":
            env_file = ENV_FILE
        if env_file is not None:
            load_dotenv(env_file)  # no pisa variables ya puestas
        values: dict[str, Any] = {}
        if config is not None:
            values.update(read_toml(Path(config)))
        values.update({k: v for k, v in flags.items() if v is not None})
        # pydantic valida por alias: las claves de la casa van con su prefijo
        return cls(**{_alias(k): v for k, v in values.items()})


def _alias(key: str) -> str:
    return f"servio_{key}" if key in HOUSE_KEYS else key


def read_toml(path: Path) -> dict[str, Any]:
    """Claves planas del `servio.toml`. Una tabla (`[camara]`) se aplana: sus claves
    valen igual que sueltas, así el archivo se puede agrupar para leerlo mejor. Una
    clave que Servio no conoce es un error (un typo no puede pasar en silencio)."""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"No existe la configuración {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path} no es un TOML válido: {e}") from e
    flat: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, dict):
            flat.update(value)
        else:
            flat[key] = value
    unknown = sorted(k for k in flat if k not in Settings.model_fields)
    if unknown:
        raise ValueError(
            f"{path}: claves que Servio no conoce: {', '.join(unknown)} "
            "(ver vision/config/servio.example.toml)"
        )
    for key in TOML_PATH_KEYS:
        value = flat.get(key)
        if isinstance(value, str) and _is_relative_path(key, value):
            resolved = path.parent / value
            flat[key] = str(resolved) if key == "source" else resolved
    return flat


def _is_relative_path(key: str, value: str) -> bool:
    if key == "source" and (value.isdigit() or "://" in value):
        return False  # cámara o stream, no una ruta
    return not Path(value).is_absolute()


_active: Settings | None = None


def activate(settings: Settings) -> Settings:
    """Deja esta configuración (flags > toml > entorno) como la del proceso: es la
    que devuelve `current()` a los módulos que se construyen sin parámetros. La
    llama `cli.load_settings` al arrancar."""
    global _active
    _active = settings
    return settings


def current() -> Settings:
    """La configuración del proceso (`activate`, con toml y flags) o, si todavía no
    se cargó ninguna, la del entorno de este momento."""
    return _active if _active is not None else Settings.load()
