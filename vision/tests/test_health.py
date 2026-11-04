"""El latido: si dejan de llegar lecturas, avisa por un canal aparte del de los mozos."""

import time

from servio_vision.health import HealthMonitor


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def monitor(sent: list[str], clock: Clock, ok: bool = True, stall_s: float = 300):
    def send(text: str) -> bool:
        sent.append(text)
        return ok

    return HealthMonitor(send, stall_s=stall_s, clock=clock)


def test_no_alert_while_readings_keep_coming():
    sent, clock = [], Clock()
    m = monitor(sent, clock)
    for _ in range(5):
        clock.now += 60
        m.beat()
        m.check()
    assert sent == []


def test_alerts_once_when_readings_stop_and_again_when_they_return():
    sent, clock = [], Clock()
    m = monitor(sent, clock)
    m.beat()
    clock.now += 299
    m.check()
    assert sent == []  # todavía dentro del margen
    clock.now += 2
    m.check()
    assert len(sent) == 1 and "5 min" in sent[0]
    clock.now += 600
    m.check()
    assert len(sent) == 1  # no repite mientras siga caído
    m.beat()
    m.check()
    assert len(sent) == 2 and "volvió" in sent[1]


def test_a_second_outage_alerts_again():
    sent, clock = [], Clock()
    m = monitor(sent, clock)
    for _ in range(2):
        m.beat()
        m.check()  # ve que volvió (la segunda vez)
        clock.now += 400
        m.check()
    assert len(sent) == 3  # caída, recuperación y caída


def test_alert_is_retried_if_the_channel_failed():
    sent, clock = [], Clock()
    m = monitor(sent, clock, ok=False)
    m.beat()
    clock.now += 400
    m.check()
    m.check()
    assert len(sent) == 2  # no se dio por avisado


def test_a_failing_send_never_raises():
    clock = Clock()

    def boom(text: str) -> bool:
        raise RuntimeError("canal caído")

    m = HealthMonitor(boom, stall_s=1, clock=clock)
    m.beat()
    clock.now += 10
    m.check()  # no lanza


def test_background_thread_checks_and_stops():
    sent: list[str] = []

    def send(text: str) -> bool:
        sent.append(text)
        return True

    m = HealthMonitor(send, stall_s=0.05, check_s=0.01)
    m.start()
    try:
        deadline = time.monotonic() + 2
        while not sent and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        m.stop()
    assert sent, "el hilo no avisó"
    assert not m.running


def test_monitor_only_for_live_sources():
    from servio_vision.app import build_health_monitor
    from servio_vision.settings import Settings

    assert build_health_monitor(Settings.load(source="salon.mp4")) is None
    assert build_health_monitor(Settings.load(source="0")) is not None


def test_stall_threshold_scales_with_a_slow_cadence():
    from servio_vision.app import build_health_monitor
    from servio_vision.settings import Settings

    fast = build_health_monitor(Settings.load(source="0", interval=30))
    slow = build_health_monitor(Settings.load(source="0", interval=300))
    assert fast is not None and slow is not None
    assert fast.stall_s == 300 and slow.stall_s == 1200


def test_health_goes_to_the_admin_chat_not_the_waiters_group(monkeypatch):
    import json

    import httpx

    from servio_vision import app
    from servio_vision.settings import Settings

    chats: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        chats.append(json.loads(request.content)["chat_id"])
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 1}})

    real = httpx.Client
    monkeypatch.setattr(
        httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler))
    )
    settings = Settings.load(
        source="0",
        telegram_bot_token="1:x",
        telegram_group_chat_id="-100",
        telegram_admin_chat_id="777",
    )
    monitor = app.build_health_monitor(settings)
    assert monitor is not None
    assert monitor._send("hola") is True
    assert chats == ["777"]


def test_without_an_admin_chat_the_alert_only_goes_to_the_log():
    from servio_vision.app import build_health_monitor
    from servio_vision.settings import Settings

    monitor = build_health_monitor(Settings.load(source="0", telegram_bot_token="1:x"))
    assert monitor is not None
    assert monitor._send("hola") is True  # ya quedó en el log: no se reintenta
