"""Formato de los ts que escriben el log (`eventos.jsonl`) y la base (`db.py`)."""

from datetime import datetime


def iso(ts: datetime) -> str:
    """El mismo formato que pydantic usa para los ts del log: UTC con "Z"."""
    return ts.isoformat().replace("+00:00", "Z")
