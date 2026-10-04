from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .model import Column, Table


class Condition:
    """A SQL condition that can be combined with ``&`` and ``|``."""

    def __and__(self, other: Condition) -> Condition:
        if not isinstance(other, Condition):
            return NotImplemented
        return Junction("AND", self, other)

    def __or__(self, other: Condition) -> Condition:
        if not isinstance(other, Condition):
            return NotImplemented
        return Junction("OR", self, other)

    def __invert__(self) -> Condition:
        return Negation(self)

    def __bool__(self) -> bool:
        raise TypeError(
            "SQL conditions cannot be used as Python booleans; "
            "combine them with '&' or '|', not 'and' or 'or'."
        )


class ColumnRef:
    """A reference to a model column used to build SQL expressions."""

    __slots__ = ("model", "column")

    def __init__(self, model: type[Table], column: Column) -> None:
        self.model = model
        self.column = column

    def _compare(self, operator: str, value: Any) -> Comparison:
        if value is None:
            if operator == "=":
                operator = "IS"
            elif operator == "<>":
                operator = "IS NOT"
            else:
                raise TypeError("None can only be compared with '==' or '!='")
        return Comparison(self, operator, value)

    def __eq__(self, other: Any) -> Condition:  # type: ignore[override]
        return self._compare("=", other)

    def __ne__(self, other: Any) -> Condition:  # type: ignore[override]
        return self._compare("<>", other)

    def __lt__(self, other: Any) -> Condition:
        return self._compare("<", other)

    def __le__(self, other: Any) -> Condition:
        return self._compare("<=", other)

    def __gt__(self, other: Any) -> Condition:
        return self._compare(">", other)

    def __ge__(self, other: Any) -> Condition:
        return self._compare(">=", other)

    def in_(self, values: Iterable[Any]) -> Condition:
        if isinstance(values, (str, bytes)):
            raise TypeError("in_() expects an iterable of values, not a string")
        return InCondition(self, tuple(values), negated=False)

    def not_in(self, values: Iterable[Any]) -> Condition:
        if isinstance(values, (str, bytes)):
            raise TypeError("not_in() expects an iterable of values, not a string")
        return InCondition(self, tuple(values), negated=True)

    def like(self, pattern: str) -> Condition:
        return self._compare("LIKE", pattern)

    def ilike(self, pattern: str) -> Condition:
        return self._compare("ILIKE", pattern)

    def is_null(self) -> Condition:
        return self._compare("IS", None)

    def is_not_null(self) -> Condition:
        return self._compare("IS NOT", None)

    def asc(self) -> Ordering:
        return Ordering(self, "ASC")

    def desc(self) -> Ordering:
        return Ordering(self, "DESC")

    def __repr__(self) -> str:
        return f"{self.model.__name__}.{self.column.attribute_name}"


class Comparison(Condition):
    __slots__ = ("left", "operator", "right")

    def __init__(self, left: ColumnRef, operator: str, right: Any) -> None:
        self.left = left
        self.operator = operator
        self.right = right


class InCondition(Condition):
    __slots__ = ("column", "values", "negated")

    def __init__(
        self, column: ColumnRef, values: tuple[Any, ...], *, negated: bool
    ) -> None:
        self.column = column
        self.values = values
        self.negated = negated


class Junction(Condition):
    __slots__ = ("operator", "left", "right")

    def __init__(self, operator: str, left: Condition, right: Condition) -> None:
        self.operator = operator
        self.left = left
        self.right = right


class Negation(Condition):
    __slots__ = ("condition",)

    def __init__(self, condition: Condition) -> None:
        self.condition = condition


class Ordering:
    __slots__ = ("column", "direction")

    def __init__(self, column: ColumnRef, direction: str) -> None:
        normalized_direction = direction.upper()
        if normalized_direction not in {"ASC", "DESC"}:
            raise ValueError("Ordering direction must be 'ASC' or 'DESC'")
        self.column = column
        self.direction = normalized_direction
