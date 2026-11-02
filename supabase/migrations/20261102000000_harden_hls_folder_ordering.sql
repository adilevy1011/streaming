-- Keep virtual HLS-only videos in playback orderings, but do not expose a
-- second ordering item when the original source video is also cataloged.

create or replace function public.folder_item_name(item_path text)
returns text
language sql
immutable
as $$
    select case
        when lower(item_path) ~ '(^|/)[^/]+-hls/index\.m3u8$'
            then regexp_replace(item_path, '^(.*/)?([^/]+)-hls/index\.m3u8$', '\2')
        else regexp_replace(item_path, '^.*/', '')
    end;
$$;

create or replace function public.folder_item_number(item_path text)
returns integer
language sql
immutable
as $$
    select case
        when lower(item_path) ~ '(^|/)[^/]+-hls/index\.m3u8$'
            then nullif((regexp_match(public.folder_item_name(item_path), '([0-9]{1,6})$'))[1], '')::integer
        else nullif((regexp_match(public.folder_item_name(item_path), '([0-9]{1,6})(?=\.[^.]+$)'))[1], '')::integer
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
          and not (
              lower(v.path) ~ '(^|/)[^/]+-hls/index\.m3u8$'
              and exists (
                  select 1
                  from public.videos source
                  where source.path <> v.path
                    and source.folder_path = v.folder_path
                    and lower(regexp_replace(source.name, '\.[^.]*$', '')) = lower(v.name)
                    and lower(source.path) !~ '(^|/)[^/]+-hls/index\.m3u8$'
              )
          )
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

select public.repair_folder_orderings();

