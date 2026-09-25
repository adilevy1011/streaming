-- Per-user access to videos that do not have an explicit user_access allowlist.

alter table public.profiles
    add column if not exists new_videos_access boolean not null default true;

update public.profiles
set new_videos_access = true
where new_videos_access is null;

drop policy if exists "Users can read permitted videos" on public.videos;
create policy "Users can read permitted videos"
    on public.videos for select to authenticated
    using (
        public.is_admin()
        or lower(auth.jwt()->>'email') = any(user_access)
        or (
            coalesce(cardinality(user_access), 0) = 0
            and exists (
                select 1
                from public.profiles
                where user_id = auth.uid() and new_videos_access = true
            )
        )
    );

drop policy if exists "Admins can update new video access" on public.profiles;
create policy "Admins can update new video access"
    on public.profiles for update to authenticated
    using (public.is_admin())
    with check (public.is_admin());

grant update (new_videos_access) on public.profiles to authenticated;

create or replace function public.prevent_non_admin_new_video_access_change()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    if new.new_videos_access is distinct from old.new_videos_access
       and not public.is_admin() then
        raise exception 'Only administrators can change new video access.';
    end if;
    return new;
end;
$$;

drop trigger if exists prevent_non_admin_new_video_access_change on public.profiles;
create trigger prevent_non_admin_new_video_access_change
    before update on public.profiles
    for each row execute function public.prevent_non_admin_new_video_access_change();

revoke all on function public.prevent_non_admin_new_video_access_change() from public;

-- Replace the original admin user-list function with the extended result.
drop function if exists public.admin_list_users();
create function public.admin_list_users()
returns table (user_id uuid, email text, admin_access boolean, new_videos_access boolean)
language sql
stable
security definer
set search_path = public, auth
as $$
    select u.id, u.email, coalesce(p.admin_access, false), coalesce(p.new_videos_access, true)
    from auth.users u
    left join public.profiles p on p.user_id = u.id
    where public.is_admin()
    order by lower(u.email);
$$;

revoke all on function public.admin_list_users() from public;
grant execute on function public.admin_list_users() to authenticated;
