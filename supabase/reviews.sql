-- Table-level review decisions for the Review page (web/review.html).
--
-- Why a separate table: the weekly re-ingest rebuilds every row in `candidates`
-- (new ids each run), so a human decision written onto candidates.status would be
-- clobbered. Instead we key decisions by a STABLE table key
--   table_key = origin_url || '::' || section_key
-- which survives re-ingest, and overlay it onto the dashboards client-side.
--
-- Run this once in the Supabase SQL editor (Dashboard -> SQL -> New query).

create table if not exists public.reviews (
    table_key   text primary key,
    status      text not null check (status in ('accepted', 'rejected', 'needs_review')),
    note        text,
    reviewer    text,
    updated_at  timestamptz not null default now()
);

alter table public.reviews enable row level security;

-- Anyone (anon) may READ decisions, so all three static pages can show the
-- sign-off badge. Only signed-in reviewers may write.
drop policy if exists reviews_read on public.reviews;
create policy reviews_read on public.reviews
    for select to anon, authenticated using (true);

drop policy if exists reviews_insert on public.reviews;
create policy reviews_insert on public.reviews
    for insert to authenticated with check (true);

drop policy if exists reviews_update on public.reviews;
create policy reviews_update on public.reviews
    for update to authenticated using (true) with check (true);
