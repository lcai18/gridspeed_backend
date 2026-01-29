-- 0) UUID generator
create extension if not exists pgcrypto;

-- ------------------------------------------------------------
-- 1) Workspaces (SaaS "tenant") + users (PM staff + residents)
-- ------------------------------------------------------------

create table if not exists workspaces (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  -- app identity
  auth_user_id uuid unique, -- maps to Supabase Auth user id (sub). nullable for non-login contacts/residents.

  -- profile
  full_name text,
  phone text,
  email text,

  -- optional role typing (keep if useful; otherwise delete)
  role text not null default 'resident', -- pm_admin | staff | resident
  created_at timestamptz not null default now()
);

-- Make email unique *within a workspace* (case-insensitive), but allow NULL.
create unique index if not exists users_workspace_email_uniq
  on users (workspace_id, lower(email))
  where email is not null;

create index if not exists idx_users_workspace on users(workspace_id);
create index if not exists idx_users_auth_user_id on users(auth_user_id);

-- Pending accounts covers:
--  - waitlist approvals (workspace_id NULL until approved/created)
--  - invites to an existing workspace (workspace_id set)
create table if not exists pending_accounts (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid references workspaces(id) on delete set null,

  email text not null,
  full_name text,

  status text not null default 'pending', -- pending | approved | rejected
  auth_user_id uuid,                     -- Supabase Auth user id (once created/known)

  created_at timestamptz not null default now(),
  approved_at timestamptz
);

-- Uniqueness rules for pending accounts:
-- - If workspace_id is set (invite flow), prevent duplicate pending rows per workspace/email.
-- - If workspace_id is NULL (waitlist flow), prevent duplicate waitlist entries by email.
create unique index if not exists pending_accounts_workspace_email_uniq
  on pending_accounts (workspace_id, lower(email))
  where workspace_id is not null;

create unique index if not exists pending_accounts_waitlist_email_uniq
  on pending_accounts (lower(email))
  where workspace_id is null;

create index if not exists idx_pending_accounts_status_created
  on pending_accounts (status, created_at);

-- ------------------------------------------------------------
-- 2) Properties & units
-- ------------------------------------------------------------

create table if not exists properties (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  address text,
  created_at timestamptz not null default now()
);

create index if not exists idx_properties_workspace on properties(workspace_id);

create table if not exists units (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references properties(id) on delete cascade,

  unit_label text not null,
  created_at timestamptz not null default now(),

  unique (property_id, unit_label)
);

create index if not exists idx_units_property on units(property_id);

-- ------------------------------------------------------------
-- 3) Occupancies (optional but recommended)
-- Map "actual tenants/residents" to units. Add now to avoid later pain.
-- ------------------------------------------------------------

create table if not exists occupancies (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,

  unit_id uuid not null references units(id) on delete cascade,
  user_id uuid not null references users(id) on delete cascade,

  start_at timestamptz,
  end_at timestamptz,

  created_at timestamptz not null default now()
);

create index if not exists idx_occupancies_unit on occupancies(unit_id);
create index if not exists idx_occupancies_user on occupancies(user_id);
create index if not exists idx_occupancies_workspace on occupancies(workspace_id);

-- Optional guard: prevent exact duplicate rows (same unit/user/start)
create unique index if not exists occupancies_unit_user_start_uniq
  on occupancies (unit_id, user_id, start_at);

-- ------------------------------------------------------------
-- 4) Work orders
-- NOTE: workspace_id is intentionally NOT stored here to avoid mismatch.
-- You can derive workspace via property_id (properties.workspace_id).
-- ------------------------------------------------------------

create table if not exists work_orders (
  id uuid primary key default gen_random_uuid(),

  property_id uuid not null references properties(id) on delete restrict,
  unit_id uuid references units(id) on delete set null,

  reported_by_user_id uuid references users(id) on delete set null,

  title text,
  description text,

  priority text, -- low | med | high | urgent (enforce in app or via CHECK if you want)
  status text not null default 'new', -- new | triaged | in_progress | waiting | done | canceled

  created_at timestamptz not null default now()
);

create index if not exists idx_work_orders_property_created
  on work_orders (property_id, created_at desc);

create index if not exists idx_work_orders_unit_created
  on work_orders (unit_id, created_at desc);

create index if not exists idx_work_orders_reported_by
  on work_orders (reported_by_user_id);

-- ------------------------------------------------------------
-- 5) Conversations & messages
-- ------------------------------------------------------------

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  work_order_id uuid not null references work_orders(id) on delete cascade,

  party_type text not null, -- resident | vendor | internal
  party_user_id uuid references users(id) on delete set null,

  created_at timestamptz not null default now()
);

create index if not exists idx_conversations_work_order
  on conversations(work_order_id);

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

  created_at timestamptz not null default now()
);

create index if not exists idx_messages_work_order_created
  on messages(work_order_id, created_at);

create index if not exists idx_messages_conversation_created
  on messages(conversation_id, created_at);

-- Optional: speed for "find_message_by_external_id" pattern
-- (GIN on jsonb helps contains queries)
create index if not exists idx_messages_raw_payload_gin
  on messages using gin (raw_payload);

-- ------------------------------------------------------------
-- 6) Media assets (attachments)
-- ------------------------------------------------------------

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

  created_at timestamptz not null default now()
);

create index if not exists idx_media_assets_work_order_created
  on media_assets(work_order_id, created_at);

create index if not exists idx_media_assets_message_created
  on media_assets(message_id, created_at);