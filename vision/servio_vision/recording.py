"""Grabar y reproducir sesiones (B-1): `main.py --record DIR` y `main.py --source DIR`.

Una **sesión** es una carpeta con:

- un JPEG por lectura, con su hora en el nombre (`20261028T203015.250000Z.jpg`), a la
  cadencia del loop y no el video entero. Los frames llegan ya anonimizados de
  `capture.py` (C-3): las grabaciones tampoco tienen caras;
- `sesion.json`: cuándo empezó, las reglas con las que corrió (cadencia, umbrales de
  los avisos, suavizado), las zonas de las mesas si eran fijas, el local y con qué
  clasificador se grabó;
- `eventos.jsonl`: las observaciones y avisos de esa corrida (una copia del log de
  `alerts` de esta corrida sola, que escribe `alerts` mientras graba).

Solo con `--record` queda alguna imagen en disco. Las sesiones van a la carpeta
compartida del equipo, nunca al repo (`vision/out/` está ignorado).

Reproducir: `main.py --source DIR --classifier hybrid` lee los frames con sus horas
reales (`capture.read_session`), usa las mismas reglas y las mismas zonas
(`app.session_settings`), y los avisos se comparan con los grabados con
`tools/comparar_sesion.py DIR`.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import numpy as np
from pydantic import ValidationError

from servio_vision.timestamps import iso
from servio_vision.zones import Zones, to_jpeg

logger = logging.getLogger(__name__)

JPEG_QUALITY = 90  # más que los recortes (85): es lo que se guarda para reproducir
SESSION_FILE = "sesion.json"
EVENTS_FILE = "eventos.jsonl"
REPLAY_EVENTS_FILE = "reproduccion.jsonl"  # los eventos de la última reproducción
FRAME_FORMAT = "%Y%m%dT%H%M%S.%fZ"
ALERT_EVENTS = ("alert_opened", "alert_reminder", "alert_resolved")


class FrameRecorder:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self.saved = 0
        logger.info("Grabando los frames procesados en %s", directory)

    def save(self, frame: np.ndarray, ts: datetime) -> Path:
        path = self.directory / frame_name(ts)
        # to_jpeg + write_bytes y no cv2.imwrite: en Windows, imwrite no escribe
        # rutas con tildes o eñes (`out/grabación`)
        path.write_bytes(to_jpeg(frame, JPEG_QUALITY))
        self.saved += 1
        return path


class SessionRecorder(FrameRecorder):
    """Una sesión completa: los frames y `sesion.json` (que aparece con el primer
    frame: una corrida que no llegó a leer nada no deja una sesión). Los eventos los
    escribe `alerts` en la misma carpeta (`mirror_log`)."""

    def __init__(
        self,
        directory: Path,
        *,
        rules: dict[str, Any],
        local_id: str,
        source: str,
        classifier: str,
        zones: Zones | None,
        live: bool,
    ) -> None:
        """`rules`: la configuración que decide los avisos (cadencia, umbrales,
        suavizado), para reproducir con la misma. `live`: venía de una cámara (pudo
        retomar avisos abiertos de la base; la reproducción arranca de cero).
        `OSError` si la carpeta ya tiene una sesión: dos grabaciones mezcladas no se
        pueden reproducir."""
        super().__init__(directory)  # `run` ya verificó la carpeta (ensure_free)
        self.info: dict[str, Any] = {
            "version": 1,
            "started_at": iso(datetime.now(UTC)),
            "rules": rules,
            "live": live,
            "local_id": local_id,
            "source": redact_source(source),  # sin usuario ni clave de la cámara
            "classifier": classifier,
            "anonymized": True,  # los frames llegan de capture.py ya sin caras (C-3)
            "zones": zones.model_dump(mode="json") if zones is not None else None,
        }

    def save(self, frame: np.ndarray, ts: datetime) -> Path:
        path = super().save(frame, ts)
        if self.saved == 1:
            self._write_info()  # recién ahora es una sesión
        return path

    def _write_info(self) -> None:
        (self.directory / SESSION_FILE).write_text(
            json.dumps(self.info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def finish(self, *, truncated: bool = False) -> int:
        """Cierra la sesión: cuántas lecturas y cuándo terminó. `truncated`: la
        grabación se cortó antes que la corrida (error de disco), queda anotado. Sin
        ningún frame no hay sesión: no escribe nada. Devuelve cuántos frames."""
        if self.saved == 0:
            logger.warning(
                "No se grabó ningún frame en %s: no queda sesión", self.directory
            )
            return 0
        self.info["readings"] = self.saved
        self.info["ended_at"] = iso(datetime.now(UTC))
        self.info["truncated"] = truncated
        if truncated:
            logger.error(
                "La grabación en %s se cortó a los %d frames: los avisos de después"
                " no se van a poder reproducir",
                self.directory,
                self.saved,
            )
        self._write_info()
        logger.info("Sesión guardada en %s: %d frames", self.directory, self.saved)
        return self.saved


def redact_source(source: str) -> str:
    """`rtsp://user:clave@cam/...` → `rtsp://cam/...`: la sesión va a la carpeta
    compartida; la clave de la cámara no."""
    if "://" not in source:
        return source
    parts = urlsplit(source)
    if "@" not in parts.netloc:
        return source
    host = parts.netloc.rsplit("@", 1)[1]
    return urlunsplit(parts._replace(netloc=host))


def ensure_free(directory: Path, log_path: Path | None = None) -> None:
    """`FileExistsError` si la carpeta ya tiene una sesión grabada, o es la del log
    de `alerts` (la copia de los eventos pisaría el log compartido)."""
    if log_path is not None and directory.resolve() == log_path.parent.resolve():
        raise FileExistsError(
            f"{directory} es la carpeta del log de avisos: grabá en otra"
            " (por ejemplo out/sesiones/<nombre>)"
        )
    if directory.is_dir() and (
        (directory / SESSION_FILE).exists() or any(directory.glob("*.jpg"))
    ):
        raise FileExistsError(
            f"{directory} ya tiene una sesión grabada: usá otra carpeta para no"
            " mezclar dos grabaciones"
        )


def frame_name(ts: datetime) -> str:
    """`ts` → `20261028T203015.250000Z.jpg`, en UTC y con microsegundos: ordena bien y
    dos lecturas no caen en el mismo nombre."""
    return ts.astimezone(UTC).strftime(FRAME_FORMAT) + ".jpg"


def frame_time(path: Path) -> datetime:
    """La hora de un frame grabado, desde su nombre (`ValueError` si no es uno)."""
    return datetime.strptime(path.stem, FRAME_FORMAT).replace(tzinfo=UTC)


def is_session(path: Path) -> bool:
    return path.is_dir() and (path / SESSION_FILE).is_file()


def read_info(directory: Path) -> dict[str, Any]:
    """`sesion.json` de una sesión grabada."""
    try:
        info = json.loads((directory / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OSError(f"{directory / SESSION_FILE} no se puede leer: {e}") from e
    if not isinstance(info, dict):
        raise OSError(f"{directory / SESSION_FILE} no es un objeto JSON")
    return info


def frames_anonymized(directory: Path) -> bool:
    """La sesión dice que sus frames ya salieron sin caras (las graba `--record`)."""
    try:
        return bool(read_info(directory).get("anonymized"))
    except OSError:
        return False


def session_zones(directory: Path) -> Zones | None:
    """Las zonas fijas con las que se grabó, o `None` si se detectaban solas."""
    raw = read_info(directory).get("zones")
    if not raw:
        return None
    try:
        return Zones.model_validate(raw)
    except ValidationError as e:
        raise OSError(f"{directory / SESSION_FILE}: zonas inválidas: {e}") from e


def session_frames(directory: Path) -> list[tuple[Path, datetime]]:
    """Los frames de la sesión en orden, con su hora. Un archivo que no es un frame
    (otra cosa que alguien dejó en la carpeta) se saltea con aviso."""
    frames = []
    for path in sorted(directory.glob("*.jpg")):
        try:
            frames.append((path, frame_time(path)))
        except ValueError:
            logger.warning("%s no es un frame de la sesión, lo salteo", path.name)
    if not frames:
        raise FileNotFoundError(f"La sesión {directory} no tiene frames")
    return frames


def last_run_lines(log_path: Path) -> list[str]:
    """Las líneas del log de `alerts` desde su último `run_started` (incluido): la
    reproducción recién hecha, para compararla. Lee el log entero (crece todo el
    día); alcanza para una herramienta que se corre a mano."""
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if '"run_started"' in line and _event(line) == "run_started":
            start = i
    return [line for line in lines[start:] if line.strip()]


def _event(line: str) -> str | None:
    try:
        data = json.loads(line)
    except ValueError:
        return None
    return data.get("event") if isinstance(data, dict) else None


def alert_signature(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """Los avisos de una corrida, como (evento, tipo, mesa, ts): lo que tiene que
    coincidir entre la grabación y la reproducción."""
    found = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict) or data.get("event") not in ALERT_EVENTS:
            continue
        try:
            found.append((data["event"], data["type"], data["table_id"], data["ts"]))
        except KeyError as e:
            logger.warning("Aviso sin %s en el log, lo salteo: %s", e, line[:80])
    return found
