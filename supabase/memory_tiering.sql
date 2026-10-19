create extension if not exists vector with schema extensions;

create table if not exists public.memory_records (
    owner_id uuid not null default auth.uid() references auth.users(id) on delete cascade,
    kind text not null check (kind in ('learning', 'security', 'thm', 'profile')),
    tier text not null check (tier in ('hot', 'warm', 'cold')),
    record_key text not null,
    position integer not null default 0,
    data jsonb not null,
    content text not null default '',
    tags text[] not null default '{}',
    importance integer not null default 5 check (importance between 1 and 10),
    embedding extensions.vector(1536),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_accessed_at timestamptz,
    primary key (owner_id, kind, record_key)
);

create index if not exists memory_records_owner_kind_position_idx
    on public.memory_records (owner_id, kind, position);
create index if not exists memory_records_owner_tier_updated_idx
    on public.memory_records (owner_id, tier, updated_at desc);
create index if not exists memory_records_owner_tier_idle_idx
    on public.memory_records (owner_id, tier, importance, last_accessed_at);
create index if not exists memory_records_tags_idx
    on public.memory_records using gin (tags);
create index if not exists memory_records_embedding_idx
    on public.memory_records using hnsw (embedding extensions.vector_cosine_ops)
    where embedding is not null;

alter table public.memory_records enable row level security;

drop policy if exists "Users can read their own JARVIS memories" on public.memory_records;
create policy "Users can read their own JARVIS memories"
    on public.memory_records for select to authenticated
    using (owner_id = (select auth.uid()));

drop policy if exists "Users can insert their own JARVIS memories" on public.memory_records;
create policy "Users can insert their own JARVIS memories"
    on public.memory_records for insert to authenticated
    with check (owner_id = (select auth.uid()));

drop policy if exists "Users can update their own JARVIS memories" on public.memory_records;
create policy "Users can update their own JARVIS memories"
    on public.memory_records for update to authenticated
    using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

drop policy if exists "Users can delete their own JARVIS memories" on public.memory_records;
create policy "Users can delete their own JARVIS memories"
    on public.memory_records for delete to authenticated
    using (owner_id = (select auth.uid()));

grant select, insert, update, delete on public.memory_records to authenticated;
revoke all on public.memory_records from anon;

-- Warm/hot の種別スナップショットを置き換える。Cold 層 (生ログのアーカイブ) は残す。
create or replace function public.replace_memory_kind(p_kind text, p_records jsonb)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;
    if p_kind not in ('learning', 'security', 'thm', 'profile') then
        raise exception 'Unsupported memory kind';
    end if;
    if jsonb_typeof(p_records) <> 'array' then
        raise exception 'Memory records must be a JSON array';
    end if;

    delete from public.memory_records
    where owner_id = auth.uid() and kind = p_kind and tier <> 'cold';

    insert into public.memory_records (
        owner_id, kind, tier, record_key, position, data, content, tags, importance, embedding
    )
    select
        auth.uid(),
        p_kind,
        item ->> 'tier',
        item ->> 'record_key',
        (item ->> 'position')::integer,
        item -> 'data',
        coalesce(item ->> 'content', ''),
        array(select jsonb_array_elements_text(coalesce(item -> 'tags', '[]'::jsonb))),
        coalesce((item ->> 'importance')::integer, 5),
        (item ->> 'embedding')::extensions.vector(1536)
    from jsonb_array_elements(p_records) as records(item);
end;
$$;

create or replace function public.import_legacy_memories(p_records jsonb)
returns integer
language plpgsql
security invoker
set search_path = ''
as $$
declare
    imported_count integer;
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;
    if jsonb_typeof(p_records) <> 'array' then
        raise exception 'Memory records must be a JSON array';
    end if;
    if exists (
        select 1 from public.memory_records where owner_id = auth.uid()
    ) then
        raise exception 'Supabase already contains memories; legacy import was not applied';
    end if;
    if exists (
        select 1
        from jsonb_array_elements(p_records) as records(item)
        where item ->> 'kind' not in ('learning', 'security', 'thm', 'profile')
    ) then
        raise exception 'Unsupported memory kind in legacy import';
    end if;

    insert into public.memory_records (
        owner_id, kind, tier, record_key, position, data, content, tags, importance, embedding
    )
    select
        auth.uid(),
        item ->> 'kind',
        item ->> 'tier',
        item ->> 'record_key',
        (item ->> 'position')::integer,
        item -> 'data',
        coalesce(item ->> 'content', ''),
        array(select jsonb_array_elements_text(coalesce(item -> 'tags', '[]'::jsonb))),
        coalesce((item ->> 'importance')::integer, 5),
        (item ->> 'embedding')::extensions.vector(1536)
    from jsonb_array_elements(p_records) as records(item);

    get diagnostics imported_count = row_count;
    return imported_count;
end;
$$;

