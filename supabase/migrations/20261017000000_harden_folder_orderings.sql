-- Harden playback ordering against trigger races, missed catalog rows, and
-- folder/file renames.  Ordering is still path based, but the database now
-- has an idempotent repair operation that derives membership from the catalog.

create or replace function public.repair_folder_orderings()
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
    folder_value text;
    stale_count integer;
    refreshed_count integer := 0;
begin
    create temporary table if not exists pg_temp.media_ordering_folders (
        folder_path text primary key
    ) on commit drop;
    truncate pg_temp.media_ordering_folders;

    with recursive folders(folder_path) as (
        select ''::text
        union
        select distinct v.folder_path
        from public.videos v
        where v.folder_path <> ''
        union
        select regexp_replace(folder_path, '/[^/]+$', '')
        from folders
        where folder_path <> ''
    )
    insert into pg_temp.media_ordering_folders(folder_path)
    select folder_path from folders;

    for folder_value in
        select folder_path
        from pg_temp.media_ordering_folders
        order by length(folder_path) desc, folder_path
    loop
        perform public.refresh_folder_ordering(folder_value);
        refreshed_count := refreshed_count + 1;
    end loop;

    select count(*) into stale_count
    from public.folder_orderings ordering
    where not exists (
        select 1
        from pg_temp.media_ordering_folders expected
        where expected.folder_path = ordering.folder_path
    );

    delete from public.folder_orderings ordering
    where not exists (
        select 1
        from pg_temp.media_ordering_folders expected
        where expected.folder_path = ordering.folder_path
    );

    return jsonb_build_object(
        'folders_refreshed', refreshed_count,
        'stale_rows_removed', stale_count
    );
end;
$$;

-- The storage trigger may run before the storage-to-catalog trigger.  Refresh
-- again after a video row is materialized so a newly uploaded video cannot be
-- permanently absent from its folder ordering.
create or replace function public.sync_folder_orderings_from_video()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if tg_op <> 'INSERT' then
        perform public.refresh_folder_ordering_ancestors(old.folder_path);
    end if;
    if tg_op <> 'DELETE' then
        perform public.refresh_folder_ordering_ancestors(new.folder_path);
    end if;
    return case when tg_op = 'DELETE' then old else new end;
end;
$$;

drop trigger if exists sync_folder_orderings_from_video on public.videos;
create trigger sync_folder_orderings_from_video
    after insert or update or delete on public.videos
    for each row execute function public.sync_folder_orderings_from_video();

-- Replace the rename implementation with a collision-safe rewrite. Storage
-- moves can create destination ordering rows through triggers; the source row
-- is authoritative for its hand-curated order, and any generated destination
-- rows are removed before the transformed rows are restored.
create or replace function public.rewrite_media_paths(old_path text, new_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
    if old_path is null or new_path is null or old_path = '' or new_path = '' then
        raise exception 'Media paths cannot be empty.';
    end if;
    if new_path = old_path or new_path like old_path || '/%' then
        raise exception 'A media path cannot be renamed into itself.';
    end if;

    update public.video_progress
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';
    update public.video_previews
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';
    update public.video_credits
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';
    update public.videos
       set subtitle_path = public.rename_media_path(subtitle_path, old_path, new_path),
           preview_image_path = public.rename_media_path(preview_image_path, old_path, new_path)
     where subtitle_path = old_path or subtitle_path like old_path || '/%'
        or preview_image_path = old_path or preview_image_path like old_path || '/%';

    create temporary table pg_temp.media_ordering_rename (
        source_folder text primary key,
        target_folder text not null,
        item_paths text[] not null,
        ordered_video_paths text[] not null
    ) on commit drop;

    insert into pg_temp.media_ordering_rename(source_folder, target_folder, item_paths, ordered_video_paths)
    select folder_path,
           public.rename_media_path(folder_path, old_path, new_path),
           coalesce(item_paths, '{}'::text[]),
           coalesce(ordered_video_paths, '{}'::text[])
    from public.folder_orderings
    where folder_path = old_path or folder_path like old_path || '/%';

    update public.folder_orderings as current
       set item_paths = array(
               select public.rename_media_path(item_path, old_path, new_path)
               from unnest(current.item_paths) as items(item_path)
           ),
           ordered_video_paths = array(
               select public.rename_media_path(video_path, old_path, new_path)
               from unnest(current.ordered_video_paths) as ordered(video_path)
           ),
           updated_at = now()
     where not exists (
         select 1 from pg_temp.media_ordering_rename moved
         where moved.source_folder = current.folder_path
     );

    delete from public.folder_orderings ordering
    where exists (
        select 1 from pg_temp.media_ordering_rename moved
        where moved.source_folder = ordering.folder_path
           or moved.target_folder = ordering.folder_path
    );

    insert into public.folder_orderings(folder_path, item_paths, ordered_video_paths, updated_at)
    select target_folder, item_paths, ordered_video_paths, now()
    from pg_temp.media_ordering_rename;

    perform public.repair_folder_orderings();
end;
$$;

-- Repair existing data as part of the migration, not only future uploads.
select public.repair_folder_orderings();

revoke all on function public.repair_folder_orderings() from public;
revoke all on function public.sync_folder_orderings_from_video() from public;
grant execute on function public.repair_folder_orderings() to service_role;
grant execute on function public.rewrite_media_paths(text, text) to service_role;

