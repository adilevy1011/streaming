-- HLS segments are implementation objects.  The manifest is the only HLS
-- object that can enter the video catalog, and only the adjacent naming
-- convention is accepted.  This migration never deletes storage objects.

create or replace function public.media_catalog_kind(object_name text, object_metadata jsonb)
returns text
language sql
immutable
as $$
    select case
        when lower(object_name) ~ '(^|/)[^/]+-hls/segment-[0-9]+\.ts$' then 'other'
        when lower(object_name) ~ '(^|/)[^/]+-hls/index\.m3u8$' then 'video'
        when coalesce(object_metadata->>'mimetype', '') like 'video/%'
            or lower(object_name) ~ '\.(mp4|m4v|webm|mov|mkv|avi|ogv|mpeg|mpg|ts)$' then 'video'
        when coalesce(object_metadata->>'mimetype', '') like 'image/%'
            or lower(object_name) ~ '\.(jpg|jpeg|png|webp|gif|avif)$' then 'image'
        when lower(object_name) ~ '\.srt$' then 'subtitle'
        else 'other'
    end;
$$;

-- The trigger must pass the complete path; a basename cannot distinguish an
-- HLS segment from an original .ts source.
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
       and old.bucket_id = 'media' and old.name not like '__previews/%'
       and new.bucket_id = 'media' and new.name not like '__previews/%'
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
            for affected_path in select path from public.media_objects where folder_path = old_folder and kind = 'video' loop
                perform public.refresh_video_catalog(affected_path);
            end loop;
        end if;
        if tg_op = 'DELETE' then return old; end if;
    end if;

    if tg_op = 'UPDATE' and old.bucket_id = 'media' and old.name not like '__previews/%'
       and (new.bucket_id <> 'media' or new.name like '__previews/%' or new.name <> old.name) then
        delete from public.media_objects where path = old.name;
        if public.media_catalog_kind(old.name, old.metadata) = 'video' then delete from public.videos where path = old.name; end if;
        for affected_path in select path from public.media_objects where folder_path = old_folder and kind = 'video' loop
            perform public.refresh_video_catalog(affected_path);
        end loop;
    end if;

    if new.bucket_id <> 'media' or new.name like '__previews/%' then return new; end if;
    object_path := new.name;
    object_name := split_part(object_path, '/', array_length(string_to_array(object_path, '/'), 1));
    object_folder := case when position('/' in object_path) = 0 then '' else regexp_replace(object_path, '/[^/]*$', '') end;
    object_mime := new.metadata->>'mimetype';
    object_kind := public.media_catalog_kind(object_path, new.metadata);
    object_size := nullif(new.metadata->>'size', '')::bigint;
    object_created := coalesce(new.created_at, now());
    object_updated := coalesce(new.updated_at, object_created);
    insert into public.media_objects(path, name, folder_path, kind, mime_type, size_bytes, created_at, updated_at)
    values (object_path, object_name, object_folder, object_kind, object_mime, object_size, object_created, object_updated)
    on conflict (path) do update set name = excluded.name, folder_path = excluded.folder_path, kind = excluded.kind,
        mime_type = excluded.mime_type, size_bytes = excluded.size_bytes, created_at = excluded.created_at, updated_at = excluded.updated_at;
    if object_kind = 'video' then perform public.refresh_video_catalog(object_path); end if;
    if preserved_access is not null then
        update public.videos set user_access = preserved_access where path = object_path;
    end if;
    for affected_path in select path from public.media_objects where folder_path = object_folder and kind = 'video' loop
        perform public.refresh_video_catalog(affected_path);
    end loop;
    return new;
end;
$$;

-- Repair stale rows created before path-aware classification.  Valid storage
-- objects remain in storage and can still be used by a published manifest.
delete from public.videos
where lower(path) ~ '(^|/)[^/]+-hls/segment-[0-9]+\.ts$';

delete from public.media_objects
where lower(path) ~ '(^|/)[^/]+-hls/segment-[0-9]+\.ts$';

update public.media_objects
set kind = 'video'
where lower(path) ~ '(^|/)[^/]+-hls/index\.m3u8$';

do $$
declare video_path text;
begin
    for video_path in
        select path from public.media_objects
        where kind = 'video'
          and (lower(path) ~ '(^|/)[^/]+-hls/index\.m3u8$'
               or lower(path) !~ '(^|/)[^/]+-hls/segment-[0-9]+\.ts$')
    loop
        perform public.refresh_video_catalog(video_path);
    end loop;
end $$;
