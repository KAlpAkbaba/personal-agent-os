"""Ayarlar > Hesaplar's REST surface (card mail-accounts-connect).

- GET    /v1/accounts                    the accounts (never a token) + the setup steps
- POST   /v1/accounts/connect            {provider, name} -> the provider's authorization URL
- PATCH  /v1/accounts/{id}               {name} -> renamed
- DELETE /v1/accounts/{id}               revoke where possible, delete the row and tokens
- GET    /v1/accounts/oauth/callback     where the provider sends the owner's browser back

Everything but the callback is owner-gated at the router level. The callback cannot be: it
is a top-level navigation from Google/Microsoft carrying no bearer token. Its authority is
the single-use, hashed, 15-minute ``state`` an owner-gated ``connect`` issued
(``app.accounts.service``); it answers with a short Turkish page and redirects nowhere.
"""

from __future__ import annotations

from html import escape
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

from app.accounts.service import AccountError, AccountsService
from app.identity.dependencies import require_owner_session

router = APIRouter(
    prefix="/v1/accounts", tags=["accounts"], dependencies=[Depends(require_owner_session)]
)
callback_router = APIRouter(prefix="/v1/accounts", tags=["accounts"])


class ConnectBody(BaseModel):
    provider: str = Field(max_length=16)
    name: str = Field(max_length=200)


class RenameBody(BaseModel):
    name: str = Field(max_length=200)


def _service(request: Request) -> AccountsService:
    return request.app.state.accounts_service


def _factory(request: Request) -> Any:
    return request.app.state.accounts_session_factory


def _refused(exc: AccountError) -> JSONResponse:
    return JSONResponse({"error": exc.code, "speech": exc.speech}, status_code=exc.status)


@router.get("")
def list_accounts(request: Request) -> dict[str, Any]:
    service = _service(request)
    with _factory(request)() as db:
        accounts = service.list(db)
    return {"accounts": accounts, "setup": service.setup_info()}


@router.post("/connect", response_model=None)
def connect(request: Request, body: ConnectBody) -> Any:
    try:
        with _factory(request)() as db:
            return _service(request).start(db, provider=body.provider, name=body.name)
    except AccountError as exc:
        return _refused(exc)


@router.patch("/{account_id}", response_model=None)
def rename(request: Request, account_id: str, body: RenameBody) -> Any:
    try:
        with _factory(request)() as db:
            return _service(request).rename(db, account_id, body.name)
    except AccountError as exc:
        return _refused(exc)


@router.delete("/{account_id}", response_model=None)
def disconnect(request: Request, account_id: str) -> Any:
    try:
        with _factory(request)() as db:
            return _service(request).disconnect(db, account_id)
    except AccountError as exc:
        return _refused(exc)


def _page(title: str, sentence: str, status: int) -> HTMLResponse:
    html = (
        '<!doctype html><html lang="tr"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head><body>"
        f"<h1>{escape(title)}</h1><p>{escape(sentence)}</p>"
        "<p>Bu sekmeyi kapatıp Ayarlar &gt; Hesaplar sayfasına dönebilirsiniz.</p>"
        "</body></html>"
    )
    return HTMLResponse(html, status_code=status, headers={"Cache-Control": "no-store"})


@callback_router.get("/oauth/callback")
def oauth_callback(
    request: Request, state: str = "", code: str | None = None, error: str | None = None
) -> HTMLResponse:
    try:
        with _factory(request)() as db:
            account = _service(request).complete(db, state=state, code=code, error=error)
    except AccountError as exc:
        return _page("Bağlantı kurulamadı", exc.speech, 400)
    address = f" ({account['address']})" if account["address"] else ""
    return _page("Hesap bağlandı", f"'{account['name']}' hesabı bağlandı{address} efendim.", 200)


__all__ = ["callback_router", "router"]
