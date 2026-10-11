-- Offer price on "Oui, dispo", trigger keyword, and link to the pilotage app.

-- Only messages containing this word start a chain (case and accents ignored),
-- so updates such as « Solution trouvée » are ignored. Empty = every message.
alter table public.mc_settings add column trigger_keyword text not null default 'chargement';

-- Carrier name written into the pilotage app when this rang books the transport.
-- Empty = the recipient's e-mail address.
alter table public.mc_levels add column carrier text not null default '';

alter table public.mc_chains
  add column price numeric,                              -- offer accepted (prix d'achat)
  add column booked_by text,                             -- recipient who accepted
  add column pilotage_ids text[] not null default '{}';  -- records(collection='transports').id

alter table public.mc_events add column price numeric;

-- Same as mc_apply_decision, plus the offer price. In cascade mode an
-- acceptance must come with a price; it is stored on the event and the chain.
create function public.mc_apply_decision(p_chain uuid, p_level int, p_actor text,
                                         p_action text, p_comment text, p_price numeric)
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
  if v_mode = 'cascade' and p_action = 'approve' and (p_price is null or p_price <= 0) then
    raise exception 'price required';
  end if;
  select count(*) into n_levels from public.mc_levels;

  select * into c from public.mc_chains where id = p_chain for update;
  if not found or c.status <> 'open' or c.level <> p_level then
    return query select c.status, c.level, false;
    return;
  end if;

  if exists (select 1 from public.mc_events e where e.chain_id = p_chain and e.level = p_level
             and e.kind = 'decision' and e.actor = p_actor) then
    return query select c.status, c.level, false;
    return;
  end if;
  insert into public.mc_events (chain_id, level, kind, actor, detail, price)
  values (p_chain, p_level, 'decision', p_actor,
          p_action || coalesce(nullif(': ' || coalesce(p_comment, ''), ': '), ''),
          case when p_action = 'approve' then p_price end);

  new_status := 'open';
  new_level := c.level;
  if v_mode = 'validation' then
    if p_action = 'reject' then new_status := 'rejected';
    elsif c.level + 1 >= n_levels then new_status := 'approved';
    else new_level := c.level + 1;
    end if;
  else
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
  update public.mc_chains set status = new_status, level = new_level, level_sent_at = now(),
    price = case when new_status = 'approved' and p_action = 'approve' then p_price else price end,
    booked_by = case when new_status = 'approved' and p_action = 'approve' then p_actor else booked_by end
  where id = p_chain;
  if new_status <> 'open' then
    insert into public.mc_events (chain_id, level, kind, detail)
    values (p_chain, new_level, 'closed', new_status);
  end if;
  return query select new_status, new_level, true;
end;
$$;

revoke execute on function public.mc_apply_decision(uuid, int, text, text, text, numeric)
  from public, anon, authenticated;

-- Update the pilotage transports linked to a chain. The status only moves
-- from a_valider (a status changed by hand in the app is never overwritten),
-- p_patch is merged into data, and p_note is appended to the notes.
create function public.mc_pilotage_update(p_ids text[], p_status text, p_patch jsonb, p_note text)
returns int
language sql
set search_path = public
as $$
  with u as (
    update public.records r set
      data = r.data || coalesce(p_patch, '{}'::jsonb) || jsonb_build_object(
        'statut', case when p_status is not null and r.data->>'statut' = 'a_valider' then p_status
                       else r.data->>'statut' end,
        'notes', concat_ws(' · ', nullif(r.data->>'notes', ''), nullif(p_note, '')),
        'updatedAt', to_jsonb(now())),
      updated_at = now(),
      updated_by = 'mail-chain'
    where r.collection = 'transports' and r.id = any(p_ids)
    returning 1
  )
  select count(*)::int from u;
$$;
revoke execute on function public.mc_pilotage_update from public, anon, authenticated;
