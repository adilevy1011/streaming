-- Database catalog for the private media bucket.
-- Storage remains the source of bytes; these tables are the searchable index.

create table if not exists public.media_objects (
    path text primary key,
    name text not null,
    folder_path text not null default '',
    kind text not null check (kind in ('video', 'image', 'subtitle', 'other')),
    mime_type text,
    size_bytes bigint,
    created_at timestamptz,
    updated_at timestamptz,
    constraint media_objects_path_not_empty check (length(path) > 0)
);

create index if not exists idx_media_objects_folder_path
    on public.media_objects(folder_path);
create index if not exists idx_media_objects_kind_folder
    on public.media_objects(kind, folder_path);

create table if not exists public.videos (
    path text primary key references public.media_objects(path) on delete cascade,
    name text not null,
    folder_path text not null default '',
    mime_type text,
    size_bytes bigint,
    created_at timestamptz,
    updated_at timestamptz,
    subtitle_path text,
    preview_image_path text,
    preview_image_updated_at timestamptz,
    constraint videos_path_not_empty check (length(path) > 0)
);

create index if not exists idx_videos_folder_path on public.videos(folder_path);
create index if not exists idx_videos_updated_at on public.videos(updated_at desc);

alter table public.media_objects enable row level security;
alter table public.videos enable row level security;

drop policy if exists "Authenticated users can read media catalog" on public.media_objects;
create policy "Authenticated users can read media catalog"
    on public.media_objects for select to authenticated using (true);

drop policy if exists "Authenticated users can read videos" on public.videos;
create policy "Authenticated users can read videos"
    on public.videos for select to authenticated using (true);

grant select on public.media_objects to authenticated;
grant select on public.videos to authenticated;

create or replace function public.media_catalog_kind(object_name text, object_metadata jsonb)
returns text
language sql
immutable
as $$
    select case
        when coalesce(object_metadata->>'mimetype', '') like 'video/%'
            or lower(object_name) ~ '\\.(mp4|m4v|webm|mov|mkv|avi|ogv|mpeg|mpg|ts)$' then 'video'
        when coalesce(object_metadata->>'mimetype', '') like 'image/%'
            or lower(object_name) ~ '\\.(jpg|jpeg|png|webp|gif|avif)$' then 'image'
        when lower(object_name) ~ '\\.srt$' then 'subtitle'
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

    source_stem := regexp_replace(source_row.name, '\\.[^.]*$', '');

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
              and lower(regexp_replace(m.name, '\\.[^.]*$', '')) = lower(source_stem)
            order by m.path limit 1
        ),
        (
            select m.updated_at from public.media_objects m
            where m.kind = 'image'
              and m.folder_path = source_row.folder_path
              and lower(regexp_replace(m.name, '\\.[^.]*$', '')) = lower(source_stem)
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
begin
    old_folder := case when tg_op <> 'INSERT' and position('/' in old.name) = 0 then ''
                       when tg_op <> 'INSERT' then regexp_replace(old.name, '/[^/]*$', '')
                       else '' end;
    if tg_op = 'DELETE' or (tg_op = 'UPDATE' and (old.bucket_id <> 'media' or old.name like '__previews/%')) then
        if old.bucket_id = 'media' and old.name not like '__previews/%' then
            delete from public.media_objects where path = old.name;
            if public.media_catalog_kind(old.name, old.metadata) = 'video' then
                delete from public.videos where path = old.name;
            end if;
            for affected_path in
                select path from public.media_objects
                where folder_path = old_folder and kind = 'video'
            loop
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
        for affected_path in
            select path from public.media_objects
            where folder_path = old_folder and kind = 'video'
        loop
            perform public.refresh_video_catalog(affected_path);
        end loop;
    end if;

    if new.bucket_id <> 'media' or new.name like '__previews/%' then
        return new;
    end if;

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
    on conflict (path) do update set
        name = excluded.name,
        folder_path = excluded.folder_path,
        kind = excluded.kind,
        mime_type = excluded.mime_type,
        size_bytes = excluded.size_bytes,
        created_at = excluded.created_at,
        updated_at = excluded.updated_at;

    if object_kind = 'video' then
        perform public.refresh_video_catalog(object_path);
    end if;

    -- An image/subtitle upload or delete can change a sibling video's resolved metadata.
    for affected_path in
        select path from public.media_objects
        where folder_path = object_folder and kind = 'video'
    loop
        perform public.refresh_video_catalog(affected_path);
    end loop;
    return new;
end;
$$;

drop trigger if exists sync_media_object_catalog on storage.objects;
create trigger sync_media_object_catalog
    after insert or update or delete on storage.objects
    for each row execute function public.sync_media_object_catalog();

-- Initial backfill for objects already in the bucket.
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

insert into public.videos(path, name, folder_path, mime_type, size_bytes, created_at, updated_at)
select path, name, folder_path, mime_type, size_bytes, created_at, updated_at
from public.media_objects
where kind = 'video'
on conflict (path) do nothing;

do $$
declare video_path text;
begin
    for video_path in select path from public.media_objects where kind = 'video' loop
        perform public.refresh_video_catalog(video_path);
    end loop;
end $$;

revoke all on function public.media_catalog_kind(text, jsonb) from public;
revoke all on function public.refresh_video_catalog(text) from public;
revoke all on function public.sync_media_object_catalog() from public;
