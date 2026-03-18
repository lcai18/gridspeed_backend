-- ============================================================
-- FULL MIGRATION-FRIENDLY SCHEMA (legacy-safe)
-- - Ensures created_at/updated_at exist on ALL tables (even if tables already existed)
-- - Adds triggers that bump updated_at only when data actually changes
-- - Drops + recreates list_units_table_rows(uuid) so it compiles cleanly
-- ============================================================

-- 0) UUID generator
create extension if not exists pgcrypto;

-- ============================================================
-- 1) TABLES (safe to run repeatedly)
-- ============================================================

-- -----------------------------
-- Workspaces
-- -----------------------------
create table if not exists workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- -----------------------------
-- Users (internal staff + residents only; vendors live in vendors table)
-- -----------------------------
create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  auth_user_id uuid unique, -- Supabase Auth user id (sub). nullable for non-login contacts

  full_name text,
  phone text,
  email text,

  role text not null default 'resident', -- pm_admin | staff | resident

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create unique index if not exists users_workspace_email_uniq
  on users (workspace_id, lower(email))
  where email is not null;

create index if not exists idx_users_workspace on users(workspace_id);
create index if not exists idx_users_auth_user_id on users(auth_user_id);

-- -----------------------------
-- Vendors
-- -----------------------------
create table if not exists vendors (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  full_name text,
  trade text,
  phone text,
  email text,
  is_active boolean not null default true,
  rating numeric,
  dispatch_priority integer,
  base_address text,
  base_lat double precision,
  base_lng double precision,
  service_radius_miles numeric default 20,
  auto_approve_cap numeric,
  automated_calls boolean not null default false,
  contact_policy text not null default 'business_hours'
    check (contact_policy in ('business_hours', '24_7')),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_vendors_workspace_trade_active
  on vendors(workspace_id, trade, is_active);

create index if not exists idx_vendors_workspace_active
  on vendors(workspace_id, is_active);

-- -----------------------------
-- Pending accounts
-- -----------------------------
create table if not exists pending_accounts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references workspaces(id) on delete set null,

  email text not null,
  full_name text,

  status text not null default 'pending', -- pending | approved | rejected
  auth_user_id uuid,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  approved_at timestamptz
);

create unique index if not exists pending_accounts_workspace_email_uniq
  on pending_accounts (workspace_id, lower(email))
  where workspace_id is not null;

create unique index if not exists pending_accounts_waitlist_email_uniq
  on pending_accounts (lower(email))
  where workspace_id is null;

create index if not exists idx_pending_accounts_status_created
  on pending_accounts (status, created_at);

