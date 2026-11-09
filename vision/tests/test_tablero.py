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
            return [{"id": lid, "name": name} for name, lid in LISTS.items()]
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
            self.cards[cid]["idMembers"].append(params["value"])
            return {}
        raise AssertionError(path)

    def put(self, path, **params):
        cid = self._card_id(path.split("/")[2])
        self.cards[cid]["idList"] = params["idList"]
        return {}

    def delete(self, path, **params):
        parts = path.split("/")
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


def test_env_value():
    text = "﻿CLAUDE_PROVIDER=cli   # comentario\nTRELLO_KEY='abc'\nTRELLO_TOKEN=\n"
    assert tablero.env_value(text, "TRELLO_KEY") == "abc"
    assert tablero.env_value(text, "CLAUDE_PROVIDER") == "cli"
    assert tablero.env_value(text, "TRELLO_TOKEN") is None
    assert tablero.env_value(text, "OTRA") is None


def test_credentials_from_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("TRELLO_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    (tmp_path / ".env").write_text("TRELLO_KEY=k1\nTRELLO_TOKEN=t1\n", encoding="utf-8")
    sub = tmp_path / "vision" / "tools"
    sub.mkdir(parents=True)
    assert tablero.credentials(sub) == ("k1", "t1")
    monkeypatch.setenv("TRELLO_TOKEN", "t2")
    assert tablero.credentials(sub) == ("k1", "t2")


def test_credentials_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("TRELLO_KEY", raising=False)
    monkeypatch.delenv("TRELLO_TOKEN", raising=False)
    with pytest.raises(SystemExit, match="TRELLO_KEY"):
        tablero.credentials(tmp_path)


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
        card("c5", "cccccc05", "P1 · C-5 · Voy", HEADER_C5),  # A-4 no está en Done
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
    monkeypatch.setattr(tablero, "credentials", lambda start: ("k", "t"))
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
    monkeypatch.setattr(tablero, "credentials", lambda start: ("k", "t"))
    monkeypatch.setattr(tablero, "Api", lambda key, token: api)
    monkeypatch.setattr(tablero, "Board", lambda api_, board_id=tablero.BOARD_ID: board)
    assert (
        tablero.main(["tomar", "cccccc05", "--agente", "yo", "--rama", "jessica"]) == 0
    )
    out = capsys.readouterr().out
    assert "jessica/c-5-voy-listo" in out and "Decisiones que mandan: 5, 6" in out
    assert tablero.main(["tomar", "cccccc05", "--agente", "yo"]) == 1
    assert "en curso" in capsys.readouterr().err
