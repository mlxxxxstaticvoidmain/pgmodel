from __future__ import annotations

import unittest
from typing import Any

from pgmodel import Column, Table
from pgmodel.registry import set_default_database


class Users(Table):
    __tablename__ = "users"

    user_id = Column(primary_key=True)
    username = Column()


class Orders(Table):
    __tablename__ = "orders"

    order_id = Column(primary_key=True)
    total = Column()


class FakeDatabase:
    """Records calls and returns canned rows, so no PostgreSQL is needed."""

    def __init__(self, name: str, rows: list[dict[str, Any]] | None = None) -> None:
        self.name = name
        self.rows = rows if rows is not None else [{"user_id": 4, "username": "maximilian"}]
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []

    def _record(self, method: str, query: str, parameters: tuple[Any, ...]) -> None:
        self.calls.append((method, query, parameters))

    async def fetch(self, query: str, *parameters: Any) -> list[dict[str, Any]]:
        self._record("fetch", query, parameters)
        return list(self.rows)

    async def fetchrow(self, query: str, *parameters: Any) -> dict[str, Any] | None:
        self._record("fetchrow", query, parameters)
        return self.rows[0] if self.rows else None

    async def fetchval(self, query: str, *parameters: Any, column: int = 0) -> Any:
        self._record("fetchval", query, parameters)
        if not self.rows:
            return None
        return list(self.rows[0].values())[column]

    async def execute(self, query: str, *parameters: Any) -> str:
        self._record("execute", query, parameters)
        return "UPDATE 1"


class ResolutionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        Table.bind_all(None)
        set_default_database(None)

    def tearDown(self) -> None:
        Table.bind_all(None)
        set_default_database(None)

    async def test_unbound_model_explains_how_to_fix_it(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "configure_from_env"):
            await Users.select()

    async def test_explicit_database_wins_over_binding(self) -> None:
        bound = FakeDatabase("bound", rows=[])
        explicit = FakeDatabase("explicit")
        Users.bind(bound)  # type: ignore[arg-type]

        await Users.select(database=explicit)  # type: ignore[arg-type]

        self.assertEqual(bound.calls, [])
        self.assertEqual(len(explicit.calls), 1)

    async def test_bound_database_is_used_without_passing_it(self) -> None:
        bound = FakeDatabase("bound")
        Users.bind(bound)  # type: ignore[arg-type]

        rows = await Users.select()

        self.assertEqual(rows, [{"user_id": 4, "username": "maximilian"}])
        self.assertEqual(bound.calls[0][0], "fetch")

    async def test_default_database_is_used_when_nothing_is_bound(self) -> None:
        default = FakeDatabase("default")
        set_default_database(default)  # type: ignore[arg-type]

        await Users.select()
        await Orders.select()

        self.assertEqual([call[0] for call in default.calls], ["fetch", "fetch"])

    async def test_bind_all_wires_every_declared_model(self) -> None:
        default = FakeDatabase("default")
        Table.bind_all(default)  # type: ignore[arg-type]

        await Users.select()
        await Orders.select()

        self.assertIs(Users.__database__, default)
        self.assertIs(Orders.__database__, default)
        self.assertEqual(len(default.calls), 2)


class QueryMethodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        Table.bind_all(None)
        set_default_database(None)
        self.db = FakeDatabase("db")
        Users.bind(self.db)  # type: ignore[arg-type]

    def tearDown(self) -> None:
        Table.bind_all(None)
        set_default_database(None)

    async def test_fetchrow_limits_to_a_single_row(self) -> None:
        row = await Users.fetchrow(Users.user_id == 4)

        self.assertEqual(row, {"user_id": 4, "username": "maximilian"})
        method, sql, parameters = self.db.calls[0]
        self.assertEqual(method, "fetchrow")
        self.assertEqual(sql, 'SELECT * FROM "public"."users" WHERE "user_id" = $1 LIMIT $2')
        self.assertEqual(parameters, (4, 1))

    async def test_fetchrow_returns_none_when_nothing_matches(self) -> None:
        empty = FakeDatabase("empty", rows=[])
        Users.bind(empty)  # type: ignore[arg-type]

        self.assertIsNone(await Users.fetchrow(Users.user_id == 999))

    async def test_fetchval_selects_one_column(self) -> None:
        single = FakeDatabase("single", rows=[{"username": "maximilian"}])
        Users.bind(single)  # type: ignore[arg-type]

        value = await Users.fetchval(Users.username, Users.user_id == 4)

        self.assertEqual(value, "maximilian")
        method, sql, parameters = single.calls[0]
        self.assertEqual(method, "fetchval")
        self.assertEqual(sql, 'SELECT "username" FROM "public"."users" WHERE "user_id" = $1 LIMIT $2')
        self.assertEqual(parameters, (4, 1))

    async def test_positional_conditions_are_combined_with_and(self) -> None:
        await Users.select(Users.username, Users.user_id > 1, Users.username.ilike("max%"))

        _, sql, parameters = self.db.calls[0]
        self.assertEqual(
            sql,
            'SELECT "username" FROM "public"."users" '
            'WHERE ("user_id" > $1 AND "username" ILIKE $2)',
        )
        self.assertEqual(parameters, (1, "max%"))

    async def test_keyword_where_is_anded_with_positional_conditions(self) -> None:
        await Users.select(Users.user_id > 1, where=Users.username.ilike("max%"))

        _, sql, _ = self.db.calls[0]
        self.assertIn('WHERE ("username" ILIKE $1 AND "user_id" > $2)', sql)

    async def test_update_works_without_a_database_argument(self) -> None:
        rows = await Users.update({"username": "maximilian"}, Users.user_id == 4)

        self.assertEqual(rows, [{"user_id": 4, "username": "maximilian"}])
        method, sql, parameters = self.db.calls[0]
        self.assertEqual(method, "fetch")
        self.assertEqual(
            sql,
            'UPDATE "public"."users" SET "username" = $1 '
            'WHERE "user_id" = $2 RETURNING *',
        )
        self.assertEqual(parameters, ("maximilian", 4))

    async def test_create_alias_inserts_a_row(self) -> None:
        await Users.create({"username": "maximilian"})

        method, sql, parameters = self.db.calls[0]
        self.assertEqual(method, "fetch")
        self.assertEqual(
            sql,
            'INSERT INTO "public"."users" ("username") VALUES ($1) RETURNING *',
        )
        self.assertEqual(parameters, ("maximilian",))

    async def test_delete_without_returning_uses_execute(self) -> None:
        await Users.delete(Users.user_id == 4, returning=None)

        method, sql, parameters = self.db.calls[0]
        self.assertEqual(method, "execute")
        self.assertEqual(sql, 'DELETE FROM "public"."users" WHERE "user_id" = $1')
        self.assertEqual(parameters, (4,))


if __name__ == "__main__":
    unittest.main()
