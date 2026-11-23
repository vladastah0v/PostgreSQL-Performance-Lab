-- Run on the generated medium dataset. Both statements operate on 1,000 orders.
BEGIN;
EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
INSERT INTO orders (id, user_id, status, total_amount, created_at, updated_at)
SELECT -id, user_id, status, total_amount, created_at, updated_at
FROM orders
WHERE id BETWEEN 1 AND 1000;
ROLLBACK;

BEGIN;
EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)
UPDATE orders
SET status = CASE WHEN status = 'pending' THEN 'paid' ELSE 'pending' END
WHERE id BETWEEN 1 AND 1000;
ROLLBACK;