-- Cold 層 (生の会話アーカイブ) を1件保存する。同じ record_key は上書き。
create or replace function public.add_cold_memory(p_record jsonb)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;
    if coalesce(p_record ->> 'tier', 'cold') <> 'cold' then
        raise exception 'add_cold_memory only accepts cold-tier records';
    end if;
    if (p_record ->> 'kind') not in ('learning', 'security', 'thm', 'profile') then
        raise exception 'Unsupported memory kind';
    end if;

    insert into public.memory_records (
        owner_id, kind, tier, record_key, position, data, content, tags, importance,
        embedding, last_accessed_at
    )
    values (
        auth.uid(),
        p_record ->> 'kind',
        'cold',
        p_record ->> 'record_key',
        coalesce((p_record ->> 'position')::integer, 0),
        p_record -> 'data',
        coalesce(p_record ->> 'content', ''),
        array(select jsonb_array_elements_text(coalesce(p_record -> 'tags', '[]'::jsonb))),
        coalesce((p_record ->> 'importance')::integer, 3),
        (p_record ->> 'embedding')::extensions.vector(1536),
        now()
    )
    on conflict (owner_id, kind, record_key) do update
        set data = excluded.data,
            content = excluded.content,
            tags = excluded.tags,
            importance = excluded.importance,
            embedding = excluded.embedding,
            tier = 'cold',
            updated_at = now(),
            last_accessed_at = now();
end;
$$;

-- 忘却・減衰: 長期間アクセスされていない低優先度の Cold 記憶だけを整理・削除する。
create or replace function public.prune_memories(
    p_max_idle_days integer default 90,
    p_min_importance integer default 4
)
returns integer
language plpgsql
volatile
security invoker
set search_path = ''
as $$
declare
    pruned_count integer;
begin
    if auth.uid() is null then
        raise exception 'Authentication required';
    end if;

    delete from public.memory_records as m
    where m.owner_id = auth.uid()
      and m.tier = 'cold'
      and m.importance <= greatest(1, least(coalesce(p_min_importance, 4), 10))
      and coalesce(m.last_accessed_at, m.created_at)
          < now() - make_interval(days => greatest(1, coalesce(p_max_idle_days, 90)));

    get diagnostics pruned_count = row_count;
    return pruned_count;
end;
$$;

drop function if exists public.search_memory_records(text, integer);
drop function if exists public.search_memory_records(text, integer, extensions.vector);

-- tier を指定して意味検索し、ヒットした記憶の last_accessed_at を更新する (減衰の指標)。
create or replace function public.search_memory_records(
    p_query text,
    p_limit integer default 6,
    p_query_embedding extensions.vector(1536) default null,
    p_tier text default 'warm'
)
returns table (kind text, tier text, data jsonb, rank real)
language plpgsql
volatile
security invoker
set search_path = ''
as $$
begin
    return query
    with ranked as (
        select
            memory.owner_id as owner_id,
            memory.kind as kind,
            memory.tier as tier,
            memory.record_key as record_key,
            memory.data as data,
            (
                case
                    when strpos(lower(memory.content), lower(p_query)) > 0 then 2.0
                    else 0.0
                end
                + case
                    when exists (
                        select 1 from unnest(memory.tags) as tags(tag)
                        where strpos(lower(tag), lower(p_query)) > 0
                    ) then 1.0
                    else 0.0
                end
                + ts_rank(
                    to_tsvector('simple', memory.content || ' ' || array_to_string(memory.tags, ' ')),
                    websearch_to_tsquery('simple', p_query)
                )
                + case
                    when p_query_embedding is not null and memory.embedding is not null
                        then 2.0 * (1.0 - (memory.embedding OPERATOR(extensions.<=>) p_query_embedding))
                    else 0.0
                end
                + memory.importance::real / 100
            )::real as rank
        from public.memory_records as memory
        where memory.owner_id = (select auth.uid())
          and memory.tier = p_tier
          and (
              strpos(lower(memory.content), lower(p_query)) > 0
              or exists (
                  select 1 from unnest(memory.tags) as tags(tag)
                  where strpos(lower(tag), lower(p_query)) > 0
              )
              or to_tsvector('simple', memory.content || ' ' || array_to_string(memory.tags, ' '))
                 @@ websearch_to_tsquery('simple', p_query)
              or (p_query_embedding is not null and memory.embedding is not null)
          )
        order by rank desc, memory.updated_at desc
        limit greatest(1, least(coalesce(p_limit, 6), 20))
    ),
    touched as (
        update public.memory_records as m
        set last_accessed_at = now()
        from ranked as r
        where m.owner_id = r.owner_id and m.kind = r.kind and m.record_key = r.record_key
        returning 1
    )
    select r.kind, r.tier, r.data, r.rank
    from ranked as r
    order by r.rank desc;
end;
$$;

revoke all on function public.replace_memory_kind(text, jsonb) from public;
revoke all on function public.import_legacy_memories(jsonb) from public;
revoke all on function public.add_cold_memory(jsonb) from public;
revoke all on function public.prune_memories(integer, integer) from public;
revoke all on function public.search_memory_records(text, integer, extensions.vector, text) from public;
grant execute on function public.replace_memory_kind(text, jsonb) to authenticated;
grant execute on function public.import_legacy_memories(jsonb) to authenticated;
grant execute on function public.add_cold_memory(jsonb) to authenticated;
grant execute on function public.prune_memories(integer, integer) to authenticated;
grant execute on function public.search_memory_records(text, integer, extensions.vector, text) to authenticated;
