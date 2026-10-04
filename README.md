# pgmodel

Небольшая асинхронная библиотека для PostgreSQL. Таблица описывается классом,
сравнения колонок превращаются в параметризованные условия SQL, а подключение
настраивается один раз на старте приложения.

## Установка

```powershell
python -m pip install -e .
```

Чтобы библиотека сама читала `.env`, установите optional extra:

```powershell
python -m pip install -e ".[dotenv]"
```

Строка подключения задаётся в `.env`:

```dotenv
DATABASE_URL=postgresql://user:password@localhost:5432/app
```

## Быстрый старт

Один вызов на старте — и дальше модели работают без передачи подключения:

```python
from pgmodel import Column, Table, configure_from_env, shutdown


class Users(Table):
    __tablename__ = "users"

    user_id = Column(primary_key=True)
    username = Column()
    email = Column()


async def startup() -> None:
    await configure_from_env(dotenv_path=".env")


async def cleanup() -> None:
    await shutdown()
```

Запросы после этого выглядят так:

```python
row = await Users.fetchrow(Users.user_id == 4)          # одна строка или None
name = await Users.fetchval(Users.username, Users.user_id == 4)
rows = await Users.select(Users.user_id, Users.username, Users.email.is_not_null())

await Users.insert({"username": "maximilian", "email": "max@example.com"})
await Users.update({"username": "maximilian"}, Users.user_id == 4)
await Users.delete(Users.user_id == 4)
```

Условия можно передавать позиционно — они объединяются через `AND`, — либо
ключевым словом `where`, либо комбинировать оба способа. Для `OR` и скобок
используйте `|`, `&` и `~`:

```python
rows = await Users.select(
    where=(Users.user_id < 10) | Users.username.ilike("max%"),
    order_by=[Users.user_id.desc()],
    limit=25,
)
```

## Интеграция с FastAPI

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.models import Users
from pgmodel import configure_from_env, shutdown

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    await configure_from_env(dotenv_path=PROJECT_ROOT / ".env")
    yield
    await shutdown()


app = FastAPI(lifespan=lifespan)


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    return await Users.fetchrow(Users.user_id == user_id)
```

## Как находится подключение

Порядок резолюции:

1. явный аргумент `database=`;
2. подключение, привязанное к классу через `Users.bind(db)`;
3. подключение по умолчанию, заданное `configure()` / `configure_from_env()`.

Если ничего не задано, модель выбрасывает понятную ошибку с подсказкой, что вызвать.

Явная привязка нужна, когда в приложении несколько баз или когда глобальное
состояние нежелательно (например, в тестах):

```python
Users.bind(db)          # одна модель
Table.bind_all(db)      # все объявленные модели сразу
Users.bind(None)        # отвязать
```

`.env` хранит только строки подключения — сами таблицы привязываются в рантайме,
поэтому объявить биндинг в `.env` нельзя.

## Сырой SQL, когда билдера не хватает

`Database` повторяет словарь asyncpg, поэтому сложные запросы (`JOIN`, CTE,
агрегаты) можно писать напрямую, используя то же подключение:

```python
from pgmodel import get_default_database

db = get_default_database()
row = await db.fetchrow(
    "SELECT u.username, o.total "
    "FROM users u JOIN orders o ON o.user_id = u.user_id "
    "WHERE u.user_id = $1",
    4,
)
```

Доступны `fetch`, `fetchrow`, `fetchval` и `execute`.

## Поведение и ограничения

- `insert`, `select`, `update` и `delete` по умолчанию используют `RETURNING`
  и возвращают список словарей. Передайте `returning=None`, если нужен только
  статус выполнения.
- `update` и `delete` требуют условие `where`. Чтобы намеренно затронуть все
  строки, передайте `all_rows=True`.
- Значения всегда передаются отдельными параметрами, а имена схем, таблиц и
  колонок экранируются как SQL-идентификаторы.
- Неизвестная колонка вызывает ошибку до отправки запроса в базу.
- Поддерживаются операции над одной таблицей. `JOIN`, группировки, транзакции
  и создание таблиц в API моделей не входят — для них используйте сырой SQL.

## Тесты

Тесты не требуют запущенного PostgreSQL:

```powershell
python -m unittest discover -s tests -v
```
