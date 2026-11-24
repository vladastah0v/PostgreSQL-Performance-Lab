SELECT order_id, id, product_id, quantity, unit_price
FROM public.order_items
WHERE order_id >= 1000 AND order_id < 1500
ORDER BY order_id, id
