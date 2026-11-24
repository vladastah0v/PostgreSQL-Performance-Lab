# Архитектура PostgreSQL Performance Lab

## Системная архитектура

```mermaid
flowchart LR
    client["HTTP-клиент / Locust"] --> api
    subgraph lab["Docker Compose"]
        api["FastAPI<br/>Маршруты, сервис, репозитории"] -->|"SQLAlchemy + asyncpg"| db[("PostgreSQL")]
        api -->|"Аналитический кэш"| redis[("Redis")]
        exporter["PostgreSQL Exporter"] -->|"Статистика PostgreSQL"| db
        prometheus["Prometheus"] -->|"/metrics"| api
        prometheus -->|"/metrics"| exporter
        grafana["Grafana"] -->|"PromQL"| prometheus
    end
```

API, PostgreSQL и Redis запускаются в основном профиле. Профиль `monitoring` добавляет три сервиса мониторинга и одноразовую инициализацию роли Exporter. [Запуск и порты](monitoring.md)

## ER-диаграмма PostgreSQL

На диаграмме показаны связи и ключевые поля прикладных таблиц. Манифест синтетического набора хранится в отдельной схеме `lab_data`. [Типы и ограничения](database.md)

```mermaid
erDiagram
    users ||--o{ orders : "размещает"
    users ||--o{ user_events : "создаёт"
    categories ||--o{ products : "содержит"
    products ||--o{ order_items : "входит в"
    orders ||--o{ order_items : "содержит"
    orders ||--o{ payments : "оплачивается"
    orders ||--o{ order_status_history : "меняет статус"

    users {
        bigint id PK
        varchar email UK
        varchar status
    }
    categories {
        bigint id PK
        varchar slug UK
    }
    products {
        bigint id PK
        bigint category_id FK
        varchar sku UK
        numeric price
    }
    orders {
        bigint id PK
        bigint user_id FK
        varchar status
        numeric total_amount
        timestamptz created_at
    }
    order_items {
        bigint id PK
        bigint order_id FK
        bigint product_id FK
        integer quantity
        numeric unit_price
    }
    payments {
        bigint id PK
        bigint order_id FK
        varchar status
        numeric amount
    }
    order_status_history {
        bigint id PK
        bigint order_id FK
        varchar old_status
        varchar new_status
    }
    user_events {
        bigint id PK
        bigint user_id FK
        varchar event_type
        jsonb payload
    }
```

## Потоки Baseline и Optimized

```mermaid
flowchart TD
    request["HTTP GET /api/v1"] --> route{"Префикс маршрута"}
    route -->|baseline| allowed{"LAB_MODE и локальный доступ"}
    allowed -->|разрешён| baseline["Сервис и репозиторий Baseline"]
    allowed -->|закрыт| denied["HTTP 404"]
    route -->|optimized| analytics{"Маршрут аналитики с включённым кэшем"}
    analytics -->|да| cache{"Результат есть в Redis"}
    analytics -->|нет| optimized["Сервис и репозиторий Optimized"]
    cache -->|HIT| response["Проверенная модель ответа"]
    cache -->|MISS или сбой Redis| optimized
    cache -->|таймаут ожидания заполнения| busy["HTTP 503"]
    optimized -->|пакетная загрузка, keyset и агрегаты| db[("PostgreSQL")]
    baseline -->|исходные SQL-запросы| db
    db --> response
```

При успешном MISS аналитический результат сохраняется с TTL. Для Baseline и Optimized интеграционные тесты сверяют форму и значения ответов. [Оптимизации](optimization.md) · [Кэширование](redis-cache.md)

## Выполнение бенчмарка

```mermaid
flowchart TD
    generate["Миграции и синтетический набор"] --> verify["Проверка данных и параметров"]
    verify --> warmup["Прогрев выбранного сценария"]
    warmup --> pair["Парные замеры до и после"]
    pair --> raw["Повторы, планы и метаданные"]
    raw --> report["Сравнения, JSON, CSV, Markdown и PNG"]
```

Эксперименты SQL и HTTP имеют разные границы замера. Для Locust каждый вариант запускается с одинаковым числом пользователей и ограничениями времени и запросов. Входы генератора отчётов выбираются явными путями. [Методика Locust](benchmarking.md) · [Форматы отчётов](reporting.md)
