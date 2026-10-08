-- REVIEW DRAFT ONLY. Do not apply to GearheadEvents/Fiui or without separate approval.
-- Dedicated Averon project only. No user, credential, membership or workspace is seeded.
begin;
create schema averon_private;
revoke all on schema averon_private from public, anon, authenticated;
grant usage on schema averon_private to service_role;
alter default privileges in schema averon_private revoke execute on functions from public;

create table averon_private.workspaces (
  id uuid primary key,
  label text not null check (length(label) between 1 and 100),
  active boolean not null default false
);
create table averon_private.memberships (
  user_id uuid primary key references auth.users(id) on delete cascade,
  workspace_id uuid not null references averon_private.workspaces(id),
  active boolean not null default false,
  permissions text[] not null default '{}' check (
    permissions <@ array['research:read', 'research:write']::text[]
    and (not ('research:write' = any(permissions)) or 'research:read' = any(permissions))
  )
);
create index memberships_workspace on averon_private.memberships(workspace_id);
create table averon_private.reports (
  workspace_id uuid not null references averon_private.workspaces(id),
  user_id uuid not null references auth.users(id) on delete cascade,
  report_id uuid not null,
  cache_key text not null check (cache_key ~ '^[0-9a-f]{64}$'),
  payload jsonb not null check (jsonb_typeof(payload) = 'object' and pg_column_size(payload) <= 524288),
  expires_at timestamptz not null,
  saved_at timestamptz not null default clock_timestamp(),
  retained_until timestamptz not null default (clock_timestamp() + interval '30 days'),
  primary key (workspace_id, user_id, report_id)
);
create index reports_owner_cache on averon_private.reports(workspace_id, user_id, cache_key, saved_at desc);
create index reports_owner_history on averon_private.reports(workspace_id, user_id, saved_at desc, report_id desc);
create index reports_owner_retention on averon_private.reports(workspace_id, user_id, retained_until);
create table averon_private.quota_events (
  workspace_id uuid not null references averon_private.workspaces(id),
  charged_at timestamptz not null default clock_timestamp(),
  targets integer not null check (targets between 1 and 5)
);
create index quota_workspace_time on averon_private.quota_events(workspace_id, charged_at);

alter table averon_private.workspaces enable row level security;
alter table averon_private.workspaces force row level security;
alter table averon_private.memberships enable row level security;
alter table averon_private.memberships force row level security;
alter table averon_private.reports enable row level security;
alter table averon_private.reports force row level security;
alter table averon_private.quota_events enable row level security;
alter table averon_private.quota_events force row level security;
-- No public/authenticated policies or grants; browser cannot read/write these tables.
revoke all on all tables in schema averon_private from public, anon, authenticated;
grant select on averon_private.workspaces, averon_private.memberships to service_role;
grant select, insert, update, delete on averon_private.reports to service_role;
grant select, insert, delete on averon_private.quota_events to service_role;
-- Minimum columns for immediate session-revocation checks; do not expose auth schema.
grant usage on schema auth to service_role;
grant select (id, user_id, not_after) on auth.sessions to service_role;

create function averon_private.verify_research_session(p_user_id uuid, p_session_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select jsonb_build_object('workspace', m.workspace_id, 'permissions', m.permissions)
  from averon_private.memberships m
  join averon_private.workspaces w on w.id = m.workspace_id and w.active
  join auth.sessions s on s.user_id = m.user_id and s.id = p_session_id
  where m.user_id = p_user_id and m.active
    and 'research:read' = any(m.permissions)
    and (s.not_after is null or s.not_after > statement_timestamp());
$$;

create function averon_private.charge_research_quota(p_workspace uuid, p_user_id uuid, p_count integer)
returns boolean language plpgsql volatile security invoker set search_path = '' as $$
declare used integer; stamp timestamptz;
begin
  if p_count is null or p_count not between 1 and 5 then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_workspace::text, 0));
  if not exists (select 1 from averon_private.memberships m join averon_private.workspaces w on w.id = m.workspace_id
    where m.workspace_id = p_workspace and m.user_id = p_user_id and m.active and w.active
      and 'research:write' = any(m.permissions)) then return false; end if;
  stamp := clock_timestamp();
  delete from averon_private.quota_events where workspace_id = p_workspace and charged_at <= stamp - interval '60 seconds';
  select coalesce(sum(targets), 0) into used from averon_private.quota_events where workspace_id = p_workspace;
  if used + p_count > 10 then return false; end if;
  insert into averon_private.quota_events(workspace_id, charged_at, targets) values (p_workspace, stamp, p_count);
  return true;
end;
$$;

-- Cache stays fresh for at most one hour; history survives separately for 30 days.
create function averon_private.get_cached_report(p_workspace uuid, p_user_id uuid, p_cache_key text)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select r.payload from averon_private.reports r
  join averon_private.workspaces w on w.id = r.workspace_id and w.active
  join averon_private.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.active
  where r.workspace_id = p_workspace and r.user_id = p_user_id and r.cache_key = p_cache_key
    and 'research:read' = any(m.permissions) and r.expires_at > statement_timestamp()
    and r.retained_until > statement_timestamp()
  order by r.saved_at desc, r.report_id desc limit 1;
