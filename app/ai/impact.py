"""The impact page's paragraph (design §7.3): figures from the log, words from the model.

Code builds the figures from the stored verdicts. The model is asked for two
or three sentences that use only those figures. The gate then reads every
number in the reply: each must be one of the figures, or the sum of two of
them stated so (both addends also appear in the paragraph). A reply that brings in any other number is replaced by a sentence code
writes from the same figures, so the page can never state a figure the log
does not hold.

The paragraph is cached per process by the figures' hash, so the page calls
the model once per change in the log, not once per load.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Protocol

from sqlalchemy import select

from app.ai.client import GenAiError
from app.data import db, repository
from app.domain.result import CheckStatus, Outcome

WRITTEN_BY_MODEL = "written by the model from the figures above"
WRITTEN_BY_CODE = "written by code from the figures above"

# A failed model call is retried after this long, not on every page load.
FAILURE_RETRY_SECONDS = 300

SYSTEM = (
    "You write one short paragraph for the impact page of a tool that screens donated "
    "medical equipment against the clinic it is going to. You may use only the figures "
    "you are given. Do not compute percentages, ratios or differences. Do not add any "
    "other number, year, standard number or estimate. You may add two of the given "
    "figures together only if you say that you did. Plain prose, no lists, no headings, "
    "no markdown."
)

PROMPT = (
    "Figures from the screening log, as JSON:\n{figures}\n\n"
    "Key meanings: screened = devices screened; blocked = screenings stopped before "
    "shipping (won't work); add_ons = screenings that work only if add-ons ship with the "
    "device; needs_info = screenings that need answers before a verdict; blockers = how "
    "many screenings each check stopped; clinics = clinics screened against; countries = "
    "their countries.\n\n"
    "Write two or three sentences for a judge reading the page: what the screening "
    "caught and why it matters. Use only these figures."
)

# Number words the gate also reads, so "twenty" cannot slip past it as text.
_UNITS = ("zero one two three four five six seven eight nine ten eleven twelve thirteen "
          "fourteen fifteen sixteen seventeen eighteen nineteen").split()
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
         "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "dozen": 12}
NUMBER_WORDS: dict[str, int] = {word: n for n, word in enumerate(_UNITS)} | _TENS
_DIGITS = re.compile(r"\d[\d,]*(?:\.\d+)?")
_WORDS = re.compile(r"[a-z]+(?:-[a-z]+)?")


class ChatClient(Protocol):
    def complete(self, prompt: str, **kwargs: Any) -> Any: ...


@dataclass(frozen=True)
class Summary:
    text: str
    by_model: bool

    @property
    def label(self) -> str:
        return WRITTEN_BY_MODEL if self.by_model else WRITTEN_BY_CODE


# ---- the figures ------------------------------------------------------------

def figures() -> dict:
    """The log's figures: the counters' tally plus blockers, clinics and countries.

    `blockers` counts FAIL results only (a check that stopped a screening),
    not the MITIGABLE ones the bars also count, so "blocked" means blocked.
    Raises RepositoryError when the log cannot be read.
    """
    counts = repository.count_by_outcome()
    try:
        with db.get_engine().connect() as conn:
            rows = conn.execute(
                select(db.verdicts.c.facility_slug, db.verdicts.c.verdict_json)
            ).all()
    except Exception as exc:  # noqa: BLE001
        raise repository.RepositoryError(f"Could not read the log: {exc}") from exc

    blockers: dict[str, int] = {}
    slugs: set[str] = set()
    for slug, payload in rows:
        slugs.add(slug)
        for result in json.loads(payload).get("results", []):
            if result.get("status") == CheckStatus.FAIL.value:
                blockers[result["label"]] = blockers.get(result["label"], 0) + 1

    countries = sorted({_country(slug) for slug in slugs} - {""})
    return {
        "screened": sum(counts.values()),
        "blocked": counts.get(Outcome.WONT_WORK.value, 0),
        "blockers": dict(sorted(blockers.items(), key=lambda kv: (-kv[1], kv[0]))),
        "add_ons": counts.get(Outcome.WORKS_IF.value, 0),
        "needs_info": counts.get(Outcome.NEEDS_INFO.value, 0),
        "clinics": len(slugs),
        "countries": countries,
    }


def _country(slug: str) -> str:
    from app.data import marketplace_repo

    try:
        found = marketplace_repo.load_facility(slug)
    except repository.RepositoryError:
        found = None
    if found is None:
        from app.data.seed import FACILITIES_BY_SLUG

        found = FACILITIES_BY_SLUG.get(slug)
    return found.country if found else ""


# ---- the model's paragraph and its gate ------------------------------------

def write_summary(figures: dict, client: ChatClient) -> str:
    """Ask the reasoning model for two or three sentences from these figures only."""
    prompt = PROMPT.format(figures=json.dumps(figures, indent=1, sort_keys=True))
    reply = client.complete(prompt, system=SYSTEM)
    return str(getattr(reply, "text", reply) or "").strip()


def figure_values(figures: dict) -> list[int]:
    """Every figure, the blockers' counts and the count of countries."""
    values = [v for k, v in figures.items() if isinstance(v, int) and not isinstance(v, bool)]
    values += [v for v in figures.get("blockers", {}).values() if isinstance(v, int)]
    values.append(len(figures.get("countries", ())))
    return values


