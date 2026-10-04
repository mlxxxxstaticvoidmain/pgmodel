from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .compiler import (
    compile_delete,
    compile_insert,
    compile_select,
    compile_update,
)
from .expressions import ColumnRef, Condition, Ordering
from .registry import get_default_database

if TYPE_CHECKING:
    from .database import Database


class Column:
    """Declares a model column and exposes it as a query expression."""

    def __init__(self, name: str | None = None, *, primary_key: bool = False) -> None:
        self.name = name
        self.primary_key = primary_key
        self.attribute_name: str | None = None

    def __set_name__(self, owner: type, name: str) -> None:
        self.attribute_name = name
        if self.name is None:
            self.name = name

    def __get__(self, instance: object | None, owner: type) -> Any:
        if instance is None:
            return ColumnRef(owner, self)
        return instance.__dict__.get(self.attribute_name)

    def __set__(self, instance: object, value: Any) -> None:
        if self.attribute_name is None:
            raise AttributeError("Column must be declared on a Table subclass")
        instance.__dict__[self.attribute_name] = value


class TableMeta(type):
    def __new__(
        metaclass: type[TableMeta],
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> TableMeta:
        cls = super().__new__(metaclass, name, bases, namespace)

        columns: dict[str, Column] = {}
        for base in reversed(cls.__mro__[1:]):
            columns.update(getattr(base, "__columns__", {}))
        columns.update(
            {
                attribute_name: value
                for attribute_name, value in namespace.items()
                if isinstance(value, Column)
            }
        )
        cls.__columns__ = columns

        if name != "Table":
            table_name = namespace.get("__tablename__", getattr(cls, "__tablename__", None))
            schema_name = namespace.get("__schema__", getattr(cls, "__schema__", "public"))
            if not isinstance(table_name, str) or not table_name:
                raise TypeError(f"{name} must define a non-empty __tablename__")
            if not isinstance(schema_name, str) or not schema_name:
                raise TypeError(f"{name} must define a non-empty __schema__")
            cls.__tablename__ = table_name
            cls.__schema__ = schema_name

        return cls


class Table(metaclass=TableMeta):
    """Base class for a PostgreSQL table model.

    The database is resolved in this order: an explicit ``database=`` argument,
    then a database bound to this class with ``bind()``, then the process-wide
    default registered by ``configure()`` / ``configure_from_env()``.
    """

    __tablename__: str
    __schema__: str = "public"
    __columns__: dict[str, Column]
    __database__: Database | None = None

    @classmethod
    def bind(cls, database: Database | None) -> None:
        """Bind this model to a database; pass None to unbind."""
        cls.__database__ = database

    @classmethod
    def bind_all(cls, database: Database | None) -> None:
        """Bind every declared Table subclass, so each model needs no wiring."""
        for model in cls._subclasses():
            model.bind(database)

    @classmethod
    def _subclasses(cls) -> Iterator[type[Table]]:
        for subclass in cls.__subclasses__():
            yield subclass
            yield from subclass._subclasses()

    @classmethod
    def _resolve_database(cls, database: Database | None) -> Database:
        if database is not None:
            return database
        if cls.__database__ is not None:
            return cls.__database__
        default = get_default_database()
        if default is not None:
            return default
        raise RuntimeError(
            f"{cls.__name__} has no database. Call 'await configure_from_env()' once at "
            f"startup, or '{cls.__name__}.bind(db)', or pass database=db explicitly."
        )

    @staticmethod
    def _split(args: tuple[Any, ...]) -> tuple[list[str | ColumnRef], list[Condition]]:
        columns: list[str | ColumnRef] = []
        conditions: list[Condition] = []
        for item in args:
            if isinstance(item, Condition):
                conditions.append(item)
            else:
                columns.append(item)
        return columns, conditions

    @staticmethod
    def _merge(where: Condition | None, conditions: Sequence[Condition]) -> Condition | None:
        merged = where
        for condition in conditions:
            merged = condition if merged is None else merged & condition
        return merged

    @classmethod
    async def insert(
        cls,
        values: Mapping[str, Any],
        *,
        returning: str | Sequence[str | ColumnRef] | None = "*",
        database: Database | None = None,
    ) -> list[dict[str, Any]] | str:
        query = compile_insert(cls, values, returning=returning)
        return await cls._run(cls._resolve_database(database), query)

    # In CRUD terminology, create means inserting a row, not CREATE TABLE.
    create = insert

    @classmethod
    async def select(
        cls,
        *args: str | ColumnRef | Condition,
        where: Condition | None = None,
        order_by: Sequence[Ordering | ColumnRef] | None = None,
        limit: int | None = None,
        offset: int | None = None,
        database: Database | None = None,
    ) -> list[dict[str, Any]]:
        columns, conditions = cls._split(args)
        query = compile_select(
            cls,
            *columns,
            where=cls._merge(where, conditions),
            order_by=order_by,
            limit=limit,
            offset=offset,
        )
        database = cls._resolve_database(database)
        return await database.fetch(query.sql, *query.parameters)

    @classmethod
    async def fetchrow(
        cls,
        *args: str | ColumnRef | Condition,
        where: Condition | None = None,
        order_by: Sequence[Ordering | ColumnRef] | None = None,
        offset: int | None = None,
        database: Database | None = None,
    ) -> dict[str, Any] | None:
        """Return the first matching row as a dict, or None. Mirrors asyncpg."""
        columns, conditions = cls._split(args)
        query = compile_select(
            cls,
            *columns,
            where=cls._merge(where, conditions),
            order_by=order_by,
            limit=1,
            offset=offset,
        )
        database = cls._resolve_database(database)
        return await database.fetchrow(query.sql, *query.parameters)

    @classmethod
    async def fetchval(
        cls,
        column: str | ColumnRef,
        *args: Condition,
        where: Condition | None = None,
        order_by: Sequence[Ordering | ColumnRef] | None = None,
        offset: int | None = None,
        database: Database | None = None,
    ) -> Any:
        """Return a single column value from the first matching row. Mirrors asyncpg."""
        _, conditions = cls._split(args)
        query = compile_select(
            cls,
            column,
            where=cls._merge(where, conditions),
            order_by=order_by,
            limit=1,
            offset=offset,
        )
        database = cls._resolve_database(database)
        return await database.fetchval(query.sql, *query.parameters)

    @classmethod
    async def update(
        cls,
        values: Mapping[str, Any],
        where: Condition | None = None,
        *,
        all_rows: bool = False,
        returning: str | Sequence[str | ColumnRef] | None = "*",
        database: Database | None = None,
    ) -> list[dict[str, Any]] | str:
        query = compile_update(
            cls, values, where=where, all_rows=all_rows, returning=returning
        )
        return await cls._run(cls._resolve_database(database), query)

    @classmethod
    async def delete(
        cls,
        where: Condition | None = None,
        *,
        all_rows: bool = False,
        returning: str | Sequence[str | ColumnRef] | None = "*",
        database: Database | None = None,
    ) -> list[dict[str, Any]] | str:
        query = compile_delete(cls, where=where, all_rows=all_rows, returning=returning)
        return await cls._run(cls._resolve_database(database), query)

    @staticmethod
    async def _run(database: Database, query: Any) -> list[dict[str, Any]] | str:
        if query.returns_rows:
            return await database.fetch(query.sql, *query.parameters)
        return await database.execute(query.sql, *query.parameters)
