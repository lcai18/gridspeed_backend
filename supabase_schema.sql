-- 0) UUID generator
create extension if not exists pgcrypto;

-- 1) Tenancy
create table if not exists tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create table if not exists users (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  full_name text,
  phone text,
  email text,
  created_at timestamptz not null default now()
);

create table if not exists pending_accounts (
  id uuid primary key default gen_random_uuid(),
  email text not null,
  full_name text,
  status text not null default 'pending', -- pending | approved | rejected
  auth_user_id uuid,
  created_at timestamptz not null default now(),
  approved_at timestamptz,
  unique (email)
);

-- 2) Properties & units
create table if not exists properties (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  address text,
  created_at timestamptz not null default now()
);

create table if not exists units (
  id uuid primary key default gen_random_uuid(),
  property_id uuid not null references properties(id) on delete cascade,
  unit_label text not null,
  created_at timestamptz not null default now(),
  unique (property_id, unit_label)
);

-- 3) Work orders
create table if not exists work_orders (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references tenants(id) on delete cascade,
  property_id uuid not null references properties(id) on delete restrict,
  unit_id uuid references units(id) on delete set null,
  reported_by_user_id uuid references users(id) on delete set null,

  title text,
  description text,
  priority text, -- low/med/high/urgent
  status text not null default 'new',

  created_at timestamptz not null default now()
);

-- 4) Conversations & messages
create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  work_order_id uuid not null references work_orders(id) on delete cascade,

  party_type text not null, -- tenant | vendor | internal
  party_user_id uuid references users(id) on delete set null,

  created_at timestamptz not null default now()
);

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

-- 5) Helpful indexes (you’ll want these immediately)
create index if not exists idx_conversations_work_order on conversations(work_order_id);
create index if not exists idx_messages_work_order_created on messages(work_order_id, created_at);
create index if not exists idx_messages_conversation_created on messages(conversation_id, created_at);

-- 6) Images & media attachments
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
