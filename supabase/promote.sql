-- Reviewer UPDATE policy on metrics (lets review.html flip accepted).
-- The skill writes with the service_role key (bypasses RLS), so no insert
-- policy is needed. Run once via the Management API. Idempotent.

drop policy if exists metrics_update on public.metrics;
create policy metrics_update on public.metrics
    for update to authenticated
    using  (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com')
    with check (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com');
