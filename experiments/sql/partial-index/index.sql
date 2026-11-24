CREATE INDEX lab_exp_pending_orders ON public.orders (created_at, id)
WHERE status = 'pending'
