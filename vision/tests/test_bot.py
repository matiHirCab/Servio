"""El bot del grupo (`alerts/bot.py`): desde dónde escucha y cómo reparte."""

import threading
import time

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


def metrics_update(update_id: int, date: float | None = None) -> dict:
    message: dict = {"chat": {"id": -1}, "text": "/metricas"}
    if date is not None:
        message["date"] = int(date)
    return {"update_id": update_id, "message": message}


def tap(update_id: int) -> dict:
    return {"update_id": update_id, "callback_query": {"id": "cb", "data": "fb:x:0"}}


class Desk:
    def __init__(self) -> None:
        self.handled: list[dict] = []

    def handle(self, update: dict) -> None:
        self.handled.append(update)


def run(channel: Channel) -> list[str]:
    ran: list[str] = []
    b = Bot(channel, commands={"metricas": lambda: ran.append("metricas")})
    b.start()
    assert channel.done.wait(5)
    b.stop()
    return ran


def test_pending_updates_are_skipped_when_the_channel_answers():
    channel = Channel([[metrics_update(7)], []])
    assert run(channel) == []  # el /metricas pendiente no corre
    assert channel.offsets[:2] == [-1, 8]  # sigue después del último pendiente


def test_without_network_at_start_old_messages_are_skipped_but_taps_are_not():
    """Sin red al arrancar no se sabe desde dónde seguir: se lee todo, se saltean
    los mensajes de antes del arranque (cada `/metricas` de ayer mandaría la imagen
    otra vez) y se manejan los toques, que pueden ser de avisos de esta corrida."""
    yesterday = time.time() - 86400
    channel = Channel(
        [None, [metrics_update(1, yesterday), tap(2), metrics_update(3, time.time())]]
    )
    desk = Desk()
    ran: list[str] = []
    b = Bot(channel, desk, {"metricas": lambda: ran.append("metricas")})  # type: ignore[arg-type]
    b.start()
    assert channel.done.wait(5)
    b.stop()
    assert ran == ["metricas"]  # solo el de ahora
    assert [u["update_id"] for u in desk.handled] == [2]
    assert channel.offsets[:2] == [-1, None]


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
    b.stop(wait_s=5)  # no está manejando nada: vuelve enseguida
    release.set()
    assert b._thread is not None
    b._thread.join(5)
    assert not b._thread.is_alive()
    assert ran == []


def test_stop_waits_for_an_update_being_handled():
    """`close()` no cierra el log ni la base con una respuesta a medio guardar."""
    handling = threading.Event()
    finish = threading.Event()
    done: list[str] = []

    def slow_metrics() -> None:
        handling.set()
        finish.wait(5)
        done.append("metricas")

    channel = Channel([[], [metrics_update(4)]])
    b = Bot(channel, commands={"metricas": slow_metrics})
    b.start()
    assert handling.wait(5)
    threading.Timer(0.2, finish.set).start()
    b.stop(wait_s=5)
    assert done == ["metricas"]  # stop esperó a que terminara


def test_stop_does_not_wait_for_the_long_poll():
    """Si el bot solo está esperando al canal, `stop` vuelve sin esperar."""
    polling = threading.Event()

    class Waiting(Channel):
        def get_updates(self, offset, timeout=20):
            if offset == -1:
                return []
            polling.set()
            threading.Event().wait(1)
            return []

    b = Bot(Waiting([]))
    b.start()
    assert polling.wait(5)
    started = time.monotonic()
    b.stop(wait_s=5)
    assert time.monotonic() - started < 0.5


def test_a_new_message_is_not_skipped_if_this_clock_is_ahead():
    """El filtro de mensajes viejos es solo para un arranque sin red, y con margen:
    con el reloj de esta máquina adelantado, una respuesta nueva no se pierde."""
    ahead = time.time() - 90  # Telegram dice que llegó "90 s antes" del arranque
    channel = Channel([[], [metrics_update(5, ahead)]])
    assert run(channel) == ["metricas"]


def test_the_old_message_filter_turns_off_once_caught_up():
    """Arrancó sin red: el filtro de mensajes viejos vale solo hasta ponerse al
    día. Después, un mensaje con fecha "vieja" (el reloj se desfasó) se maneja."""
    old = time.time() - 86400
    channel = Channel([None, [metrics_update(1, old)], [metrics_update(2, old)]])
    assert run(channel) == ["metricas"]  # el primero se saltea, el segundo no
