"""Prueba rápida: `python -m servio_vision.llm plato1.jpg [plato2.jpg ...]`."""

import logging
import sys
from pathlib import Path

from servio_vision.llm import EstimateRequest, UnknownProviderError, make_estimator


def main(argv: list[str]) -> int:
    if not argv:
        print("Uso: python -m servio_vision.llm plato1.jpg [plato2.jpg ...]")
        return 2
    logging.basicConfig(level=logging.INFO)
    try:
        plates = [Path(p).read_bytes() for p in argv]
        estimator = make_estimator()
    except (OSError, UnknownProviderError) as e:
        print(e)
        return 2
    report = estimator.estimate(EstimateRequest(plates, "T1"))
    if report is None:
        print(
            f"El modelo ({estimator.provider}) no devolvió una respuesta válida: "
            "mirá el log de arriba"
        )
        return 1
    print(report.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
