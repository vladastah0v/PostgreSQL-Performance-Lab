SELECT ranked.product_id, p.name, ranked.units_sold, ranked.revenue
FROM (
    SELECT i.product_id, sum(i.quantity) AS units_sold,
           sum(i.quantity * i.unit_price) AS revenue
    FROM public.order_items AS i
    JOIN public.orders AS o ON o.id = i.order_id
    WHERE o.status IN ('paid', 'shipped', 'delivered')
      AND o.created_at >= TIMESTAMPTZ '2025-01-01 00:00:00+00'
      AND o.created_at < TIMESTAMPTZ '2026-01-01 00:00:00+00'
    GROUP BY i.product_id
    ORDER BY units_sold DESC, revenue DESC, i.product_id
    LIMIT 20
) AS ranked
JOIN public.products AS p ON p.id = ranked.product_id
ORDER BY ranked.units_sold DESC, ranked.revenue DESC, ranked.product_id
