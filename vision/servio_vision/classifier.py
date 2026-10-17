"""Interfaz `Classifier` y registro por nombre (contrato P1 ↔ P3).

Fuente de verdad: docs/context/03-contratos.md («Visión interna»).
Cada implementación vive en `servio_vision/classifiers/` y se importa recién
cuando se pide, así `fake` anda sin instalar boto3, anthropic ni ultralytics.
"""

from importlib import import_module
from typing import Any, Protocol

from servio_vision.schemas import Item, TableReport


class Classifier(Protocol):
    def classify(
        self, image: bytes, table_id: str, previous_items: list[Item]
    ) -> TableReport | None:
        """Lee el recorte JPEG de una mesa.

        `previous_items` son los ítems de la lectura anterior, para mantener ids.
        Devuelve `None` si la lectura falló (se descarta, no cuenta para el suavizado).
        """
        ...


# nombre → "módulo:Clase"
_REGISTRY: dict[str, str] = {
    "fake": "servio_vision.classifiers.fake:FakeClassifier",  # P3
    "claude": "servio_vision.classifiers.claude:ClaudeClassifier",  # Tomás, respaldo
    "yolo": "servio_vision.classifiers.yolo:YoloClassifier",  # Tomás, plan B sin internet
    "hybrid": "servio_vision.classifiers.hybrid:HybridClassifier",  # Tomás, el de la demo
}


def available_classifiers() -> list[str]:
    return sorted(_REGISTRY)


def get_classifier(name: str, **kwargs: Any) -> Classifier:
    """Crea el clasificador `name` ("fake" | "yolo" | "hybrid" | "claude") con `kwargs`."""
    try:
        target = _REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"Clasificador desconocido: {name!r}. Opciones: {available_classifiers()}"
        ) from None
    module_name, class_name = target.split(":")
    try:
        module = import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name == module_name:
            raise NotImplementedError(
                f"El clasificador {name!r} todavía no está implementado ({module_name})."
            ) from e
        raise  # falta una dependencia del clasificador (ej. boto3)
    cls = getattr(module, class_name, None)
    if cls is None:
        raise NotImplementedError(
            f"{module_name} no define la clase {class_name!r} que espera el registro."
        )
    return cls(**kwargs)
