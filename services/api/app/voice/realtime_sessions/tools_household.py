"""The house's stock, by voice: "tuvalet kağıdı azaldı", "listeye süt ekle", "ne almam lazım".

Four tools, registered from ``tools.default_registry()`` by ONE added line
(:func:`register_household_tools`):

* ``household.level`` (action) - an item ran low, ran out, or was bought;
* ``household.list_add`` / ``household.list_remove`` (actions) - the shopping list;
* ``household.list_read`` (query) - the list read out, with the items the rhythm says run out
  soon.

The owner's own words win over the model's argument: the router's ``household_item`` /
``household_level`` / ``household_quantity`` on the turn record are used when present, and the
model's ``item`` / ``level`` / ``quantity`` only when the router named nothing. No item at all
is a question ("Hangi ürün efendim?"), never a guess. The argument keys avoid the relay's
filtered words (text/audio/token).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.household import service
from app.voice.errors import VoiceError, VoiceErrorClass

if TYPE_CHECKING:
    from app.voice.realtime_sessions.tools import ToolContext, ToolRegistry

TOOL_HOUSEHOLD_LEVEL: Final = "household.level"
TOOL_HOUSEHOLD_LIST_ADD: Final = "household.list_add"
TOOL_HOUSEHOLD_LIST_REMOVE: Final = "household.list_remove"
TOOL_HOUSEHOLD_LIST_READ: Final = "household.list_read"
HOUSEHOLD_TOOL_NAMES: Final[tuple[str, ...]] = (
    TOOL_HOUSEHOLD_LEVEL,
    TOOL_HOUSEHOLD_LIST_ADD,
    TOOL_HOUSEHOLD_LIST_REMOVE,
    TOOL_HOUSEHOLD_LIST_READ,
)

SPEECH_NOT_ON_LIST_TR: Final = "Listede {item} yok."


def _require_db(ctx: ToolContext, tool: str) -> Any:
    if ctx.db is None:
        raise VoiceError(
            VoiceErrorClass.DEPENDENCY_UNAVAILABLE,
            f"{tool} needs the durable state; no database on this session",
        )
    return ctx.db


def _turn(ctx: ToolContext) -> dict[str, Any]:
    return dict(ctx.context.get("last_utterance") or {})


def _said(ctx: ToolContext, arguments: dict[str, Any], field: str, argument: str) -> Any:
    """The router's word for ``field`` on this turn, else the model's ``argument``."""
    spoken = _turn(ctx).get(field)
    if isinstance(spoken, str) and spoken.strip():
        return spoken.strip()
    raw = arguments.get(argument)
    return raw.strip() if isinstance(raw, str) else raw


def _clarification() -> dict[str, Any]:
    return {"status": "needs_clarification", "speech": service.SPEECH_WHICH, "candidates": []}


def _refused(error: service.HouseholdRefused) -> dict[str, Any]:
    return {"status": "refused", "speech": error.message}


def _receipt(change: service.ItemChange) -> dict[str, Any]:
    row = change.item
    return {
        "status": "ok",
        "item_id": str(row.id),
        "item": row.name,
        "level": row.level,
        "on_list": row.on_list,
        "quantity": row.list_quantity,
        "already": change.already,
        "speech": change.speech,
    }


def household_level(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Tuvalet kağıdı azaldı." / "Deterjan bitti." / "Süt aldım." """
    db = _require_db(ctx, TOOL_HOUSEHOLD_LEVEL)
    item = _said(ctx, arguments, "household_item", "item")
    if not item:
        return _clarification()
    level = _said(ctx, arguments, "household_level", "level")
    try:
        return _receipt(service.set_level(db, item, level, now=ctx.now))
    except service.HouseholdRefused as error:
        db.rollback()
        return _refused(error)


