# Сравнительный отчёт PostgreSQL Performance Lab

Разность измеряется как время до минус время после. Снижение задержки равно разности, делённой на время до, в процентах. Ускорение показывает отношение времени до ко времени после. Отрицательная разность указывает на рост задержки.

Каждое сравнение использует свой исходный эксперимент. SQL-время, полная HTTP-задержка и варианты PostgreSQL и Redis описаны в соответствующих разделах. Прочерк означает отсутствие значения в источнике.

## Графики

- [Время выполнения SQL до и после](sql-execution.png)
- [Задержка API p50, p95 и p99](api-latency.png)
- [Пропускная способность Locust](rps-by-concurrency.png)
- [Доля ошибок Locust](error-rate.png)
- [Холодный и прогретый Redis-кэш](redis-cache.png)
- [Влияние индексов на SELECT и INSERT/UPDATE](index-impact.png)

## SQL-эксперименты

Исходные файлы эксперимента. [Артефакты](../sql-medium-2026-11-24)

| Операция | Статус | Варианты | Метрика, мс | До | После | Δ, мс | Снижение, % | Ускорение, × | SQL-запросы | Планы |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Поиск пользователей | COMPLETED | До → После | Медиана SQL | 3,097 | 0,809 | 2,288 | 73,878 | 3,828 | 1 → 1 | [До](../sql-medium-2026-11-24/missing-index/explain_before.json) · [После](../sql-medium-2026-11-24/missing-index/explain_after.json) |
| Заказы по статусу и дате | COMPLETED | До → После | Медиана SQL | 15,918 | 0,304 | 15,614 | 98,09 | 52,362 | 1 → 1 | [До](../sql-medium-2026-11-24/composite-index/explain_before.json) · [После](../sql-medium-2026-11-24/composite-index/explain_after.json) |
| N+1 и пакетная загрузка | COMPLETED | До → После | Медиана SQL | 478,69 | 54,625 | 424,065 | 88,589 | 8,763 | 22 → 3 | [До](../sql-medium-2026-11-24/n-plus-one/explain_before.json) · [После](../sql-medium-2026-11-24/n-plus-one/explain_after.json) |
| OFFSET и keyset | COMPLETED | До → После | Медиана SQL | 8,826 | 0,094 | 8,732 | 98,935 | 93,894 | 1 → 1 | [До](../sql-medium-2026-11-24/pagination/explain_before.json) · [После](../sql-medium-2026-11-24/pagination/explain_after.json) |
| Агрегация рейтинга товаров | COMPLETED | До → После | Медиана SQL | 263,453 | 193,345 | 70,108 | 26,611 | 1,363 | 1 → 1 | [До](../sql-medium-2026-11-24/aggregation/explain_before.json) · [После](../sql-medium-2026-11-24/aggregation/explain_after.json) |
| Частичный индекс заказов | COMPLETED | До → После | Медиана SQL | 19,789 | 0,33 | 19,459 | 98,332 | 59,967 | 1 → 1 | [До](../sql-medium-2026-11-24/partial-index/explain_before.json) · [После](../sql-medium-2026-11-24/partial-index/explain_after.json) |
| Покрывающий индекс позиций | COMPLETED | До → После | Медиана SQL | 0,574 | 0,726 | -0,152 | -26,481 | 0,791 | 1 → 1 | [До](../sql-medium-2026-11-24/covering-index/explain_before.json) · [После](../sql-medium-2026-11-24/covering-index/explain_after.json) |
| JSONB и GIN | COMPLETED | До → После | Медиана SQL | 132,883 | 13,981 | 118,902 | 89,479 | 9,505 | 1 → 1 | [До](../sql-medium-2026-11-24/jsonb/explain_before.json) · [После](../sql-medium-2026-11-24/jsonb/explain_after.json) |

### Изменения и источники

