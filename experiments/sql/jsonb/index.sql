CREATE INDEX lab_exp_event_payload ON public.user_events
USING gin (payload jsonb_path_ops)
