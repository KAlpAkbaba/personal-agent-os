"""B22 req 704/705: one Turkish error dictionary, and one door between an exception and a
person.

`catalog` holds the Turkish for every error class the eight subsystem taxonomies declare;
`owner` turns a failure into the response body the owner's surfaces render, and refuses to
pass developer text through. Nothing here knows about HTTP status codes: a route decides
what a failure MEANS to a caller, and this package decides what it SAYS to the owner.
"""

from app.errors.catalog import TR, OwnerMessage, describe, sentence
from app.errors.owner import (
    log_and_detail,
    looks_like_developer_text,
    owner_detail,
    owner_sentence,
)

__all__ = [
    "TR",
    "OwnerMessage",
    "describe",
    "log_and_detail",
    "looks_like_developer_text",
    "owner_detail",
    "owner_sentence",
    "sentence",
]
