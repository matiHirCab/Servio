"""Grabar y reproducir sesiones (B-1): `main.py --record DIR` y `main.py --source DIR`.

Una **sesión** es una carpeta con:

- un JPEG por lectura, con su hora en el nombre (`20261028T203015.250000Z.jpg`), a la
  cadencia del loop y no el video entero. Los frames llegan ya anonimizados de
  `capture.py` (C-3): las grabaciones tampoco tienen caras;
- `sesion.json`: cuándo empezó, la cadencia, las zonas de las mesas si eran fijas, el
  local y con qué clasificador se grabó;
- `eventos.jsonl`: las observaciones y avisos de esa corrida (la parte del log de
  `alerts` que va de su `run_started` al final).

Solo con `--record` queda alguna imagen en disco. Las sesiones van a la carpeta
compartida del equipo, nunca al repo (`vision/out/` está ignorado).

Reproducir: `main.py --source DIR --classifier hybrid` lee los frames con sus horas
reales (`capture.read_session`), usa la misma cadencia y las mismas zonas, y los
avisos se comparan con los grabados con `tools/comparar_sesion.py DIR`.
"""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from servio_vision.zones import Zones, to_jpeg

logger = logging.getLogger(__name__)

JPEG_QUALITY = 90  # más que los recortes (85): es lo que se guarda para reproducir
SESSION_FILE = "sesion.json"
EVENTS_FILE = "eventos.jsonl"
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
    """Una sesión completa: los frames, `sesion.json` y, al cerrar, los eventos."""

    def __init__(
        self,
        directory: Path,
        *,
        interval: float,
        local_id: str,
        source: str,
        classifier: str,
        zones: Zones | None,
    ) -> None:
        super().__init__(directory)
        self.info: dict[str, Any] = {
            "version": 1,
            "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "interval": interval,
            "local_id": local_id,
            "source": source,
            "classifier": classifier,
            "zones": zones.model_dump(mode="json") if zones is not None else None,
        }
        self._write_info()

    def _write_info(self) -> None:
        (self.directory / SESSION_FILE).write_text(
            json.dumps(self.info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    def finish(self, log_path: Path) -> int:
        """Cierra la sesión: cuántas lecturas y los eventos de esta corrida (la parte
        del log de `alerts` desde su último `run_started`). Devuelve cuántas líneas."""
        self.info["readings"] = self.saved
        self.info["ended_at"] = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        self._write_info()
        lines = last_run_lines(log_path)
        (self.directory / EVENTS_FILE).write_text(
            "".join(line + "\n" for line in lines), encoding="utf-8"
        )
        logger.info(
            "Sesión guardada en %s: %d frames, %d eventos",
            self.directory,
            self.saved,
            len(lines),
        )
        return len(lines)


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
        return json.loads((directory / SESSION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise OSError(f"{directory / SESSION_FILE} no se puede leer: {e}") from e


def session_zones(directory: Path) -> Zones | None:
    """Las zonas fijas con las que se grabó, o `None` si se detectaban solas."""
    raw = read_info(directory).get("zones")
    return Zones.model_validate(raw) if raw else None


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
    """Las líneas del log de `alerts` desde su último `run_started` (incluido)."""
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()
    start = 0
    for i, line in enumerate(lines):
        if line.startswith('{"event": "run_started"'):
            start = i
    return [line for line in lines[start:] if line.strip()]


def alert_signature(lines: list[str]) -> list[tuple[str, str, str, str]]:
    """Los avisos de una corrida, como (evento, tipo, mesa, ts): lo que tiene que
    coincidir entre la grabación y la reproducción."""
    found = []
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if data.get("event") in ALERT_EVENTS:
            found.append((data["event"], data["type"], data["table_id"], data["ts"]))
    return found


def session_zones_arg(source: str | None) -> Zones | None:
    """Zonas de la sesión si `source` es una; `None` si no lo es o se detectaban."""
    if source is None or not is_session(Path(source)):
        return None
    return session_zones(Path(source))
