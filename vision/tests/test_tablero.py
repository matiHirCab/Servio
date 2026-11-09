"""Tests de tools/tablero.py: el protocolo para tomar tarjetas sin pisarse, con un Trello falso."""

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import tablero

LISTS = {
    "Semana 3 · 11/11 → 17/11": "L3",
    "Semana 4 · 18/11": "L4",
    "In progress": "LP",
    "Done": "LD",
    "Ideas a futuro": "LI",
}
ME = "me1"


class FakeTrello:
    """Lo justo de la API para el tablero: listas, etiquetas, tarjetas, comentarios."""

    def __init__(self, cards: list[dict]) -> None:
        self.cards = {c["id"]: c for c in cards}
        self.labels: dict[str, dict] = {}
        self.comments: dict[str, list[dict]] = {c["id"]: [] for c in cards}
        self.clock = 0
        self.before_comment_read = None  # gancho para simular una carrera

    def _card_id(self, key: str) -> str:
        for c in self.cards.values():
            if c["id"] == key or c["shortLink"] == key:
                return c["id"]
        raise AssertionError(key)

    def get(self, path, **params):
        if path.endswith("/lists"):
            closed = getattr(self, "closed_lists", set())
            return [
                {"id": lid, "name": name, "closed": lid in closed}
                for name, lid in LISTS.items()
            ]
        if path.endswith("/labels"):
            return list(self.labels.values())
        if path.endswith("/members"):
            return [
                {"id": ME, "username": "jessica"},
                {"id": "m2", "username": "tomas"},
            ]
        if path == "/members/me":
            return {"id": ME}
        if path.endswith("/cards"):
            return [self._out(c) for c in self.cards.values()]
        if path.endswith("/actions"):
            cid = path.split("/")[2]
            if self.before_comment_read:
                self.before_comment_read(cid)
                self.before_comment_read = None
            return list(self.comments[cid])
        if path.startswith("/cards/"):
            return self._out(self.cards[self._card_id(path.split("/")[2])])
        raise AssertionError(path)

    def post(self, path, **params):
        if path == "/labels":
            lid = f"lb{len(self.labels)}"
            self.labels[params["name"]] = {
                "id": lid,
                "name": params["name"],
                "color": params["color"],
            }
            return {"id": lid}
        cid = self._card_id(path.split("/")[2])
        if path.endswith("/comments"):
            return {"id": self.add_comment(cid, params["text"])}
        if path.endswith("/idLabels"):
            self.cards[cid]["idLabels"].append(params["value"])
            return {}
        if path.endswith("/idMembers"):
            if (
                getattr(self, "reject_duplicate_members", False)
                and params["value"] in self.cards[cid]["idMembers"]
            ):
                raise SystemExit("Trello respondió 400: member is already on the card")
            self.cards[cid]["idMembers"].append(params["value"])
            return {}
        raise AssertionError(path)

    def put(self, path, **params):
        cid = self._card_id(path.split("/")[2])
        self.cards[cid]["idList"] = params["idList"]
        return {}

    def delete(self, path, **params):
        parts = path.split("/")
        if parts[1] == "actions":  # borrar un comentario propio
            for rows in self.comments.values():
                rows[:] = [r for r in rows if r["id"] != parts[2]]
            return {}
        cid = self._card_id(parts[2])
        self.cards[cid]["idLabels"].remove(parts[4])
        return {}

    def add_comment(self, cid: str, text: str) -> str:
        self.clock += 1
        row = {
            "id": f"a{self.clock}",
            "date": f"2026-11-09T10:00:{self.clock:02d}.000Z",
            "data": {"text": text},
        }
        self.comments[cid].append(row)
        return row["id"]

    def _out(self, c: dict) -> dict:
        labels = [
            {"name": lb["name"]}
            for lb in self.labels.values()
            if lb["id"] in c["idLabels"]
        ]
        return {**c, "labels": labels}

    def list_of(self, key: str) -> str:
        lid = self.cards[self._card_id(key)]["idList"]
        return next(n for n, i in LISTS.items() if i == lid)

    def label_names(self, key: str) -> set[str]:
        ids = self.cards[self._card_id(key)]["idLabels"]
        return {lb["name"] for lb in self.labels.values() if lb["id"] in ids}


def card(cid, short, name, desc, list_id="L3"):
    return {
        "id": cid,
        "shortLink": short,
        "name": name,
        "desc": desc,
        "idList": list_id,
        "idLabels": [],
        "idMembers": [],
    }


HEADER_C5 = (
    "**Área:** alerts · **Toca:** `vision/servio_vision/alerts/`, `docs/context/05-reglas-alertas.md`\n"
    "**Depende de:** A-4 (aaaaaaa4)\n"
    "**Decisiones:** 5, 6\n\n"
    "Botones «Voy», «Listo»… (ver C-7 (cccccc07), que no es dependencia)."
)
HEADER_C7 = (
    "**Área:** alerts · **Toca:** `vision/servio_vision/alerts/bot.py`\n"
    "**Depende de:** C-5 (cccccc05)\n\n"
    "Cada mozo recibe sus mesas."
)


