# N+1 и пакетная загрузка

Обе стороны вызывают реальные ORM-репозитории с одинаковой схемой. `selectinload` загружает позиции страницы одним запросом, заменяя отдельные выборки для заказов.

Порядок и значения строк совпали. Число строк в результате — 20. Медиана SQL-времени за 5 повторов составила 478.690 мс до изменения и 54.625 мс после. Число `SELECT` на операцию до изменения — 22, после — 3.

В [плане до изменения](explain_before.json) наблюдались Gather Merge, Index Scan (pk_users), Limit, Seq Scan (order_items), Seq Scan (orders), Sort.

В [плане после изменения](explain_after.json) наблюдались Gather, Index Only Scan (pk_users), Limit, Seq Scan (order_items), Seq Scan (orders), Sort.
