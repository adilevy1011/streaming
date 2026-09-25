-- Keep administrators in explicit video allowlists as well as the admin-only
-- RLS bypass. This preserves their explicitly granted access if admin_access
-- is later removed.

create or replace function public.include_admins_in_video_access()
returns trigger
language plpgsql
security definer
set search_path = public, auth
as $$
begin
    -- An empty allowlist means the video is available to all eligible users,
    -- so there is no per-video list to augment.
    if coalesce(cardinality(new.user_access), 0) = 0 then
        return new;
    end if;

    select coalesce(array_agg(email order by email), '{}'::text[])
    into new.user_access
    from (
        select distinct lower(trim(value)) as email
        from unnest(coalesce(new.user_access, '{}'::text[])) as entries(value)
        where trim(value) <> ''
        union
        select distinct lower(u.email) as email
        from auth.users u
        join public.profiles p on p.user_id = u.id
        where p.admin_access = true and u.email is not null
    ) allowed;

    return new;
end;
$$;

drop trigger if exists include_admins_in_video_access on public.videos;
create trigger include_admins_in_video_access
    before insert or update of user_access on public.videos
    for each row execute function public.include_admins_in_video_access();

create or replace function public.add_admin_to_video_access()
returns trigger
language plpgsql
security definer
set search_path = public, auth
as $$
declare
    admin_email text;
begin
    if new.admin_access is not true or old.admin_access is true or new.user_id is null then
        return new;
    end if;

    select lower(u.email)
    into admin_email
    from auth.users u
    where u.id = new.user_id and u.email is not null;

    if admin_email is null then
        return new;
    end if;

    update public.videos
    set user_access = array_append(user_access, admin_email)
    where coalesce(cardinality(user_access), 0) > 0
      and not (admin_email = any(user_access));

    return new;
end;
$$;

drop trigger if exists add_admin_to_video_access on public.profiles;
create trigger add_admin_to_video_access
    after update of admin_access on public.profiles
    for each row execute function public.add_admin_to_video_access();

-- Backfill existing explicit allowlists for every current administrator.
update public.videos v
set user_access = (
    select array_agg(email order by email)
    from (
        select distinct lower(trim(value)) as email
        from unnest(coalesce(v.user_access, '{}'::text[])) as entries(value)
        where trim(value) <> ''
        union
        select distinct lower(u.email) as email
        from auth.users u
        join public.profiles p on p.user_id = u.id
        where p.admin_access = true and u.email is not null
    ) allowed
)
where coalesce(cardinality(v.user_access), 0) > 0;

revoke all on function public.include_admins_in_video_access() from public;
revoke all on function public.add_admin_to_video_access() from public;
