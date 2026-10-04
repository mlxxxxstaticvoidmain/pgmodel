from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .expressions import (
    ColumnRef,
    Comparison,
    Condition,
    InCondition,
    Junction,
    Negation,
    Ordering,
)
if TYPE_CHECKING:
    from .model import Column


@dataclass(frozen=True)
class CompiledQuery:
    sql: str
    parameters: tuple[Any, ...]
    returns_rows: bool


def quote_identifier(identifier: str) -> str:
    """Quote a PostgreSQL identifier; values are parameterized separately."""
    if not isinstance(identifier, str) or not identifier:
        raise ValueError("SQL identifiers must be non-empty strings")
    if "\x00" in identifier:
        raise ValueError("SQL identifiers cannot contain a null character")
    return '"' + identifier.replace('"', '""') + '"'


def _table_sql(model: type) -> str:
    try:
        schema = model.__schema__
        table = model.__tablename__
    except AttributeError as error:
        raise TypeError("CRUD operations must use a Table subclass") from error
    return f"{quote_identifier(schema)}.{quote_identifier(table)}"


def _column(model: type, value: str | ColumnRef) -> Column:
    if isinstance(value, ColumnRef):
        if value.model is not model:
            raise ValueError("This query currently supports columns from one table only")
        column = value.column
        if column.attribute_name not in model.__columns__:
            raise ValueError(f"Column {column.attribute_name!r} is not declared on {model.__name__}")
        return column

    if not isinstance(value, str):
        raise TypeError("Column names must be strings or model column references")
    if value == "*":
        raise ValueError("'*' is only valid as a complete select/returning projection")

    column = model.__columns__.get(value)
    if column is not None:
        return column

    # Also accept the physical database name when a Column maps to a different name.
    matching = [item for item in model.__columns__.values() if item.name == value]
    if len(matching) == 1:
        return matching[0]
    raise ValueError(f"Unknown column {value!r} for model {model.__name__}")


def _column_sql(model: type, value: str | ColumnRef) -> str:
    return quote_identifier(_column(model, value).name or "")


def _projection_sql(
    model: type, columns: Sequence[str | ColumnRef] | None
) -> str:
    if not columns:
        return "*"
    if len(columns) == 1 and isinstance(columns[0], str) and columns[0] == "*":
        return "*"
    if any(isinstance(column, str) and column == "*" for column in columns):
        raise ValueError("'*' cannot be combined with named columns")
    return ", ".join(_column_sql(model, column) for column in columns)


def _returning_sql(
    model: type, returning: str | Sequence[str | ColumnRef] | None
) -> str | None:
    if returning is None:
        return None
    if isinstance(returning, str) and returning == "*":
        return "*"
    if isinstance(returning, str):
        columns: Sequence[str | ColumnRef] = (returning,)
    else:
        columns = returning
    if not columns:
        raise ValueError("returning must contain at least one column, or be None")
    return _projection_sql(model, columns)


def _compile_condition(
    model: type, condition: Condition, parameters: list[Any]
) -> str:
    if isinstance(condition, Comparison):
        allowed_operators = {"=", "<>", "<", "<=", ">", ">=", "LIKE", "ILIKE", "IS", "IS NOT"}
        if condition.operator not in allowed_operators:
            raise ValueError(f"Unsupported comparison operator: {condition.operator!r}")
        left = _column_sql(model, condition.left)
        if condition.operator in {"IS", "IS NOT"}:
            if condition.right is not None:
                raise ValueError("IS and IS NOT comparisons only support None")
            return f"{left} {condition.operator} NULL"
        if isinstance(condition.right, ColumnRef):
            right = _column_sql(model, condition.right)
        else:
            parameters.append(condition.right)
            right = f"${len(parameters)}"
        return f"{left} {condition.operator} {right}"

    if isinstance(condition, InCondition):
        left = _column_sql(model, condition.column)
        if not condition.values:
            return "TRUE" if condition.negated else "FALSE"
        placeholders: list[str] = []
        for value in condition.values:
            parameters.append(value)
            placeholders.append(f"${len(parameters)}")
        operator = "NOT IN" if condition.negated else "IN"
        return f"{left} {operator} ({', '.join(placeholders)})"

    if isinstance(condition, Junction):
        left = _compile_condition(model, condition.left, parameters)
        right = _compile_condition(model, condition.right, parameters)
        return f"({left} {condition.operator} {right})"

    if isinstance(condition, Negation):
        inner = _compile_condition(model, condition.condition, parameters)
        return f"NOT ({inner})"

    raise TypeError("where must be built from model columns and supported operators")