def board_with(*cards, labels=True):
    api = FakeTrello(list(cards))
    board = tablero.Board(api, board_id="B")
    if labels:
        board.ensure_labels()
    return api, board


# --------------------------------------------------------------- lógica pura


def test_parse_header_reads_only_the_first_block():
    h = tablero.parse_header(HEADER_C5)
    assert h.depends_on == ["aaaaaaa4"]  # cccccc07 está en el texto libre, no cuenta
    assert h.touches == [
        "vision/servio_vision/alerts/",
        "docs/context/05-reglas-alertas.md",
    ]
    assert h.area == "alerts"
    assert h.decisions == "5, 6"


def test_parse_header_without_header():
    h = tablero.parse_header("Texto suelto sin campos.")
    assert h == tablero.Header()


def test_paths_overlap_prefix_and_equal():
    assert tablero.paths_overlap(
        ["vision/servio_vision/alerts/"], ["vision/servio_vision/alerts/bot.py"]
    )
    assert tablero.paths_overlap(["docs/a.md"], ["docs/a.md"])
    assert not tablero.paths_overlap(
        ["vision/servio_vision/alerts"], ["vision/servio_vision/alertsx.py"]
    )
    assert not tablero.paths_overlap(["docs/a.md"], ["docs/b.md"])


def test_card_code_and_slug():
    assert tablero.card_code("P1 · C-5 · «Voy», «Listo» y «No hacía falta»") == "C-5"
    assert tablero.card_code("P1 · F-1b · Implementar la vista") == "F-1b"
    assert tablero.card_code("P3 · PR-29 · Decidir el PR #29") == "PR-29"
    assert tablero.card_code("Cámara armada en cada casa") is None
    assert (
        tablero.slug("P1 · C-5 · «Voy», «Listo» y «No hacía falta»")
        == "c-5-voy-listo-y-no"
    )
    assert tablero.slug("Cámara armada en cada casa") == "camara-armada-en-cada"


def test_lock_roundtrip():
    text = tablero.lock_text(
        "abcdef123456",
        "claude-jessica",
        "Semana 3 · 11/11",
        datetime(2026, 11, 9, 10, tzinfo=UTC),
    )
    assert tablero.parse_lock(text) == (
        "abcdef123456",
        "claude-jessica",
        "Semana 3 · 11/11",
    )
    assert tablero.parse_lock("un comentario cualquiera") is None


def test_winner_is_oldest_active_lock_and_unlock_resets():
    t = lambda tok, who: tablero.lock_text(
        tok, who, "Semana 3", datetime(2026, 11, 9, tzinfo=UTC)
    )
    comments = [
        ("2026-11-09T10:00:02Z", t("bbbbbbbbbbbb", "segundo")),
        ("2026-11-09T10:00:01Z", t("aaaaaaaaaaaa", "primero")),
    ]
    assert tablero.winner(comments)[1] == "primero"
    comments.append(("2026-11-09T10:00:03Z", "🔓 soltada: sin terminar"))
    assert tablero.winner(comments) is None
    comments.append(("2026-11-09T10:00:04Z", t("cccccccccccc", "tercero")))
    assert tablero.winner(comments)[1] == "tercero"
    comments.append(("2026-11-09T10:00:05Z", "✅ Integrada en develop"))
    assert tablero.winner(comments) is None


def test_short_links_takes_every_eight_char_token():
    # «opcional» no es tarjeta: el tablero lo avisa sin bloquear (ver test_unknown_reference…)
    assert tablero.short_links("A-4 (opcional), C-5 (9oDZRoKz), X (aaaaaaa4)") == [
        "opcional",
        "9oDZRoKz",
        "aaaaaaa4",
    ]


def test_parse_header_survives_trello_paragraphs():
    # el editor de Trello guarda cada Enter como párrafo: líneas en blanco entre los campos
    desc = "**Área:** x\n\n**Depende de:** C-5 (9oDZRoKz)\n\n**Toca:** `a/b.py`\n\ntexto libre (cccccc07)"
    h = tablero.parse_header(desc)
    assert h.depends_on == ["9oDZRoKz"]
    assert h.touches == ["a/b.py"]


def test_parse_header_allows_a_heading_or_bullets_before_the_fields():
    desc = "## Contexto\n\n- **Área:** x\n- **Depende de:** C-5 (9oDZRoKz)\n\ntexto (cccccc07)"
    h = tablero.parse_header(desc)
    assert h.area == "x"
    assert h.depends_on == ["9oDZRoKz"]


def test_unknown_reference_warns_but_does_not_block():
    api, board = board_with(
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · X",
            "**Toca:** `a.py`\n**Depende de:** B-4 (Telegram)\n\nx",
        ),
    )
    cards = board.cards()
    state, why = board.status(cards[0], cards)
    assert state == "libre"
    assert any("no es ninguna tarjeta" in w for w in why)
    board.sync_labels(cards)
    assert api.label_names("cccccc09") == {"Libre"}


