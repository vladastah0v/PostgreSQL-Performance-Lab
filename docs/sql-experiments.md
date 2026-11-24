# SQL-эксперименты

Восемь сравнений выполнены на PostgreSQL 16.16 в отдельной базе `performance_lab_sql` на ревизии `0001`. Использован `medium` с `seed=42`, 100 000 заказов и 500 000 событий. Перед измерениями выполнен `VACUUM (ANALYZE)`. Docker Desktop предоставлял 2 CPU и около 5,8 ГиБ памяти. [Конфигурация и метаданные](../benchmarks/results/sql-medium-2026-11-24/metadata.json)

## Результаты

| Эксперимент | До, медиана мс | После, медиана мс | Наблюдение |
| --- | ---: | ---: | --- |
| [Поиск пользователей](../benchmarks/results/sql-medium-2026-11-24/missing-index/summary.md) | 3,097 | 0,809 | `Bitmap Index Scan` по городу сократил число кандидатов для фильтра статуса. |
| [Составной индекс](../benchmarks/results/sql-medium-2026-11-24/composite-index/summary.md) | 15,918 | 0,304 | `Index Scan` использовал статус, диапазон дат и порядок выдачи. |
| [N+1](../benchmarks/results/sql-medium-2026-11-24/n-plus-one/summary.md) | 478,690 | 54,625 | `selectinload` сократил историю из 20 заказов с 22 до 3 `SELECT` на той же схеме. |
| [OFFSET и keyset](../benchmarks/results/sql-medium-2026-11-24/pagination/summary.md) | 8,826 | 0,094 | Оба варианта вернули одну страницу на глубине 8 000. Во втором плане сканирование началось с уже известного курсора. |
| [Агрегация](../benchmarks/results/sql-medium-2026-11-24/aggregation/summary.md) | 263,453 | 193,345 | Агрегация и `LIMIT` выполнены до чтения названий товаров по первичному ключу. |
| [Частичный индекс](../benchmarks/results/sql-medium-2026-11-24/partial-index/summary.md) | 19,789 | 0,330 | `Index Scan` выбрал последние 100 заказов `pending` из индекса размером 176 КиБ. |
| [Покрывающий индекс](../benchmarks/results/sql-medium-2026-11-24/covering-index/summary.md) | 0,574 | 0,726 | `Index Only Scan` показал `Heap Fetches = 0`. В первом повторе число обращений к общим буферам сократилось с 22 до 14. Медиана на прогретых данных выросла. |
| [JSONB и GIN](../benchmarks/results/sql-medium-2026-11-24/jsonb/summary.md) | 132,883 | 13,981 | GIN `jsonb_path_ops` заменил последовательное сканирование событий на bitmap-выборку по `@>`. |

Покрывающий индекс занял 15,9 МиБ при 8,5 МиБ у обычного B-tree. Этот прогон показал сокращение обращений к буферам и рост времени выполнения. [Как работает карта видимости PostgreSQL](https://www.postgresql.org/docs/16/indexes-index-only-scans.html)

## Методика

Перед каждым вариантом выполняется один прогревочный вызов. Затем измеряются пять повторов `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` с одними и теми же параметрами. Для ORM-сценария время операции — сумма `Execution Time` всех её `SELECT`. Для каждого повтора отдельно сохраняются планирование, буферные счётчики и сумма `Heap Fetches` по узлам, которые сообщают эту метрику. HTTP-задержка измеряется на этапе нагрузочных тестов. [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/16/sql-explain.html)

База, данные и настройки одинаковы для двух вариантов. Runner сверяет результаты с учётом порядка и откатывает транзакцию после каждого варианта. Он сохраняет все повторы, диапазоны времени и полный план каждого SQL-запроса первого повтора. Варианты запускаются последовательно, при сохранённых кешах ОС и PostgreSQL. Размер и время построения индексов записаны отдельно от времени `SELECT`. Стоимость записи для индексов Optimized API измерена отдельным экспериментом. [Сводка текущего прогона](../benchmarks/results/sql-medium-2026-11-24/comparison.csv), [стоимость записи](optimization.md#стоимость-записи)

## Воспроизведение

Для первого запуска создайте `performance_lab_sql` в локальном Compose, примените ревизию `0001` и загрузите исходный набор. Повторные прогоны используют уже подготовленную базу и новый каталог результатов.

```bash
docker compose up -d --wait postgres
docker compose exec -T postgres sh -ec 'createdb -U "$POSTGRES_USER" performance_lab_sql'
POSTGRES_DB=performance_lab_sql uv run --locked alembic upgrade 0001
POSTGRES_DB=performance_lab_sql uv run --locked python -m scripts.generate_data --profile medium --seed 42
docker compose exec -T postgres sh -ec 'psql -X -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d performance_lab_sql -c "VACUUM (ANALYZE);"'
uv run --locked python -m scripts.run_sql_experiments --database performance_lab_sql --repetitions 5
```

`--scenario` запускает один эксперимент. Для `small` можно передать `--offset 100`, чтобы выбрать существующую страницу. По умолчанию артефакты попадают в новый каталог `benchmarks/results/sql_<UTC timestamp>`. Для явного пути используйте `--output`. Runner проверяет версию схемы, наличие манифеста синтетических данных и число строк во всех восьми таблицах. [SQL-источники](../experiments/sql)
