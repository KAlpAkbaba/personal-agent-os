"""The money ledger, by voice: "hesabımda ne kadar var", "bu ay markete ne harcadım", "evet ama
750", "harcamayı geri al", "markete iki yüz lira nakit verdim" (money-ledger).

Six tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_money_tools`):

* ``money.balance`` / ``money.spent`` (queries) - the last balance a bank mail stated, with
  its time; this month's spends, by category when one was named;
* ``money.spend_yes`` / ``money.spend_no`` (actions) - his answer to "X liralık bir harcama
  yaptınız mı?": the asked amount (or the one he said) booked, or nothing;
* ``money.undo`` (action) - the spend just booked taken back (cancelled, kept, not counted);
* ``money.cash`` (action) - a cash spend he states, booked apart.

None of them moves money or reaches a bank: they read and write JARVIS's own rows. The owner's
own words win over the model's argument (``money_amount_kurus`` / ``money_category`` on the
turn record, else ``amount`` / ``category``). The argument keys avoid the relay's filtered
words (text/audio/token).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.money import categories, pending, service
from app.money.amounts import format_tl, money_in, parse_amount
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_MONEY_BALANCE: Final = "money.balance"
TOOL_MONEY_SPENT: Final = "money.spent"
TOOL_MONEY_SPEND_YES: Final = "money.spend_yes"
TOOL_MONEY_SPEND_NO: Final = "money.spend_no"
TOOL_MONEY_UNDO: Final = "money.undo"
TOOL_MONEY_CASH: Final = "money.cash"
MONEY_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_MONEY_BALANCE,
    TOOL_MONEY_SPENT,
    TOOL_MONEY_SPEND_YES,
    TOOL_MONEY_SPEND_NO,
    TOOL_MONEY_UNDO,
    TOOL_MONEY_CASH,
)

SPEECH_NO_QUESTION: Final = "Bekleyen bir harcama sorum yok efendim."
SPEECH_NOTHING_TO_UNDO: Final = "Geri alınacak yeni bir harcama kaydı yok efendim."


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _amount(ctx: ToolContext, arguments: dict[str, Any]) -> int | None:
    """The owner's words' amount (kuruş), else the model's ``amount`` (TL: 750, "750,50")."""
    spoken = _turn(ctx).get("money_amount_kurus")
    if isinstance(spoken, int) and not isinstance(spoken, bool) and spoken > 0:
        return spoken
    raw = arguments.get("amount")
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float) and raw > 0:
        return round(float(raw) * 100)
    if isinstance(raw, str) and raw.strip():
        return parse_amount(raw) or next(iter(money_in(raw, bare=True)), None)
    return None


def _category(ctx: ToolContext, arguments: dict[str, Any]) -> str | None:
    spoken = _turn(ctx).get("money_category")
    if isinstance(spoken, str) and spoken.strip():
        return categories.normalize(spoken)
    return categories.normalize(arguments.get("category"))


def _entry(row: Any) -> dict[str, Any]:
    return {
        "entry_id": str(row.id),
        "amount_kurus": row.amount_kurus,
        "entry_status": row.status,
        "method": row.method,
        "category": row.category,
    }