def test_card_without_header_is_flagged():
    _api, board = board_with(card("c9", "cccccc09", "P2 · C-9 · X", "solo texto"))
    cards = board.cards()
    assert board.status(cards[0], cards) == (
        "libre",
        ["sin encabezado: no se sabe qué toca"],
    )


def test_finish_refuses_wrong_card_or_other_agents_card():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7),
    )
    with pytest.raises(SystemExit, match="no está en curso"):
        board.finish("cccccc07", 80, "")
    with pytest.raises(SystemExit, match="ya está hecha"):
        board.finish("aaaaaaa4", 80, "")
    board.take("cccccc05", "ana")
    with pytest.raises(SystemExit, match="la tiene tomada ana"):
        board.finish("cccccc05", 80, "", agent="beto")
    board.finish("cccccc05", 80, "", agent="ana")
    assert api.list_of("cccccc05") == "Done"


def test_loser_deletes_its_own_lock_and_stale_lock_is_reported():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    old = tablero.lock_text("ffffffffffff", "viejo", "Semana 3", datetime.now(UTC))
    api.add_comment("c5", old)
    api.comments["c5"][-1]["date"] = "2026-01-01T00:00:00.000Z"  # hace meses
    with pytest.raises(SystemExit, match="candado viejo de viejo"):
        board.take("cccccc05", "yo")
    texts = [c["data"]["text"] for c in api.comments["c5"]]
    assert texts == [old]  # el perdedor no dejó su candado


def test_take_backs_off_if_an_overlapping_card_was_taken_meanwhile():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )

    def sneak(
        cid,
    ):  # entre el candado y la segunda mirada, otro tomó C-9, que toca lo mismo
        api.cards["c9"]["idList"] = "LP"

    api.before_comment_read = sneak
    with pytest.raises(SystemExit) as e:
        board.take("cccccc05", "yo")
    assert e.value.code[1] == 2 and "C-9 toca lo mismo" in e.value.code[0]
    assert api.list_of("cccccc05").startswith("Semana 3")
    assert api.comments["c5"][-1]["data"]["text"].startswith("🔓")


def test_header_with_paths_as_bullets_under_toca():
    desc = "**Toca:**\n- `a.py`\n- `b/`\n**Depende de:** X (aaaaaaa4)\n\ntexto"
    h = tablero.parse_header(desc)
    assert h.touches == ["a.py", "b/"]
    assert h.depends_on == ["aaaaaaa4"]


def test_card_in_an_archived_list_counts_as_done():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "L4"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.closed_lists = {"L4"}
    board._lists = None  # releer las listas
    cards = board.cards()
    c5 = next(c for c in cards if c.short == "cccccc05")
    assert board.status(c5, cards) == ("libre", [])


def test_release_refuses_other_agents_card_unless_forced():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.take("cccccc05", "ana")
    with pytest.raises(SystemExit, match="la tiene tomada ana"):
        board.release("cccccc05", "", agent="beto")
    board.release("cccccc05", "", agent="beto", force=True)
    assert api.list_of("cccccc05").startswith("Semana 3")


def test_freed_only_lists_cards_that_were_blocked():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5, "LP"),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7),
        card(
            "n1", "nuevaaa1", "P2 · N-1 · Nueva sin etiquetas", "**Toca:** `x.py`\n\nx"
        ),
    )
    board.sync_labels(board.cards())
    assert api.label_names("nuevaaa1") == {"Libre"}
    _done, freed = board.finish("cccccc05", 72, "")
    assert [c.short for c in freed] == ["cccccc07"]  # la nueva nunca estuvo bloqueada


def test_two_overlapping_cards_taken_at_once_the_newer_lock_backs_off():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )

    def sneak(cid):  # C-9 entró a In progress con un candado anterior al nuestro
        api.cards["c9"]["idList"] = "LP"
        api.add_comment(
            "c9",
            tablero.lock_text("eeeeeeeeeeee", "otro", "Semana 3", datetime.now(UTC)),
        )
        api.comments["c9"][-1]["date"] = "2026-11-09T09:00:00.000Z"

    api.before_comment_read = sneak
    with pytest.raises(SystemExit) as e:
        board.take("cccccc05", "yo")
    assert e.value.code[1] == 2 and "entró antes" in e.value.code[0]
    assert api.list_of("cccccc05").startswith("Semana 3")  # volvió a su lista
    assert api.list_of("cccccc09") == "In progress"


