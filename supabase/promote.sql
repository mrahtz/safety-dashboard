-- Embeddable source cards for the review page (web/review.html).
--
-- The original model/system cards refuse framing (X-Frame-Options), so the
-- review page renders the source on the left from this table via an
-- <iframe srcdoc> instead — full control, always embeds. The extract-benchmarks
-- skill upserts the card HTML (with a <base href="<origin>/"> injected so the
-- card's root-relative assets still resolve) when it ingests an HTML source.
--
-- Run once via the Management API (or Dashboard → SQL editor).

create table if not exists public.cards (
    source     text primary key,        -- origin_url (matches metrics.source_url)
    html       text not null,           -- the card page, <base href> injected
    stored_at  timestamptz not null default now()
);
alter table public.cards enable row level security;

-- Reviewer-only read (same allowlist as the review page; the skill writes with
-- the service_role key, which bypasses RLS).
drop policy if exists cards_read on public.cards;
create policy cards_read on public.cards
    for select to authenticated
    using (lower(auth.jwt() ->> 'email') = 'matthew.rahtz@gmail.com');
