"""El tablero de Trello para agentes que trabajan en paralelo.

Varios agentes (o personas) toman tarjetas del mismo tablero sin pisarse: cada
tarjeta dice de qué depende y qué archivos toca, el tablero marca cuáles están
libres, y tomar una es una operación con candado: la primera que la reclama se la
queda y pasa a «In progress» en el mismo paso.

    python tools/tablero.py listar                 # qué se puede tomar y qué no, y por qué
    python tools/tablero.py tomar 9oDZRoKz --agente claude-jessica
    python tools/tablero.py soltar 9oDZRoKz --agente claude-jessica    # devolverla si no se termina
    python tools/tablero.py terminar 9oDZRoKz --pr 72 --agente claude-jessica
    python tools/tablero.py sincronizar            # recalcula Libre / Bloqueada en todas

En la descripción de cada tarjeta, las primeras líneas son el encabezado que este
programa lee (ver «Tablero y trabajo en paralelo» en AGENTS.md):

    **Área:** alerts · **Toca:** `vision/servio_vision/alerts/`, `docs/context/05-reglas-alertas.md`
    **Depende de:** C-5 (9oDZRoKz), A-4 (hecha)
    **Decisiones:** 5, 6

`Depende de` nombra tarjetas por su enlace corto entre paréntesis: una tarjeta está
libre cuando todas están en «Done» (o archivadas). `Toca` son rutas del repo: dos
tarjetas que tocan lo mismo no se trabajan a la vez. La etiqueta «Humano» marca lo
que un agente no puede hacer (armar una cámara, comer en casa) y no se toma desde acá.

El candado es un comentario «🔒 …» en la tarjeta; un «🔓 …» (lo deja `soltar`) o un
«✅ …» (lo deja `terminar`) lo borra. Si una tarjeta quedó con un candado viejo (la
movieron a mano, se cortó la red), `soltar` lo limpia y `tomar --forzar` lo pisa.

Credenciales: TRELLO_KEY y TRELLO_TOKEN en el entorno o en el `.env` del repo (ver
`.env.example`; también se busca en el `.env` de la carpeta de arriba del repo).
Cada persona usa las suyas; son de su cuenta de Trello. Se corre desde `vision/` con
el venv del repo (usa `python-dotenv`, como `settings.py`; nada más). Sale con 0 si hizo lo
pedido, 1 si no se puede (tarjeta bloqueada, ocupada, sin permiso), 2 si otro
agente la tomó justo antes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cached_property
from pathlib import Path
from typing import Any, NoReturn

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from servio_vision import ENV_FILE

BOARD_ID = "6ad386f07003eed0f9a752c8"
API = "https://api.trello.com/1"
DEFAULT_ENV_FILES = (ENV_FILE, ENV_FILE.parent.parent / ".env")

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
# Lo que escribe la herramienta y borra los candados anteriores. Un «✅ probado» de una
# persona no cuenta.
RESET_PREFIXES = (f"{UNLOCK} soltada", f"{UNLOCK} candado", f"{DONE_MARK} Integrada")
RETRIES_429 = 3  # reintentos cuando Trello devuelve «demasiadas llamadas»
LOCK_READ_RETRIES = 4  # lecturas hasta ver el candado propio
# La edad de un candado se mide con la hora de Trello cuando hay un comentario propio
# para comparar (tomar); en soltar y en --forzar se usa el reloj de esta máquina, que se
# supone en hora (NTP). Con un reloj 10 minutos corrido, un candado fresco parece viejo.
STALE_LOCK_MIN = (
    10  # un candado de más que esto en una tarjeta que no está en curso es viejo
)

CARD_FIELDS = "name,desc,idList,labels,shortLink,idMembers,closed"

_PAREN_TOKEN = re.compile(r"\(([A-Za-z0-9_-]{8})\)")
_BACKTICKED = re.compile(r"`([^`]+)`")
# una línea que son solo rutas o solo referencias a tarjetas («C-5 (9oDZRoKz)»)
_ONLY_PATHS = re.compile(
    r"(?:(?:`[^`]+`(?:\s*\([^()`]*\))?|[A-Z][A-Za-z0-9-]*\s*\([A-Za-z0-9_-]{8}\))[\s,;·]*)+"
)
# `**Clave:** valor` o `**Clave**: valor`
_HEADER_FIELD = re.compile(
    r"\*\*(?P<key>[^*:]+?)(?::\*\*|\*\*:)(?P<value>.*?)(?=\*\*[^*:]+?(?::\*\*|\*\*:)|$)"
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
    depends_text: str = ""  # lo escrito, para avisar si no trae enlaces
    touches: list[str] = field(default_factory=list)  # rutas del repo
    area: str = ""
    decisions: str = ""


def short_links(value: str) -> list[str]:
    """Lo que parece un enlace corto de Trello entre paréntesis (8 caracteres). Si
    es una palabra («(opcional)») y no una tarjeta, el tablero lo avisa y no bloquea."""
    return _PAREN_TOKEN.findall(value)


def header_lines(desc: str) -> str:
    """Las líneas del encabezado, juntas: las primeras con un campo `**Clave:**`.
    Las líneas en blanco no cortan (el editor de Trello guarda cada Enter como un
    párrafo), y tampoco un título o una viñeta antes del primer campo («## Contexto»,
    «- **Toca:** …»); la primera línea de texto común después de un campo, sí."""
    taken: list[str] = []
    for line in desc.replace("\r", "").split("\n"):
        stripped = re.sub(r"^(?:[-*•]\s+|#+\s+)", "", line.strip())  # viñeta o título
        if not stripped:
            continue
        if _HEADER_FIELD.search(stripped) or (
            taken and _ONLY_PATHS.fullmatch(stripped)
        ):
            taken.append(stripped)
        elif taken:
            break  # la primera línea de texto común cierra el encabezado
    return " ".join(taken)


def parse_header(desc: str) -> Header:
    """Lee los campos `**Clave:** valor` del encabezado de la descripción. Lo de
    abajo es texto libre y puede nombrar otras tarjetas sin que cuenten."""
    header = Header()
    seen: set[str] = set()
    for m in _HEADER_FIELD.finditer(header_lines(desc)):
        key = m.group("key").strip().lower()
        value = m.group("value").strip(" ·")
        if key.startswith("depende"):
            key = "depende"
        elif key.startswith(("área", "area")):
            key = "area"
        elif key.startswith("decision"):
            key = "decisiones"
        if key in seen:  # una negrita del cuerpo no pisa al encabezado
            continue
        seen.add(key)
        if key == "depende":
            header.depends_on = short_links(value)
            header.depends_text = value
        elif key == "toca":
            header.touches = [p.strip() for p in _BACKTICKED.findall(value)]
        elif key == "area":
            header.area = value
        elif key == "decisiones":
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
            if (
                not xn
                or not yn
                or xn == yn
                or xn.startswith(yn + "/")
                or yn.startswith(xn + "/")
            ):  # «*.py», «**/*.md»: sin carpeta, toca todo
                hits.append(f"{x} ~ {y}")
    return hits


def _norm(path: str) -> str:
    """`./vision/tools/*.py` → `vision/tools`: lo que hay antes del comodín es la carpeta."""
    path = path.replace("\\", "/").strip().removeprefix("./")
    head, glob, _rest = _split_glob(path)
    if glob and not head.endswith("/"):  # `tests/test_*.py` → la carpeta `tests`
        head = head.rsplit("/", 1)[0] if "/" in head else ""
    return head.rstrip("/")


def _split_glob(path: str) -> tuple[str, str, str]:
    m = re.search(r"[*?\[]", path)
    if not m:
        return path, "", ""
    return path[: m.start()], m.group(0), path[m.end() :]


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
    plain = (
        unicodedata.normalize("NFKD", rest.lower()).encode("ascii", "ignore").decode()
    )
    words = re.findall(r"[a-z0-9]+", plain)[:4]
    base = "-".join(w for w in [code.lower() if code else ""] + words if w)
    return base or "tarjeta"


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
        if t.startswith(RESET_PREFIXES):
            claims = []
        elif (lock := parse_lock(t)) is not None:
            claims.append(lock)
    return claims


def winner(comments: list[tuple[str, str]]) -> tuple[str, str, str] | None:
    """Quién se quedó con la tarjeta: el primer candado vigente."""
    claims = active_claims(comments)
    return claims[0] if claims else None


def lock_date(comments: list[tuple[str, str]], token: str) -> datetime:
    """Cuándo se puso el candado con ese token (la fecha del comentario)."""
    for date, text in comments:
        lock = parse_lock(text)
        if lock and lock[0] == token:
            return datetime.fromisoformat(date)  # Python 3.11 entiende la Z
    return datetime.now(UTC)


def lock_age_minutes(
    comments: list[tuple[str, str]], token: str, now: datetime | None = None
) -> float:
    """Minutos desde que se puso el candado con ese token (por la fecha del comentario)."""
    now = now or datetime.now(UTC)
    return (now - lock_date(comments, token)).total_seconds() / 60


def credentials(
    env_files: Iterable[Path] = DEFAULT_ENV_FILES,
    environ: Mapping[str, str] = os.environ,
) -> tuple[str, str]:
    """TRELLO_KEY y TRELLO_TOKEN: del entorno, o del primer `.env` que los tenga
    (el del repo, el de la carpeta de arriba). Se leen con `python-dotenv`, el mismo
    lector que `settings.py`, sin tocar `os.environ`."""
    values: dict[str, str | None] = {}
    for env in env_files:
        if env.is_file():
            for key, value in dotenv_values(env).items():
                if (
                    value and key not in values
                ):  # un `TRELLO_TOKEN=` vacío no tapa al de abajo
                    values[key] = value
    key = environ.get("TRELLO_KEY") or values.get("TRELLO_KEY")
    token = environ.get("TRELLO_TOKEN") or values.get("TRELLO_TOKEN")
    if not key or not token:
        raise SystemExit(
            "Faltan TRELLO_KEY y TRELLO_TOKEN (en el entorno o en el .env del repo; "
            "ver .env.example). Se sacan de https://trello.com/power-ups/admin con tu cuenta."
        )
    return key, token


# ------------------------------------------------------------------ Trello


def _retry_after(value: str | None) -> float:
    """Segundos a esperar según Retry-After, entre 0 y 10; si es una fecha o basura, 2 s."""
    try:
        wait = float(value or 2)
    except ValueError:
        return 2.0
    return 2.0 if math.isnan(wait) else min(10.0, max(0.0, wait))


class Api:
    """Lo mínimo de la API REST de Trello, con urllib. La clave y el token van
    siempre en la URL, que es como Trello los documenta."""

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
        url = f"{API}{path}?" + urllib.parse.urlencode(self.auth)
        data = None
        if method in ("GET", "DELETE"):
            if params:
                url += "&" + urllib.parse.urlencode(params)
        else:
            data = urllib.parse.urlencode(params).encode()
        req = urllib.request.Request(url, data=data, method=method)
        for attempt in range(RETRIES_429 + 1):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    body = resp.read()
                return json.loads(body) if body else None
            except urllib.error.HTTPError as e:
                if (
                    e.code == 429 and attempt < RETRIES_429
                ):  # tope de Trello: esperar y seguir
                    time.sleep(_retry_after(e.headers.get("Retry-After")))
                    continue
                detail = e.read().decode(errors="replace")[:200]
                raise SystemExit(
                    f"Trello respondió {e.code} a {method} {path}: {detail}"
                ) from e
            except urllib.error.URLError as e:
                raise SystemExit(f"No se pudo hablar con Trello: {e.reason}") from e
            except (
                TimeoutError,
                OSError,
                ValueError,
            ) as e:  # socket, lectura, JSON roto
                raise SystemExit(f"No se pudo hablar con Trello: {e}") from e
        return None  # no se llega: el for devuelve o levanta


@dataclass
class Card:
    id: str
    short: str
    name: str
    desc: str
    list_name: str
    labels: list[str]
    members: list[str]  # usuarios
    member_ids: list[str]
    closed: bool = False
    notes: list[str] = field(default_factory=list)  # avisos de `status` al tomarla
    label_ids: dict[str, str] = field(
        default_factory=dict
    )  # nombre → id de la etiqueta puesta

    @cached_property
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
        self._closed_lists: set[str] = set()

    # --- lectura

    @property
    def lists(self) -> dict[str, str]:
        """Las listas abiertas (nombre → id); las archivadas van a `closed_lists`."""
        if self._lists is None:
            rows = self.api.get(
                f"/boards/{self.board_id}/lists", fields="name,closed", filter="all"
            )
            self._lists = {r["name"]: r["id"] for r in rows if not r.get("closed")}
            self._closed_lists = {r["id"] for r in rows if r.get("closed")}
            for needed in (LIST_IN_PROGRESS, LIST_DONE):
                if needed not in self._lists:
                    raise SystemExit(
                        f"El tablero no tiene una lista «{needed}»: el protocolo la necesita tal cual."
                    )
        return self._lists

    @property
    def closed_lists(self) -> set[str]:
        _ = self.lists
        return self._closed_lists

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

    def work_lists(self, extra: bool = False) -> list[str]:
        """«Semana 3 …» antes que «Semana 10 …»: se ordena por el número."""
        return sorted(
            (n for n in self.lists if is_work_list(n, extra)), key=_list_order
        )

    def cards(self) -> list[Card]:
        """Todas, archivadas incluidas: una dependencia hecha y archivada sigue hecha."""
        rows = self.api.get(
            f"/boards/{self.board_id}/cards", fields=CARD_FIELDS, filter="all"
        )
        return [self._card(r) for r in rows]

    def card(self, short_or_id: str) -> Card:
        return self._card(self.api.get(f"/cards/{short_or_id}", fields=CARD_FIELDS))

    def _card(self, r: dict[str, Any]) -> Card:
        return Card(
            id=r["id"],
            short=r["shortLink"],
            name=r["name"],
            desc=r.get("desc", ""),
            list_name=self.list_name(r["idList"]),
            labels=[lb["name"] for lb in r.get("labels", []) if lb.get("name")],
            closed=bool(r.get("closed", False)) or r["idList"] in self.closed_lists,
            label_ids={
                lb["name"]: lb["id"]
                for lb in r.get("labels", [])
                if lb.get("name") and lb.get("id")
            },
            members=[self.members.get(m, m) for m in r.get("idMembers", [])],
            member_ids=list(r.get("idMembers", [])),
        )

    def comments(self, card: Card) -> list[tuple[str, str]]:
        """Los comentarios, del más nuevo al más viejo, paginados hasta encontrar un 🔓 o
        un ✅ (lo anterior no cuenta para el candado) o hasta que no haya más."""
        out: list[tuple[str, str]] = []
        before: str | None = None
        while True:
            params: dict[str, Any] = {"filter": "commentCard", "limit": 100}
            if before:
                params["before"] = before
            rows = self.api.get(f"/cards/{card.id}/actions", **params)
            out.extend((r["date"], r["data"]["text"]) for r in rows)
            if len(rows) < 100 or any(
                r["data"]["text"].strip().startswith(RESET_PREFIXES) for r in rows
            ):
                return out
            before = rows[-1]["id"]

    def _still_there(self, card: Card, origin: str) -> None:
        """La tarjeta sigue en la lista de la que se la vio; si no, no se toca."""
        fresh_card = self.card(card.id)
        if fresh_card.list_name != origin or fresh_card.closed:
            raise SystemExit(
                f"{card.name}: mientras tanto pasó a «{fresh_card.list_name}»; no se toma."
            )

    def _find(self, short: str) -> Card:
        """La tarjeta de este tablero con ese enlace o id; un enlace ajeno no sirve."""
        card = next(
            (c for c in self.cards() if c.short == short or c.id == short), None
        )
        if card is None:
            raise SystemExit(f"No hay una tarjeta {short} en el tablero.")
        return card

    def _comments_with(
        self, card: Card, token: str, mine: str
    ) -> list[tuple[str, str]]:
        """Los comentarios, esperando a que el candado propio (`mine`) aparezca: Trello
        puede tardar un instante en listar un comentario recién hecho. Si nunca
        aparece, avisa; el candado lo borra quien llamó."""
        for attempt in range(LOCK_READ_RETRIES):
            comments = self.comments(card)
            if any((lock := parse_lock(t)) and lock[0] == token for _d, t in comments):
                return comments
            time.sleep(0.5 * (attempt + 1))
        raise SystemExit(
            f"{card.name}: Trello no muestra el candado recién puesto; probá de nuevo en un rato."
        )

    @staticmethod
    def done_links(cards: list[Card]) -> set[str]:
        return {c.short for c in cards if c.list_name == LIST_DONE or c.closed}

    # --- estado de cada tarjeta

    def status(
        self,
        card: Card,
        cards: list[Card],
        done: set[str] | None = None,
        known: set[str] | None = None,
    ) -> tuple[str, list[str]]:
        """('libre' | 'bloqueada' | 'humano' | 'en curso' | 'hecha' | 'choca', motivos)."""
        if card.list_name == LIST_DONE or card.closed:
            return "hecha", []
        if card.list_name == LIST_IN_PROGRESS:
            who = ", ".join(card.members) or "alguien"
            return "en curso", [who]
        if LABEL_HUMAN in card.labels:
            return "humano", ["la hace una persona"]
        known = {c.short for c in cards} if known is None else known
        missing = missing_deps(
            card.header, self.done_links(cards) if done is None else done
        )
        unknown = [
            f"«{m}» no es ninguna tarjeta: revisar el encabezado"
            for m in missing
            if m not in known
        ]
        missing = [m for m in missing if m in known]
        if missing:
            names = [self._name_of(m, cards) for m in missing]
            return "bloqueada", [f"espera {n}" for n in names] + unknown
        clashes = self.clashes(card, cards)
        if clashes:
            return "choca", clashes + unknown
        if is_work_list(card.list_name, extra=True) and not card.header.touches:
            unknown.append("sin encabezado: no se sabe qué toca")
        text = card.header.depends_text.strip().lower()
        if text and text != "nada" and not card.header.depends_on:
            unknown.append(
                f"«Depende de: {card.header.depends_text}» no trae enlaces (XXXXXXXX): no bloquea"
            )
        return "libre", unknown

    def clashes(self, card: Card, cards: list[Card]) -> list[str]:
        """Con qué tarjetas en curso comparte archivos."""
        out = []
        for other in cards:
            if (
                other.id == card.id
                or other.list_name != LIST_IN_PROGRESS
                or other.closed
            ):
                continue
            hits = paths_overlap(card.header.touches, other.header.touches)
            if hits:
                out.append(
                    f"toca lo mismo que {other.code or other.short} en curso ({hits[0]})"
                )
        return out

    @staticmethod
    def _name_of(short: str, cards: list[Card]) -> str:
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
            card.label_ids[name] = self.labels[name]
        elif not on and has:
            label_id = card.label_ids.pop(name, None) or self.labels[name]
            self.api.delete(f"/cards/{card.id}/idLabels/{label_id}")
            card.labels.remove(name)

    def move(self, card: Card, list_name: str) -> None:
        self.api.put(f"/cards/{card.id}", idList=self.lists[list_name], pos="top")
        card.list_name = list_name

    def comment(self, card: Card, text: str) -> str:
        row = self.api.post(f"/cards/{card.id}/actions/comments", text=text)
        return row["id"]

    def me(self) -> str:
        return self.api.get("/members/me", fields="id")["id"]

    def sync_labels(
        self, cards: list[Card], only_dependents_of: str | None = None
    ) -> list[Card]:
        """Pone Libre / Bloqueada según las dependencias. Devuelve las recién liberadas.
        Con `only_dependents_of`, solo mira las tarjetas que dependen de ese enlace."""
        self.ensure_labels()
        done = self.done_links(cards)
        known = {c.short for c in cards}
        freed = []
        for card in cards:
            if card.closed or not is_work_list(card.list_name, extra=True):
                continue
            if (
                only_dependents_of
                and only_dependents_of not in card.header.depends_on
                and card.short != only_dependents_of  # la propia, al soltarla
            ):
                continue
            if LABEL_HUMAN in card.labels:
                self.set_label(card, LABEL_FREE, False)
                self.set_label(card, LABEL_BLOCKED, False)
                continue
            blocked = any(d in known for d in missing_deps(card.header, done))
            was_blocked = LABEL_BLOCKED in card.labels
            self.set_label(card, LABEL_BLOCKED, blocked)
            self.set_label(card, LABEL_FREE, not blocked)
            if was_blocked and not blocked:
                freed.append(card)
        return freed

    # --- el protocolo

    @staticmethod
    def _check_agent(agent: str) -> None:
        if not agent or any(ch in agent for ch in " ·«»\n"):
            raise SystemExit(
                f"--agente «{agent}»: una palabra sin espacios, por ejemplo claude-jessica."
            )

    def take(self, short: str, agent: str, force: bool = False) -> Card:
        self._check_agent(agent)
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
        if not is_work_list(card.list_name, extra=True) and not force:
            raise SystemExit(
                f"{card.name}: está en «{card.list_name}», que no es una lista de trabajo."
            )
        card.notes = why
        # Con --forzar, un candado viejo (la movieron a mano, se cortó la red) se pisa a
        # propósito; uno fresco es otro agente tomándola ahora y sigue la carrera normal.
        if (
            force
            and (old := winner(before := self.comments(card))) is not None
            and lock_age_minutes(before, old[0]) > STALE_LOCK_MIN
        ):
            self.comment(card, f"{UNLOCK} candado viejo pisado por {agent}")
        token = uuid.uuid4().hex[:12]
        origin = card.list_name
        mine: str | None = None
        try:
            mine = self.comment(
                card, lock_text(token, agent, origin, datetime.now(UTC))
            )
            comments = self._comments_with(card, token, mine)
            won = winner(comments)
            if won is None:
                # un 🔓 o ✅ cayó justo después del nuestro. Si fue un ✅ (otro la terminó)
                # o alguien la movió, la tarjeta ya no es la que miramos: no se toca.
                self._still_there(card, origin)
                self.api.delete(f"/actions/{mine}")
                mine = None
                mine = self.comment(
                    card, lock_text(token, agent, origin, datetime.now(UTC))
                )
                comments = self._comments_with(card, token, mine)
                won = winner(comments)
            if won is None or won[0] != token:  # el candado lo borra el except de abajo
                who = won[1] if won else "otro"
                now = lock_date(
                    comments, token
                )  # la hora de Trello, no la de esta máquina
                age = lock_age_minutes(comments, won[0], now) if won else 0.0
                if age > STALE_LOCK_MIN:
                    raise SystemExit(
                        f"{card.name}: tiene un candado viejo de {who} (hace {age:.0f} min) "
                        "y no está en curso: `soltar --agente <quién>` lo limpia, o "
                        "`tomar --forzar` lo pisa."
                    )
                if won is None:
                    raise SystemExit(
                        (
                            f"{card.name}: cambió de manos dos veces seguidas; probá de nuevo.",
                            2,
                        )
                    )
                raise SystemExit((f"{card.name}: la tomó {who} justo antes.", 2))
            self._still_there(card, origin)  # alguien pudo moverla a mano en el medio
            self.move(card, LIST_IN_PROGRESS)
        except BaseException:
            # cualquier cosa que falle entre el candado y el movimiento (red, Ctrl-C, una
            # carrera perdida) no deja el candado puesto en una tarjeta que no se movió
            if mine:
                try:
                    moved = self.card(card.id).list_name == LIST_IN_PROGRESS
                except (
                    SystemExit
                ):  # sin red no se sabe: mejor un candado de más que una
                    moved = True  # tarjeta en curso sin candado
                if not moved:
                    try:
                        self.api.delete(f"/actions/{mine}")
                    except SystemExit:
                        pass
            raise
        # Ya en curso, los choques de archivos se miran de nuevo: otro agente pudo tomar
        # una tarjeta que toca lo mismo en el medio. Si las dos entraron a la vez, retrocede
        # la del candado más nuevo (la otra, al mirar, ve que la suya es la vieja y sigue).
        rolled_back = False
        try:
            if state != "choca":
                mine_at = lock_date(comments, token)
                for other in self.cards():
                    if (
                        other.id == card.id
                        or other.closed
                        or not (
                            other.list_name == LIST_IN_PROGRESS
                            or is_work_list(other.list_name, extra=True)
                        )
                    ):
                        continue
                    hit = paths_overlap(card.header.touches, other.header.touches)
                    if not hit:
                        continue
                    # en curso, o con un candado reciente (la está tomando y todavía no la movió)
                    theirs = winner(other_comments := self.comments(other))
                    theirs_at = lock_date(other_comments, theirs[0]) if theirs else None
                    if other.list_name != LIST_IN_PROGRESS and (
                        theirs is None
                        or lock_age_minutes(other_comments, theirs[0], mine_at)
                        > STALE_LOCK_MIN
                    ):
                        continue
                    # retrocede la del candado más nuevo; a igual hora, la del token mayor
                    if theirs_at is None or (
                        theirs_at,
                        theirs[0] if theirs else "",
                    ) <= (
                        mine_at,
                        token,
                    ):
                        self.comment(
                            card,
                            f"{UNLOCK} soltada: toca lo mismo que {other.code or other.short}",
                        )
                        self.move(card, origin)
                        rolled_back = True
                        raise SystemExit(
                            (
                                f"{card.name}: {other.code or other.short} toca lo mismo y entró antes ({hit[0]}).",
                                2,
                            )
                        )
        except BaseException:
            if (
                not rolled_back
            ):  # un error en la segunda mirada (red, Ctrl-C) no deja la tarjeta colgada
                try:
                    self.comment(card, f"{UNLOCK} soltada: falló la segunda mirada")
                finally:
                    self.move(card, origin)
            raise
        try:  # la tarjeta ya es nuestra: lo de acá abajo es cosmético
            self.set_label(card, LABEL_FREE, False)
            self.set_label(card, LABEL_BLOCKED, False)
            me = self.me()
            if me not in card.member_ids:
                self.api.post(f"/cards/{card.id}/idMembers", value=me)
        except SystemExit as e:
            card.notes.append(f"tomada, pero no se pudo etiquetar o asignar: {e.code}")
        return card

    def release(
        self, short: str, reason: str, agent: str = "", force: bool = False
    ) -> Card:
        card = self._find(short)
        won = winner(comments := self.comments(card))
        if card.list_name != LIST_IN_PROGRESS:
            if won is None:
                raise SystemExit(
                    f"{card.name}: no está en curso (está en «{card.list_name}»)."
                )
            if lock_age_minutes(comments, won[0]) <= STALE_LOCK_MIN and not force:
                raise SystemExit(
                    f"{card.name}: {won[1]} la está tomando ahora mismo (candado de hace un "
                    "momento); no es un candado viejo."
                )
            # candado viejo en una tarjeta que ya volvió a su lista: solo se limpia
            self.comment(
                card, f"{UNLOCK} candado viejo limpiado: {reason or 'sin motivo'}"
            )
            return card
        if won is not None and agent and won[1] != agent and not force:
            raise SystemExit(
                f"{card.name}: la tiene tomada {won[1]}, no {agent}. Con --forzar se suelta igual."
            )
        origin = won[2] if won and won[2] in self.lists else None
        if origin is None:  # la pusieron en curso a mano: a la primera lista de trabajo
            work = self.work_lists()
            if not work:
                raise SystemExit(
                    f"{card.name}: no hay ninguna lista «Semana…» adonde devolverla."
                )
            origin = work[0]
        self.comment(card, f"{UNLOCK} soltada: {reason or 'sin terminar'}")
        self.move(card, origin)
        self.sync_labels(self.cards(), only_dependents_of=card.short)
        return card

    def finish(
        self,
        short: str,
        pr: int | None,
        note: str,
        agent: str = "",
        force: bool = False,
    ) -> tuple[Card, list[Card]]:
        card = self._find(short)
        if card.list_name == LIST_DONE or card.closed:
            raise SystemExit(f"{card.name}: ya está hecha.")
        if card.list_name != LIST_IN_PROGRESS and not force:
            raise SystemExit(
                f"{card.name}: no está en curso (está en «{card.list_name}»); "
                "¿es la tarjeta correcta? Con --forzar se termina igual."
            )
        won = winner(self.comments(card))
        if won is not None and agent and won[1] != agent and not force:
            raise SystemExit(
                f"{card.name}: la tiene tomada {won[1]}, no {agent}. Con --forzar se termina igual."
            )
        text = f"{DONE_MARK} Integrada en develop"
        if pr:
            text += f": https://github.com/matiHirCab/Servio/pull/{pr}"
        if note:
            text += f". {note}"
        self.comment(card, text)
        self.move(card, LIST_DONE)
        self.set_label(card, LABEL_FREE, False)
        self.set_label(card, LABEL_BLOCKED, False)
        freed = self.sync_labels(self.cards(), only_dependents_of=card.short)
        return card, freed


def _list_order(name: str) -> tuple[int, str]:
    m = re.search(r"\d+", name)
    return (int(m.group(0)) if m else 10**6, name)


def is_work_list(name: str, extra: bool = False) -> bool:
    prefixes = WORK_LIST_PREFIXES + (EXTRA_LIST_PREFIXES if extra else ())
    return name.startswith(prefixes)


# --------------------------------------------------------------------- CLI


def cmd_listar(board: Board, args: argparse.Namespace) -> int:
    cards = board.cards()
    done = board.done_links(cards)
    known = {c.short for c in cards}
    rows = []
    for card in cards:
        if card.closed:
            continue
        if card.list_name == LIST_IN_PROGRESS or is_work_list(
            card.list_name, args.todas
        ):
            state, why = board.status(card, cards, done, known)
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
    order = [LIST_IN_PROGRESS, *board.work_lists(args.todas)]
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
            line = f"  {marks.get(state, '·')} {state:9} {card.short}  {card.name}"
            if why:
                line += f"  ← {'; '.join(why)}"
            print(line)
    free = [c for c, s, _ in rows if s == "libre"]
    print(
        f"\n{len(free)} libre(s). Tomar una: python tools/tablero.py tomar <enlace> --agente <quién>"
    )
    return 0


def cmd_ver(board: Board, args: argparse.Namespace) -> int:
    cards = board.cards()
    card = next(
        (c for c in cards if c.short == args.tarjeta or c.id == args.tarjeta), None
    )
    if card is None:
        raise SystemExit(f"No hay una tarjeta {args.tarjeta} en el tablero.")
    state, why = board.status(card, cards)
    print(
        f"{card.name}\nhttps://trello.com/c/{card.short} · {card.list_name} · {state}"
    )
    if why:
        print("  " + "; ".join(why))
    comments = board.comments(card)
    if (lock := winner(comments)) is not None:
        print(
            f"  🔒 candado de {lock[1]} (hace {lock_age_minutes(comments, lock[0]):.0f} min)"
        )
    print()
    print(card.desc)
    return 0


def cmd_tomar(board: Board, args: argparse.Namespace) -> int:
    card = board.take(args.tarjeta, args.agente, force=args.forzar)
    branch = f"{args.rama or 'agente'}/{slug(card.name)}"
    print(f"Tomada: {card.name}\nhttps://trello.com/c/{card.short}")
    for note in card.notes:
        print(f"  ⚠ {note}")
    print(f"Rama sugerida (desde develop): {branch}")
    header = card.header
    if header.touches:
        print("Toca: " + ", ".join(header.touches))
    if header.decisions:
        print(f"Decisiones que mandan: {header.decisions}")
    return 0


def cmd_soltar(board: Board, args: argparse.Namespace) -> int:
    card = board.release(args.tarjeta, args.motivo, args.agente or "", args.forzar)
    print(f"Soltada: {card.name} → «{card.list_name}»")
    return 0


def cmd_terminar(board: Board, args: argparse.Namespace) -> int:
    card, freed = board.finish(
        args.tarjeta, args.pr, args.nota, args.agente or "", args.forzar
    )
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


class _Parser(argparse.ArgumentParser):
    def error(
        self, message: str
    ) -> NoReturn:  # argparse sale con 2; acá el 2 es la carrera
        self.print_usage(sys.stderr)
        raise SystemExit(f"{self.prog}: {message}")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
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
        "--forzar",
        action="store_true",
        help="aunque esté bloqueada, choque o tenga un candado viejo",
    )
    s.set_defaults(fn=cmd_tomar)

    s = sub.add_parser(
        "soltar",
        help="devolverla a su lista sin terminarla (o limpiar un candado viejo)",
    )
    s.add_argument("tarjeta")
    s.add_argument("--motivo", default="")
    s.add_argument(
        "--agente",
        required=True,
        help="quién la suelta; si no es quien la tomó, hace falta --forzar",
    )
    s.add_argument("--forzar", action="store_true", help="aunque la haya tomado otro")
    s.set_defaults(fn=cmd_soltar)

    s = sub.add_parser("terminar", help="pasarla a Done y liberar las que dependían")
    s.add_argument("tarjeta")
    s.add_argument("--pr", type=int, help="número del PR mergeado en develop")
    s.add_argument("--nota", default="")
    s.add_argument(
        "--agente",
        required=True,
        help="quién la termina; si no es quien la tomó, hace falta --forzar",
    )
    s.add_argument(
        "--forzar",
        action="store_true",
        help="aunque no esté en curso o la haya tomado otro",
    )
    s.set_defaults(fn=cmd_terminar)

    s = sub.add_parser("sincronizar", help="recalcular Libre / Bloqueada")
    s.set_defaults(fn=cmd_sincronizar)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (
        sys.stdout,
        sys.stderr,
    ):  # por un pipe en Windows sale cp1252: UTF-8 siempre
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        args = build_parser().parse_args(argv)
        key, token = credentials()
        board = Board(Api(key, token))
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