def test_two_overlapping_cards_taken_at_once_the_older_lock_keeps_going():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )

    def sneak(cid):  # C-9 entró a In progress pero su candado es posterior al nuestro
        api.cards["c9"]["idList"] = "LP"
        api.add_comment(
            "c9",
            tablero.lock_text("eeeeeeeeeeee", "otro", "Semana 3", datetime.now(UTC)),
        )
        api.comments["c9"][-1]["date"] = "2026-11-09T11:00:00.000Z"

    api.before_comment_read = sneak
    board.take("cccccc05", "yo")
    assert api.list_of("cccccc05") == "In progress"


def test_header_accepts_colon_outside_bold_and_bulleted_dependencies():
    desc = (
        "**Toca**: `a.py`\n**Depende de:**\n- C-5 (cccccc05)\n- A-4 (aaaaaaa4)\n\ntexto"
    )
    h = tablero.parse_header(desc)
    assert h.touches == ["a.py"]
    assert h.depends_on == ["cccccc05", "aaaaaaa4"]


def test_usage_error_exits_with_one_not_two(capsys, monkeypatch):
    monkeypatch.setattr(tablero, "credentials", lambda *a, **k: ("k", "t"))
    assert tablero.main(["tomar", "cccccc05"]) == 1  # falta --agente
    assert "--agente" in capsys.readouterr().err


def test_take_when_an_unlock_lands_right_after_our_lock():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )

    def sneak(cid):  # alguien soltó la tarjeta justo después de nuestro candado
        api.add_comment(cid, "🔓 soltada: tarde")

    api.before_comment_read = sneak
    board.take("cccccc05", "yo")
    assert api.list_of("cccccc05") == "In progress"
    texts = [c["data"]["text"] for c in api.comments["c5"]]
    assert texts[-1].startswith("🔒") and "yo" in texts[-1]


def test_overlapping_card_locked_but_not_yet_moved_counts():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )

    def sneak(
        cid,
    ):  # C-9 tiene candado anterior al nuestro pero su agente aún no la movió
        api.add_comment(
            "c9",
            tablero.lock_text("eeeeeeeeeeee", "otro", "Semana 3", datetime.now(UTC)),
        )
        api.comments["c9"][-1]["date"] = (
            "2026-11-09T10:00:00.500Z"  # fresco, anterior al nuestro
        )

    api.before_comment_read = sneak
    with pytest.raises(SystemExit) as e:
        board.take("cccccc05", "yo")
    assert e.value.code[1] == 2
    assert api.list_of("cccccc05").startswith("Semana 3")


def test_api_retries_on_429(monkeypatch):
    import urllib.error

    calls = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many", {"Retry-After": "0"}, None
            )
        return Resp()

    monkeypatch.setattr(tablero.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(tablero.time, "sleep", lambda s: None)
    assert tablero.Api("K", "T").get("/x") == {"ok": True}
    assert len(calls) == 2


def test_header_field_after_a_prose_line_still_counts():
    desc = "**Toca:**\n- `a.py` (solo lectura)\n**Depende de:** C-5 (cccccc05)\n\ntexto (cccccc07)"
    h = tablero.parse_header(desc)
    assert h.depends_on == ["cccccc05"]


def test_paths_overlap_with_globs_and_dot_slash():
    assert tablero.paths_overlap(["vision/tools/*.py"], ["vision/tools/tablero.py"])
    assert tablero.paths_overlap(["./docs/a.md"], ["docs/a.md"])
    assert not tablero.paths_overlap(
        ["vision/tools/*.py"], ["vision/servio_vision/x.py"]
    )


def test_slug_strips_any_accent():
    assert tablero.slug("P1 · D-2 · Überprüfen çà") == "d-2-uberprufen-ca"


def test_retry_after_parsing():
    assert tablero._retry_after("3") == 3.0
    assert tablero._retry_after("Wed, 09 Nov 2026 10:00:05 GMT") == 2.0
    assert tablero._retry_after(None) == 2.0


def test_release_of_a_stale_lock_does_not_need_the_same_agent():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.take("cccccc05", "viejo")
    api.cards["c5"]["idList"] = "L3"  # la arrastraron de vuelta a mano
    api.comments["c5"][-1]["date"] = "2026-01-01T00:00:00.000Z"  # el candado es viejo
    board.release("cccccc05", "colgada", agent="yo")  # lo que la herramienta recomienda
    board.take("cccccc05", "yo")
    assert api.list_of("cccccc05") == "In progress"


def test_forced_take_does_not_wipe_a_fresh_lock():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "L3"),  # no hecha: C-5 bloqueada
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.add_comment(
        "c5", tablero.lock_text("ffffffffffff", "ana", "Semana 3", datetime.now(UTC))
    )
    with pytest.raises(SystemExit) as e:
        board.take(
            "cccccc05", "beto", force=True
        )  # fuerza el bloqueo, pero ana la está tomando
    assert e.value.code == ("P1 · C-5 · Voy: la tomó ana justo antes.", 2)
    texts = [c["data"]["text"] for c in api.comments["c5"]]
    assert len(texts) == 1 and "ana" in texts[0]  # el candado de ana sigue solo


