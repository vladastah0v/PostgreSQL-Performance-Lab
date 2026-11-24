SELECT id, full_name, email, city, status
FROM public.users
WHERE lower(city) = 'osaka' AND status = 'blocked'
ORDER BY id
LIMIT 100
