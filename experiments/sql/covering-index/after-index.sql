CREATE INDEX lab_exp_items ON public.order_items (order_id, id)
INCLUDE (product_id, quantity, unit_price)