def test_network_error_in_the_second_look_rolls_the_card_back():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )
    real_get = api.get

    def flaky(path, **params):
        if path == "/cards/c9/actions":
            raise SystemExit("Trello respondió 503")
        return real_get(path, **params)

    def arm(cid):  # C-9 entra en curso y, a partir de la segunda mirada, la red falla
        api.cards["c9"]["idList"] = "LP"
        api.get = flaky

    api.before_comment_read = arm
    with pytest.raises(SystemExit, match="503"):
        board.take("cccccc05", "yo")
    assert api.list_of("cccccc05").startswith("Semana 3")
    assert api.comments["c5"][-1]["data"]["text"].startswith("🔓")


def test_first_occurrence_of_a_header_field_wins():
    desc = (
        "**Toca:** `vision/a.py`\n**Depende de:** C-5 (cccccc05)\n\n"
        "**Depende de** lo que diga Tomás sobre C-7 (cccccc07).\n**Toca** también `docs/x.md`."
    )
    h = tablero.parse_header(desc)
    assert h.touches == ["vision/a.py"]
    assert h.depends_on == ["cccccc05"]


def test_glob_without_folder_touches_everything():
    assert tablero.paths_overlap(["**/*.md"], ["docs/a.md"])


def test_release_owner_guard_does_not_expire_while_in_progress():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.take("cccccc05", "ana")
    api.comments["c5"][-1]["date"] = (
        "2026-01-01T00:00:00.000Z"  # ana lleva meses con ella
    )
    with pytest.raises(SystemExit, match="la tiene tomada ana"):
        board.release("cccccc05", "", agent="beto")


def test_release_does_not_wipe_a_fresh_lock_of_a_take_in_flight():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.add_comment(
        "c5", tablero.lock_text("ffffffffffff", "ana", "Semana 3", datetime.now(UTC))
    )
    with pytest.raises(SystemExit, match="la está tomando ahora mismo"):
        board.release("cccccc05", "", agent="beto")
    board.release("cccccc05", "", agent="beto", force=True)
    assert api.comments["c5"][-1]["data"]["text"].startswith("🔓")


def test_take_waits_for_its_own_lock_to_be_visible(monkeypatch):
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    real_get = api.get
    lag = {"reads": 0}

    def lagging(path, **params):
        rows = real_get(path, **params)
        if path.endswith("/actions"):
            lag["reads"] += 1
            if lag["reads"] == 1:  # la primera lectura todavía no muestra el comentario
                return rows[:-1]
        return rows

    api.get = lagging
    monkeypatch.setattr(tablero.time, "sleep", lambda s: None)
    board.take("cccccc05", "yo")
    assert api.list_of("cccccc05") == "In progress"


def test_take_fails_clearly_if_the_lock_never_shows_up(monkeypatch):
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    real_get = api.get

    def blind(path, **params):
        rows = real_get(path, **params)
        return [] if path.endswith("/actions") else rows

    api.get = blind
    monkeypatch.setattr(tablero.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="no muestra el candado"):
        board.take("cccccc05", "yo")
    assert api.list_of("cccccc05").startswith("Semana 3")


def test_body_bold_fields_do_not_reach_the_header():
    desc = "**Área:** x · **Toca:** `a/`\n\nTexto.\n**Depende de:** C-9 (zzzzzzz9) para el futuro"
    assert tablero.parse_header(desc).depends_on == []


def test_bold_word_inside_a_value_does_not_cut_it():
    h = tablero.parse_header(
        "**Toca:** `a/`, **importante** `b/`\n**Depende de**: nada"
    )
    assert h.touches == ["a/", "b/"]


def test_glob_with_a_name_stem_means_its_folder():
    assert tablero.paths_overlap(
        ["vision/tests/test_*.py"], ["vision/tests/test_tablero.py"]
    )
    assert not tablero.paths_overlap(["vision/tests/test_*.py"], ["vision/tools/x.py"])


def test_take_gives_up_cleanly_and_removes_its_lock(monkeypatch):
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    real_get = api.get
    api.get = lambda path, **p: [] if path.endswith("/actions") else real_get(path, **p)
    monkeypatch.setattr(tablero.time, "sleep", lambda s: None)
    with pytest.raises(SystemExit, match="no muestra el candado"):
        board.take("cccccc05", "yo")
    assert api.comments["c5"] == []  # no dejó su candado


def test_second_look_rolls_back_on_any_error():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )
    real_get = api.get

    def flaky(path, **params):
        if path == "/cards/c9/actions":
            raise TimeoutError("read timed out")
        return real_get(path, **params)

    def arm(cid):
        api.cards["c9"]["idList"] = "LP"
        api.get = flaky

    api.before_comment_read = arm
    with pytest.raises(TimeoutError):
        board.take("cccccc05", "yo")
    assert api.list_of("cccccc05").startswith("Semana 3")


