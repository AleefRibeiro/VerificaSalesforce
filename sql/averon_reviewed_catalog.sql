-- ADDITIVE REVIEW DRAFT ONLY; after averon_private_pilot.sql in a dedicated Averon project.
-- No remote execution, users, moderator, company, workspace or private-report import is seeded.
begin;
create table averon_private.catalog_moderator (
  singleton boolean primary key default true check (singleton),
  user_id uuid not null unique references auth.users(id)
);
create table averon_private.catalog_companies (
  workspace_id uuid not null references averon_private.workspaces(id),
  domain text not null check (length(domain) between 3 and 253),
  company_name text not null check (length(company_name) between 2 and 200),
  name_key text not null,
  status text not null check (status in ('Uso confirmado','Indício','Não confirmado')),
  qualification text not null check (length(qualification) between 20 and 1200),
  reviewed_at timestamptz not null,
  evidence jsonb not null check (jsonb_typeof(evidence)='array' and jsonb_array_length(evidence) between 1 and 20),
  primary key(workspace_id,domain)
);
create index catalog_company_name_prefix on averon_private.catalog_companies(workspace_id,name_key text_pattern_ops,domain);
create index catalog_domain_prefix on averon_private.catalog_companies(workspace_id,domain text_pattern_ops);
create table averon_private.catalog_contributions (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references averon_private.workspaces(id),
  user_id uuid not null references auth.users(id) on delete cascade,
  domain text not null check (length(domain) between 3 and 253),
  company_name text not null check (length(company_name) between 2 and 200),
  action text not null check (action in ('confirmar','contestar')),
  reason text not null check (length(reason) between 20 and 1200),
  source_url text not null check (length(source_url) <= 2048 and source_url ~ '^https://[^/?#@:]+(/[^?#]*)?$'),
  source_hash bytea not null check (octet_length(source_hash)=32),
  published_at timestamptz,
  submitted_at timestamptz not null default clock_timestamp(),
  state text not null default 'pendente' check (state in ('pendente','aprovada','rejeitada')),
  reviewed_at timestamptz,
  review_note text,
  reviewed_by uuid references auth.users(id)
);
create index catalog_own_contributions on averon_private.catalog_contributions(workspace_id,user_id,submitted_at desc,id desc);
create index catalog_pending_review on averon_private.catalog_contributions(workspace_id,submitted_at,id) where state='pendente';
create unique index catalog_pending_duplicate on averon_private.catalog_contributions(workspace_id,user_id,domain,action,source_hash) where state='pendente';
alter table averon_private.catalog_moderator enable row level security;
alter table averon_private.catalog_moderator force row level security;
alter table averon_private.catalog_companies enable row level security;
alter table averon_private.catalog_companies force row level security;
alter table averon_private.catalog_contributions enable row level security;
alter table averon_private.catalog_contributions force row level security;
revoke all on averon_private.catalog_moderator, averon_private.catalog_companies, averon_private.catalog_contributions from public,anon,authenticated;
grant select on averon_private.catalog_moderator to service_role;
grant select,insert,update on averon_private.catalog_companies,averon_private.catalog_contributions to service_role;

-- Only the trusted singleton row adds the moderation permission. No JWT metadata/role.
create or replace function averon_private.verify_research_session(p_user_id uuid,p_session_id uuid)
returns jsonb language sql stable security invoker set search_path='' as $$
  select jsonb_build_object('workspace',m.workspace_id,'permissions',
    case when exists(select 1 from averon_private.catalog_moderator a where a.user_id=m.user_id)
      then m.permissions || array['catalog:moderate']::text[] else m.permissions end)
  from averon_private.memberships m join averon_private.workspaces w on w.id=m.workspace_id and w.active
  join auth.sessions s on s.user_id=m.user_id and s.id=p_session_id
  where m.user_id=p_user_id and m.active and 'research:read'=any(m.permissions)
    and (s.not_after is null or s.not_after>statement_timestamp());
$$;