def household_list_add(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Listeye süt ekle." / "Listeye iki paket makarna yaz." """
    db = _require_db(ctx, TOOL_HOUSEHOLD_LIST_ADD)
    item = _said(ctx, arguments, "household_item", "item")
    if not item:
        return _clarification()
    quantity = _said(ctx, arguments, "household_quantity", "quantity")
    try:
        return _receipt(service.add_to_list(db, item, quantity=quantity, now=ctx.now))
    except service.HouseholdRefused as error:
        db.rollback()
        return _refused(error)


def household_list_remove(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Listeden sütü çıkar." """
    db = _require_db(ctx, TOOL_HOUSEHOLD_LIST_REMOVE)
    item = _said(ctx, arguments, "household_item", "item")
    if not item or not isinstance(item, str):
        return _clarification()
    change = service.remove_from_list(db, item, now=ctx.now)
    if change is None:
        return {
            "status": "not_on_list",
            "item": item,
            "speech": SPEECH_NOT_ON_LIST_TR.format(item=item),
        }
    return _receipt(change)


def household_list_read(ctx: ToolContext, arguments: dict[str, Any]) -> dict[str, Any]:
    """ "Ne almam lazım?" / "Listeyi oku." / "Markete gidiyorum." """
    del arguments
    db = _require_db(ctx, TOOL_HOUSEHOLD_LIST_READ)
    listed = service.shopping_list(db, now=ctx.now)
    return {
        "status": "ok",
        "count": len(listed.items),
        "items": [
            {"item": r.name, "level": r.level, "quantity": r.list_quantity} for r in listed.items
        ],
        "soon": [r.name for r in listed.soon],
        "speech": service.list_speech(listed),
    }


def register_household_tools(reg: ToolRegistry) -> ToolRegistry:
    from app.voice.realtime_sessions.tools import ToolSpec

    item = {"type": "string", "maxLength": 60}
    reg.register(
        ToolSpec(
            name=TOOL_HOUSEHOLD_LEVEL,
            description=(
                "Evdeki bir ürünün durumunu kaydeder: 'tuvalet kağıdı azaldı', 'deterjan "
                "bitti', 'evde süt kalmadı', 'süt aldım'. 'item' ürünün adı, 'level' 'var' "
                "(alındı), 'azaldı' ya da 'bitti'. Azalan ve biten alışveriş listesine girer. "
                "Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": item,
                    "level": {"type": "string", "enum": ["var", "azaldı", "bitti"]},
                },
                "additionalProperties": False,
            },
            handler=household_level,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_HOUSEHOLD_LIST_ADD,
            description=(
                "Alışveriş listesine ekler: 'listeye süt ekle', 'listeye iki paket makarna "
                "yaz'. 'item' ürünün adı, 'quantity' söylendiyse miktar. Dönen 'speech' "
                "metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"item": item, "quantity": {"type": "string", "maxLength": 40}},
                "additionalProperties": False,
            },
            handler=household_list_add,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_HOUSEHOLD_LIST_REMOVE,
            description=(
                "Alışveriş listesinden çıkarır: 'listeden sütü çıkar', 'çayı listeden sil'. "
                "'item' ürünün adı. Dönen 'speech' metnini aynen oku."
            ),
            parameters={
                "type": "object",
                "properties": {"item": item},
                "additionalProperties": False,
            },
            handler=household_list_remove,
        )
    )
    reg.register(
        ToolSpec(
            name=TOOL_HOUSEHOLD_LIST_READ,
            description=(
                "Alışveriş listesini okur: 'ne almam lazım', 'listeyi oku', 'listede ne var', "
                "'markete gidiyorum', 'evde ne eksik'. Yakında bitecekleri de söyler. Dönen "
                "'speech' metnini aynen oku."
            ),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=household_list_read,
        )
    )
    return reg


__all__ = [
    "HOUSEHOLD_TOOL_NAMES",
    "TOOL_HOUSEHOLD_LEVEL",
    "TOOL_HOUSEHOLD_LIST_ADD",
    "TOOL_HOUSEHOLD_LIST_READ",
    "TOOL_HOUSEHOLD_LIST_REMOVE",
    "household_level",
    "household_list_add",
    "household_list_read",
    "household_list_remove",
    "register_household_tools",
]
