from __future__ import annotations

import unittest

from pgmodel import Column, Table
from pgmodel.compiler import compile_delete, compile_insert, compile_select, compile_update


class Users(Table):
    __tablename__ = "users"

    user_id = Column(primary_key=True)
    username = Column()
    active = Column(name="is_active")


class QueryCompilerTests(unittest.TestCase):
    def test_select_compiles_conditions_and_binds_values(self) -> None:
        query = compile_select(
            Users,
            Users.user_id,
            Users.username,
            where=(Users.user_id == 4) & Users.username.ilike("max%"),
            order_by=[Users.username.desc()],
            limit=20,
        )

        self.assertEqual(
            query.sql,
            'SELECT "user_id", "username" FROM "public"."users" '
            'WHERE ("user_id" = $1 AND "username" ILIKE $2) '
            'ORDER BY "username" DESC LIMIT $3',
        )
        self.assertEqual(query.parameters, (4, "max%", 20))
        self.assertTrue(query.returns_rows)

    def test_update_compiles_set_and_where_values_separately(self) -> None:
        query = compile_update(
            Users,
            {"username": "maximilian"},
            where=Users.user_id == 4,
        )

        self.assertEqual(
            query.sql,
            'UPDATE "public"."users" SET "username" = $1 '
            'WHERE "user_id" = $2 RETURNING *',
        )
        self.assertEqual(query.parameters, ("maximilian", 4))

    def test_insert_supports_mapped_column_names(self) -> None:
        query = compile_insert(Users, {"active": True}, returning=[Users.user_id])

        self.assertEqual(
            query.sql,
            'INSERT INTO "public"."users" ("is_active") VALUES ($1) '
            'RETURNING "user_id"',
        )
        self.assertEqual(query.parameters, (True,))

    def test_delete_requires_a_condition_by_default(self) -> None:
        with self.assertRaisesRegex(ValueError, "require a where condition"):
            compile_delete(Users)

        query = compile_delete(Users, where=Users.user_id == 9, returning=None)
        self.assertEqual(
            query.sql,
            'DELETE FROM "public"."users" WHERE "user_id" = $1',
        )
        self.assertEqual(query.parameters, (9,))
        self.assertFalse(query.returns_rows)

    def test_empty_in_condition_is_valid_sql(self) -> None:
        query = compile_select(Users, where=Users.user_id.in_([]))
        self.assertEqual(
            query.sql,
            'SELECT * FROM "public"."users" WHERE FALSE',
        )
        self.assertEqual(query.parameters, ())

    def test_unknown_columns_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown column"):
            compile_update(Users, {"password": "secret"}, where=Users.user_id == 1)

    def test_identifiers_are_quoted(self) -> None:
        class StrangeTable(Table):
            __tablename__ = 'users"; DROP TABLE users; --'
            user_id = Column()

        query = compile_select(StrangeTable)
        self.assertEqual(
            query.sql,
            'SELECT * FROM "public"."users""; DROP TABLE users; --"',
        )


if __name__ == "__main__":
    unittest.main()
