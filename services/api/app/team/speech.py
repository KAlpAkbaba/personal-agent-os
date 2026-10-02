"""The Ofis page's data, said aloud: ONE Turkish paragraph for "ekip ne yapıyor?".

:func:`office_paragraph` is a pure function over :func:`app.team.office.office_view`'s answer, so
the voice and the page can never disagree. No task id is read out (an id means nothing to the
ear); a title is cut at its first clause and at :data:`TITLE_WORDS` words; the whole paragraph
stays within about sixty words. A dollar figure is always preceded by "tahmini" (it is an
estimate, never a bill).
"""

from __future__ import annotations

import re
from typing import Any

IDLE = "Ekip şu an çalışmıyor efendim."
TITLE_WORDS = 6
MAX_TITLES = 3
_CLAUSE_END = re.compile(r"\s+[-–—]\s+|[:;,.()\[\]]")
_NUMBERS = ("sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz", "on")


def _number(n: int) -> str:
    return _NUMBERS[n] if 0 <= n < len(_NUMBERS) else str(n)


def _first_clause(title: Any) -> str:
    text = _CLAUSE_END.split(str(title or "").strip(), maxsplit=1)[0].strip()
    return " ".join(text.split()[:TITLE_WORDS])


def _runs(agent: dict[str, Any]) -> list[dict[str, Any]]:
    """The runs of a working seat: its ``runs`` (a seat may hold several - three inspections),
    or the seat itself for a view that does not list them."""
    listed = [r for r in agent.get("runs") or [] if isinstance(r, dict)]
    return listed or [agent]


def _titles(agents: list[dict[str, Any]]) -> list[str]:
    seen: list[str] = []
    for agent in agents:
        for run in _runs(agent):
            title = _first_clause(run.get("task_title")) or str(agent.get("role") or "")
            if title and title not in seen:
                seen.append(title)
    return seen


def _list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " ve " + items[-1]


def _dollars(amount: float) -> str:
    return f"{amount:.2f}".replace(".", ",")


def office_paragraph(view: dict[str, Any]) -> str:
    cycle = view.get("cycle") or {}
    agents = [a for a in view.get("agents") or [] if isinstance(a, dict)]
    working = [a for a in agents if a.get("state") == "working"]
    returned = {
        a.get("task_id") for a in agents if a.get("state") == "returned" and a.get("task_id")
    }
    approvals = len(view.get("approvals") or [])
    sentences: list[str] = []

    if working:
        titles = _titles(working)
        shown, more = titles[:MAX_TITLES], len(titles) > MAX_TITLES
        capacity = int(cycle.get("capacity") or 6)
        # What the page's top bar counts: RUNS (three inspections on one seat are three at
        # work). The seats' own runs are the count for a view that does not carry the number.
        counted = cycle.get("running_agents")
        at_work = (
            counted
            if isinstance(counted, int) and counted > 0
            else sum(len(_runs(a)) for a in working)
        )
        sentences.append(
            f"{_number(capacity).capitalize()} kişiden {_number(at_work)} çalışan "
            f"çalışıyor: {_list(shown)}{' ve diğerleri' if more else ''}."
        )
    else:
        sentences.append(IDLE)
    if returned:
        sentences.append(f"{_number(len(returned)).capitalize()} görev geri döndü.")
    if approvals:
        sentences.append(f"{_number(approvals).capitalize()} onayınız bekliyor.")
    state = (cycle.get("usage_limit") or {}).get("state")
    if state == "waiting":
        sentences.append("Kullanım sınırı nedeniyle ekip bekliyor.")
    elif state == "stopped":
        sentences.append("Kullanım sınırına takıldı, ekip durdu.")
    usd = cycle.get("estimated_usd") or 0
    if working and usd:
        sentences.append(f"Tahmini maliyet {_dollars(float(usd))} dolar.")
    return " ".join(sentences)