create function averon_private.search_public_catalog(p_workspace uuid,p_query text,p_offset integer)
returns jsonb language sql stable security invoker set search_path='' as $$
  with matches as (
    select c.*,row_number() over(order by c.domain) as position
    from averon_private.catalog_companies c join averon_private.workspaces w on w.id=c.workspace_id and w.active
    where c.workspace_id=p_workspace and length(p_query)<=200 and p_offset between 0 and 10000
      and (p_query='' or c.domain=p_query
        or c.domain like replace(replace(replace(p_query,'\','\\'),'%','\%'),'_','\_') || '%' escape '\'
        or c.name_key like replace(replace(replace(p_query,'\','\\'),'%','\%'),'_','\_') || '%' escape '\')
    order by c.domain limit 21 offset p_offset
  ) select jsonb_build_object('items',coalesce(jsonb_agg(jsonb_build_object(
    'domain',domain,'company_name',company_name,'status',status,'qualification',qualification,
    'reviewed_at',reviewed_at,'evidence',jsonb_build_array(evidence->(jsonb_array_length(evidence)-1))) order by position)
    filter(where position<=p_offset+20),'[]'::jsonb),
    'next_offset',case when count(*)>20 and p_offset<=9980 then p_offset+20 else null end) from matches;
$$;

create function averon_private.get_public_company(p_workspace uuid,p_domain text)
returns jsonb language sql stable security invoker set search_path='' as $$
  select jsonb_build_object('domain',c.domain,'company_name',c.company_name,'status',c.status,
    'qualification',c.qualification,'reviewed_at',c.reviewed_at,'evidence',c.evidence)
  from averon_private.catalog_companies c join averon_private.workspaces w on w.id=c.workspace_id and w.active
  where c.workspace_id=p_workspace and c.domain=p_domain;
$$;

create function averon_private.submit_catalog_contribution(p_workspace uuid,p_user_id uuid,p_contribution jsonb)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare inserted averon_private.catalog_contributions; stamp timestamptz;
begin
  if p_contribution is null or jsonb_typeof(p_contribution)<>'object' or pg_column_size(p_contribution)>8192
    or not exists(select 1 from averon_private.memberships m join averon_private.workspaces w on w.id=m.workspace_id
      where m.workspace_id=p_workspace and m.user_id=p_user_id and m.active and w.active
        and 'research:write'=any(m.permissions)) then return null; end if;
  perform pg_advisory_xact_lock(hashtextextended('catalog-submit:'||p_workspace::text||':'||p_user_id::text,0));
  stamp:=clock_timestamp();
  if (select count(*) from averon_private.catalog_contributions where workspace_id=p_workspace and user_id=p_user_id and state='pendente')>=20
    or (select count(*) from averon_private.catalog_contributions where workspace_id=p_workspace and user_id=p_user_id and submitted_at>stamp-interval '60 seconds')>=10
    or exists(select 1 from averon_private.catalog_contributions where workspace_id=p_workspace and user_id=p_user_id
      and domain=p_contribution->>'domain' and action=p_contribution->>'action' and source_url=p_contribution->>'source_url' and state='pendente')
    then return jsonb_build_object('conflict',true); end if;
  if (p_contribution->>'published_at')::timestamptz>stamp then return jsonb_build_object('conflict',true); end if;
  insert into averon_private.catalog_contributions(workspace_id,user_id,domain,company_name,action,reason,source_url,source_hash,published_at,submitted_at)
    values(p_workspace,p_user_id,p_contribution->>'domain',p_contribution->>'company_name',p_contribution->>'action',
      p_contribution->>'reason',p_contribution->>'source_url',sha256(convert_to(p_contribution->>'source_url','UTF8')),(p_contribution->>'published_at')::timestamptz,stamp) returning * into inserted;
  return to_jsonb(inserted)-array['workspace_id','user_id','reviewed_by','source_hash'];
end;
$$;

create function averon_private.list_catalog_contributions(p_workspace uuid,p_user_id uuid,p_offset integer,p_moderation boolean)
returns jsonb language plpgsql stable security invoker set search_path='' as $$
declare result jsonb;
begin
  if p_offset not between 0 and 10000 or p_moderation is null
    or not exists(select 1 from averon_private.memberships m join averon_private.workspaces w on w.id=m.workspace_id
      where m.workspace_id=p_workspace and m.user_id=p_user_id and m.active and w.active and 'research:read'=any(m.permissions))
    or (p_moderation and not exists(select 1 from averon_private.catalog_moderator a where a.user_id=p_user_id)) then return null; end if;
  with page as (
    select c.*,row_number() over(order by c.submitted_at desc,c.id desc) as position
    from averon_private.catalog_contributions c where c.workspace_id=p_workspace
      and (case when p_moderation then c.state='pendente' else c.user_id=p_user_id end)
    order by c.submitted_at desc,c.id desc limit 21 offset p_offset
  ) select jsonb_build_object('items',coalesce(jsonb_agg(to_jsonb(page)-array['workspace_id','user_id','reviewed_by','position','source_hash'] order by position)
    filter(where position<=p_offset+20),'[]'::jsonb),'next_offset',case when count(*)>20 and p_offset<=9980 then p_offset+20 else null end)
    into result from page;
  return result;
end;
$$;

create function averon_private.review_catalog_contribution(p_workspace uuid,p_user_id uuid,p_id uuid,p_review jsonb)
returns jsonb language plpgsql volatile security invoker set search_path='' as $$
declare entry averon_private.catalog_contributions; stamp timestamptz; evidence_list jsonb; new_evidence jsonb; name_search text;
begin
  if not exists(select 1 from averon_private.catalog_moderator a join averon_private.memberships m on m.user_id=a.user_id
      join averon_private.workspaces w on w.id=m.workspace_id where a.user_id=p_user_id and m.workspace_id=p_workspace
        and m.active and w.active and 'research:write'=any(m.permissions)) then return null; end if;
  select * into entry from averon_private.catalog_contributions where id=p_id and workspace_id=p_workspace for update;
  if not found or entry.state<>'pendente' then return jsonb_build_object('conflict',true); end if;
  if p_review is null or jsonb_typeof(p_review)<>'object' or p_review->>'decision' is null or p_review->>'qualification' is null
    or p_review->>'decision' not in ('aprovar','rejeitar') or length(p_review->>'qualification') not between 20 and 1200
    or (p_review->>'decision'='aprovar' and (p_review->>'status' is null or p_review->>'status' not in ('Uso confirmado','Indício','Não confirmado')))
    or (p_review->>'decision'='rejeitar' and (p_review->>'status' is not null or (p_review->>'direct_evidence')::boolean is true))
    then return jsonb_build_object('conflict',true); end if;
  if p_review->>'status'='Uso confirmado' and (entry.action<>'confirmar' or entry.published_at is null
    or (p_review->>'direct_evidence')::boolean is distinct from true) then return jsonb_build_object('conflict',true); end if;
  stamp:=clock_timestamp();
  if p_review->>'decision'='aprovar' then
    perform pg_advisory_xact_lock(hashtextextended('catalog-company:'||p_workspace::text||':'||entry.domain,0));
    select evidence into evidence_list from averon_private.catalog_companies where workspace_id=p_workspace and domain=entry.domain;
    new_evidence:=jsonb_build_object('source_url',entry.source_url,'published_at',entry.published_at,'reviewed_at',stamp,
      'qualification',p_review->>'qualification','action',entry.action);
    evidence_list:=coalesce(evidence_list,'[]'::jsonb)||jsonb_build_array(new_evidence);
    select jsonb_agg(value order by ordinality) into evidence_list from jsonb_array_elements(evidence_list) with ordinality
      where ordinality>greatest(jsonb_array_length(evidence_list)-20,0);
    name_search:=translate(lower(entry.company_name),'áàâãäéèêëíìîïóòôõöúùûüçñ','aaaaaeeeeiiiiooooouuuucn');
    insert into averon_private.catalog_companies(workspace_id,domain,company_name,name_key,status,qualification,reviewed_at,evidence)
      values(p_workspace,entry.domain,entry.company_name,name_search,p_review->>'status',p_review->>'qualification',stamp,evidence_list)
      on conflict(workspace_id,domain) do update set company_name=excluded.company_name,name_key=excluded.name_key,status=excluded.status,
        qualification=excluded.qualification,reviewed_at=excluded.reviewed_at,evidence=excluded.evidence;
  end if;
  update averon_private.catalog_contributions set state=case when p_review->>'decision'='aprovar' then 'aprovada' else 'rejeitada' end,
    reviewed_at=stamp,review_note=p_review->>'qualification',reviewed_by=p_user_id where id=p_id returning * into entry;
  return to_jsonb(entry)-array['workspace_id','user_id','reviewed_by','source_hash'];
end;
$$;
revoke all on function averon_private.search_public_catalog(uuid,text,integer),averon_private.get_public_company(uuid,text),
  averon_private.submit_catalog_contribution(uuid,uuid,jsonb),averon_private.list_catalog_contributions(uuid,uuid,integer,boolean),
  averon_private.review_catalog_contribution(uuid,uuid,uuid,jsonb) from public,anon,authenticated;
grant execute on function averon_private.search_public_catalog(uuid,text,integer),averon_private.get_public_company(uuid,text),
  averon_private.submit_catalog_contribution(uuid,uuid,jsonb),averon_private.list_catalog_contributions(uuid,uuid,integer,boolean),
  averon_private.review_catalog_contribution(uuid,uuid,uuid,jsonb) to service_role;
commit;