$$;
create function averon_private.get_private_report(p_workspace uuid, p_user_id uuid, p_report_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select r.payload from averon_private.reports r
  join averon_private.workspaces w on w.id = r.workspace_id and w.active
  join averon_private.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.active
  where r.workspace_id = p_workspace and r.user_id = p_user_id and r.report_id = p_report_id
    and 'research:read' = any(m.permissions) and r.expires_at > statement_timestamp()
    and r.retained_until > statement_timestamp();
$$;

create function averon_private.get_history_report(p_workspace uuid, p_user_id uuid, p_report_id uuid)
returns jsonb language sql stable security invoker set search_path = '' as $$
  select r.payload from averon_private.reports r
  join averon_private.workspaces w on w.id = r.workspace_id and w.active
  join averon_private.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.active
  where r.workspace_id = p_workspace and r.user_id = p_user_id and r.report_id = p_report_id
    and 'research:read' = any(m.permissions) and r.retained_until > statement_timestamp();
$$;
create function averon_private.list_private_history(p_workspace uuid, p_user_id uuid, p_offset integer)
returns jsonb language sql stable security invoker set search_path = '' as $$
  with page as (
    select r.report_id, r.payload, r.saved_at, r.retained_until,
      row_number() over (order by r.saved_at desc, r.report_id desc) as position
    from averon_private.reports r
    join averon_private.workspaces w on w.id = r.workspace_id and w.active
    join averon_private.memberships m on m.workspace_id = r.workspace_id and m.user_id = r.user_id and m.active
    where r.workspace_id = p_workspace and r.user_id = p_user_id and p_offset between 0 and 127
      and 'research:read' = any(m.permissions) and r.retained_until > statement_timestamp()
    order by r.saved_at desc, r.report_id desc limit 21 offset greatest(coalesce(p_offset, 0), 0)
  )
  select jsonb_build_object('items', coalesce(jsonb_agg(jsonb_build_object(
    'id', report_id, 'domain', payload->'target'->>'domain', 'checked_at', payload->'checked_at',
    'saved_at', saved_at, 'retained_until', retained_until, 'status', payload->'conclusion'->>'status'
  ) order by position) filter (where position <= coalesce(p_offset, 0) + 20), '[]'::jsonb),
    'next_offset', case when count(*) > 20 then p_offset + 20 else null end) from page;
$$;

create function averon_private.save_private_report(p_workspace uuid, p_user_id uuid, p_cache_key text, p_report jsonb)
returns boolean language plpgsql volatile security invoker set search_path = '' as $$
declare expiry timestamptz; report_uuid uuid; stamp timestamptz;
begin
  if p_report is null or jsonb_typeof(p_report) <> 'object' or pg_column_size(p_report) > 524288
     or p_cache_key is null or p_cache_key !~ '^[0-9a-f]{64}$'
     or (p_report->>'synthetic') is distinct from 'false'
     or jsonb_path_exists(p_report, '$.** ? (@.synthetic == true)') then return false; end if;
  expiry := (p_report->>'expires_at')::timestamptz;
  report_uuid := (p_report->>'id')::uuid;
  if expiry is null or report_uuid is null then return false; end if;
  perform pg_advisory_xact_lock(hashtextextended(p_workspace::text || ':' || p_user_id::text, 0));
  stamp := clock_timestamp();
  if expiry <= stamp or expiry > stamp + interval '1 hour'
     or not exists (select 1 from averon_private.memberships m join averon_private.workspaces w on w.id = m.workspace_id
       where m.workspace_id = p_workspace and m.user_id = p_user_id and m.active and w.active
         and 'research:write' = any(m.permissions)) then return false; end if;
  delete from averon_private.reports where workspace_id = p_workspace and user_id = p_user_id and retained_until <= stamp;
  insert into averon_private.reports(workspace_id, user_id, report_id, cache_key, payload, expires_at, saved_at, retained_until)
  values (p_workspace, p_user_id, report_uuid, p_cache_key, p_report, expiry, stamp, stamp + interval '30 days')
  on conflict (workspace_id, user_id, report_id) do nothing;
  delete from averon_private.reports where workspace_id = p_workspace and user_id = p_user_id and report_id in (
    select report_id from averon_private.reports where workspace_id = p_workspace and user_id = p_user_id
    order by saved_at desc, report_id desc offset 128
  );
  return true;
end;
$$;

revoke all on all functions in schema averon_private from public, anon, authenticated;
grant execute on function averon_private.verify_research_session(uuid, uuid),
  averon_private.charge_research_quota(uuid, uuid, integer), averon_private.get_cached_report(uuid, uuid, text),
  averon_private.get_private_report(uuid, uuid, uuid), averon_private.save_private_report(uuid, uuid, text, jsonb),
  averon_private.list_private_history(uuid, uuid, integer), averon_private.get_history_report(uuid, uuid, uuid) to service_role;
commit;
