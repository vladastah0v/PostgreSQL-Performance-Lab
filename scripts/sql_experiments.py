from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Self

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.schemas.parameters import OrderSearch
from app.services.commerce import CommerceService
from scripts.explain_queries import REPOSITORIES

SQL_ROOT = Path(__file__).resolve().parents[1] / "experiments" / "sql"
type Read = Callable[[AsyncConnection], Awaitable[list | dict]]
type PrepareRead = Callable[[AsyncConnection], Awaitable[Read]]


@dataclass(frozen=True)
class Index:
    name: str
    sql: str


@dataclass(frozen=True)
class Variant:
    prepare: PrepareRead
    index: Index | None = None

    @classmethod
    def query(cls, read: Read, index: Index | None = None) -> Self:
        async def prepare(_connection: AsyncConnection) -> Read:
            return read

        return cls(prepare, index)


@dataclass(frozen=True)
class Experiment:
    name: str
    title: str
    change: str
    before: Variant
    after: Variant


def source(name: str, file: str) -> str:
    return (SQL_ROOT / name / file).read_text(encoding="utf-8").strip()


def sql_read(name: str, file: str = "query.sql", **params) -> Read:
    statement = text(source(name, file))

    async def read(connection: AsyncConnection) -> list[dict]:
        rows = (await connection.execute(statement, params)).mappings().all()
        return [dict(row) for row in rows]

    return read


def indexed(name: str, title: str, change: str, index_name: str) -> Experiment:
    read = sql_read(name)
    return Experiment(
        name,
        title,
        change,
        Variant.query(read),
        Variant.query(read, Index(index_name, source(name, "index.sql"))),
    )


def orm_orders(implementation: str, user_id: int) -> Read:
    async def read(connection: AsyncConnection) -> dict:
        async with AsyncSession(bind=connection) as session:
            service = CommerceService(REPOSITORIES[implementation](session))
            page = await service.user_orders(user_id, OrderSearch(limit=20))
        if not page.items:
            raise ValueError("Choose a user with orders for the N+1 experiment")
        return page.model_dump(mode="json")

    return read


def pagination(offset: int) -> Experiment:
    index = Index("lab_exp_orders_status_date", source("composite-index", "index.sql"))
    offset_read = sql_read("pagination", "offset.sql", limit=20, offset=offset)
    find_anchor = sql_read("pagination", "offset.sql", limit=1, offset=offset - 1)

    async def prepare_keyset(connection: AsyncConnection) -> Read:
        anchors = await find_anchor(connection)
        if not anchors:
            raise ValueError(
                "The pagination offset exceeds the matching orders. Set --offset to a smaller value."
            )
        anchor = anchors[0]
        return sql_read(
            "pagination",
            "keyset.sql",
            limit=20,
            id=anchor["id"],
            created_at=anchor["created_at"],
        )

    return Experiment(
        "pagination",
        "OFFSET и keyset",
        "Оба запроса используют один составной индекс. Keyset начинает сканирование от пары `(created_at, id)`. Получение этой пары вынесено за пределы измерения.",
        Variant.query(offset_read, index),
        Variant(prepare_keyset, index),
    )


def experiments(user_id: int, offset: int) -> tuple[Experiment, ...]:
    covering_read = sql_read("covering-index")
    return (
        indexed(
            "missing-index",
            "Поиск пользователей",
            "Индекс по `lower(city)` сокращает число строк для проверки статуса. Сортировка по ID сохраняется.",
            "lab_exp_users_city",
        ),
        indexed(
            "composite-index",
            "Заказы по статусу и дате",
            "Один B-tree поддерживает равенство по статусу, диапазон дат и требуемый порядок строк.",
            "lab_exp_orders_status_date",
        ),
        Experiment(
            "n-plus-one",
            "N+1 и пакетная загрузка",
            "Обе стороны вызывают реальные ORM-репозитории с одинаковой схемой. `selectinload` загружает позиции страницы одним запросом, заменяя отдельные выборки для заказов.",
            Variant.query(orm_orders("baseline", user_id)),
            Variant.query(orm_orders("optimized", user_id)),
        ),
        pagination(offset),
        Experiment(
            "aggregation",
            "Агрегация рейтинга товаров",
            "Позиции агрегируются по ID товара до чтения его названия. В справочник товаров попадают только строки, вошедшие в рейтинг.",
            Variant.query(sql_read("aggregation", "before.sql")),
            Variant.query(sql_read("aggregation", "after.sql")),
        ),
        indexed(
            "partial-index",
            "Частичный индекс заказов",
            "В индекс входят заказы `pending`. Он позволяет сразу прочитать последние заказы этого статуса в требуемом порядке.",
            "lab_exp_pending_orders",
        ),
        Experiment(
            "covering-index",
            "Покрывающий индекс позиций",
            "Ключ B-tree остаётся одинаковым. `INCLUDE` добавляет колонки ответа. `VACUUM` перед прогоном делает страницы неизменяемого набора доступными для чтения по карте видимости.",
            Variant.query(
                covering_read,
                Index("lab_exp_items", source("covering-index", "before-index.sql")),
            ),
            Variant.query(
                covering_read,
                Index("lab_exp_items", source("covering-index", "after-index.sql")),
            ),
        ),
        indexed(
            "jsonb",
            "JSONB и GIN",
            "Оператор `@>` сохраняет условия поиска. GIN `jsonb_path_ops` находит кандидатов по значениям в JSON-документе.",
            "lab_exp_event_payload",
        ),
    )
