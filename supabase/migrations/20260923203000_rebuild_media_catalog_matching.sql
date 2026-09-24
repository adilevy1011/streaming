-- Repair the media catalog's extension matching.
-- The original catalog migration used over-escaped PostgreSQL regexes, which
-- prevented .srt files and same-stem artwork from being associated with videos.

create or replace function public.media_catalog_kind(object_name text, object_metadata jsonb)
returns text
language sql
immutable
as $$
    select case
        when coalesce(object_metadata->>'mimetype', '') like 'video/%'
            or lower(object_name) ~ '\.(mp4|m4v|webm|mov|mkv|avi|ogv|mpeg|mpg|ts)$' then 'video'
        when coalesce(object_metadata->>'mimetype', '') like 'image/%'
            or lower(object_name) ~ '\.(jpg|jpeg|png|webp|gif|avif)$' then 'image'
        when lower(object_name) ~ '\.srt$' then 'subtitle'
        else 'other'
    end;
$$;

create or replace function public.refresh_video_catalog(video_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    source_row public.media_objects%rowtype;
    source_stem text;
begin
    select * into source_row
    from public.media_objects
    where path = video_path and kind = 'video';

    if not found then
        delete from public.videos where path = video_path;
        return;
    end if;

    source_stem := regexp_replace(source_row.name, '\.[^.]*$', '');

    insert into public.videos (
        path, name, folder_path, mime_type, size_bytes, created_at, updated_at,
        subtitle_path, preview_image_path, preview_image_updated_at
    )
    values (
        source_row.path, source_row.name, source_row.folder_path, source_row.mime_type,
        source_row.size_bytes, source_row.created_at, source_row.updated_at,
        (
            select m.path from public.media_objects m
            where m.kind = 'subtitle'
              and m.folder_path = source_row.folder_path
              and lower(m.name) = lower(source_stem || '.srt')
            order by m.path limit 1
        ),
        (
            select m.path from public.media_objects m
            where m.kind = 'image'
              and m.folder_path = source_row.folder_path
              and lower(regexp_replace(m.name, '\.[^.]*$', '')) = lower(source_stem)
            order by m.path limit 1
        ),
        (
            select m.updated_at from public.media_objects m
            where m.kind = 'image'
              and m.folder_path = source_row.folder_path
              and lower(regexp_replace(m.name, '\.[^.]*$', '')) = lower(source_stem)
            order by m.path limit 1
        )
    )
    on conflict (path) do update set
        name = excluded.name,
        folder_path = excluded.folder_path,
        mime_type = excluded.mime_type,
        size_bytes = excluded.size_bytes,
        created_at = excluded.created_at,
        updated_at = excluded.updated_at,
        subtitle_path = excluded.subtitle_path,
        preview_image_path = excluded.preview_image_path,
        preview_image_updated_at = excluded.preview_image_updated_at;
end;
$$;

-- Rebuild only the derived catalog tables. Storage objects remain untouched.
delete from public.videos;
delete from public.media_objects;

insert into public.media_objects(path, name, folder_path, kind, mime_type, size_bytes, created_at, updated_at)
select
    o.name,
    regexp_replace(o.name, '^.*/', ''),
    case when position('/' in o.name) = 0 then '' else regexp_replace(o.name, '/[^/]*$', '') end,
    public.media_catalog_kind(regexp_replace(o.name, '^.*/', ''), o.metadata),
    o.metadata->>'mimetype',
    nullif(o.metadata->>'size', '')::bigint,
    o.created_at,
    o.updated_at
from storage.objects o
where o.bucket_id = 'media' and o.name not like '__previews/%'
on conflict (path) do update set
    name = excluded.name,
    folder_path = excluded.folder_path,
    kind = excluded.kind,
    mime_type = excluded.mime_type,
    size_bytes = excluded.size_bytes,
    created_at = excluded.created_at,
    updated_at = excluded.updated_at;

do $$
declare video_path text;
begin
    for video_path in select path from public.media_objects where kind = 'video' loop
        perform public.refresh_video_catalog(video_path);
    end loop;
end $$;

