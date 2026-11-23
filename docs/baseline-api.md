# Baseline API

Маршруты `/api/v1/baseline` регистрируются при `LAB_MODE=true`. В стандартном Compose порт API привязан к `127.0.0.1`. Приложение проверяет непосредственного клиента и заголовок `Host`. В Compose разрешён адрес шлюза Docker, через который Docker Desktop передаёт запросы с локального порта. Запросы с заголовками проксирования и запросы от других контейнеров к Baseline возвращают `404`. [Публикация портов Docker](https://docs.docker.com/engine/network/port-publishing/)

```bash
LAB_MODE=true docker compose up --build -d --wait
curl 'http://127.0.0.1:8008/api/v1/baseline/orders?status=delivered&date_from=2025-01-01&limit=5'
```

## Маршруты

Пути в таблице указаны относительно `/api/v1/baseline`.

| GET | Фильтры и результат |
| --- | --- |
| `/users/search` | `name` и `email` ищутся как буквальные подстроки без учёта регистра. `city` сравнивается целиком без учёта регистра. `status` сопоставляется точно. Сортировка по `id` по возрастанию. |
| `/users/{user_id}/orders` | Заказы пользователя с `item_count`, количеством строк заказа. Поддерживает `status`, `date_from` и `date_to`. |
| `/orders` | Все заказы с фильтрами `status`, `date_from` и `date_to`. |
| `/orders/{order_id}/details` | Заказ со всеми позициями и платежами. Позиции и платежи сортируются по `id`. |
| `/analytics/sales` | Количество заказов и выручка за `date_from`–`date_to`. `period=day` группирует по дням, `period=month` по месяцам. |
| `/analytics/top-products` | Рейтинг за `date_from`–`date_to` по числу проданных единиц, затем по выручке и `product_id`. `limit` задаёт размер рейтинга. |
| `/analytics/customers` | Общее число клиентов, число покупателей с одним или более заказами, число повторных покупателей, количество заказов и выручка. Интервал `date_from`–`date_to` применяется к заказам. |
| `/events` | События для обязательного `user_id` с фильтрами `event_type`, `date_from` и `date_to`. |

Границы дат включены и интерпретируются как календарные дни UTC. Заказы в списках и события сортируются по `created_at`, затем по `id`, оба по убыванию. Страницы принимают `limit` от 1 до 100 и `offset` от 0, возвращают `items`, `limit`, `offset` и `has_more`. Денежные суммы сериализуются строками. Аналитика продаж учитывает заказы в статусах `paid`, `shipped` и `delivered`.

## Исходные планы PostgreSQL

Скрипт запускает операции уровня сервиса и снимает `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` для запросов репозитория в транзакции только для чтения. Для каждого SQL-шаблона сохраняется число вызовов и один план с параметрами первого вызова. Запись `Execution Time` относится к этому SQL-вызову. [PostgreSQL EXPLAIN](https://www.postgresql.org/docs/16/sql-explain.html)

```bash
uv run --locked python -m scripts.explain_queries \
  --user-id 1 --order-id 1 --output experiments/sql/baseline/initial-plans.json
```

В сохранённом диагностическом прогоне на `medium` поиск пользователей, выборки заказов и событий использовали `Seq Scan`. История пользователя с 20 заказами выполнила 22 `SELECT`, включая проверку пользователя, выборку заказов и по одному чтению позиций на заказ. Рейтинг товаров показал соединения по таблицам заказов, позиций и товаров перед агрегацией. [JSON с исходными планами](../experiments/sql/baseline/initial-plans.json)