- **Поиск пользователей.** Индекс по `lower(city)` сокращает число строк для проверки статуса. Сортировка по ID сохраняется. [baseline.json](../sql-medium-2026-11-24/missing-index/baseline.json) [optimized.json](../sql-medium-2026-11-24/missing-index/optimized.json) [summary.md](../sql-medium-2026-11-24/missing-index/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **Заказы по статусу и дате.** Один B-tree поддерживает равенство по статусу, диапазон дат и требуемый порядок строк. [baseline.json](../sql-medium-2026-11-24/composite-index/baseline.json) [optimized.json](../sql-medium-2026-11-24/composite-index/optimized.json) [summary.md](../sql-medium-2026-11-24/composite-index/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **N+1 и пакетная загрузка.** Обе стороны вызывают реальные ORM-репозитории с одинаковой схемой. `selectinload` загружает позиции страницы одним запросом, заменяя отдельные выборки для заказов. [baseline.json](../sql-medium-2026-11-24/n-plus-one/baseline.json) [optimized.json](../sql-medium-2026-11-24/n-plus-one/optimized.json) [summary.md](../sql-medium-2026-11-24/n-plus-one/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **OFFSET и keyset.** Оба запроса используют один составной индекс. Keyset начинает сканирование от пары `(created_at, id)`. Получение этой пары вынесено за пределы измерения. [baseline.json](../sql-medium-2026-11-24/pagination/baseline.json) [optimized.json](../sql-medium-2026-11-24/pagination/optimized.json) [summary.md](../sql-medium-2026-11-24/pagination/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **Агрегация рейтинга товаров.** Позиции агрегируются по ID товара до чтения его названия. В справочник товаров попадают только строки, вошедшие в рейтинг. [baseline.json](../sql-medium-2026-11-24/aggregation/baseline.json) [optimized.json](../sql-medium-2026-11-24/aggregation/optimized.json) [summary.md](../sql-medium-2026-11-24/aggregation/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **Частичный индекс заказов.** В индекс входят заказы `pending`. Он позволяет сразу прочитать последние заказы этого статуса в требуемом порядке. [baseline.json](../sql-medium-2026-11-24/partial-index/baseline.json) [optimized.json](../sql-medium-2026-11-24/partial-index/optimized.json) [summary.md](../sql-medium-2026-11-24/partial-index/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **Покрывающий индекс позиций.** Ключ B-tree остаётся одинаковым. `INCLUDE` добавляет колонки ответа. `VACUUM` перед прогоном делает страницы неизменяемого набора доступными для чтения по карте видимости. [baseline.json](../sql-medium-2026-11-24/covering-index/baseline.json) [optimized.json](../sql-medium-2026-11-24/covering-index/optimized.json) [summary.md](../sql-medium-2026-11-24/covering-index/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)
- **JSONB и GIN.** Оператор `@>` сохраняет условия поиска. GIN `jsonb_path_ops` находит кандидатов по значениям в JSON-документе. [baseline.json](../sql-medium-2026-11-24/jsonb/baseline.json) [optimized.json](../sql-medium-2026-11-24/jsonb/optimized.json) [summary.md](../sql-medium-2026-11-24/jsonb/summary.md) [comparison.csv](../sql-medium-2026-11-24/comparison.csv)

### Метрики нагрузки и ресурсов

| Операция | Измерение | Единица | До → после |
| --- | --- | --- | --- |
| Поиск пользователей | Попадания в общие буферы | — | 142 → 141 |
| Поиск пользователей | Чтения общих блоков | — | 0 → 0 |
| Поиск пользователей | Размер индекса | байт | — → 90 112 |
| Заказы по статусу и дате | Попадания в общие буферы | — | 1 010 → 103 |
| Заказы по статусу и дате | Чтения общих блоков | — | 0 → 0 |
| Заказы по статусу и дате | Размер индекса | байт | — → 4 816 896 |
| N+1 и пакетная загрузка | Попадания в общие буферы | — | 49 193 → 3 385 |
| N+1 и пакетная загрузка | Чтения общих блоков | — | 0 → 0 |
| OFFSET и keyset | Попадания в общие буферы | — | 8 115 → 23 |
| OFFSET и keyset | Чтения общих блоков | — | 0 → 0 |
| OFFSET и keyset | Размер индекса | байт | 4 816 896 → 4 816 896 |
| Агрегация рейтинга товаров | Попадания в общие буферы | — | 4 430 → 4 466 |
| Агрегация рейтинга товаров | Чтения общих блоков | — | 0 → 0 |
| Частичный индекс заказов | Попадания в общие буферы | — | 1 010 → 104 |
| Частичный индекс заказов | Чтения общих блоков | — | 0 → 0 |
| Частичный индекс заказов | Размер индекса | байт | — → 180 224 |
| Покрывающий индекс позиций | Попадания в общие буферы | — | 22 → 14 |
| Покрывающий индекс позиций | Чтения общих блоков | — | 0 → 0 |
| Покрывающий индекс позиций | Размер индекса | байт | 8 953 856 → 16 670 720 |
| JSONB и GIN | Попадания в общие буферы | — | 7 767 → 3 182 |
| JSONB и GIN | Чтения общих блоков | — | 0 → 0 |
| JSONB и GIN | Размер индекса | байт | — → 2 260 992 |

## Профилирование FastAPI

Исходные файлы эксперимента. [Артефакты](../fastapi-medium-2026-11-24)

| Операция | Статус | Варианты | Метрика, мс | До | После | Δ, мс | Снижение, % | Ускорение, × | SQL-запросы | Планы |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| История заказов | COMPLETED | Baseline → Optimized | p50 HTTP | 213,356 | 83,269 | 130,087 | 60,972 | 2,562 | 22 → 3 | — |
| Рейтинг товаров | COMPLETED | Baseline → Optimized | p50 HTTP | 1 520,655 | 709,324 | 811,331 | 53,354 | 2,144 | 1 → 1 | — |
| Пагинация заказов | COMPLETED | OFFSET → Keyset | p50 HTTP | 43,752 | 35,007 | 8,745 | 19,988 | 1,25 | 1 → 1 | — |
| Два соединения в пуле | COMPLETED | Контроль → Изменение | p50 HTTP | 441,963 | 163,39 | 278,573 | 63,031 | 2,705 | 1 → 1 | — |
| Одно соединение и overflow | COMPLETED | Контроль → Изменение | p50 HTTP | 441,963 | 183,587 | 258,376 | 58,461 | 2,407 | 1 → 1 | — |
| Короткий таймаут пула | COMPLETED | Контроль → Изменение | p50 HTTP | 441,963 | 115,709 | 326,254 | 73,819 | 3,82 | 1 → 1 | — |
| Два воркера | COMPLETED | Контроль → Изменение | p50 HTTP | 441,963 | 248,276 | 193,687 | 43,824 | 1,78 | 1 → 1 | — |
| Материализация ORM и Core | COMPLETED | ORM → Core | Медиана операции | 31,222 | 27,838 | 3,384 | 10,839 | 1,122 | — → — | — |
| Профиль попадания Redis | OBSERVED | HIT → — | p50 HTTP | 27,812 | — | — | — | — | 0 → — | — |

### Изменения и источники

- **История заказов.** Пакетная загрузка связанных позиций с selectinload. [latency.json](../fastapi-medium-2026-11-24/latency.json)
- **Рейтинг товаров.** Агрегация и LIMIT выполняются до чтения названий. [latency.json](../fastapi-medium-2026-11-24/latency.json)
- **Пагинация заказов.** Keyset вместо OFFSET на той же странице. Курсор получен до замера. [latency.json](../fastapi-medium-2026-11-24/latency.json)
- **Два соединения в пуле.** Пул 2 + 0, воркеров 1, таймаут 2 с. HTTP-квантили рассчитаны по успешным ответам. Доля ошибок включает все ответы. [pool.json](../fastapi-medium-2026-11-24/pool.json)
- **Одно соединение и overflow.** Пул 1 + 1, воркеров 1, таймаут 2 с. HTTP-квантили рассчитаны по успешным ответам. Доля ошибок включает все ответы. [pool.json](../fastapi-medium-2026-11-24/pool.json)
- **Короткий таймаут пула.** Пул 1 + 0, воркеров 1, таймаут 0.04 с. HTTP-квантили рассчитаны по успешным ответам. Доля ошибок включает все ответы. [pool.json](../fastapi-medium-2026-11-24/pool.json)
- **Два воркера.** Пул 1 + 0, воркеров 2, таймаут 2 с. HTTP-квантили рассчитаны по успешным ответам. Доля ошибок включает все ответы. [pool.json](../fastapi-medium-2026-11-24/pool.json)
- **Материализация ORM и Core.** Одинаковая SQL-выборка и модели ответа. Измерено время клиентской обработки строк и SQL. [orm.json](../fastapi-medium-2026-11-24/orm.json)
- **Профиль попадания Redis.** Одиночная серия HIT. Число SQL-запросов и HTTP-квантили сохранены для этой серии. [cache.json](../fastapi-medium-2026-11-24/cache.json)

### Метрики нагрузки и ресурсов

| Операция | Измерение | Единица | До → после |
| --- | --- | --- | --- |
| История заказов | p50 HTTP | мс | 213,356 → 83,269 |
| История заказов | p95 HTTP | мс | 295,076 → 117,328 |
| История заказов | p99 HTTP | мс | 308,825 → 152,178 |
| История заказов | Доля ошибок | % | 0 → 0 |
| История заказов | p50 получения соединения | мс | 7,338 → 8,157 |
| История заказов | p50 размера ответа | байт | 2 459 → 2 459 |
| Рейтинг товаров | p50 HTTP | мс | 1 520,655 → 709,324 |
| Рейтинг товаров | p95 HTTP | мс | 1 698,453 → 896,672 |
| Рейтинг товаров | p99 HTTP | мс | 1 725,603 → 1 211,16 |
| Рейтинг товаров | Доля ошибок | % | 0 → 0 |
| Рейтинг товаров | p50 получения соединения | мс | 7,597 → 8,149 |
| Рейтинг товаров | p50 размера ответа | байт | 1 674 → 1 674 |
| Пагинация заказов | p50 HTTP | мс | 43,752 → 35,007 |
| Пагинация заказов | p95 HTTP | мс | 53,545 → 40,908 |
| Пагинация заказов | p99 HTTP | мс | 58,984 → 43,547 |
| Пагинация заказов | Доля ошибок | % | 0 → 0 |
| Пагинация заказов | p50 получения соединения | мс | 6,592 → 7,462 |
| Пагинация заказов | p50 размера ответа | байт | 2 232 → 2 229 |
| Два соединения в пуле | p50 HTTP | мс | 441,963 → 163,39 |
| Два соединения в пуле | p95 HTTP | мс | 889,38 → 303,944 |
| Два соединения в пуле | p99 HTTP | мс | 1 251,035 → 317,058 |
| Два соединения в пуле | Доля ошибок | % | 0 → 0 |
| Два соединения в пуле | p50 получения соединения | мс | 247,638 → 55,489 |
| Два соединения в пуле | p50 размера ответа | байт | 21 377 → 21 377 |
| Одно соединение и overflow | p50 HTTP | мс | 441,963 → 183,587 |
| Одно соединение и overflow | p95 HTTP | мс | 889,38 → 257,037 |
| Одно соединение и overflow | p99 HTTP | мс | 1 251,035 → 278,657 |
| Одно соединение и overflow | Доля ошибок | % | 0 → 0 |
| Одно соединение и overflow | p50 получения соединения | мс | 247,638 → 53,886 |
| Одно соединение и overflow | p50 размера ответа | байт | 21 377 → 21 377 |
| Короткий таймаут пула | p50 HTTP | мс | 441,963 → 115,709 |
| Короткий таймаут пула | p95 HTTP | мс | 889,38 → 151,319 |
| Короткий таймаут пула | p99 HTTP | мс | 1 251,035 → 151,319 |
| Короткий таймаут пула | Доля ошибок | % | 0 → 75 |
| Короткий таймаут пула | p50 получения соединения | мс | 247,638 → 9,543 |
| Короткий таймаут пула | p50 размера ответа | байт | 21 377 → 21 377 |
| Два воркера | p50 HTTP | мс | 441,963 → 248,276 |
| Два воркера | p95 HTTP | мс | 889,38 → 384,613 |
| Два воркера | p99 HTTP | мс | 1 251,035 → 464,742 |
| Два воркера | Доля ошибок | % | 0 → 0 |
| Два воркера | p50 получения соединения | мс | 247,638 → 137,898 |
| Два воркера | p50 размера ответа | байт | 21 377 → 21 377 |
| Профиль попадания Redis | p50 HTTP | мс | 27,812 → — |
| Профиль попадания Redis | p95 HTTP | мс | 89,057 → — |
| Профиль попадания Redis | p99 HTTP | мс | 92,911 → — |
| Профиль попадания Redis | Доля ошибок | % | 0 → — |
| Профиль попадания Redis | p50 получения соединения | мс | 0 → — |
| Профиль попадания Redis | p50 размера ответа | байт | 1 674 → — |

## Режимы Redis-кэша

Исходные файлы эксперимента. [Артефакты](../redis-medium-2026-11-24)

| Операция | Статус | Варианты | Метрика, мс | До | После | Δ, мс | Снижение, % | Ускорение, × | SQL-запросы | Планы |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| sales · MISS | COMPLETED | Без кэша → Холодный кэш | p50 HTTP | 80,339 | 82,118 | -1,779 | -2,214 | 0,978 | — → — | — |
| sales · HIT | COMPLETED | Без кэша → Прогретый кэш | p50 HTTP | 80,339 | 10,052 | 70,287 | 87,488 | 7,992 | — → — | — |
| top-products · MISS | COMPLETED | Без кэша → Холодный кэш | p50 HTTP | 439,686 | 438,178 | 1,508 | 0,343 | 1,003 | — → — | — |
| top-products · HIT | COMPLETED | Без кэша → Прогретый кэш | p50 HTTP | 439,686 | 12,291 | 427,395 | 97,205 | 35,773 | — → — | — |
| customers · MISS | COMPLETED | Без кэша → Холодный кэш | p50 HTTP | 55,7 | 56,586 | -0,886 | -1,591 | 0,984 | — → — | — |
| customers · HIT | COMPLETED | Без кэша → Прогретый кэш | p50 HTTP | 55,7 | 9,182 | 46,518 | 83,515 | 6,066 | — → — | — |

### Изменения и источники

- **sales · MISS.** Инвалидация меняла версию ключа перед каждым измерением. [disabled.json](../redis-medium-2026-11-24/disabled.json) [cold.json](../redis-medium-2026-11-24/cold.json)
- **sales · HIT.** Кэш прогрет до начала измерений. [disabled.json](../redis-medium-2026-11-24/disabled.json) [warm.json](../redis-medium-2026-11-24/warm.json)
- **top-products · MISS.** Инвалидация меняла версию ключа перед каждым измерением. [disabled.json](../redis-medium-2026-11-24/disabled.json) [cold.json](../redis-medium-2026-11-24/cold.json)
- **top-products · HIT.** Кэш прогрет до начала измерений. [disabled.json](../redis-medium-2026-11-24/disabled.json) [warm.json](../redis-medium-2026-11-24/warm.json)
- **customers · MISS.** Инвалидация меняла версию ключа перед каждым измерением. [disabled.json](../redis-medium-2026-11-24/disabled.json) [cold.json](../redis-medium-2026-11-24/cold.json)
- **customers · HIT.** Кэш прогрет до начала измерений. [disabled.json](../redis-medium-2026-11-24/disabled.json) [warm.json](../redis-medium-2026-11-24/warm.json)

### Метрики нагрузки и ресурсов

| Операция | Измерение | Единица | До → после |
| --- | --- | --- | --- |
| sales · MISS | p50 HTTP | мс | 80,339 → 82,118 |
| sales · MISS | p95 HTTP | мс | 137,136 → 148,041 |
| sales · MISS | p99 HTTP | мс | 194,604 → 163,62 |
| sales · HIT | p50 HTTP | мс | 80,339 → 10,052 |
| sales · HIT | p95 HTTP | мс | 137,136 → 13,863 |
| sales · HIT | p99 HTTP | мс | 194,604 → 28,028 |
| top-products · MISS | p50 HTTP | мс | 439,686 → 438,178 |
| top-products · MISS | p95 HTTP | мс | 594,777 → 709,16 |
| top-products · MISS | p99 HTTP | мс | 711,567 → 826,505 |
| top-products · HIT | p50 HTTP | мс | 439,686 → 12,291 |
| top-products · HIT | p95 HTTP | мс | 594,777 → 28,435 |
| top-products · HIT | p99 HTTP | мс | 711,567 → 150,523 |
| customers · MISS | p50 HTTP | мс | 55,7 → 56,586 |
| customers · MISS | p95 HTTP | мс | 72,596 → 99,279 |
| customers · MISS | p99 HTTP | мс | 76,5 → 108,246 |
| customers · HIT | p50 HTTP | мс | 55,7 → 9,182 |
| customers · HIT | p95 HTTP | мс | 72,596 → 11,456 |
| customers · HIT | p99 HTTP | мс | 76,5 → 13,251 |

## Нагрузка Locust

Исходные файлы эксперимента. [Артефакты](../locust-medium-2026-11-24)

| Операция | Статус | Варианты | Метрика, мс | До | После | Δ, мс | Снижение, % | Ускорение, × | SQL-запросы | Планы |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Все операции · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 3 467,363 | 2 613,39 | 853,972 | 24,629 | 1,327 | — → — | — |
| users-search · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 119,373 | 228,448 | -109,076 | -91,374 | 0,523 | — → — | — |
| user-orders · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 249,14 | 281,437 | -32,297 | -12,963 | 0,885 | — → — | — |
| orders · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 159,064 | 191,863 | -32,799 | -20,62 | 0,829 | — → — | — |
| order-details · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 149,981 | 223,85 | -73,869 | -49,252 | 0,67 | — → — | — |
| sales · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 084,12 | 1 678,316 | 405,804 | 19,471 | 1,242 | — → — | — |
| top-products · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 245,277 | 3 484,91 | 760,367 | 17,911 | 1,218 | — → — | — |
| customers · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 158,822 | 1 093,827 | 1 064,995 | 49,332 | 1,974 | — → — | — |
| events · users-10 | COMPLETED | Baseline → Optimized | p95 HTTP | 53,5 | 176,592 | -123,092 | -230,079 | 0,303 | — → — | — |
| Все операции · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 5 363,365 | 2 684,702 | 2 678,663 | 49,944 | 1,998 | — → — | — |
| users-search · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 521,479 | 1 174,771 | 1 346,708 | 53,409 | 2,146 | — → — | — |
| user-orders · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 3 278,2 | 1 000,699 | 2 277,501 | 69,474 | 3,276 | — → — | — |
| orders · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 145,148 | 1 100,071 | 1 045,077 | 48,718 | 1,95 | — → — | — |
| order-details · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 619,108 | 1 202,241 | 1 416,867 | 54,097 | 2,179 | — → — | — |
| sales · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 189,266 | 1 904,331 | 2 284,935 | 54,543 | 2,2 | — → — | — |
| top-products · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 6 052,078 | 3 913,571 | 2 138,507 | 35,335 | 1,546 | — → — | — |
| customers · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 114,128 | 2 006,218 | 2 107,91 | 51,236 | 2,051 | — → — | — |
| events · users-25 | COMPLETED | Baseline → Optimized | p95 HTTP | 2 087,898 | 1 329,563 | 758,335 | 36,32 | 1,57 | — → — | — |
| Все операции · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 6 567,119 | 4 387,157 | 2 179,963 | 33,195 | 1,497 | — → — | — |
| users-search · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 158,434 | 3 554,739 | 603,695 | 14,517 | 1,17 | — → — | — |
| user-orders · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 929,683 | 3 282,14 | 1 647,543 | 33,421 | 1,502 | — → — | — |
| orders · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 220,654 | 3 819,154 | 401,5 | 9,513 | 1,105 | — → — | — |
| order-details · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 5 009,555 | 3 366,794 | 1 642,761 | 32,793 | 1,488 | — → — | — |
| sales · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 6 054,501 | 4 580,14 | 1 474,361 | 24,351 | 1,322 | — → — | — |
| top-products · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 7 669,724 | 6 356,749 | 1 312,975 | 17,119 | 1,207 | — → — | — |
| customers · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 6 489,051 | 3 890,789 | 2 598,262 | 40,041 | 1,668 | — → — | — |
| events · users-50 | COMPLETED | Baseline → Optimized | p95 HTTP | 4 521,705 | 2 973,532 | 1 548,174 | 34,239 | 1,521 | — → — | — |

### Изменения и источники

- **Все операции · users-10.** Медиана метрик отдельных прогонов. HTTP-ошибки входят в измерение. [comparison.csv](../locust-medium-2026-11-24/comparison.csv) [report.json](../locust-medium-2026-11-24/users-10/run-01/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-10/run-02/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-10/run-01/optimized/report.json) [report.json](../locust-medium-2026-11-24/users-10/run-02/optimized/report.json)
- **Все операции · users-25.** Медиана метрик отдельных прогонов. HTTP-ошибки входят в измерение. [comparison.csv](../locust-medium-2026-11-24/comparison.csv) [report.json](../locust-medium-2026-11-24/users-25/run-01/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-25/run-02/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-25/run-01/optimized/report.json) [report.json](../locust-medium-2026-11-24/users-25/run-02/optimized/report.json)
- **Все операции · users-50.** Медиана метрик отдельных прогонов. HTTP-ошибки входят в измерение. [comparison.csv](../locust-medium-2026-11-24/comparison.csv) [report.json](../locust-medium-2026-11-24/users-50/run-01/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-50/run-02/baseline/report.json) [report.json](../locust-medium-2026-11-24/users-50/run-01/optimized/report.json) [report.json](../locust-medium-2026-11-24/users-50/run-02/optimized/report.json)

### Метрики нагрузки и ресурсов

| Операция | Измерение | Единица | До → после |
| --- | --- | --- | --- |
| Все операции · users-10 | p50 HTTP | мс | 113,126 → 105,929 |
| Все операции · users-10 | p95 HTTP | мс | 3 467,363 → 2 613,39 |
| Все операции · users-10 | p99 HTTP | мс | 4 223,524 → 3 484,91 |
| Все операции · users-10 | Пропускная способность | запр./с | 10,988 → 16,371 |
| Все операции · users-10 | Доля ошибок | % | 0 → 0 |
| Все операции · users-10 | CPU API | % | 39,4 → 33,255 |
| Все операции · users-10 | CPU PostgreSQL | % | 167,45 → 206,52 |
| Все операции · users-10 | Пик памяти API | байт | 149 369 651 → 138 045 030,5 |
| Все операции · users-10 | Пик памяти PostgreSQL | байт | 247 673 651 → 218 261 094,5 |
| Все операции · users-10 | Пик соединений PostgreSQL | — | 10 → 10 |
| Все операции · users-25 | p50 HTTP | мс | 1 708,814 → 755,215 |
| Все операции · users-25 | p95 HTTP | мс | 5 363,365 → 2 684,702 |
| Все операции · users-25 | p99 HTTP | мс | 6 052,078 → 3 621,72 |
| Все операции · users-25 | Пропускная способность | запр./с | 9,808 → 20,932 |
| Все операции · users-25 | Доля ошибок | % | 0 → 0 |
| Все операции · users-25 | CPU API | % | 47,1 → 29,29 |
| Все операции · users-25 | CPU PostgreSQL | % | 193,815 → 182,48 |
| Все операции · users-25 | Пик памяти API | байт | 151 309 517 → 144 021 913,5 |
| Все операции · users-25 | Пик памяти PostgreSQL | байт | 246 992 077 → 233 884 876,5 |
| Все операции · users-25 | Пик соединений PostgreSQL | — | 10 → 10 |
| Все операции · users-50 | p50 HTTP | мс | 3 231,775 → 2 135,068 |
| Все операции · users-50 | p95 HTTP | мс | 6 567,119 → 4 387,157 |
| Все операции · users-50 | p99 HTTP | мс | 7 669,724 → 6 146,844 |
| Все операции · users-50 | Пропускная способность | запр./с | 11,576 → 17,669 |
| Все операции · users-50 | Доля ошибок | % | 1,183 → 0,428 |
| Все операции · users-50 | CPU API | % | 25,52 → 43,085 |
| Все операции · users-50 | CPU PostgreSQL | % | 199,4 → 245,665 |
| Все операции · users-50 | Пик памяти API | байт | 158 334 976 → 155 818 393,5 |
| Все операции · users-50 | Пик памяти PostgreSQL | байт | 243 584 205 → 225 338 982,5 |
| Все операции · users-50 | Пик соединений PostgreSQL | — | 10 → 10 |

## Стоимость записи с индексами

Исходные файлы эксперимента. [Артефакты](../../../experiments/indexes/optimized-api.json)

| Операция | Статус | Варианты | Метрика, мс | До | После | Δ, мс | Снижение, % | Ускорение, × | SQL-запросы | Планы |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| INSERT | COMPLETED | 0001 → 0002 | Медиана SQL | 24,856 | 72,32 | -47,464 | -190,956 | 0,344 | — → — | — |
| UPDATE | COMPLETED | 0001 → 0002 | Медиана SQL | 10,862 | 36,251 | -25,389 | -233,741 | 0,3 | — → — | — |

### Изменения и источники

- **INSERT.** Ревизия 0002 добавляет индексы Optimized API. Измерены операции на 1000 строках с откатом после каждого прогона. [optimized-api.json](../../../experiments/indexes/optimized-api.json)
- **UPDATE.** Ревизия 0002 добавляет индексы Optimized API. Измерены операции на 1000 строках с откатом после каждого прогона. [optimized-api.json](../../../experiments/indexes/optimized-api.json)

### Метрики нагрузки и ресурсов

| Операция | Измерение | Единица | До → после |
| --- | --- | --- | --- |
| INSERT | Попадания в общие буферы | — | 5 086 → 16 509 |
| INSERT | Чтения общих блоков | — | 0 → 0 |
| INSERT | WAL | байт | 175 388 → 489 900 |
| UPDATE | Попадания в общие буферы | — | 8 386 → 17 644 |
| UPDATE | Чтения общих блоков | — | 0 → 0 |
| UPDATE | WAL | байт | 240 591 → 486 534 |

Формат источников, команды и границы измерений описаны в документации экспериментов.
