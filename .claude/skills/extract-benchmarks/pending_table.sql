-- Staging table for freshly extracted benchmark rows (extract-benchmarks skill).
--
-- Separate from the live `metrics`/`candidates` tables: these rows are the raw
-- output of a single card/page ingest, parked for review before anything is
-- promoted. One row per (model, condition, benchmark) data point. Every row
-- carries fig_num (its source table/figure number); table rows additionally set
-- row_idx/col_idx, graph rows leave them null -- matching the skill's CSV.
--
-- Run this once via the Management API (Dashboard -> SQL editor, or the curl in
-- SKILL.md). Idempotent.

create table if not exists public.pending (
    id          bigint generated always as identity primary key,
    source      text not null,          -- URL or filename this run ingested
    model       text,
    condition   text,
    benchmark   text,
    value       text,                   -- kept as printed (preserve precision; e.g. "11/12")
    units       text,
    fig_num     integer,                -- source table/figure number (always set)
    row_idx     integer,                -- tables only
    col_idx     integer,                -- tables only
    ingested_at timestamptz not null default now()
);

create index if not exists pending_source_idx on public.pending (source);

alter table public.pending enable row level security;

-- service_role (used by upload_pending.py) bypasses RLS, so no write policy is
-- needed. Uncomment to let the anon key READ staged rows from a dashboard:
-- drop policy if exists pending_read on public.pending;
-- create policy pending_read on public.pending
--     for select to anon, authenticated using (true);
