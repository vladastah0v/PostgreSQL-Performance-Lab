SELECT id, user_id, status, total_amount, created_at
FROM public.orders
WHERE status = 'pending'
ORDER BY created_at DESC, id DESC
LIMIT 100