def test_comments_are_paged_until_a_reset_marker():
    api, board = board_with(card("c5", "cccccc05", "C-5", HEADER_C5))
    for n in range(250):
        api.add_comment("c5", f"charla {n}")
    api.add_comment(
        "c5", tablero.lock_text("aaaaaaaaaaaa", "ana", "Semana 3", datetime.now(UTC))
    )
    real_get = api.get

    def paged(path, **params):
        rows = real_get(path, **params)
        if path.endswith("/actions"):
            rows = sorted(
                rows, key=lambda r: r["date"], reverse=True
            )  # Trello: del más nuevo al más viejo
            if params.get("before"):
                ids = [r["id"] for r in rows]
                rows = rows[ids.index(params["before"]) + 1 :]
            return rows[: params.get("limit", 100)]
        return rows

    api.get = paged
    c5 = board.card("cccccc05")
    assert len(board.comments(c5)) == 251  # llegó al final: no hay 🔓
    api.add_comment("c5", "🔓 soltada")
    assert (
        len(board.comments(c5)) <= 100
    )  # se frenó en la primera página, que trae el 🔓


def test_finish_only_resyncs_dependents():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5, "LP"),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7),
        card("n1", "nuevaaa1", "P2 · N-1 · Nueva", "**Toca:** `x.py`\n\nx"),
    )
    board.finish("cccccc05", 72, "")
    assert api.label_names("cccccc07") == {"Libre"}
    assert api.label_names("nuevaaa1") == set()  # no se tocó: no depende de C-5


def test_take_does_not_drag_a_just_finished_card_back():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )

    def sneak(cid):  # otro la terminó justo después de nuestro candado
        api.add_comment(cid, "✅ Integrada en develop")
        api.cards[cid]["idList"] = "LD"

    api.before_comment_read = sneak
    with pytest.raises(SystemExit, match="pasó a «Done»"):
        board.take("cccccc05", "yo")
    assert api.list_of("cccccc05") == "Done"
    assert not any(c["data"]["text"].startswith("🔒") for c in api.comments["c5"])


def test_take_refuses_cards_outside_work_lists():
    _api, board = board_with(
        card("d8", "dddddd08", "D-8 · Descartada", "**Toca:** `x`\n\nx", "LI")
    )
    with pytest.raises(SystemExit, match="no es una lista de trabajo"):
        board.take("dddddd08", "yo")


def test_api_turns_timeouts_into_exit_messages(monkeypatch):
    def boom(req, timeout):
        raise TimeoutError("read timed out")

    monkeypatch.setattr(tablero.urllib.request, "urlopen", boom)
    with pytest.raises(SystemExit, match="No se pudo hablar con Trello"):
        tablero.Api("K", "T").get("/x")


def test_retry_after_is_clamped():
    assert tablero._retry_after("3600") == 10.0
    assert tablero._retry_after("inf") == 10.0
    assert tablero._retry_after("nan") == 2.0


def test_network_error_after_the_lock_removes_it():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    real_put = api.put

    def failing_move(path, **params):
        raise SystemExit("Trello respondió 503")

    api.put = failing_move
    with pytest.raises(SystemExit, match="503"):
        board.take("cccccc05", "yo")
    assert api.comments["c5"] == []  # el candado se borró
    api.put = real_put


def test_tomar_prints_the_card_notes(capsys, monkeypatch):
    api, board = board_with(
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · X",
            "**Toca:** `a.py`\n**Depende de:** B-4 (Telegram)\n\nx",
        ),
    )
    monkeypatch.setattr(tablero, "credentials", lambda *a, **k: ("k", "t"))
    monkeypatch.setattr(tablero, "Api", lambda key, token: api)
    monkeypatch.setattr(tablero, "Board", lambda api_, board_id=tablero.BOARD_ID: board)
    assert tablero.main(["tomar", "cccccc09", "--agente", "yo"]) == 0
    assert "no es ninguna tarjeta" in capsys.readouterr().out


def test_credentials_from_env_files(tmp_path):
    repo = tmp_path / ".env"
    repo.write_text("TRELLO_KEY='k1'   # comentario\nTRELLO_TOKEN=\n", encoding="utf-8")
    above = tmp_path / "arriba.env"
    above.write_text("TRELLO_TOKEN=t1\n", encoding="utf-8")
    # el repo gana, y lo que le falta lo completa el de arriba
    assert tablero.credentials([repo, above], environ={}) == ("k1", "t1")
    # el entorno le gana a los archivos
    assert tablero.credentials([repo, above], environ={"TRELLO_TOKEN": "t2"}) == (
        "k1",
        "t2",
    )
    # un archivo que no existe no molesta
    assert tablero.credentials(
        [tmp_path / "no.env", above], environ={"TRELLO_KEY": "k3"}
    ) == ("k3", "t1")


def test_credentials_missing(tmp_path):
    with pytest.raises(SystemExit, match="TRELLO_KEY"):
        tablero.credentials([tmp_path / "no.env"], environ={})


