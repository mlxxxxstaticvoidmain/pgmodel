from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .compiler import (
    CompiledQuery,
    compile_delete,
    compile_insert,
    compile_select,
    compile_update,
)
from .expressions import ColumnRef, Condition, Ordering

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

    Subclasses declare ``__tablename__`` and their columns. The database is
    passed to each operation explicitly so one model can be used with more
    than one connection or database.
    """

    __tablename__: str
    __schema__: str = "public"
    __columns__: dict[str, Column]

    @classmethod
    async def insert(
        cls,
        database: Database,
        values: Mapping[str, Any],
        *,
        returning: str | Sequence[str | ColumnRef] | None = "*",
    ) -> list[dict[str, Any]] | str:
        query = compile_insert(cls, values, returning=returning)
        return await cls._run(database, query)

    # In CRUD terminology, create means inserting a row, not CREATE TABLE.
    create = insert

    @classmethod
    async def select(
        cls,
        database: Database,
        *columns: str | ColumnRef,
        where: Condition | None = None,
        order_by: Sequence[Ordering | ColumnRef] | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        query = compile_select(
            cls,
            *columns,
            where=where,
            order_by=order_by,
            limit=limit,
            offset=offset,
        )
        result = await cls._run(database, query)
        assert isinstance(result, list)
        return result

    @classmethod
    async def update(
        cls,
        database: Database,
        values: Mapping[str, Any],
        where: Condition | None = None,
        *,
        all_rows: bool = False,
        returning: str | Sequence[str | ColumnRef] | None = "*",
    ) -> list[dict[str, Any]] | str:
        query = compile_update(
            cls, values, where=where, all_rows=all_rows, returning=returning
        )
        return await cls._run(database, query)

    @classmethod
    async def delete(
        cls,
        database: Database,
        where: Condition | None = None,
        *,
        all_rows: bool = False,
        returning: str | Sequence[str | ColumnRef] | None = "*",
    ) -> list[dict[str, Any]] | str:
        query = compile_delete(
            cls, where=where, all_rows=all_rows, returning=returning
        )
        return await cls._run(database, query)

    @staticmethod
    async def _run(database: Database, query: CompiledQuery) -> list[dict[str, Any]] | str:
        if query.returns_rows:
            return await database.fetch(query.sql, *query.parameters)
        return await database.execute(query.sql, *query.parameters)
