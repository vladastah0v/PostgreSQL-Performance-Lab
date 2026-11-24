# Агрегация рейтинга товаров

Позиции агрегируются по ID товара до чтения его названия. В справочник товаров попадают только строки, вошедшие в рейтинг.

Порядок и значения строк совпали. Число строк в результате — 20. Медиана SQL-времени за 5 повторов составила 263.453 мс до изменения и 193.345 мс после. Число `SELECT` на операцию до изменения — 1, после — 1.

В [плане до изменения](explain_before.json) наблюдались Aggregate, Gather Merge, Hash, Hash Join, Limit, Seq Scan (order_items), Seq Scan (orders), Seq Scan (products), Sort.

В [плане после изменения](explain_after.json) наблюдались Aggregate, Gather Merge, Hash, Hash Join, Index Scan (pk_products), Limit, Nested Loop, Seq Scan (order_items), Seq Scan (orders), Sort.