def test_api_puts_auth_in_the_url_for_every_method(monkeypatch):
    seen = []

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok": true}'

    def fake_urlopen(req, timeout):
        seen.append((req.get_method(), req.full_url, req.data))
        return Resp()

    monkeypatch.setattr(tablero.urllib.request, "urlopen", fake_urlopen)
    api = tablero.Api("K", "T")
    api.get("/x", a="1")
    api.post("/x", b="2")
    api.delete("/x/y")
    for method, url, _data in seen:
        assert "key=K" in url and "token=T" in url, method
    assert seen[0][2] is None and "a=1" in seen[0][1]
    assert seen[1][2] == b"b=2"
    assert seen[2][2] is None


# ------------------------------------------------------------- el protocolo


def test_status_free_blocked_human_in_progress():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4 · Avisos", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7),
        card("cam", "cammmmm1", "Cámara armada en cada casa", "", "L3"),
    )
    api.cards["cam"]["idLabels"].append(api.labels["Humano"]["id"])
    cards = board.cards()
    by = {c.short: c for c in cards}
    assert board.status(by["cccccc05"], cards) == ("libre", [])
    assert board.status(by["cccccc07"], cards) == ("bloqueada", ["espera C-5"])
    assert board.status(by["cammmmm1"], cards)[0] == "humano"
    assert board.status(by["aaaaaaa4"], cards)[0] == "hecha"


def test_take_moves_to_in_progress_and_assigns_me():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.sync_labels(board.cards())
    assert api.label_names("cccccc05") == {"Libre"}
    taken = board.take("cccccc05", "claude-jessica")
    assert taken.short == "cccccc05"
    assert api.list_of("cccccc05") == "In progress"
    assert api.label_names("cccccc05") == set()
    assert ME in api.cards["c5"]["idMembers"]
    texts = [c["data"]["text"] for c in api.comments["c5"]]
    assert (
        len(texts) == 1 and texts[0].startswith("🔒") and "claude-jessica" in texts[0]
    )


def test_take_refuses_blocked_unless_forced():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "L3"),  # A-4 existe pero no está en Done
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    with pytest.raises(SystemExit, match="bloqueada"):
        board.take("cccccc05", "x")
    assert api.list_of("cccccc05").startswith("Semana 3")
    board.take("cccccc05", "x", force=True)
    assert api.list_of("cccccc05") == "In progress"


def test_take_refuses_in_progress_human_and_unknown():
    api, board = board_with(
        card("c5", "cccccc05", "P1 · C-5 · Voy", "", "LP"),
        card("cam", "cammmmm1", "Cámara", "", "L3"),
    )
    api.cards["cam"]["idLabels"].append(api.labels["Humano"]["id"])
    with pytest.raises(SystemExit, match="en curso"):
        board.take("cccccc05", "x")
    with pytest.raises(SystemExit, match="persona"):
        board.take("cammmmm1", "x")
    with pytest.raises(SystemExit, match="No hay"):
        board.take("nooooooo", "x")


def test_take_refuses_file_clash_with_card_in_progress():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5, "LP"),
        card(
            "c9",
            "cccccc09",
            "P2 · C-9 · Otra de alerts",
            "**Toca:** `vision/servio_vision/alerts/engine.py`\n\nx",
        ),
    )
    with pytest.raises(SystemExit, match="toca lo mismo que C-5"):
        board.take("cccccc09", "x")
    board.take("cccccc09", "x", force=True)
    assert api.list_of("cccccc09") == "In progress"


def test_race_second_agent_loses_and_backs_off():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    # otro agente deja su candado entre que posteamos el nuestro y leemos los comentarios:
    # como el suyo quedó antes en la lista (ordenada por fecha), gana él.

    def sneak(cid):
        mine = api.comments[cid].pop()
        api.add_comment(
            cid,
            tablero.lock_text(
                "ffffffffffff", "otro-agente", "Semana 3", datetime.now(UTC)
            ),
        )
        mine["date"] = "2026-11-09T10:00:59.000Z"
        api.comments[cid].append(mine)

    api.before_comment_read = sneak
    with pytest.raises(SystemExit) as e:
        board.take("cccccc05", "yo")
    assert e.value.code == ("P1 · C-5 · Voy: la tomó otro-agente justo antes.", 2)
    assert api.list_of("cccccc05").startswith("Semana 3")  # no se movió


def test_finish_moves_to_done_and_frees_dependents():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5, "LP"),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7),
    )
    board.sync_labels(board.cards())
    assert api.label_names("cccccc07") == {"Bloqueada"}
    _done, freed = board.finish("cccccc05", 72, "")
    assert api.list_of("cccccc05") == "Done"
    assert [c.short for c in freed] == ["cccccc07"]
    assert api.label_names("cccccc07") == {"Libre"}
    text = api.comments["c5"][-1]["data"]["text"]
    assert text.startswith("✅") and "pull/72" in text


