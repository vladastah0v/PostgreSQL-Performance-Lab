# PostgreSQL Performance Lab

Лаборатория для воспроизводимого сравнения SQL-запросов и HTTP API на синтетических данных интернет-магазина. У одинаковых операций есть реализации Baseline и Optimized. Можно проверить совпадение результатов, изучить планы выполнения и измерить, как индексы, число SQL-запросов, пул соединений и Redis влияют на задержки под нагрузкой.

**Стек.** Python 3.12, FastAPI, SQLAlchemy, asyncpg, PostgreSQL 16, Alembic, Redis 7, Docker Compose, Locust, Prometheus, Grafana, pytest, Ruff и Matplotlib.

## Быстрый старт

Для API нужны Docker и Docker Compose. Для тестов, генерации отчётов и экспериментов с хоста установите `uv`.

```bash
LAB_MODE=true docker compose up --build -d --wait
docker compose exec api alembic upgrade head
docker compose exec api python -m scripts.generate_data --profile small --seed 42

curl -fsS http://127.0.0.1:8008/health/ready
curl -fsS 'http://127.0.0.1:8008/api/v1/baseline/users/1/orders?limit=5'
curl -fsS 'http://127.0.0.1:8008/api/v1/optimized/users/1/orders?limit=5'
```

Swagger UI показывает доступные маршруты. Baseline доступен локально при `LAB_MODE=true`. Порты и настройки меняются через переменные окружения или `.env`. [Swagger UI](http://127.0.0.1:8008/docs) · [Пример настроек](.env.example)

Повторная генерация с теми же параметрами проверяет и использует существующий набор. Для другого профиля используйте пустую мигрированную базу. Остановить сервисы с сохранением данных можно командой `docker compose stop`. [Генератор данных](docs/data-generation.md)

| Профиль | Пользователи | Заказы | События |
| --- | ---: | ---: | ---: |
| `small` | 1 000 | 10 000 | 50 000 |
| `medium` | 10 000 | 100 000 | 500 000 |
| `large` | 100 000 | 1 000 000 | 5 000 000 |

## Архитектура и схема

FastAPI передаёт операции через общий сервис в репозиторий Baseline или Optimized. Оба репозитория работают с PostgreSQL и возвращают одни и те же модели ответа. Optimized кэширует результаты трёх аналитических операций в Redis. Метрики API и PostgreSQL собирает Prometheus, а Grafana показывает их на дашборде. [Четыре диаграммы Mermaid](docs/architecture.md)

Ревизия `0001` создаёт восемь таблиц для пользователей, каталога, заказов, платежей, истории статусов и событий. Схема использует внешние ключи, ограничения статусов, `NUMERIC` для денежных сумм и `JSONB` для событий. Ревизия `0002` добавляет восемь индексов для Optimized. [Схема PostgreSQL](docs/database.md) · [Индексы](docs/optimization.md)

## Маршруты API

Все восемь операций поддерживают `GET` и доступны с префиксом `/api/v1/{baseline|optimized}`.

| Маршрут | Операция |
| --- | --- |
| `/users/search` | Поиск пользователей |
| `/users/{user_id}/orders` | История заказов пользователя |
| `/orders` | Список заказов с фильтрами |
| `/orders/{order_id}/details` | Заказ, позиции, платежи и история статусов |
| `/analytics/sales` | Продажи по периодам |
| `/analytics/top-products` | Рейтинг товаров |
| `/analytics/customers` | Статистика покупателей |
| `/events` | Поиск событий пользователей |

## Эксперименты и методика

- **SQL.** Восемь экспериментов сравнивают одиночный, составной, частичный и покрывающий индексы, GIN для JSONB, N+1, keyset-пагинацию и агрегацию. Сохраняются повторы, число запросов и планы `EXPLAIN (ANALYZE, BUFFERS)`. Для восьми индексов Optimized API отдельно измерена стоимость записи. [SQL-эксперименты](docs/sql-experiments.md) · [Стоимость записи](docs/optimization.md#стоимость-записи)
- **FastAPI.** Профилирование показывает время SQL, получения соединения, сериализации и остальной обработки. Отдельные прогоны сравнивают настройки пула, число воркеров Uvicorn и материализацию ORM и Core. [Профилирование FastAPI](docs/performance-results.md)
- **Redis.** Измеряются три режима одной операции: отключённый кэш, холодный кэш и прогретый кэш. Результаты аналитики имеют TTL, при обновлении набора генератор меняет версию пространства ключей. [Кэширование Redis](docs/redis-cache.md)
- **Locust.** В каждой паре используются одинаковые набор, фильтры и настройки API. Перед нагрузкой скрипт проверяет эквивалентность JSON. Прогрев отделён от замера. Сравнение SQL-реализаций проходит с выключенным Redis-кэшем. Сохраняются RPS, p50/p95/p99, ошибки, ресурсы, соединения и результаты каждого повтора. [Нагрузочные тесты и команды](docs/benchmarking.md)

Для замеров на хосте выполните `uv sync --locked` и подготовьте отдельную базу нужного профиля. Воспроизводимые команды для полных экспериментов приведены в документах выше. Отчёт строится из уже записанных JSON и CSV. [Генерация отчёта и PNG](docs/reporting.md)

## Результаты на наборе medium

Набор с `seed=42` содержит 10 000 пользователей, 100 000 заказов и 500 000 событий. Прогоны выполнены в Docker Desktop с 2 vCPU и около 5,76 ГиБ памяти. SQL-время и HTTP-задержка приведены в границах соответствующих экспериментов. [Сводный отчёт с исходными данными и графиками](benchmarks/results/report-medium-2026-11-24/report.md)

| Эксперимент | Baseline / до | Optimized / после |
| --- | ---: | ---: |
| N+1, медиана SQL | 478,690 мс · 22 SELECT | 54,625 мс · 3 SELECT |
| Покрывающий индекс, медиана SQL | 0,574 мс | 0,726 мс |
| Вставка 1 000 заказов, медиана SQL | 24,856 мс | 72,320 мс |
| Рейтинг товаров, HTTP p50 без кэша и с прогретым кэшем | 439,686 мс | 12,291 мс |

Смешанный сценарий Locust выполнен с одним воркером Uvicorn и Redis-кэшем, выключенным для обоих вариантов. Каждое значение — медиана двух повторов, окно отправки запросов в каждом повторе — 10 секунд. [Повторы и разброс](benchmarks/results/locust-medium-2026-11-24/comparison.csv)

| Виртуальные пользователи | RPS Baseline → Optimized | p95 Baseline → Optimized | Ошибки Baseline → Optimized |
| ---: | ---: | ---: | ---: |
| 10 | 10,99 → 16,37 | 3,47 → 2,61 с | 0% → 0% |
| 25 | 9,81 → 20,93 | 5,36 → 2,68 с | 0% → 0% |
| 50 | 11,58 → 17,67 | 6,57 → 4,39 с | 1,18% → 0,43% |

## Мониторинг

```bash
LAB_MODE=true docker compose --profile monitoring up --build -d --wait --wait-timeout 180
```

Профиль поднимает Prometheus, Grafana и PostgreSQL Exporter. Дашборд показывает HTTP-задержку, RPS и 5xx, длительность SQL, пул и активные сессии PostgreSQL, транзакции, долю Redis HIT, CPU и память контейнера API. Остановить все сервисы с сохранением данных можно командой `docker compose --profile monitoring stop`. [Дашборд Grafana](http://127.0.0.1:3008/d/postgres-performance-lab) · [Цели Prometheus](http://127.0.0.1:9098/targets) · [Настройка мониторинга](docs/monitoring.md)

## Тесты и CI

Для локального запуска используйте мигрированную лабораторную PostgreSQL и Redis. Следующие команды используют порты и учётные данные Compose по умолчанию.

```bash
docker compose up -d --wait postgres redis
uv sync --locked
uv run --locked alembic upgrade head
uv run --locked alembic check
uv run --locked ruff check .
uv run --locked ruff format --check .
TEST_DATABASE_URL=postgresql+asyncpg://lab:lab-local-password@127.0.0.1:55432/performance_lab \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
  uv run --locked pytest --cov=app --cov-report=term-missing --cov-fail-under=75
```

GitHub Actions запускает Ruff, миграции и pytest на временных PostgreSQL и Redis, проверяет порог покрытия кода `app` в 75% и собирает Docker-образ. Полные бенчмарки запускаются локально в выбранной конфигурации. [Workflow CI](.github/workflows/ci.yml) · [Итоговая проверка](docs/verification.md)

## Дальнейшие измерения

Полные прогоны на `large` и профили конкуренции Locust `high` (100) и `stress` (200) имеют статус `NOT_MEASURED`. Для проверки на более мощной машине сохраните новую конфигурацию и отдельный каталог результатов.