def allowed_numbers(figures: dict, stated: set[int] | frozenset[int] = frozenset()) -> set[int]:
    """Every figure, the count of countries, and each sum of two figures stated so.

    A sum counts as stated only when both of its addends are among `stated`,
    the numbers the paragraph itself gives (§7.3: "a sum of two of them,
    stated so"). With nothing stated, no sum is allowed: twelve is not a
    figure just because three and nine are.
    """
    values = figure_values(figures)
    allowed = set(values)
    allowed |= {a + b for a, b in combinations(values, 2) if a in stated and b in stated}
    return allowed


def numbers_in(text: str) -> list[int | float]:
    """Every number the text states, in digits or as a word ("twenty", "twenty-one")."""
    found: list[int | float] = []
    for token in _DIGITS.findall(text):
        clean = token.replace(",", "").rstrip(".")
        found.append(float(clean) if "." in clean else int(clean))
    for word in _WORDS.findall(text.lower()):
        parts = word.split("-")
        if all(p in NUMBER_WORDS for p in parts):
            found.append(sum(NUMBER_WORDS[p] for p in parts))
    return found


def gate(paragraph: str, figures: dict) -> str:
    """The paragraph if every number in it is a figure or a stated sum of two; else code's sentence."""
    text = (paragraph or "").strip()
    if not text:
        return code_sentence(figures)
    found = numbers_in(text)
    stated = {n for n in found if isinstance(n, int)}
    allowed = allowed_numbers(figures, stated)
    for number in found:
        if isinstance(number, float) or number not in allowed:
            return code_sentence(figures)
    return text


def code_sentence(figures: dict) -> str:
    """The same figures in a sentence code writes, used when the model's is refused."""
    screened = figures.get("screened", 0)
    if not screened:
        return "Nothing has been screened yet."
    clinics = figures.get("clinics", 0)
    countries = figures.get("countries", [])
    where = f" in {', '.join(countries)}" if countries else ""
    parts = [f"{screened} device screening{'s' if screened != 1 else ''} against "
             f"{clinics} clinic{'s' if clinics != 1 else ''}{where}"]
    parts.append(f"{figures.get('blocked', 0)} stopped before shipping")
    parts.append(f"{figures.get('add_ons', 0)} cleared only with add-ons sent alongside")
    parts.append(f"{figures.get('needs_info', 0)} waiting on answers")
    sentence = f"{parts[0]}: {', '.join(parts[1:])}."
    blockers = figures.get("blockers") or {}
    if blockers:
        label, count = next(iter(blockers.items()))
        sentence += f" The check that stopped the most was {label.lower()}, {count} times."
    return sentence[0].upper() + sentence[1:]


# ---- the cached paragraph ---------------------------------------------------

_CACHE: dict[str, tuple[Summary, float | None]] = {}


def figures_key(figures: dict) -> str:
    return hashlib.sha256(json.dumps(figures, sort_keys=True).encode()).hexdigest()


def summary(figures: dict, client: ChatClient, *, now: float | None = None) -> Summary:
    """The gated paragraph for these figures, from the cache when it holds one.

    A paragraph the gate kept or refused is cached for the life of the
    process. A model failure gives code's sentence and is retried after
    FAILURE_RETRY_SECONDS, so a down service is not called on every load.
    """
    now = time.time() if now is None else now
    key = figures_key(figures)
    held = _CACHE.get(key)
    if held is not None:
        cached, retry_at = held
        if retry_at is None or now < retry_at:
            return cached

    if not figures.get("screened"):
        result, retry_at = Summary(code_sentence(figures), by_model=False), None
    else:
        try:
            written = write_summary(figures, client)
        except GenAiError:
            result, retry_at = Summary(code_sentence(figures), by_model=False), \
                now + FAILURE_RETRY_SECONDS
        else:
            kept = gate(written, figures)
            result, retry_at = Summary(kept, by_model=kept == written.strip()), None
    _CACHE[key] = (result, retry_at)
    return result


def clear_cache() -> None:
    _CACHE.clear()
