-- WhatsApp group message -> chain of e-mail requests, one list (rang) after another.
-- Tables are prefixed mc_ so they live next to other apps in the same project.
-- Every table has RLS on and no policy: only the Edge Functions (service role)
-- can read or write. Edit mc_settings and mc_levels in the Supabase Table Editor.

create table public.mc_settings (
  id int primary key default 1 check (id = 1),
  -- validation: Valider -> rang suivant (validée au dernier rang), Refuser -> close.
  -- cascade:    Accepter -> close (attribuée), Refuser -> rang suivant once every
  --             recipient of the rang has refused (personne au dernier rang).
  mode text not null default 'validation' check (mode in ('validation', 'cascade')),
  -- WhatsApp group ids (…@g.us) to watch. Empty = watch nothing; groups are
  -- still recorded in mc_seen_groups so the right id can be picked.
  groups text[] not null default '{}',
  -- Phone numbers allowed to start a request (06…, +33…). Empty = whole group.
  allowed_senders text[] not null default '{}',
  max_chains_per_hour int not null default 10,
  notify_on_close text[] not null default '{}',
  mail_from text not null default '',            -- e.g. Demandes <demandes@mon-domaine.fr>
  decision_page_url text not null default '',    -- Netlify page with the two buttons
  link_ttl_hours int not null default 168,
  webhook_token text not null default encode(extensions.gen_random_bytes(24), 'hex'),
  link_secret text not null default encode(extensions.gen_random_bytes(32), 'hex')
);
insert into public.mc_settings default values;

create table public.mc_levels (
  position int primary key check (position >= 0),  -- 0 = rang 1
  name text not null,
  recipients text[] not null check (cardinality(recipients) > 0)
);

create table public.mc_chains (
  id uuid primary key default gen_random_uuid(),
  wa_message_id text unique,
  grp text not null,
  sender text not null,
  text text not null,
  level int not null default 0,
  status text not null default 'open' check (status in ('open', 'approved', 'rejected')),
  created_at timestamptz not null default now(),
  level_sent_at timestamptz not null default now()
);

create table public.mc_events (
  id bigserial primary key,
  chain_id uuid references public.mc_chains (id) on delete cascade,
  at timestamptz not null default now(),
  level int not null default 0,
  kind text not null,          -- sent, send_failed, decision, closed
  actor text not null default '',
  detail text not null default ''
);
create index on public.mc_events (chain_id, at);

create table public.mc_seen_groups (
  group_id text primary key,
  name text not null default '',
  last_seen timestamptz not null default now()
);

alter table public.mc_settings enable row level security;
alter table public.mc_levels enable row level security;
alter table public.mc_chains enable row level security;
alter table public.mc_events enable row level security;
alter table public.mc_seen_groups enable row level security;

-- Apply one recipient's answer atomically. The row lock serialises concurrent
-- clicks, so a level is never skipped and a closed chain never reopens.
-- Returns the chain state after the answer and whether it moved.
create function public.mc_apply_decision(p_chain uuid, p_level int, p_actor text,
                                         p_action text, p_comment text default '')
returns table (status text, level int, moved boolean)
language plpgsql
set search_path = public
as $$
#variable_conflict use_column
declare
  v_mode text;
  n_levels int;
  c public.mc_chains;
  n_recipients int;
  n_refused int;
  new_status text;
  new_level int;
begin
  if p_action not in ('approve', 'reject') then
    raise exception 'invalid action %', p_action;
  end if;
  select mode into v_mode from public.mc_settings where id = 1;
  select count(*) into n_levels from public.mc_levels;

  select * into c from public.mc_chains where id = p_chain for update;
  if not found or c.status <> 'open' or c.level <> p_level then
    return query select c.status, c.level, false;
    return;
  end if;

  -- One answer per recipient and level.
  if exists (select 1 from public.mc_events e where e.chain_id = p_chain and e.level = p_level
             and e.kind = 'decision' and e.actor = p_actor) then
    return query select c.status, c.level, false;
    return;
  end if;
  insert into public.mc_events (chain_id, level, kind, actor, detail)
  values (p_chain, p_level, 'decision', p_actor,
          p_action || coalesce(nullif(': ' || p_comment, ': '), ''));

  new_status := 'open';
  new_level := c.level;
  if v_mode = 'validation' then
    if p_action = 'reject' then new_status := 'rejected';
    elsif c.level + 1 >= n_levels then new_status := 'approved';
    else new_level := c.level + 1;
    end if;
  else  -- cascade
    if p_action = 'approve' then new_status := 'approved';
    else
      select cardinality(recipients) into n_recipients from public.mc_levels where position = c.level;
      select count(distinct e.actor) into n_refused from public.mc_events e
        where e.chain_id = p_chain and e.level = c.level and e.kind = 'decision' and e.detail like 'reject%';
      if n_refused >= n_recipients then
        if c.level + 1 >= n_levels then new_status := 'rejected';
        else new_level := c.level + 1;
        end if;
      end if;
    end if;
  end if;

  if new_status = c.status and new_level = c.level then
    return query select c.status, c.level, false;
    return;
  end if;
  update public.mc_chains set status = new_status, level = new_level, level_sent_at = now()
  where id = p_chain;
  if new_status <> 'open' then
    insert into public.mc_events (chain_id, level, kind, detail)
    values (p_chain, new_level, 'closed', new_status);
  end if;
  return query select new_status, new_level, true;
end;
$$;

revoke execute on function public.mc_apply_decision from public, anon, authenticated;
