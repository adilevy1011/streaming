-- Keep path-keyed state attached to media when an admin renames a file or
-- folder. Storage is moved first by the admin API, then this function updates
-- all references in one transaction.

create or replace function public.rename_media_path(path_value text, old_path text, new_path text)
returns text
language sql
immutable
as $$
    select case
        when path_value = old_path then new_path
        when path_value like old_path || '/%' then new_path || substr(path_value, length(old_path) + 1)
        else path_value
    end;
$$;

create or replace function public.admin_rewrite_media_paths(old_path text, new_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    ordering record;
    target_folder text;
begin
    if not public.is_admin() then
        raise exception 'Admin access required.';
    end if;
    if old_path is null or new_path is null or old_path = '' or new_path = '' then
        raise exception 'Media paths cannot be empty.';
    end if;

    -- These tables are keyed directly by the source video path.
    update public.video_progress
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';
    update public.video_previews
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';
    update public.video_credits
       set media_path = public.rename_media_path(media_path, old_path, new_path)
     where media_path = old_path or media_path like old_path || '/%';

    -- Catalog triggers recreate the moved media rows. Update any related
    -- fields that may still contain the old path as well.
    update public.videos
       set subtitle_path = public.rename_media_path(subtitle_path, old_path, new_path),
           preview_image_path = public.rename_media_path(preview_image_path, old_path, new_path)
     where subtitle_path = old_path or subtitle_path like old_path || '/%'
        or preview_image_path = old_path or preview_image_path like old_path || '/%';

    -- Folder playback order stores both direct item paths and flattened video
    -- paths, so rewrite the folder key and every array element.
    -- Moving children causes the storage trigger to create destination rows.
    -- Merge the old rows into those rows so a rename preserves hand-curated
    -- order and does not collide with the folder_orderings primary key.
    for ordering in
        select folder_path, item_paths, ordered_video_paths
        from public.folder_orderings
        where folder_path = old_path or folder_path like old_path || '/%'
        order by length(folder_path) desc
    loop
        target_folder := public.rename_media_path(ordering.folder_path, old_path, new_path);
        if exists (select 1 from public.folder_orderings where folder_path = target_folder)
           and target_folder <> ordering.folder_path then
            update public.folder_orderings
               set item_paths = array(select public.rename_media_path(item_path, old_path, new_path)
                                      from unnest(ordering.item_paths) as items(item_path)),
                   ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path)
                                               from unnest(ordering.ordered_video_paths) as ordered(video_path)),
                   updated_at = now()
             where folder_path = target_folder;
            delete from public.folder_orderings where folder_path = ordering.folder_path;
        else
            update public.folder_orderings
               set folder_path = target_folder,
                   item_paths = array(select public.rename_media_path(item_path, old_path, new_path)
                                      from unnest(item_paths) as items(item_path)),
                   ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path)
                                               from unnest(ordered_video_paths) as ordered(video_path)),
                   updated_at = now()
             where folder_path = ordering.folder_path;
        end if;
    end loop;

    -- Root and ancestor rows are not themselves renamed, but their arrays
    -- still contain moved direct items or flattened videos.
    update public.folder_orderings
       set item_paths = array(select public.rename_media_path(item_path, old_path, new_path)
                              from unnest(item_paths) as items(item_path)),
           ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path)
                                       from unnest(ordered_video_paths) as ordered(video_path)),
           updated_at = now()
     where exists (select 1 from unnest(item_paths) item_path
                   where item_path = old_path or item_path like old_path || '/%')
        or exists (select 1 from unnest(ordered_video_paths) video_path
                   where video_path = old_path or video_path like old_path || '/%');
end;
$$;

revoke all on function public.rename_media_path(text, text, text) from public;
revoke all on function public.admin_rewrite_media_paths(text, text) from public;
grant execute on function public.admin_rewrite_media_paths(text, text) to authenticated;

-- The catalog trigger deletes/recreates a moved video row. Preserve its
-- administrator access list while doing that rebuild.
create or replace function public.sync_media_object_catalog()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
declare
    object_path text;
    object_name text;
    object_folder text;
    object_kind text;
    object_mime text;
    object_size bigint;
    object_created timestamptz;
    object_updated timestamptz;
    affected_path text;
    old_folder text;
    preserved_access text[];
begin
    if tg_op = 'UPDATE'
       and old.bucket_id = 'media'
       and old.name not like '__previews/%'
       and new.bucket_id = 'media'
       and new.name not like '__previews/%'
       and new.name <> old.name
       and public.media_catalog_kind(old.name, old.metadata) = 'video' then
        select user_access into preserved_access from public.videos where path = old.name;
    end if;

    old_folder := case when tg_op <> 'INSERT' and position('/' in old.name) = 0 then ''
                       when tg_op <> 'INSERT' then regexp_replace(old.name, '/[^/]*$', '')
                       else '' end;
    if tg_op = 'DELETE' or (tg_op = 'UPDATE' and (old.bucket_id <> 'media' or old.name like '__previews/%')) then
        if old.bucket_id = 'media' and old.name not like '__previews/%' then
            delete from public.media_objects where path = old.name;
            if public.media_catalog_kind(old.name, old.metadata) = 'video' then
                delete from public.videos where path = old.name;
            end if;
            for affected_path in select path from public.media_objects
                where folder_path = old_folder and kind = 'video' loop
                perform public.refresh_video_catalog(affected_path);
            end loop;
        end if;
        if tg_op = 'DELETE' then return old; end if;
    end if;

    if tg_op = 'UPDATE' and old.bucket_id = 'media' and old.name not like '__previews/%'
       and (new.bucket_id <> 'media' or new.name like '__previews/%' or new.name <> old.name) then
        delete from public.media_objects where path = old.name;
        if public.media_catalog_kind(old.name, old.metadata) = 'video' then
            delete from public.videos where path = old.name;
        end if;
        for affected_path in select path from public.media_objects
            where folder_path = old_folder and kind = 'video' loop
            perform public.refresh_video_catalog(affected_path);
        end loop;
    end if;

    if new.bucket_id <> 'media' or new.name like '__previews/%' then return new; end if;
    object_path := new.name;
    object_name := split_part(object_path, '/', array_length(string_to_array(object_path, '/'), 1));
    object_folder := case when position('/' in object_path) = 0 then ''
                          else regexp_replace(object_path, '/[^/]*$', '') end;
    object_mime := new.metadata->>'mimetype';
    object_kind := public.media_catalog_kind(object_name, new.metadata);
    object_size := nullif(new.metadata->>'size', '')::bigint;
    object_created := coalesce(new.created_at, now());
    object_updated := coalesce(new.updated_at, object_created);

    insert into public.media_objects(path, name, folder_path, kind, mime_type, size_bytes, created_at, updated_at)
    values (object_path, object_name, object_folder, object_kind, object_mime, object_size, object_created, object_updated)
    on conflict (path) do update set name = excluded.name, folder_path = excluded.folder_path,
        mime_type = excluded.mime_type, size_bytes = excluded.size_bytes,
        created_at = excluded.created_at, updated_at = excluded.updated_at;
    if object_kind = 'video' then perform public.refresh_video_catalog(object_path); end if;
    if preserved_access is not null then
        update public.videos set user_access = preserved_access where path = object_path;
    end if;
    for affected_path in select path from public.media_objects
        where folder_path = object_folder and kind = 'video' loop
        perform public.refresh_video_catalog(affected_path);
    end loop;
    return new;
end;
$$;
