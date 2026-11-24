# Мониторинг PostgreSQL Performance Lab

Профиль `monitoring` запускает Prometheus, Grafana и PostgreSQL Exporter вместе с API, PostgreSQL и Redis. `LAB_MODE=true` открывает маршруты Baseline для локального сравнения.

```bash
LAB_MODE=true docker compose --profile monitoring up --build -d --wait --wait-timeout 180
docker compose exec api alembic upgrade head
```

- Grafana с готовым дашбордом доступна на порту `GRAFANA_PORT`, по умолчанию `3008`. Учётные данные первого запуска задаются `GRAFANA_ADMIN_USER` и `GRAFANA_ADMIN_PASSWORD`. [PostgreSQL Performance Lab в Grafana](http://127.0.0.1:3008/d/postgres-performance-lab)
- Prometheus доступен на порту `PROMETHEUS_PORT`, по умолчанию `9098`. В списке целей должны быть `api`, `postgres` и `prometheus`. Состояние подключения Exporter к базе показывает метрика `pg_up`. [Цели сбора Prometheus](http://127.0.0.1:9098/targets)
- FastAPI публикует `/metrics`, `/health/live` и `/health/ready` на порту `API_PORT`, по умолчанию `8008`. [Метрики FastAPI](http://127.0.0.1:8008/metrics)

Опубликованные порты привязаны к `127.0.0.1`. PostgreSQL Exporter доступен внутри сети Compose. Параметры портов и локальные значения учётных данных приведены в [`.env.example`](../.env.example).

## Что показывает дашборд

| Панель | Как получаются данные |
| --- | --- |
| Задержка, RPS, доля 5xx | FastAPI считает ответы для шаблонов маршрутов `/api/v1/`. Prometheus вычисляет p50, p95 и p99 по гистограмме, дашборд группирует серии по Baseline и Optimized. Счётчик хранит и остальные HTTP-статусы. |
| Длительность SQL | p95 выполнения через курсор SQLAlchemy и средняя серверная длительность из `pg_stat_statements`. Гистограмма SQLAlchemy также включает SQL-проверки готовности. |
| Соединения | Активные сессии PostgreSQL, занятые соединения и максимальная ёмкость пулов FastAPI. Метрики приложения суммируются по воркерам Uvicorn. Ёмкость включает overflow. |
| Медленные запросы | До пяти идентификаторов с максимальным средним временем выполнения за окно, среди 50 записей, отобранных Exporter по накопленному времени. Можно выбрать базу данных. |
| Транзакции | Фиксации и откаты PostgreSQL в секунду по выбранной базе. |
| Попадания в Redis | `HIT / (HIT + MISS)` для каждой аналитической операции. При отсутствии обращений к кэшу доля не рассчитывается. |
| CPU и память | Метрики cgroup v2 контейнера API. 100% CPU соответствует одному ядру. Память включает файловый кэш контейнера. |

Prometheus опрашивает цели каждые 5 секунд. Для p95 и p99 используются корзины гистограммы, поэтому значения на панели приближённые. Для замеров с минимальным влиянием мониторинга `API_METRICS_ENABLED=false` выключает измерения HTTP, SQL, пула и ресурсов. Метрики Redis-кэша остаются доступны для экспериментов с кэшированием. [Режим нескольких процессов Prometheus Client](https://prometheus.github.io/client_python/multiprocess/)

## PostgreSQL и метки

Compose загружает `pg_stat_statements` через `shared_preload_libraries`. Одноразовый сервис `postgres-monitoring-init` проверяет расширение в `POSTGRES_DB` и настраивает роль `lab_metrics` с `pg_monitor`. Это работает и с уже инициализированным томом. Пароль роли задаётся `POSTGRES_EXPORTER_PASSWORD`. [PostgreSQL Exporter](https://github.com/prometheus-community/postgres_exporter/tree/v0.20.1)

Метки приложения содержат HTTP-метод, шаблон маршрута и статус, результат SQL или фиксированную операцию кэша. Exporter публикует идентификатор SQL-запроса. Исходный SQL, параметры запроса, данные пользователя и полные URL в Prometheus не передаются. [Конфигурация Prometheus](../infra/prometheus/prometheus.yml)

Остановить сервисы и сохранить данные PostgreSQL, Redis, Grafana и Prometheus можно командой:

```bash
docker compose --profile monitoring stop
```