def test_release_returns_to_origin_list():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.take("cccccc05", "yo")
    board.release("cccccc05", "me quedé sin contexto")
    assert api.list_of("cccccc05") == "Semana 3 · 11/11 → 17/11"
    assert api.comments["c5"][-1]["data"]["text"].startswith("🔓")
    assert api.label_names("cccccc05") == {"Libre"}
    # y se puede volver a tomar: el 🔓 borró el candado anterior
    board.take("cccccc05", "otro")
    assert api.list_of("cccccc05") == "In progress"


def test_release_refuses_card_not_in_progress():
    _api, board = board_with(card("c5", "cccccc05", "C-5", "", "L3"))
    with pytest.raises(SystemExit, match="no está en curso"):
        board.release("cccccc05", "")


def test_release_cleans_a_stale_lock_on_a_card_moved_back_by_hand():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    board.take("cccccc05", "yo")
    api.cards["c5"]["idList"] = "L3"  # alguien la arrastró de vuelta sin comentar 🔓
    api.comments["c5"][-1]["date"] = "2026-01-01T00:00:00.000Z"  # el candado es viejo
    with pytest.raises(SystemExit, match="candado viejo de yo"):
        board.take("cccccc05", "otro")
    board.release("cccccc05", "quedó colgada")  # limpia el candado, no mueve nada
    assert api.list_of("cccccc05").startswith("Semana 3")
    board.take("cccccc05", "otro")
    assert api.list_of("cccccc05") == "In progress"


def test_take_forced_overrides_a_stale_lock():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.add_comment(
        "c5", tablero.lock_text("ffffffffffff", "viejo", "Semana 3", datetime.now(UTC))
    )
    api.comments["c5"][-1]["date"] = "2026-01-01T00:00:00.000Z"  # hace meses
    with pytest.raises(SystemExit):
        board.take("cccccc05", "yo")
    board.take("cccccc05", "yo", force=True)
    assert api.list_of("cccccc05") == "In progress"
    assert (
        tablero.winner([(c["date"], c["data"]["text"]) for c in api.comments["c5"]])[1]
        == "yo"
    )


def test_release_without_lock_falls_back_to_first_work_list():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card(
            "c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5, "LP"
        ),  # puesta en curso a mano
    )
    board.release("cccccc05", "")
    assert api.list_of("cccccc05") == "Semana 3 · 11/11 → 17/11"


def test_archived_dependency_counts_as_done():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "L3"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.cards["a4"]["closed"] = True
    cards = board.cards()
    c5 = next(c for c in cards if c.short == "cccccc05")
    assert board.status(c5, cards) == ("libre", [])


def test_take_does_not_reassign_a_member_already_on_the_card():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
    )
    api.cards["c5"]["idMembers"].append(ME)
    api.reject_duplicate_members = True
    board.take("cccccc05", "yo")
    assert api.cards["c5"]["idMembers"] == [ME]


def test_sync_labels_ignores_human_and_done():
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("cam", "cammmmm1", "Cámara", "", "L3"),
    )
    api.cards["cam"]["idLabels"].append(api.labels["Humano"]["id"])
    board.sync_labels(board.cards())
    assert api.label_names("cammmmm1") == {"Humano"}
    assert api.label_names("aaaaaaa4") == set()


def test_cli_listar_json(capsys, monkeypatch):
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),
        card("c7", "cccccc07", "P1 · C-7 · Por mozo", HEADER_C7, "L4"),
    )
    monkeypatch.setattr(tablero, "credentials", lambda *a, **k: ("k", "t"))
    monkeypatch.setattr(tablero, "Api", lambda key, token: api)
    monkeypatch.setattr(tablero, "Board", lambda api_, board_id=tablero.BOARD_ID: board)
    assert tablero.main(["listar", "--json"]) == 0
    import json

    rows = json.loads(capsys.readouterr().out)
    states = {r["code"]: r["state"] for r in rows}
    assert states == {"C-5": "libre", "C-7": "bloqueada"}


def test_cli_take_prints_branch_and_exit_codes(capsys, monkeypatch):
    api, board = board_with(
        card("a4", "aaaaaaa4", "A-4", "", "LD"),
        card("c5", "cccccc05", "P1 · C-5 · «Voy», «Listo»", HEADER_C5),
    )
    monkeypatch.setattr(tablero, "credentials", lambda *a, **k: ("k", "t"))
    monkeypatch.setattr(tablero, "Api", lambda key, token: api)
    monkeypatch.setattr(tablero, "Board", lambda api_, board_id=tablero.BOARD_ID: board)
    assert (
        tablero.main(["tomar", "cccccc05", "--agente", "yo", "--rama", "jessica"]) == 0
    )
    out = capsys.readouterr().out
    assert "jessica/c-5-voy-listo" in out and "Decisiones que mandan: 5, 6" in out
    assert tablero.main(["tomar", "cccccc05", "--agente", "yo"]) == 1
    assert "en curso" in capsys.readouterr().err
