"""``/v1/money``: the owner reads his ledger, answers a spend question, takes a row back.

GET reads everything (the last balance per bank with its time, this month's entries, the open
questions, the sentences the voice would say); POST ``/entries/{id}/cancel`` takes a row back
(kept, never counted); POST ``/questions/{id}/answer`` answers "X liralık bir harcama yaptınız
mı?" (``answer`` = yes | no, optional ``amount``); POST ``/cash`` books a cash spend (``amount``
as Turkish digits, optional ``category``). Under the owner session. None of these moves money
or reaches a bank. A refusal is ``{detail: {code, message}}`` with a Turkish message.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.identity.dependencies import require_owner_session
from app.money import categories, service
from app.money.amounts import parse_amount
from app.money.models import MoneyEntry, MoneyQuestion

router = APIRouter(dependencies=[Depends(require_owner_session)])


def _stamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC).isoformat()


def _entry(row: MoneyEntry) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "direction": row.direction,
        "amount_kurus": row.amount_kurus,
        "status": row.status,
        "source": row.source,
        "method": row.method,
        "category": row.category,
        "description": row.description,
        "bank": service.bank_name(row.bank) if row.bank else None,
        "occurred_at": _stamp(row.occurred_at),
        "confirmed_at": _stamp(row.confirmed_at),
    }


def _question(row: MoneyQuestion) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "amount_kurus": row.amount_kurus,
        "reason": row.reason,
        "asked_at": _stamp(row.asked_at),
        "answer": row.answer,
        "speech": service.question_line(row),
    }


def _not_found(what: str) -> HTTPException:
    return HTTPException(404, {"code": "not_found", "message": f"Bu {what} yok."})


def _refused(message: str) -> HTTPException:
    return HTTPException(422, {"code": "money_refused", "message": message})


async def _payload(request: Request) -> dict[str, Any]:
    raw = await request.body()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError as error:
        raise _refused("İstek okunamadı; JSON olarak gönder.") from error
    if not isinstance(payload, dict):
        raise _refused("İstek bir JSON nesnesi olmalı.")
    return payload


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        raise _not_found(what) from error


def _kurus(raw: object) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        value = parse_amount(raw)
        if value is None:
            raise _refused("Tutarı anlayamadım; '750' ya da '1.234,56' gibi yaz.")
        return value
    raise _refused("Tutar yazı olarak gelmeli: '750' ya da '1.234,56'.")


@router.get("/v1/money")
async def read_money(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts

    def run() -> dict[str, Any]:
        now = datetime.now(UTC)
        with artifacts.session() as db:
            summary = service.spent(db, category=None, now=now)
            return {
                "balances": [
                    {
                        "bank": service.bank_name(r.bank),
                        "balance_kurus": r.balance_kurus,
                        "as_of": _stamp(r.as_of),
                    }
                    for r in service.balances(db)
                ],
                "balance_speech": service.balance_speech(db, now=now),
                "month": {
                    "total_kurus": summary.total_kurus,
                    "count": summary.count,
                    "tentative": summary.tentative,
                    "cash_kurus": summary.cash_kurus,
                    "speech": service.spent_speech(summary),
                },
                "entries": [_entry(r) for r in service.entries(db)],
                "questions": [_question(q) for q in service.questions(db)],
                "categories": list(categories.CATEGORIES),
            }

    return await asyncio.to_thread(run)


@router.post("/v1/money/entries/{entry_id}/cancel")
async def cancel_entry(entry_id: str, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    target = _uuid(entry_id, "kayıt")

    def run() -> dict[str, Any] | None:
        with artifacts.session() as db:
            row = service.cancel(db, target, now=datetime.now(UTC))
            return None if row is None else {"entry": _entry(row)}

    result = await asyncio.to_thread(run)
    if result is None:
        raise _not_found("kayıt")
    return result


@router.post("/v1/money/questions/{question_id}/answer")
async def answer_question(question_id: str, request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    target = _uuid(question_id, "soru")
    payload = await _payload(request)
    answer = payload.get("answer")
    if answer not in ("yes", "no"):
        raise _refused("Yanıt 'yes' ya da 'no' olmalı.")
    kurus = _kurus(payload.get("amount"))

    def run() -> dict[str, Any] | None:
        now = datetime.now(UTC)
        with artifacts.session() as db:
            question = db.get(MoneyQuestion, target)
            if question is None:
                return None
            if question.answered_at is not None:
                return {"question": _question(question), "entry": None, "already": True}
            if answer == "no":
                service.answer_no(db, question, now=now)
                return {"question": _question(question), "entry": None}
            try:
                row = service.answer_yes(db, question, kurus=kurus, now=now)
            except service.MoneyRefused as error:
                db.rollback()
                raise _refused(error.message) from error
            return {"question": _question(question), "entry": _entry(row)}

    result = await asyncio.to_thread(run)
    if result is None:
        raise _not_found("soru")
    return result


@router.post("/v1/money/cash")
async def book_cash(request: Request) -> dict[str, Any]:
    artifacts = request.app.state.artifacts
    payload = await _payload(request)
    kurus = _kurus(payload.get("amount"))
    if kurus is None:
        raise _refused("Tutar gerekli.")
    category = categories.normalize(payload.get("category"))

    def run() -> dict[str, Any]:
        now = datetime.now(UTC)
        with artifacts.session() as db:
            try:
                row = service.book_spend(
                    db,
                    kurus,
                    status=service.STATUS_CONFIRMED,
                    source=service.SOURCE_WEB,
                    method=service.METHOD_CASH,
                    occurred_at=now,
                    now=now,
                    description="Nakit",
                    category=category,
                )
            except service.MoneyRefused as error:
                db.rollback()
                raise _refused(error.message) from error
            return {"entry": _entry(row)}

    return await asyncio.to_thread(run)


__all__ = ["router"]
