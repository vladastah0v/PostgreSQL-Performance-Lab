SELECT count(*) AS event_count
FROM public.user_events
WHERE payload @> '{"source":"campaign","device":"tablet"}'::jsonb
