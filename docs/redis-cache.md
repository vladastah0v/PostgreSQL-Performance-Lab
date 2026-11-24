# Redis-кэш аналитики

Кэш включён по умолчанию для `sales`, `top-products` и `customers` под `/api/v1/optimized/analytics`. Тело ответа одинаково при промахе и попадании. Заголовок `X-Cache-Status` показывает `MISS`, `HIT` или `BYPASS`. Попадание обслуживается без SQL-запроса.

## Настройки

| Переменная | По умолчанию | Назначение |
| --- | --- | --- |
| `ANALYTICS_CACHE_ENABLED` | `true` | Включает кэш для аналитики. |
| `ANALYTICS_CACHE_TTL` | `60` | Срок записи в секундах от начала вычисления. Попадания его не продлевают. |
| `ANALYTICS_CACHE_WAIT` | `5` | Сколько секунд параллельный запрос ждёт вычисляемый результат. |
| `ANALYTICS_CACHE_NAMESPACE` | `lab` | Разделяет среды, которые используют один Redis. Имя базы добавляется к области автоматически. |
| `REDIS_TIMEOUT` | `0.25` | Таймаут подключения и команды Redis в секундах. |

Ключ включает операцию, версию набора, TTL и хеш всех нормализованных параметров запроса. Результат хранится как JSON, денежные значения сохраняют точность. Если вычисление заняло весь TTL, ответ возвращается клиенту без сохранения записи. При выключенном кэше `/health/ready` проверяет только PostgreSQL.

## Инвалидация и параллельные запросы

Генератор переключает версию кэша после успешной фиксации загрузки или очистки таблиц. Последующие запросы читают новую версию. Старые записи удаляются по TTL. Уже выполняющийся запрос из предыдущей версии может завершиться со своим результатом. Его результат не попадёт в кэш новой версии. При недоступности Redis во время инвалидации генератор пишет предупреждение, а прежние записи остаются ограничены TTL.

После прямого изменения аналитических данных оператор может переключить версию из контейнера API.

```bash
docker compose exec api python -m scripts.invalidate_analytics_cache
```

Для одного ключа Redis выдаёт временную блокировку одному запросу. Остальные проверяют готовность результата и получают `503` с `Retry-After: 1`, если истекло время ожидания. Запись результата и снятие блокировки сверяют токен владельца. При недоступном Redis аналитика выполняет запрос к PostgreSQL напрямую. [Блокировки Redis](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/)

## Метрики

`/metrics` публикует счётчик `analytics_cache_requests_total` с метками `operation` и `outcome`. Значения `hit` и `miss` учитывают попадания и запуски вычисления, `bypass` — выключенный режим, `error` — переход к PostgreSQL при ошибке Redis, `busy` — исчерпанное ожидание. Метки используют фиксированные значения. [Счётчики Prometheus](https://prometheus.github.io/client_python/instrumenting/counter/)

## Воспроизведение замеров

Все три режима измеряются на Optimized API c одинаковыми параметрами аналитики за 2025 год. На каждый маршрут приходится один прогревочный вызов для PostgreSQL и 20 последовательных измерений. Сценарий `cold` переключает версию перед каждым замером. Сценарий `warm` заполняет запись до замера и затем проверяет `HIT` на каждом повторе. Скрипт принимает только ответы с ожидаемым статусом кэша и сверяет хеши JSON между режимами. Замер охватывает полный локальный HTTP-запрос с чтением тела.

```bash
export ANALYTICS_CACHE_NAMESPACE=cache-benchmark
results=benchmarks/results/redis-local
ANALYTICS_CACHE_ENABLED=false docker compose up --build -d --wait
uv run --locked python -m scripts.benchmark_analytics_cache --mode disabled --output "$results"
ANALYTICS_CACHE_ENABLED=true docker compose up -d --wait
uv run --locked python -m scripts.benchmark_analytics_cache --mode cold --output "$results"
uv run --locked python -m scripts.benchmark_analytics_cache --mode warm --output "$results"
```

## Итоговый прогон

Измерения выполнены на наборе `medium` с `seed=42`, PostgreSQL 16.16, Redis 7.4.12 и двумя CPU в Docker Desktop. Все три режима вернули одинаковый JSON для каждого маршрута.

| Маршрут | Отключён, p50 мс | Холодный, p50 мс | Прогретый, p50 мс |
| --- | ---: | ---: | ---: |
| `sales` | 80,339 | 82,118 | 10,052 |
| `top-products` | 439,686 | 438,178 | 12,291 |
| `customers` | 55,700 | 56,586 | 9,182 |

Сводный CSV включает p50, p95, p99 и диапазоны. JSON содержит каждый замер и состояние счётчиков в конце режима. [Результаты прогона](../benchmarks/results/redis-medium-2026-11-24)

Интеграционные тесты на локальном Compose используют PostgreSQL и Redis. Каждый тест Redis создаёт отдельную область ключей.

```bash
TEST_DATABASE_URL=postgresql+asyncpg://lab:lab-local-password@127.0.0.1:55432/performance_lab \
TEST_REDIS_URL=redis://127.0.0.1:56379/0 \
uv run --locked pytest
```
