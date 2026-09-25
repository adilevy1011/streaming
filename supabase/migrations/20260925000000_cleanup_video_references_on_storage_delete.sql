-- Remove all database rows keyed by a source video when its Storage object is deleted.
-- Storage is the source of truth for media bytes, so these references cannot be
-- left behind for a later catalog reconciliation to clean up.

create or replace function public.cleanup_video_references_on_storage_delete()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if old.bucket_id <> 'media'
       or old.name like '__previews/%'
       or (
           public.media_catalog_kind(old.name, old.metadata) <> 'video'
           and not exists (
               select 1
               from public.videos
               where path = old.name
           )
       ) then
        return old;
    end if;

    -- These tables intentionally use storage paths rather than database foreign
    -- keys because Storage objects live outside the public schema.
    delete from public.video_progress where media_path = old.name;
    delete from public.video_previews where media_path = old.name;
    delete from public.video_credits where media_path = old.name;
    delete from public.videos where path = old.name;
    delete from public.media_objects where path = old.name;

    return old;
end;
$$;

drop trigger if exists cleanup_video_references_on_storage_delete on storage.objects;
create trigger cleanup_video_references_on_storage_delete
    after delete on storage.objects
    for each row execute function public.cleanup_video_references_on_storage_delete();

revoke all on function public.cleanup_video_references_on_storage_delete() from public;
