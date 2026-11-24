SELECT id, user_id, status, total_amount, created_at
FROM public.orders
WHERE status = 'delivered'
  AND created_at >= TIMESTAMPTZ '2025-10-01 00:00:00+00'
  AND created_at < TIMESTAMPTZ '2025-11-01 00:00:00+00'
ORDER BY created_at DESC, id DESC
LIMIT 100
