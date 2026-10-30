"""El bot del grupo (`alerts/bot.py`): desde dónde escucha y cómo reparte."""

import threading

from servio_vision.alerts import bot
from servio_vision.alerts.bot import Bot


class Channel:
    """Canal falso: `answers` es lo que devuelve cada `get_updates`, en orden (None
    es «falló»); cuando se acaban, nada nuevo y avisa que terminó."""

    def __init__(self, answers) -> None:
        self.answers = list(answers)
        self.offsets: list[int | None] = []
        self.done = threading.Event()

    def get_updates(self, offset, timeout=20):
        self.offsets.append(offset)
        if self.answers:
            return self.answers.pop(0)
        self.done.set()
        return []

    def is_group(self, chat_id) -> bool:
        return chat_id == -1


def metrics_update(update_id: int) -> dict:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": -1}, "text": "/metricas"},
    }


def run(channel: Channel) -> list[str]:
    ran: list[str] = []
    b = Bot(channel, commands={"metricas": lambda: ran.append("metricas")})
    b.start()
    assert channel.done.wait(5)
    b.stop()
    return ran


def test_old_updates_are_skipped_even_if_the_first_call_fails(monkeypatch):
    """Sin red al arrancar no se sabe desde dónde seguir: se insiste, en vez de
    leer todo lo viejo (cada `/metricas` de ayer mandaría la imagen otra vez)."""
    monkeypatch.setattr(bot, "RETRY_START_S", 0.01)
    channel = Channel([None, None, [metrics_update(7)]])
    assert run(channel) == []  # el /metricas viejo no corre
    assert channel.offsets[:3] == [-1, -1, -1]
    assert channel.offsets[3] == 8  # sigue después del último pendiente


def test_new_updates_are_handled_after_the_pending_ones():
    channel = Channel([[], [metrics_update(3)]])
    assert run(channel) == ["metricas"]
    assert channel.offsets[:2] == [-1, None]  # sin pendientes: desde lo próximo


def test_updates_that_arrive_after_stop_are_not_handled():
    """La consulta larga no se puede cortar: lo que traiga después de `stop()` ya
    no se maneja (el log y la base de la corrida están cerrados)."""
    polling = threading.Event()
    release = threading.Event()

    class Slow(Channel):
        def get_updates(self, offset, timeout=20):
            if offset == -1:
                return []
            polling.set()
            release.wait(5)  # la consulta larga, en curso al cerrar
            return [metrics_update(9)]

    ran: list[str] = []
    b = Bot(Slow([]), commands={"metricas": lambda: ran.append("metricas")})
    b.start()
    assert polling.wait(5)
    b.stop()
    release.set()
    b.stop(wait_s=5)  # espera a que el hilo termine
    assert b._thread is not None and not b._thread.is_alive()
    assert ran == []
