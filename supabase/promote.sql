-- Review-page "Accept → main" flow + embeddable source cards.
--
-- The review page (web/review.html) reads the staged `pending` table and lets the
-- reviewer promote a figure's numbers into `metrics` (the table the dashboard
-- reads). Promotion = insert into metrics (status='accepted') + delete from
-- pending. Both writes happen from the browser as the signed-in reviewer, so
-- they need RLS policies gated to the allowlisted email (same model as
-- pending_read; see CLAUDE.md "Auth (review page)").
--
-- Run once via the Management API (or Dashboard → SQL editor).

-- Promote: allow the reviewer to INSERT accepted rows into metrics.
drop policy if exists metrics_insert on public.metrics;
create policy metrics_insert on public.metrics
    for insert to authenticated
    with check (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com');

-- Promote: allow the reviewer to DELETE the staged rows once accepted.
-- (Defined alongside the pending table in
--  .claude/skills/extract-benchmarks/pending_table.sql.)
drop policy if exists pending_delete on public.pending;
create policy pending_delete on public.pending
    for delete to authenticated
    using (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com');

-- Embeddable source cards. The original model/system cards refuse framing
-- (X-Frame-Options), and Supabase Storage serves objects with
-- `content-security-policy: default-src 'none'; sandbox` (so a stored HTML file
-- won't render in a frame either). So we keep the card HTML in a table and the
-- review page renders it via an <iframe srcdoc> — full control, always embeds.
-- Store the HTML with a <base href="<origin>/"> injected so the card's
-- root-relative assets still resolve to the origin.
create table if not exists public.cards (
    source     text primary key,        -- origin_url (matches pending.source / sources.origin_url)
    html       text not null,           -- the card page, <base href> injected
    stored_at  timestamptz not null default now()
);
alter table public.cards enable row level security;
drop policy if exists cards_read on public.cards;
create policy cards_read on public.cards
    for select to authenticated
    using (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com');
