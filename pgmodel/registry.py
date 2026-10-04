"""Process-wide default database, so models need no per-call wiring."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database import Database

_default_database: Database | None = None


def set_default_database(database: Database | None) -> None:
    global _default_database
    _default_database = database


def get_default_database() -> Database | None:
    return _default_database
