SELECT p.id AS product_id, p.name, sum(i.quantity) AS units_sold,
       sum(i.quantity * i.unit_price) AS revenue
FROM public.products AS p
JOIN public.order_items AS i ON i.product_id = p.id
JOIN public.orders AS o ON o.id = i.order_id
WHERE o.status IN ('paid', 'shipped', 'delivered')
  AND o.created_at >= TIMESTAMPTZ '2025-01-01 00:00:00+00'
  AND o.created_at < TIMESTAMPTZ '2026-01-01 00:00:00+00'
GROUP BY p.id, p.name
ORDER BY units_sold DESC, revenue DESC, p.id
LIMIT 20
