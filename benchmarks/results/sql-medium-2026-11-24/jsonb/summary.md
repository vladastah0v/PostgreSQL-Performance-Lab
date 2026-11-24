# JSONB и GIN

Оператор `@>` сохраняет условия поиска. GIN `jsonb_path_ops` находит кандидатов по значениям в JSON-документе.

Порядок и значения строк совпали. Число строк в результате — 1. Медиана SQL-времени за 5 повторов составила 132.883 мс до изменения и 13.981 мс после. Число `SELECT` на операцию до изменения — 1, после — 1.

В [плане до изменения](explain_before.json) наблюдались Aggregate, Gather, Seq Scan (user_events).

В [плане после изменения](explain_after.json) наблюдались Aggregate, Bitmap Heap Scan (user_events), Bitmap Index Scan (lab_exp_event_payload).
