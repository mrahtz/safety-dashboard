-- Canonical DDL + drift migration for the long-format `metrics` table (and its
-- parent `sources`). web/common.js reads these with the anon key; publish.py
-- upserts them with the service_role key (explicit ids, on_conflict=id).
--
-- Why this file exists: the live tables were created out-of-band, so when the
-- code moved to the long-format schema (review state went from a `status`
-- column on metrics to a boolean `accepted`, with `status` relocating to the
-- `reviews` table) the live `metrics` table silently drifted -- it kept `status`
-- and never got `accepted`, so the dashboard's `select=…,accepted,…` 400s with
-- `42703 column metrics.accepted does not exist`. This file is the missing
-- source of truth: it mirrors src/llm_metrics/schema.py and carries the
-- idempotent ALTERs that reconcile an already-existing table.
--
-- Run once via the Management API (Dashboard -> SQL editor, or the curl in
-- CLAUDE.md "Keys & secrets"). Idempotent.

-- 1. Canonical shape (fresh setups; mirrors schema.py). ids are supplied
--    explicitly by publish.py, so `id` is a plain bigint PK, not an identity.
create table if not exists public.sources (
    id           bigint primary key,
    kind         text not null check (kind in ('html', 'pdf')),
    origin_url   text not null,
    sha256       text not null unique,
    blob         text not null,            -- base64 of the frozen source (publish.py)
    retrieved_at text not null
);

create table if not exists public.metrics (
    id          bigint primary key,
    source_id   bigint  not null references public.sources(id),
    model       text    not null,
    condition   text    not null default '',
    benchmark   text    not null,
    value       text    not null,
    units       text    not null default '',
    row_idx     integer,
    col_idx     integer,
    section_key text,
    accepted    boolean not null default false
);

-- 2. Reconcile an already-existing (pre-long-format) `metrics` table. The old
--    schema stored review state as a NOT NULL `status` column -- which both
--    blocks publish.py inserts (they omit it) and lacks the `accepted` column
--    the frontend selects.
alter table public.metrics add column if not exists accepted boolean not null default false;
alter table public.metrics drop column if exists status;

-- RLS for anon read is assumed already in place (the table currently serves
-- anon reads -- the failure is a missing column, not a policy). If setting up
-- fresh, add:
--   alter table public.metrics enable row level security;
--   create policy metrics_read on public.metrics for select to anon, authenticated using (true);
--   alter table public.sources enable row level security;
--   create policy sources_read on public.sources for select to anon, authenticated using (true);
