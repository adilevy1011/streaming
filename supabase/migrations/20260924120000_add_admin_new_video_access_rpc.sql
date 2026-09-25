-- Admin-only profile update for users whose profile row may not exist yet.

create or replace function public.admin_set_new_videos_access(
    target_user_id uuid,
    enabled boolean
)
returns table (user_id uuid, admin_access boolean, new_videos_access boolean)
language plpgsql
security definer
set search_path = public, auth
as $$
begin
    if not public.is_admin() then
        raise exception 'Admin access required.';
    end if;

    if not exists (select 1 from auth.users u where u.id = target_user_id) then
        raise exception 'User not found.';
    end if;

    insert into public.profiles (user_id, new_videos_access)
    values (target_user_id, enabled)
    on conflict (user_id) do update
        set new_videos_access = excluded.new_videos_access;

    return query
    select p.user_id, p.admin_access, p.new_videos_access
    from public.profiles p
    where p.user_id = target_user_id;
end;
$$;

revoke all on function public.admin_set_new_videos_access(uuid, boolean) from public;
grant execute on function public.admin_set_new_videos_access(uuid, boolean) to authenticated;