-- -----------------------------
-- Properties
-- -----------------------------
create table if not exists properties (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  address text,
  zip_code text,
  lat double precision,
  lng double precision,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_properties_workspace on properties(workspace_id);

-- -----------------------------
-- Units
-- -----------------------------
create table if not exists units (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references properties(id) on delete cascade,

  unit_label text not null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (property_id, unit_label)
);

create index if not exists idx_units_property on units(property_id);

-- -----------------------------
-- Occupancies
-- -----------------------------
create table if not exists occupancies (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  unit_id uuid not null references units(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,

  start_at timestamptz,
  end_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_occupancies_unit on occupancies(unit_id);
create index if not exists idx_occupancies_user on occupancies(user_id);
create index if not exists idx_occupancies_workspace on occupancies(workspace_id);
create index if not exists idx_occupancies_unit_end_start on occupancies(unit_id, end_at, start_at);

create unique index if not exists occupancies_unit_user_start_uniq
  on occupancies (unit_id, user_id, start_at);

-- -----------------------------
-- Work orders
-- -----------------------------
create table if not exists work_orders (
  id uuid primary key default gen_random_uuid(),

  property_id uuid not null references properties(id) on delete restrict,
  unit_id uuid references units(id) on delete set null,

  reported_by_user_id uuid references users(id) on delete set null,

  title text,
  description text,

  priority text, -- low | med | high | urgent
  status text not null default 'new', -- new | triaged | in_progress | waiting | done | canceled

  issue_category text,
  severity text,
  needs_more_info boolean,
  dispatch_recommendation text,
  likely_trade text,
  summary text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_work_orders_property_created
  on work_orders (property_id, created_at desc);

create index if not exists idx_work_orders_unit_created
  on work_orders (unit_id, created_at desc);

create index if not exists idx_work_orders_reported_by
  on work_orders (reported_by_user_id);

-- -----------------------------
-- Work order dispatches
-- -----------------------------
create table if not exists work_order_dispatches (
  id uuid primary key default gen_random_uuid(),
  work_order_id uuid not null references work_orders(id) on delete cascade,
  vendor_id uuid not null references vendors(id) on delete restrict,
  status text not null default 'recommended', -- recommended | assigned | contacted | accepted | declined | completed | canceled
  scheduled_at timestamptz,
  notes text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  constraint work_order_dispatches_status_chk
    check (status in ('recommended', 'assigned', 'contacted', 'accepted', 'declined', 'completed', 'canceled'))
);

create index if not exists idx_work_order_dispatches_work_order_created
  on work_order_dispatches (work_order_id, created_at desc);

create index if not exists idx_work_order_dispatches_vendor_created
  on work_order_dispatches (vendor_id, created_at desc);

-- -----------------------------
-- Conversations
-- -----------------------------
create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  work_order_id uuid not null references work_orders(id) on delete cascade,

  party_type text not null, -- resident | vendor | internal
  party_user_id uuid references users(id) on delete set null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_conversations_work_order
  on conversations(work_order_id);

-- -----------------------------
-- Messages
-- -----------------------------
create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  work_order_id uuid not null references work_orders(id) on delete cascade,

  direction text not null, -- inbound | outbound
  channel text not null,   -- sms | call | email | app

  sender_user_id uuid references users(id) on delete set null,
  recipient_user_id uuid references users(id) on delete set null,

  body text,
  raw_payload jsonb,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_messages_work_order_created
  on messages(work_order_id, created_at);

create index if not exists idx_messages_conversation_created
  on messages(conversation_id, created_at);

create index if not exists idx_messages_raw_payload_gin
  on messages using gin (raw_payload);

-- -----------------------------
-- Media assets
-- -----------------------------
create table if not exists media_assets (
  id uuid primary key default gen_random_uuid(),

  work_order_id uuid not null references work_orders(id) on delete cascade,
  message_id uuid references messages(id) on delete set null,
  uploaded_by_user_id uuid references users(id) on delete set null,

  media_type text not null default 'image',
  file_name text,
  content_type text,
  byte_size integer,
  width integer,
  height integer,

  storage_path text not null,
  public_url text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_media_assets_work_order_created
  on media_assets(work_order_id, created_at);

create index if not exists idx_media_assets_message_created
  on media_assets(message_id, created_at);

-- ============================================================
-- 2) LEGACY-SAFE COLUMN BACKFILL (critical!)
-- If tables existed already, CREATE TABLE IF NOT EXISTS won't add columns.
-- These ALTERs guarantee updated_at/created_at exist before functions reference them.
-- ============================================================

alter table workspaces       add column if not exists created_at timestamptz not null default now();
alter table workspaces       add column if not exists updated_at timestamptz not null default now();

alter table users            add column if not exists created_at timestamptz not null default now();
alter table users            add column if not exists updated_at timestamptz not null default now();

alter table vendors          add column if not exists created_at timestamptz not null default now();
alter table vendors          add column if not exists updated_at timestamptz not null default now();
alter table vendors          add column if not exists full_name text;
alter table vendors          add column if not exists trade text;
alter table vendors          add column if not exists phone text;
alter table vendors          add column if not exists email text;
alter table vendors          add column if not exists is_active boolean not null default true;
alter table vendors          add column if not exists rating numeric;
alter table vendors          add column if not exists dispatch_priority integer;
alter table vendors          add column if not exists base_address text;
alter table vendors          add column if not exists base_lat double precision;
alter table vendors          add column if not exists base_lng double precision;
alter table vendors          add column if not exists service_radius_miles numeric;
alter table vendors          add column if not exists auto_approve_cap numeric;
alter table vendors          add column if not exists automated_calls boolean not null default false;
alter table vendors          add column if not exists contact_policy text not null default 'business_hours';
alter table vendors          drop constraint if exists vendors_contact_policy_check;
alter table vendors          add constraint vendors_contact_policy_check
  check (contact_policy in ('business_hours', '24_7'));
alter table vendors          alter column service_radius_miles set default 20;

alter table pending_accounts add column if not exists created_at timestamptz not null default now();
alter table pending_accounts add column if not exists updated_at timestamptz not null default now();

alter table properties       add column if not exists created_at timestamptz not null default now();
alter table properties       add column if not exists updated_at timestamptz not null default now();
alter table properties       add column if not exists zip_code text;
alter table properties       add column if not exists lat double precision;
alter table properties       add column if not exists lng double precision;

drop table if exists vendor_service_areas;

alter table units            add column if not exists created_at timestamptz not null default now();
alter table units            add column if not exists updated_at timestamptz not null default now();

alter table occupancies      add column if not exists created_at timestamptz not null default now();
alter table occupancies      add column if not exists updated_at timestamptz not null default now();

alter table work_orders      add column if not exists created_at timestamptz not null default now();
alter table work_orders      add column if not exists updated_at timestamptz not null default now();
alter table work_orders      add column if not exists issue_category text;
alter table work_orders      add column if not exists severity text;
alter table work_orders      add column if not exists needs_more_info boolean;
alter table work_orders      add column if not exists dispatch_recommendation text;
alter table work_orders      add column if not exists likely_trade text;
alter table work_orders      add column if not exists summary text;

alter table work_order_dispatches add column if not exists created_at timestamptz not null default now();
alter table work_order_dispatches add column if not exists updated_at timestamptz not null default now();
alter table work_order_dispatches add column if not exists scheduled_at timestamptz;
alter table work_order_dispatches add column if not exists notes text;

alter table conversations    add column if not exists created_at timestamptz not null default now();
alter table conversations    add column if not exists updated_at timestamptz not null default now();

alter table messages         add column if not exists created_at timestamptz not null default now();
alter table messages         add column if not exists updated_at timestamptz not null default now();

alter table media_assets     add column if not exists created_at timestamptz not null default now();
alter table media_assets     add column if not exists updated_at timestamptz not null default now();

-- ============================================================
-- 3) updated_at trigger function + triggers
-- - bumps updated_at ONLY if non-updated_at fields actually changed
-- ============================================================

create or replace function set_updated_at()
returns trigger
language plpgsql
as $$
begin
  if (to_jsonb(new) - 'updated_at') is distinct from (to_jsonb(old) - 'updated_at') then
    new.updated_at = now();
  end if;
  return new;
end;
$$;

do $$
declare
  t text;
begin
  foreach t in array array[
    'workspaces',
    'users',
    'vendors',
    'pending_accounts',
    'properties',
    'units',
    'occupancies',
    'work_orders',
    'work_order_dispatches',
    'conversations',
    'messages',
    'media_assets'
  ]
  loop
    execute format('drop trigger if exists set_%s_updated_at on %I;', t, t);
    execute format(
      'create trigger set_%s_updated_at
       before update on %I
       for each row execute function set_updated_at();',
      t, t
    );
  end loop;
end;
$$;

-- ============================================================
-- 4) Dashboard helper function (DROP + recreate avoids 42P13)
-- ============================================================

drop function if exists list_units_table_rows(uuid);

create function list_units_table_rows(workspace_id uuid)
returns table (
  property_id uuid,
  property_address text,
  property_zip_code text,
  property_updated_at timestamptz,

  unit_id uuid,
  unit_label text,

  tenant_user_id uuid,
  tenant_full_name text,
  tenant_email text,
  tenant_phone text,

  manager_name text,
  maintenance_requests text,

  work_order_updated_at timestamptz,   -- <-- last activity from work orders
  has_open_request boolean,
  latest_work_orders jsonb
)
language sql
stable
as $$
  select
    p.id as property_id,
    p.address as property_address,
    p.zip_code as property_zip_code,
    p.updated_at as property_updated_at,

    u.id as unit_id,
    u.unit_label as unit_label,

    tenant.user_id as tenant_user_id,
    tenant.full_name as tenant_full_name,
    tenant.email as tenant_email,
    tenant.phone as tenant_phone,

    coalesce(manager_reporter.full_name, default_manager.full_name, 'Unassigned') as manager_name,
    coalesce(wo_agg.maintenance_requests, '') as maintenance_requests,

    wo_agg.last_updated_at as work_order_updated_at,
    coalesce(wo_agg.has_open_request, false) as has_open_request,
    wo_agg.latest_work_orders as latest_work_orders
  from properties p
  join units u on u.property_id = p.id

  left join lateral (
    select o.user_id, usr.full_name, usr.email, usr.phone
    from occupancies o
    join users usr on usr.id = o.user_id
    where o.unit_id = u.id
    order by (o.end_at is null) desc, o.start_at desc nulls last, o.created_at desc
    limit 1
  ) tenant on true

  left join lateral (
    with scoped as (
      select wo.*
      from work_orders wo
      where (
        wo.unit_id = u.id
        or (
          wo.unit_id is null
          and wo.property_id = p.id
          and not exists (
            select 1 from work_orders w2 where w2.unit_id = u.id
          )
        )
      )
    ),
    ordered as (
      select * from scoped
      order by coalesce(updated_at, created_at) desc
    ),
    limited as (
      select * from ordered limit 3
    )
    select
      max(coalesce(updated_at, created_at)) as last_updated_at,
      string_agg(title, ' ; ' order by coalesce(updated_at, created_at) desc) as maintenance_requests,
      bool_or(status not in ('done', 'canceled')) as has_open_request,
      (
        select jsonb_agg(
          jsonb_build_object(
            'id', id,
            'title', title,
            'status', status,
            'updated_at', updated_at,
            'created_at', created_at,
            'reported_by_user_id', reported_by_user_id
          )
          order by coalesce(updated_at, created_at) desc
        )
        from limited
      ) as latest_work_orders,
      (select reported_by_user_id from ordered limit 1) as latest_reported_by_user_id
    from ordered
  ) wo_agg on true

  left join lateral (
    select u.full_name
    from users u
    where u.id = wo_agg.latest_reported_by_user_id
      and u.role in ('staff', 'pm_admin')
    limit 1
  ) manager_reporter on true

  left join lateral (
    select u.full_name
    from users u
    where u.workspace_id = p.workspace_id
      and u.role in ('staff', 'pm_admin')
    order by u.created_at asc
    limit 1
  ) default_manager on true

  where p.workspace_id = workspace_id
  order by p.created_at asc, u.unit_label asc;
$$;
