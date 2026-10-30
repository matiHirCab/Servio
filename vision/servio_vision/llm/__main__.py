"""Prueba rápida: `python -m servio_vision.llm plato1.jpg [plato2.jpg ...]`."""

import logging
import sys
from pathlib import Path

from servio_vision.llm import EstimateRequest, make_estimator


def main(argv: list[str]) -> int:
    if not argv:
        print("Uso: python -m servio_vision.llm plato1.jpg [plato2.jpg ...]")
        return 2
    logging.basicConfig(level=logging.INFO)
    plates = [Path(p).read_bytes() for p in argv]
    report = make_estimator().estimate(EstimateRequest(plates, "T1"))
    if report is None:
        print("Claude no devolvió una respuesta válida")
        return 1
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
