from __future__ import annotations

import os
from typing import Any

from .registry import get_default_database, set_default_database


class Database:
    """Async connection-pool wrapper for PostgreSQL via asyncpg."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        if not dsn:
            raise ValueError("A PostgreSQL connection string is required")
        if min_size < 1:
            raise ValueError("min_size must be at least 1")
        if max_size < min_size:
            raise ValueError("max_size must be greater than or equal to min_size")
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Any | None = None

    @classmethod
    def from_env(
        cls,
        variable: str = "DATABASE_URL",
        *,
        dotenv_path: str | os.PathLike[str] | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> Database:
        if dotenv_path is not None:
            try:
                from dotenv import load_dotenv
            except ImportError as error:
                raise RuntimeError(
                    "Loading a .env file requires the optional dependency "
                    "'python-dotenv'; install pgmodel[dotenv]"
                ) from error
            load_dotenv(dotenv_path, override=False)

        dsn = os.getenv(variable)
        if not dsn:
            raise RuntimeError(f"Environment variable {variable!r} is not set")
        return cls(dsn, min_size=min_size, max_size=max_size)

    async def connect(self) -> Database:
        if self._pool is not None:
            return self
        try:
            import asyncpg
        except ImportError as error:
            raise RuntimeError(
                "asyncpg is required to connect to PostgreSQL; install the package dependencies"
            ) from error
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
        )
        return self

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def fetch(self, query: str, *parameters: Any) -> list[dict[str, Any]]:
        pool = self._require_pool()
        records = await pool.fetch(query, *parameters)
        return [dict(record) for record in records]

    async def fetchrow(self, query: str, *parameters: Any) -> dict[str, Any] | None:
        pool = self._require_pool()
        record = await pool.fetchrow(query, *parameters)
        return dict(record) if record is not None else None

    async def fetchval(self, query: str, *parameters: Any, column: int = 0) -> Any:
        pool = self._require_pool()
        return await pool.fetchval(query, *parameters, column=column)

    async def execute(self, query: str, *parameters: Any) -> str:
        pool = self._require_pool()
        return await pool.execute(query, *parameters)

    async def __aenter__(self) -> Database:
        return await self.connect()

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        await self.close()

    def _require_pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("Database is not connected; call 'await database.connect()' first")
        return self._pool


async def configure(
    dsn: str,
    *,
    min_size: int = 1,
    max_size: int = 10,
) -> Database:
    """Connect and register the process-wide default database."""
    database = await Database(dsn, min_size=min_size, max_size=max_size).connect()
    set_default_database(database)
    return database


async def configure_from_env(
    variable: str = "DATABASE_URL",
    *,
    dotenv_path: str | os.PathLike[str] | None = None,
    min_size: int = 1,
    max_size: int = 10,
) -> Database:
    """Connect using a .env variable and register it as the default database."""
    database = await Database.from_env(
        variable,
        dotenv_path=dotenv_path,
        min_size=min_size,
        max_size=max_size,
    ).connect()
    set_default_database(database)
    return database


async def shutdown() -> None:
    """Close the default database and unregister it."""
    database = get_default_database()
    if database is not None:
        await database.close()
        set_default_database(None)
