-- Persistent ordering for files and virtual folders in the media catalog.

create table if not exists public.folder_orderings (
    folder_path text primary key,
    item_paths text[] not null default '{}'::text[],
    ordered_video_paths text[] not null default '{}'::text[],
    updated_at timestamptz not null default now(),
    constraint folder_orderings_path_not_empty check (folder_path is not null)
);

create index if not exists idx_folder_orderings_updated_at
    on public.folder_orderings(updated_at desc);

alter table public.folder_orderings enable row level security;

drop policy if exists "Authenticated users can read folder orderings" on public.folder_orderings;
create policy "Authenticated users can read folder orderings"
    on public.folder_orderings for select to authenticated using (true);

drop policy if exists "Admins can manage folder orderings" on public.folder_orderings;
create policy "Admins can manage folder orderings"
    on public.folder_orderings for all to authenticated
    using (public.is_admin())
    with check (public.is_admin());

grant select on public.folder_orderings to authenticated;
grant insert, update, delete on public.folder_orderings to authenticated;

create or replace function public.folder_item_name(item_path text)
returns text
language sql
immutable
as $$
    select regexp_replace(item_path, '^.*/', '');
$$;

create or replace function public.folder_item_number(item_path text)
returns integer
language sql
immutable
as $$
    select nullif((regexp_match(public.folder_item_name(item_path), '([0-9]{1,6})(?=\.[^.]+$)'))[1], '')::integer;
$$;

create or replace function public.folder_item_precedes(left_path text, right_path text)
returns boolean
language plpgsql
immutable
as $$
declare
    left_number integer := public.folder_item_number(left_path);
    right_number integer := public.folder_item_number(right_path);
    left_name text := lower(public.folder_item_name(left_path));
    right_name text := lower(public.folder_item_name(right_path));
begin
    if left_number is not null and right_number is not null and left_number <> right_number then
        return left_number < right_number;
    end if;
    if left_number is not null and right_number is null then
        return true;
    end if;
    if left_number is null and right_number is not null then
        return false;
    end if;
    return left_name < right_name;
end;
$$;

create or replace function public.folder_direct_items(target_folder text)
returns text[]
language sql
security definer
set search_path = public
as $$
    with direct_videos as (
        select v.path
        from public.videos v
        where v.folder_path = target_folder
    ), direct_folders as (
        select distinct
            case when target_folder = '' then split_part(v.folder_path, '/', 1)
                 else target_folder || '/' || split_part(substr(v.folder_path, length(target_folder) + 2), '/', 1)
            end as path
        from public.videos v
        where (target_folder = '' and v.folder_path <> '')
           or (target_folder <> '' and v.folder_path like target_folder || '/%')
    )
    select coalesce(array_agg(path order by public.folder_item_number(path) nulls last, lower(public.folder_item_name(path)), path), '{}'::text[])
    from (
        select path from direct_videos
        union
        select path from direct_folders where path is not null and path <> ''
    ) items;
$$;

create or replace function public.insert_folder_item(existing_paths text[], new_path text)
returns text[]
language plpgsql
immutable
as $$
declare
    result text[] := coalesce(existing_paths, '{}'::text[]);
    index_value integer;
begin
    if new_path = any(result) then
        return result;
    end if;
    for index_value in 1..coalesce(array_length(result, 1), 0) loop
        if public.folder_item_precedes(new_path, result[index_value]) then
            return result[1:index_value - 1] || array[new_path] || result[index_value:];
        end if;
    end loop;
    return result || array[new_path];
end;
$$;

