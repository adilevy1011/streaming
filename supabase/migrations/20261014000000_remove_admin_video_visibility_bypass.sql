-- Remove the legacy administrator model. User-specific media access remains
-- controlled by videos.user_access and profiles.new_videos_access.

drop trigger if exists add_admin_to_video_access on public.profiles;
drop trigger if exists include_admins_in_video_access on public.videos;
drop trigger if exists prevent_non_admin_new_video_access_change on public.profiles;

drop policy if exists "Admins can read profiles" on public.profiles;
drop policy if exists "Admins can update new video access" on public.profiles;
drop policy if exists "Admins can update video access" on public.videos;
drop policy if exists "Admins can manage folder orderings" on public.folder_orderings;

drop function if exists public.admin_list_users();
drop function if exists public.admin_set_new_videos_access(uuid, boolean);
drop function if exists public.admin_update_folder_ordering(text, text[]);
drop function if exists public.admin_rewrite_media_paths(text, text);
drop function if exists public.include_admins_in_video_access();
drop function if exists public.add_admin_to_video_access();
drop function if exists public.prevent_non_admin_new_video_access_change();
drop function if exists public.is_admin();

alter table public.profiles drop column if exists admin_access;

create or replace function public.rewrite_media_paths(old_path text, new_path text)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    ordering record;
    target_folder text;
begin
    if old_path is null or new_path is null or old_path = '' or new_path = '' then
        raise exception 'Media paths cannot be empty.';
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
               set item_paths = array(select public.rename_media_path(item_path, old_path, new_path) from unnest(ordering.item_paths) as items(item_path)),
                   ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path) from unnest(ordering.ordered_video_paths) as ordered(video_path)),
                   updated_at = now()
             where folder_path = target_folder;
            delete from public.folder_orderings where folder_path = ordering.folder_path;
        else
            update public.folder_orderings
               set folder_path = target_folder,
                   item_paths = array(select public.rename_media_path(item_path, old_path, new_path) from unnest(item_paths) as items(item_path)),
                   ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path) from unnest(ordered_video_paths) as ordered(video_path)),
                   updated_at = now()
             where folder_path = ordering.folder_path;
        end if;
    end loop;

    update public.folder_orderings
       set item_paths = array(select public.rename_media_path(item_path, old_path, new_path) from unnest(item_paths) as items(item_path)),
           ordered_video_paths = array(select public.rename_media_path(video_path, old_path, new_path) from unnest(ordered_video_paths) as ordered(video_path)),
           updated_at = now()
     where exists (select 1 from unnest(item_paths) item_path where item_path = old_path or item_path like old_path || '/%')
        or exists (select 1 from unnest(ordered_video_paths) video_path where video_path = old_path or video_path like old_path || '/%');
end;
$$;

revoke all on function public.rewrite_media_paths(text, text) from public;
grant execute on function public.rewrite_media_paths(text, text) to service_role;

drop policy if exists "Users can read permitted videos" on public.videos;
create policy "Users can read permitted videos"
    on public.videos for select to authenticated
    using (
        lower(auth.jwt()->>'email') = any(user_access)
        or (
            coalesce(cardinality(user_access), 0) = 0
            and exists (
                select 1
                from public.profiles
                where user_id = auth.uid() and new_videos_access = true
            )
        )
    );
