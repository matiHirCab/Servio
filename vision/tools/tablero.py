"""El tablero de Trello para agentes que trabajan en paralelo.

Varios agentes (o personas) toman tarjetas del mismo tablero sin pisarse: cada
tarjeta dice de qué depende y qué archivos toca, el tablero marca cuáles están
libres, y tomar una es una operación con candado: la primera que la reclama se la
queda y pasa a «In progress» en el mismo paso.

    python tools/tablero.py listar                 # qué se puede tomar y qué no, y por qué
    python tools/tablero.py tomar 9oDZRoKz --agente claude-jessica
    python tools/tablero.py soltar 9oDZRoKz        # devolverla a su lista si no se termina
    python tools/tablero.py terminar 9oDZRoKz --pr 72
    python tools/tablero.py sincronizar            # recalcula Libre / Bloqueada en todas

En la descripción de cada tarjeta, las primeras líneas son el encabezado que este
programa lee (ver «Tablero y trabajo en paralelo» en AGENTS.md):

    **Área:** alerts · **Toca:** `vision/servio_vision/alerts/`, `docs/context/05-reglas-alertas.md`
    **Depende de:** C-5 (9oDZRoKz), A-4 (hecha)
    **Decisiones:** 5, 6

`Depende de` nombra tarjetas por su enlace corto entre paréntesis: una tarjeta está
libre cuando todas están en «Done». `Toca` son rutas del repo: dos tarjetas que
tocan lo mismo no se trabajan a la vez. La etiqueta «Humano» marca lo que un agente
no puede hacer (armar una cámara, comer en casa) y no se toma desde acá.

Credenciales: TRELLO_KEY y TRELLO_TOKEN en el entorno o en el `.env` del repo (ver
`.env.example`). Cada persona usa las suyas; son de su cuenta de Trello. Sale con 0
si hizo lo pedido, 1 si no se puede (tarjeta bloqueada, ocupada, sin permiso), 2 si
otro agente la tomó justo antes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

BOARD_ID = "6ad386f07003eed0f9a752c8"
API = "https://api.trello.com/1"

LIST_IN_PROGRESS = "In progress"
LIST_DONE = "Done"
WORK_LIST_PREFIXES = ("Semana",)  # las listas de las que se toman tarjetas
EXTRA_LIST_PREFIXES = ("Después", "Backlog")  # con --todas

LABEL_FREE = "Libre"
LABEL_BLOCKED = "Bloqueada"
LABEL_HUMAN = "Humano"
LABEL_COLORS = {LABEL_FREE: "green", LABEL_BLOCKED: "red", LABEL_HUMAN: "yellow"}

LOCK = "🔒"
UNLOCK = "🔓"
DONE_MARK = "✅"

_SHORT_LINK = re.compile(r"\(([A-Za-z0-9_-]{8})\)")
_BACKTICKED = re.compile(r"`([^`]+)`")
_HEADER_FIELD = re.compile(
    r"\*\*(?P<key>[^*:]+):\*\*(?P<value>.*?)(?=\*\*[^*:]+:\*\*|$)"
)
_CODE = re.compile(r"\b([A-Z]{1,2}-\d+[a-z]?)\b")
_LOCK_LINE = re.compile(
    rf"^{LOCK} (?P<token>[0-9a-f]{{12}}) · (?P<agent>.+?) · desde «(?P<origin>[^»]*)»"
)


# ---------------------------------------------------------------- lógica pura


@dataclass
class Header:
    """Lo que el encabezado de una tarjeta le dice al tablero."""

    depends_on: list[str] = field(default_factory=list)  # enlaces cortos
    touches: list[str] = field(default_factory=list)  # rutas del repo
    area: str = ""
    decisions: str = ""


def parse_header(desc: str) -> Header:
    """Lee los campos `**Clave:** valor` de las primeras líneas de la descripción.

    Solo mira hasta la primera línea en blanco: lo de abajo es texto libre y puede
    nombrar otras tarjetas sin que cuenten como dependencias."""
    head = desc.replace("\r", "").split("\n\n", 1)[0]
    header = Header()
    for m in _HEADER_FIELD.finditer(head.replace("\n", " ")):
        key = m.group("key").strip().lower()
        value = m.group("value").strip(" ·")
        if key.startswith("depende"):
            header.depends_on = _SHORT_LINK.findall(value)
        elif key == "toca":
            header.touches = [p.strip() for p in _BACKTICKED.findall(value)]
        elif key.startswith(("área", "area")):
            header.area = value
        elif key.startswith("decision"):
            header.decisions = value
    return header


def missing_deps(header: Header, done: set[str]) -> list[str]:
    """Las dependencias que todavía no están en Done."""
    return [d for d in header.depends_on if d not in done]


def paths_overlap(a: list[str], b: list[str]) -> list[str]:
    """Rutas que chocan: iguales, o una dentro de la otra. Vacío si no se tocan."""
    hits = []
    for x in a:
        for y in b:
            xn, yn = _norm(x), _norm(y)
            if xn == yn or xn.startswith(yn + "/") or yn.startswith(xn + "/"):
                hits.append(f"{x} ~ {y}")
    return hits


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip().rstrip("/")


def card_code(name: str) -> str | None:
    """`P1 · C-5 · «Voy»…` → `C-5`; None si la tarjeta no tiene código."""
    m = _CODE.search(name)
    return m.group(1) if m else None


def slug(name: str) -> str:
    """Nombre de rama a partir del nombre de la tarjeta: `c-5-voy-listo`."""
    code = card_code(name)
    rest = name
    if code:
        rest = name.split(code, 1)[1]
    rest = re.sub(r"P\d\s*·", "", rest)
    words = re.findall(r"[a-záéíóúñ0-9]+", rest.lower())
    words = [_ascii(w) for w in words[:4]]
    base = "-".join(w for w in [code.lower() if code else ""] + words if w)
    return base or "tarjeta"


def _ascii(word: str) -> str:
    return word.translate(str.maketrans("áéíóúñ", "aeioun"))


def lock_text(token: str, agent: str, origin: str, now: datetime) -> str:
    return f"{LOCK} {token} · {agent} · desde «{origin}» · {now.strftime('%d/%m %H:%M')} UTC"


def parse_lock(text: str) -> tuple[str, str, str] | None:
    """(token, agente, lista de origen) de un comentario de candado; None si no lo es."""
    m = _LOCK_LINE.match(text.strip())
    return (m.group("token"), m.group("agent"), m.group("origin")) if m else None


def active_claims(comments: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
    """Los candados vigentes, del más viejo al más nuevo.

    `comments` son (fecha ISO, texto). Un 🔓 o un ✅ borra los candados anteriores:
    lo que cuenta es lo que se reclamó después del último de esos."""
    ordered = sorted(comments)
    claims: list[tuple[str, str, str]] = []
    for _date, text in ordered:
        t = text.strip()
        if t.startswith((UNLOCK, DONE_MARK)):
            claims = []
        elif (lock := parse_lock(t)) is not None:
            claims.append(lock)
    return claims


def winner(comments: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """Quién se quedó con la tarjeta: el primer candado vigente."""
    claims = active_claims(comments)
    return claims[0] if claims else None


def env_value(text: str, key: str) -> str | None:
    """`KEY=valor   # comentario` → `valor`, de un .env; None si no está."""
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line.startswith(key + "="):
            continue
        value = line.split("=", 1)[1]
        value = value.split(" #", 1)[0].strip().strip("'\"")
        return value or None
    return None


def credentials(start: Path) -> tuple[str, str]:
    """TRELLO_KEY y TRELLO_TOKEN: del entorno, o del primer .env que los tenga
    subiendo desde `start` (la carpeta vision/, el repo, la carpeta de arriba)."""
    key, token = os.environ.get("TRELLO_KEY"), os.environ.get("TRELLO_TOKEN")
    for folder in [start, *start.parents]:
        if key and token:
            break
        env = folder / ".env"
        if env.is_file():
            text = env.read_text(encoding="utf-8", errors="replace")
            key = key or env_value(text, "TRELLO_KEY")
            token = token or env_value(text, "TRELLO_TOKEN")
    if not key or not token:
        raise SystemExit(
            "Faltan TRELLO_KEY y TRELLO_TOKEN (en el entorno o en el .env del repo; "
            "ver .env.example). Se sacan de https://trello.com/power-ups/admin con tu cuenta."
        )
    return key, token


# ------------------------------------------------------------------ Trello


class Api:
    """Lo mínimo de la API REST de Trello, con urllib: sin dependencias."""

    def __init__(self, key: str, token: str) -> None:
        self.auth = {"key": key, "token": token}

    def get(self, path: str, **params: Any) -> Any:
        return self._call("GET", path, params)

    def post(self, path: str, **params: Any) -> Any:
        return self._call("POST", path, params)

    def put(self, path: str, **params: Any) -> Any:
        return self._call("PUT", path, params)

    def delete(self, path: str, **params: Any) -> Any:
        return self._call("DELETE", path, params)

    def _call(self, method: str, path: str, params: dict[str, Any]) -> Any:
        query = urllib.parse.urlencode({**self.auth, **params})
        url = f"{API}{path}"
        data = None
        if method == "GET":
            url += "?" + query
        else:
            data = query.encode()
        req = urllib.request.Request(url, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:200]
            raise SystemExit(
                f"Trello respondió {e.code} a {method} {path}: {detail}"
            ) from e
        except urllib.error.URLError as e:
            raise SystemExit(f"No se pudo hablar con Trello: {e.reason}") from e
        return json.loads(body) if body else None


@dataclass
class Card:
    id: str
    short: str
    name: str
    desc: str
    list_name: str
    labels: list[str]
    members: list[str]

    @property
    def header(self) -> Header:
        return parse_header(self.desc)

    @property
    def code(self) -> str | None:
        return card_code(self.name)


class Board:
    """El tablero: listas, etiquetas y tarjetas, con las operaciones del protocolo."""

    def __init__(self, api: Api, board_id: str = BOARD_ID) -> None:
        self.api = api
        self.board_id = board_id
        self._lists: dict[str, str] | None = None  # nombre → id
        self._labels: dict[str, str] | None = None  # nombre → id
        self._members: dict[str, str] | None = None  # id → usuario

    # --- lectura

    @property
    def lists(self) -> dict[str, str]:
        if self._lists is None:
            rows = self.api.get(f"/boards/{self.board_id}/lists", fields="name")
            self._lists = {r["name"]: r["id"] for r in rows}
        return self._lists

    @property
    def labels(self) -> dict[str, str]:
        if self._labels is None:
            rows = self.api.get(f"/boards/{self.board_id}/labels", fields="name,color")
            self._labels = {r["name"]: r["id"] for r in rows if r["name"]}
        return self._labels

    @property
    def members(self) -> dict[str, str]:
        if self._members is None:
            rows = self.api.get(f"/boards/{self.board_id}/members", fields="username")
            self._members = {r["id"]: r["username"] for r in rows}
        return self._members

    def list_name(self, list_id: str) -> str:
        for name, lid in self.lists.items():
            if lid == list_id:
                return name
        return "?"

    def cards(self) -> list[Card]:
        rows = self.api.get(
            f"/boards/{self.board_id}/cards",
            fields="name,desc,idList,labels,shortLink,idMembers",
        )
        return [self._card(r) for r in rows]

    def card(self, short_or_id: str) -> Card:
        r = self.api.get(
            f"/cards/{short_or_id}",
            fields="name,desc,idList,labels,shortLink,idMembers",
        )
        return self._card(r)

    def _card(self, r: dict[str, Any]) -> Card:
        return Card(
            id=r["id"],
            short=r["shortLink"],
            name=r["name"],
            desc=r.get("desc", ""),
            list_name=self.list_name(r["idList"]),
            labels=[lb["name"] for lb in r.get("labels", []) if lb.get("name")],
            members=[self.members.get(m, m) for m in r.get("idMembers", [])],
        )

    def comments(self, card: Card) -> list[tuple[str, str]]:
        rows = self.api.get(
            f"/cards/{card.id}/actions", filter="commentCard", limit=100
        )
        return [(r["date"], r["data"]["text"]) for r in rows]

    def done_links(self, cards: list[Card]) -> set[str]:
        return {c.short for c in cards if c.list_name == LIST_DONE}

    # --- estado de cada tarjeta

    def status(self, card: Card, cards: list[Card]) -> tuple[str, list[str]]:
        """('libre' | 'bloqueada' | 'humano' | 'en curso' | 'hecha' | 'choca', motivos)."""
        if card.list_name == LIST_DONE:
            return "hecha", []
        if card.list_name == LIST_IN_PROGRESS:
            who = ", ".join(card.members) or "alguien"
            return "en curso", [who]
        if LABEL_HUMAN in card.labels:
            return "humano", ["la hace una persona"]
        missing = missing_deps(card.header, self.done_links(cards))
        if missing:
            names = [self._name_of(m, cards) for m in missing]
            return "bloqueada", [f"espera {n}" for n in names]
        clashes = self.clashes(card, cards)
        if clashes:
            return "choca", clashes
        return "libre", []

    def clashes(self, card: Card, cards: list[Card]) -> list[str]:
        """Con qué tarjetas en curso comparte archivos."""
        out = []
        for other in cards:
            if other.id == card.id or other.list_name != LIST_IN_PROGRESS:
                continue
            hits = paths_overlap(card.header.touches, other.header.touches)
            if hits:
                out.append(
                    f"toca lo mismo que {other.code or other.short} en curso ({hits[0]})"
                )
        return out

    def _name_of(self, short: str, cards: list[Card]) -> str:
        for c in cards:
            if c.short == short:
                return c.code or c.name[:30]
        return f"{short} (no está en el tablero)"

    # --- escritura

    def ensure_labels(self) -> None:
        for name, color in LABEL_COLORS.items():
            if name not in self.labels:
                row = self.api.post(
                    "/labels", name=name, color=color, idBoard=self.board_id
                )
                self.labels[name] = row["id"]

    def set_label(self, card: Card, name: str, on: bool) -> None:
        has = name in card.labels
        if on and not has:
            self.api.post(f"/cards/{card.id}/idLabels", value=self.labels[name])
            card.labels.append(name)
        elif not on and has:
            self.api.delete(f"/cards/{card.id}/idLabels/{self.labels[name]}")
            card.labels.remove(name)

    def move(self, card: Card, list_name: str) -> None:
        self.api.put(f"/cards/{card.id}", idList=self.lists[list_name], pos="top")
        card.list_name = list_name

    def comment(self, card: Card, text: str) -> str:
        row = self.api.post(f"/cards/{card.id}/actions/comments", text=text)
        return row["id"]

    def me(self) -> str:
        return self.api.get("/members/me", fields="id")["id"]

    def sync_labels(self, cards: list[Card]) -> list[Card]:
        """Pone Libre / Bloqueada según las dependencias. Devuelve las recién liberadas."""
        self.ensure_labels()
        freed = []
        for card in cards:
            if not is_work_list(card.list_name, extra=True):
                continue
            if LABEL_HUMAN in card.labels:
                self.set_label(card, LABEL_FREE, False)
                self.set_label(card, LABEL_BLOCKED, False)
                continue
            blocked = bool(missing_deps(card.header, self.done_links(cards)))
            was_free = LABEL_FREE in card.labels
            self.set_label(card, LABEL_BLOCKED, blocked)
            self.set_label(card, LABEL_FREE, not blocked)
            if not blocked and not was_free:
                freed.append(card)
        return freed

    # --- el protocolo

    def take(self, short: str, agent: str, force: bool = False) -> Card:
        cards = self.cards()
        card = next((c for c in cards if c.short == short or c.id == short), None)
        if card is None:
            raise SystemExit(f"No hay una tarjeta {short} en el tablero.")
        state, why = self.status(card, cards)
        if state in ("en curso", "hecha"):
            raise SystemExit(f"{card.name}: ya está {state} ({'; '.join(why)}).")
        if state == "humano":
            raise SystemExit(f"{card.name}: la hace una persona, no se toma desde acá.")
        if state in ("bloqueada", "choca") and not force:
            raise SystemExit(
                f"{card.name}: {state}: {'; '.join(why)}. Con --forzar se toma igual."
            )
        token = uuid.uuid4().hex[:12]
        origin = card.list_name
        self.comment(card, lock_text(token, agent, origin, datetime.now(UTC)))
        won = winner(self.comments(card))
        if won is None or won[0] != token:
            who = won[1] if won else "otro"
            raise SystemExit(f"{card.name}: la tomó {who} justo antes.", 2)
        self.move(card, LIST_IN_PROGRESS)
        self.set_label(card, LABEL_FREE, False)
        self.set_label(card, LABEL_BLOCKED, False)
        me = self.me()
        if me not in card.members:
            self.api.post(f"/cards/{card.id}/idMembers", value=me)
        return card

    def release(self, short: str, reason: str) -> Card:
        card = self.card(short)
        if card.list_name != LIST_IN_PROGRESS:
            raise SystemExit(
                f"{card.name}: no está en curso (está en «{card.list_name}»)."
            )
        won = winner(self.comments(card))
        origin = won[2] if won and won[2] in self.lists else None
        self.comment(card, f"{UNLOCK} soltada: {reason or 'sin terminar'}")
        if origin:
            self.move(card, origin)
        self.sync_labels(self.cards())
        return card

    def finish(self, short: str, pr: int | None, note: str) -> tuple[Card, list[Card]]:
        card = self.card(short)
        text = f"{DONE_MARK} Integrada en develop"
        if pr:
            text += f": https://github.com/matiHirCab/Servio/pull/{pr}"
        if note:
            text += f". {note}"
        self.comment(card, text)
        self.move(card, LIST_DONE)
        self.set_label(card, LABEL_FREE, False)
        self.set_label(card, LABEL_BLOCKED, False)
        freed = self.sync_labels(self.cards())
        return card, freed


def is_work_list(name: str, extra: bool = False) -> bool:
    prefixes = WORK_LIST_PREFIXES + (EXTRA_LIST_PREFIXES if extra else ())
    return name.startswith(prefixes)


# --------------------------------------------------------------------- CLI


def cmd_listar(board: Board, args: argparse.Namespace) -> int:
    cards = board.cards()
    rows = []
    for card in cards:
        if card.list_name == LIST_IN_PROGRESS or is_work_list(
            card.list_name, args.todas
        ):
            state, why = board.status(card, cards)
            rows.append((card, state, why))
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "short": c.short,
                        "code": c.code,
                        "name": c.name,
                        "list": c.list_name,
                        "state": s,
                        "why": w,
                        "touches": c.header.touches,
                        "depends_on": c.header.depends_on,
                    }
                    for c, s, w in rows
                ],
                ensure_ascii=False,
                indent=1,
            )
        )
        return 0
    order = [
        LIST_IN_PROGRESS,
        *sorted(n for n in board.lists if is_work_list(n, args.todas)),
    ]
    marks = {
        "libre": "🟢",
        "bloqueada": "🔴",
        "choca": "🟠",
        "humano": "🟡",
        "en curso": "🔧",
    }
    for list_name in order:
        group = [r for r in rows if r[0].list_name == list_name]
        if not group:
            continue
        print(f"\n== {list_name}")
        for card, state, why in group:
            line = f"  {marks.get(state, '·')} {card.short}  {card.name}"
            if why:
                line += f"  ← {'; '.join(why)}"
            print(line)
    free = [c for c, s, _ in rows if s == "libre"]
    print(
        f"\n{len(free)} libre(s). Tomar una: python tools/tablero.py tomar <enlace> --agente <quién>"
    )
    return 0


def cmd_ver(board: Board, args: argparse.Namespace) -> int:
    card = board.card(args.tarjeta)
    cards = board.cards()
    state, why = board.status(card, cards)
    print(
        f"{card.name}\nhttps://trello.com/c/{card.short} · {card.list_name} · {state}"
    )
    if why:
        print("  " + "; ".join(why))
    print()
    print(card.desc)
    return 0


def cmd_tomar(board: Board, args: argparse.Namespace) -> int:
    card = board.take(args.tarjeta, args.agente, force=args.forzar)
    branch = f"{args.rama or 'agente'}/{slug(card.name)}"
    print(f"Tomada: {card.name}\nhttps://trello.com/c/{card.short}")
    print(f"Rama sugerida (desde develop): {branch}")
    header = card.header
    if header.touches:
        print("Toca: " + ", ".join(header.touches))
    if header.decisions:
        print(f"Decisiones que mandan: {header.decisions} (docs/decisiones-25-11.md)")
    return 0


def cmd_soltar(board: Board, args: argparse.Namespace) -> int:
    card = board.release(args.tarjeta, args.motivo)
    print(f"Soltada: {card.name} → «{card.list_name}»")
    return 0


def cmd_terminar(board: Board, args: argparse.Namespace) -> int:
    card, freed = board.finish(args.tarjeta, args.pr, args.nota)
    print(f"Terminada: {card.name}")
    for c in freed:
        print(f"  🟢 se liberó {c.code or c.short}: {c.name}")
    return 0


def cmd_sincronizar(board: Board, args: argparse.Namespace) -> int:
    freed = board.sync_labels(board.cards())
    print(f"Etiquetas al día. {len(freed)} tarjeta(s) recién liberada(s).")
    for c in freed:
        print(f"  🟢 {c.code or c.short}: {c.name}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("listar", help="qué se puede tomar y qué no")
    s.add_argument(
        "--todas", action="store_true", help="también «Después del 25/11» y Backlog"
    )
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_listar)

    s = sub.add_parser("ver", help="la descripción de una tarjeta")
    s.add_argument("tarjeta", help="enlace corto (lo que va en trello.com/c/XXXX) o id")
    s.set_defaults(fn=cmd_ver)

    s = sub.add_parser("tomar", help="reclamarla y pasarla a In progress")
    s.add_argument("tarjeta")
    s.add_argument(
        "--agente", required=True, help="quién la toma (p. ej. claude-jessica)"
    )
    s.add_argument("--rama", help="prefijo de rama; por defecto «agente»")
    s.add_argument(
        "--forzar", action="store_true", help="aunque esté bloqueada o choque"
    )
    s.set_defaults(fn=cmd_tomar)

    s = sub.add_parser("soltar", help="devolverla a su lista sin terminarla")
    s.add_argument("tarjeta")
    s.add_argument("--motivo", default="")
    s.set_defaults(fn=cmd_soltar)

    s = sub.add_parser("terminar", help="pasarla a Done y liberar las que dependían")
    s.add_argument("tarjeta")
    s.add_argument("--pr", type=int, help="número del PR mergeado en develop")
    s.add_argument("--nota", default="")
    s.set_defaults(fn=cmd_terminar)

    s = sub.add_parser("sincronizar", help="recalcular Libre / Bloqueada")
    s.set_defaults(fn=cmd_sincronizar)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # la consola de Windows no siempre es UTF-8
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    key, token = credentials(Path(__file__).resolve().parent)
    board = Board(Api(key, token))
    try:
        return args.fn(board, args)
    except SystemExit as e:
        if isinstance(e.code, tuple):  # (mensaje, código)
            print(e.code[0], file=sys.stderr)
            return int(e.code[1])
        if isinstance(e.code, str):
            print(e.code, file=sys.stderr)
            return 1
        raise


if __name__ == "__main__":
    sys.exit(main())