def money_balance(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hesabımda ne kadar var?" - with the time the bank's mail said it."""
    del arguments
    db = _require_db(ctx, TOOL_MONEY_BALANCE)
    rows = service.balances(db)
    return {
        "status": "ok",
        "balances": [
            {
                "bank": service.bank_name(r.bank),
                "balance_kurus": r.balance_kurus,
                "as_of": r.as_of.isoformat(),
            }
            for r in rows
        ],
        "speech": service.balance_speech(db, now=ctx.now),
    }


def money_spent(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Bu ay markete ne harcadım?" / "Bu ay ne kadar harcadım?" """
    db = _require_db(ctx, TOOL_MONEY_SPENT)
    summary = service.spent(db, category=_category(ctx, arguments), now=ctx.now)
    return {
        "status": "ok",
        "category": summary.category,
        "total_kurus": summary.total_kurus,
        "count": summary.count,
        "tentative": summary.tentative,
        "cash_kurus": summary.cash_kurus,
        "speech": service.spent_speech(summary),
    }


def money_spend_yes(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Evet." / "Evet ama 750." - to "X liralık bir harcama yaptınız mı?" """
    db = _require_db(ctx, TOOL_MONEY_SPEND_YES)
    question = service.open_question(db, now=ctx.now)
    if question is None:
        return {"status": "no_question", "speech": SPEECH_NO_QUESTION}
    kurus = _amount(ctx, arguments)
    if kurus is None and not question.amount_kurus:
        return {"status": "needs_clarification", "speech": service.SPEECH_HOW_MUCH}
    try:
        row = service.answer_yes(db, question, kurus=kurus, now=ctx.now)
    except service.MoneyRefused as error:
        db.rollback()
        return {"status": "refused", "speech": error.message}
    pending.clear_question()
    return {
        "status": "ok",
        **_entry(row),
        "speech": f"{format_tl(row.amount_kurus)} harcamayı deftere yazdım efendim.",
    }


def money_spend_no(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Hayır." / "Hayır harcamadım." """
    del arguments
    db = _require_db(ctx, TOOL_MONEY_SPEND_NO)
    question = service.open_question(db, now=ctx.now)
    if question is None:
        return {"status": "no_question", "speech": SPEECH_NO_QUESTION}
    service.answer_no(db, question, now=ctx.now)
    pending.clear_question()
    return {"status": "ok", "speech": "Peki efendim, deftere bir şey yazmadım."}


def money_undo(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Harcamayı geri al." / "Geri al." right after a booking was said."""
    del arguments
    db = _require_db(ctx, TOOL_MONEY_UNDO)
    row = service.last_undoable(db, now=ctx.now)
    if row is None:
        return {"status": "nothing_to_undo", "speech": SPEECH_NOTHING_TO_UNDO}
    service.cancel(db, row.id, now=ctx.now)
    pending.clear_booking()
    return {
        "status": "ok",
        **_entry(row),
        "speech": f"{format_tl(row.amount_kurus)} harcamayı defterden çıkardım efendim.",
    }


def money_cash(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Markete iki yüz lira nakit verdim." """
    db = _require_db(ctx, TOOL_MONEY_CASH)
    kurus = _amount(ctx, arguments)
    if kurus is None:
        return {"status": "needs_clarification", "speech": service.SPEECH_HOW_MUCH}
    category = _category(ctx, arguments)
    try:
        row = service.book_spend(
            db,
            kurus,
            status=service.STATUS_CONFIRMED,
            source=service.SOURCE_VOICE,
            method=service.METHOD_CASH,
            occurred_at=ctx.now,
            now=ctx.now,
            description="Nakit (sesle)",
            category=category,
        )
    except service.MoneyRefused as error:
        db.rollback()
        return {"status": "refused", "speech": error.message}
    where = f"{categories.SPOKEN[category]} " if category in categories.SPOKEN else ""
    return {
        "status": "ok",
        **_entry(row),
        "speech": f"{where}{format_tl(row.amount_kurus)} nakit harcamayı deftere yazdım efendim.",
    }


def register_money_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    amount = {"type": "string", "maxLength": 40}
    category = {"type": "string", "enum": list(categories.CATEGORIES)}
    empty = {"type": "object", "properties": {}, "additionalProperties": False}
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_BALANCE,
            description=(
                "Bankanın bildirim postalarından bilinen son bakiyeyi, postanın saatiyle söyler: "
                "'hesabımda ne kadar var', 'bakiyem ne kadar'. Bankaya bağlanmaz, para hareketi "
                "yapmaz. Dönen 'speech' metnini aynen oku."
            ),
            parameters=empty,
            handler=money_balance,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_SPENT,
            description=(
                "Bu ayki harcamaları söyler: 'bu ay ne kadar harcadım', 'bu ay markete ne "
                "harcadım'. 'category' söylendiyse kategori. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"category": category},
                "additionalProperties": False,
            },
            handler=money_spent,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_SPEND_YES,
            description=(
                "Sahibin 'X liralık bir harcama yaptınız mı?' sorusuna 'evet' yanıtı: sorulan "
                "tutarı, ya da söylediği tutarı ('evet ama 750' -> amount '750') deftere yazar. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"amount": amount},
                "additionalProperties": False,
            },
            handler=money_spend_yes,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_SPEND_NO,
            description=(
                "Sahibin harcama sorusuna 'hayır' yanıtı: deftere bir şey yazılmaz. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters=empty,
            handler=money_spend_no,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_UNDO,
            description=(
                "Az önce deftere yazılan harcamayı geri alır: 'harcamayı geri al'. Para hareketi "
                "değildir, yalnız defter kaydıdır. Dönen 'speech' metnini aynen oku."
            ),
            parameters=empty,
            handler=money_undo,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_MONEY_CASH,
            description=(
                "Nakit bir harcamayı deftere yazar: 'markete iki yüz lira nakit verdim'. 'amount' "
                "TL tutarı, 'category' söylendiyse yeri. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"amount": amount, "category": category},
                "additionalProperties": False,
            },
            handler=money_cash,
        )
    )
    return reg


__all__ = [
    "MONEY_TOOL_NAMES",
    "money_balance",
    "money_cash",
    "money_spend_no",
    "money_spend_yes",
    "money_spent",
    "money_undo",
    "register_money_tools",
]