create or replace function public.refresh_folder_ordering(target_folder text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    direct_items text[] := public.folder_direct_items(target_folder);
    previous_items text[];
    result_items text[] := '{}'::text[];
    item_path text;
    flattened text[] := '{}'::text[];
    nested_paths text[];
begin
    select item_paths into previous_items
    from public.folder_orderings
    where folder_path = target_folder;

    -- Preserve existing order, remove deleted/moved items, then insert new items
    -- using numeric filename ordering or alphabetical ordering.
    if previous_items is not null then
        foreach item_path in array previous_items loop
            if item_path = any(direct_items) then
                result_items := result_items || array[item_path];
            end if;
        end loop;
    end if;
    foreach item_path in array direct_items loop
        if not (item_path = any(result_items)) then
            result_items := public.insert_folder_item(result_items, item_path);
        end if;
    end loop;

    foreach item_path in array result_items loop
        if exists (select 1 from public.videos where path = item_path) then
            flattened := flattened || array[item_path];
        else
            select coalesce(ordered_video_paths, '{}'::text[]) into nested_paths
            from public.folder_orderings
            where folder_path = item_path;
            if nested_paths is not null then
                flattened := flattened || nested_paths;
            end if;
        end if;
    end loop;

    insert into public.folder_orderings(folder_path, item_paths, ordered_video_paths, updated_at)
    values (target_folder, result_items, flattened, now())
    on conflict (folder_path) do update set
        item_paths = excluded.item_paths,
        ordered_video_paths = excluded.ordered_video_paths,
        updated_at = excluded.updated_at;
end;
$$;

create or replace function public.refresh_folder_ordering_ancestors(folder_path_value text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    current_folder text := coalesce(folder_path_value, '');
begin
    loop
        perform public.refresh_folder_ordering(current_folder);
        exit when current_folder = '';
        if position('/' in current_folder) = 0 then
            perform public.refresh_folder_ordering('');
            exit;
        end if;
        current_folder := regexp_replace(current_folder, '/[^/]+$', '');
    end loop;
end;
$$;

create or replace function public.admin_update_folder_ordering(target_folder text, new_items text[])
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    direct_items text[] := public.folder_direct_items(target_folder);
    item_path text;
    flattened text[] := '{}'::text[];
    nested_paths text[];
    parent_folder text;
begin
    if not public.is_admin() then
        raise exception 'Admin access required.';
    end if;
    if coalesce(array_length(new_items, 1), 0) <> coalesce(array_length(direct_items, 1), 0)
       or exists (
           select 1 from unnest(coalesce(new_items, '{}'::text[])) item
           where not (item = any(direct_items))
       )
       or exists (
           select 1 from (
               select item, count(*) as item_count
               from unnest(coalesce(new_items, '{}'::text[])) item
               group by item
           ) duplicates where duplicates.item_count > 1
       ) then
        raise exception 'Folder ordering must contain exactly the current direct items.';
    end if;

    foreach item_path in array new_items loop
        if exists (select 1 from public.videos where path = item_path) then
            flattened := flattened || array[item_path];
        else
            select coalesce(ordered_video_paths, '{}'::text[]) into nested_paths
            from public.folder_orderings where folder_path = item_path;
            flattened := flattened || coalesce(nested_paths, '{}'::text[]);
        end if;
    end loop;

    insert into public.folder_orderings(folder_path, item_paths, ordered_video_paths, updated_at)
    values (target_folder, new_items, flattened, now())
    on conflict (folder_path) do update set
        item_paths = excluded.item_paths,
        ordered_video_paths = excluded.ordered_video_paths,
        updated_at = excluded.updated_at;

    parent_folder := case when position('/' in target_folder) = 0 then '' else regexp_replace(target_folder, '/[^/]+$', '') end;
    perform public.refresh_folder_ordering_ancestors(parent_folder);
end;
$$;

create or replace function public.sync_folder_orderings()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    old_folder text := '';
    new_folder text := '';
begin
    if tg_op <> 'INSERT' and position('/' in old.name) > 0 then
        old_folder := regexp_replace(old.name, '/[^/]*$', '');
    end if;
    if tg_op <> 'DELETE' and position('/' in new.name) > 0 then
        new_folder := regexp_replace(new.name, '/[^/]*$', '');
    end if;
    if tg_op <> 'INSERT' and old.bucket_id = 'media' and old.name not like '__previews/%' then
        perform public.refresh_folder_ordering_ancestors(old_folder);
    end if;
    if tg_op <> 'DELETE' and new.bucket_id = 'media' and new.name not like '__previews/%' then
        perform public.refresh_folder_ordering_ancestors(new_folder);
    end if;
    return case when tg_op = 'DELETE' then old else new end;
end;
$$;

drop trigger if exists sync_folder_orderings on storage.objects;
create trigger sync_folder_orderings
    after insert or update or delete on storage.objects
    for each row execute function public.sync_folder_orderings();

-- Build orderings for the current catalog. Existing rows are preserved by the
-- refresh function; this block is primarily for first installation.
do $$
declare
    folder_value text;
begin
    for folder_value in
        with recursive folder_paths(folder_path) as (
            select distinct folder_path from public.videos
            union
            select regexp_replace(folder_path, '/[^/]+$', '')
            from folder_paths
            where folder_path <> '' and position('/' in folder_path) > 0
        )
        select folder_path from (
            select ''::text as folder_path
            union
            select folder_path from folder_paths
        ) folders
        order by length(folder_path) desc, folder_path
    loop
        perform public.refresh_folder_ordering(folder_value);
    end loop;
end $$;

revoke all on function public.folder_item_name(text) from public;
revoke all on function public.folder_item_number(text) from public;
revoke all on function public.folder_item_precedes(text, text) from public;
revoke all on function public.folder_direct_items(text) from public;
revoke all on function public.insert_folder_item(text[], text) from public;
revoke all on function public.refresh_folder_ordering(text) from public;
revoke all on function public.refresh_folder_ordering_ancestors(text) from public;
revoke all on function public.sync_folder_orderings() from public;
revoke all on function public.admin_update_folder_ordering(text, text[]) from public;
grant execute on function public.admin_update_folder_ordering(text, text[]) to authenticated;