def _where_sql(
    model: type, where: Condition | None, parameters: list[Any]
) -> str:
    if where is None:
        return ""
    if not isinstance(where, Condition):
        raise TypeError("where must be a condition, e.g. User.id == 4")
    return " WHERE " + _compile_condition(model, where, parameters)


def _normalize_values(model: type, values: Mapping[str, Any]) -> list[tuple[Column, Any]]:
    if not isinstance(values, Mapping):
        raise TypeError("values must be a mapping such as {'username': 'maximilian'}")

    normalized: list[tuple[Column, Any]] = []
    seen_columns: set[str] = set()
    for key, value in values.items():
        if not isinstance(key, str):
            raise TypeError("value mapping keys must be model attribute names")
        column = _column(model, key)
        attribute_name = column.attribute_name or ""
        if attribute_name in seen_columns:
            raise ValueError(f"Column {attribute_name!r} was provided more than once")
        seen_columns.add(attribute_name)
        normalized.append((column, value))
    return normalized


def _require_where(where: Condition | None, all_rows: bool) -> None:
    if where is None and not all_rows:
        raise ValueError(
            "UPDATE and DELETE require a where condition. "
            "Pass all_rows=True to intentionally affect every row."
        )


def _append_returning(
    model: type,
    returning: str | Sequence[str | ColumnRef] | None,
) -> tuple[str, bool]:
    projection = _returning_sql(model, returning)
    if projection is None:
        return "", False
    return f" RETURNING {projection}", True


def compile_insert(
    model: type,
    values: Mapping[str, Any],
    *,
    returning: str | Sequence[str | ColumnRef] | None = "*",
) -> CompiledQuery:
    normalized = _normalize_values(model, values)
    parameters = [value for _, value in normalized]
    if normalized:
        columns = ", ".join(quote_identifier(column.name or "") for column, _ in normalized)
        placeholders = ", ".join(f"${index}" for index in range(1, len(parameters) + 1))
        statement = f"INSERT INTO {_table_sql(model)} ({columns}) VALUES ({placeholders})"
    else:
        statement = f"INSERT INTO {_table_sql(model)} DEFAULT VALUES"

    returning_clause, returns_rows = _append_returning(model, returning)
    return CompiledQuery(statement + returning_clause, tuple(parameters), returns_rows)


def compile_select(
    model: type,
    *columns: str | ColumnRef,
    where: Condition | None = None,
    order_by: Sequence[Ordering | ColumnRef] | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> CompiledQuery:
    parameters: list[Any] = []
    projection = _projection_sql(model, columns)
    statement = f"SELECT {projection} FROM {_table_sql(model)}"
    statement += _where_sql(model, where, parameters)

    if order_by:
        order_parts: list[str] = []
        for item in order_by:
            if isinstance(item, Ordering):
                column_sql = _column_sql(model, item.column)
                direction = item.direction
            else:
                column_sql = _column_sql(model, item)
                direction = "ASC"
            order_parts.append(f"{column_sql} {direction}")
        statement += " ORDER BY " + ", ".join(order_parts)

    for label, value in (("limit", limit), ("offset", offset)):
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{label} must be a non-negative integer")
            parameters.append(value)
            statement += f" {label.upper()} ${len(parameters)}"

    return CompiledQuery(statement, tuple(parameters), True)


def compile_update(
    model: type,
    values: Mapping[str, Any],
    *,
    where: Condition | None = None,
    all_rows: bool = False,
    returning: str | Sequence[str | ColumnRef] | None = "*",
) -> CompiledQuery:
    _require_where(where, all_rows)
    normalized = _normalize_values(model, values)
    if not normalized:
        raise ValueError("UPDATE requires at least one column value")

    parameters = [value for _, value in normalized]
    assignments = ", ".join(
        f"{quote_identifier(column.name or '')} = ${index}"
        for index, (column, _) in enumerate(normalized, start=1)
    )
    statement = f"UPDATE {_table_sql(model)} SET {assignments}"
    statement += _where_sql(model, where, parameters)
    returning_clause, returns_rows = _append_returning(model, returning)
    return CompiledQuery(statement + returning_clause, tuple(parameters), returns_rows)


def compile_delete(
    model: type,
    *,
    where: Condition | None = None,
    all_rows: bool = False,
    returning: str | Sequence[str | ColumnRef] | None = "*",
) -> CompiledQuery:
    _require_where(where, all_rows)
    parameters: list[Any] = []
    statement = f"DELETE FROM {_table_sql(model)}"
    statement += _where_sql(model, where, parameters)
    returning_clause, returns_rows = _append_returning(model, returning)
    return CompiledQuery(statement + returning_clause, tuple(parameters), returns_rows)
