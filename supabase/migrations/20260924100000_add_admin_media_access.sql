-- Admin-controlled media visibility.
-- An empty user_access list preserves the existing behavior and makes a video
-- visible to every authenticated user. A non-empty list is an email allowlist.

alter table public.profiles
    add column if not exists admin_access boolean not null default false;

update public.profiles
set admin_access = false
where admin_access is null;

-- Keep users from promoting themselves or changing the admin flag through the
-- client-facing authenticated role. Admin promotion is managed by a trusted
-- database/admin operation.
revoke insert on public.profiles from authenticated;
grant insert (user_id, subtitles_enabled) on public.profiles to authenticated;
revoke update on public.profiles from authenticated;
grant update (subtitles_enabled) on public.profiles to authenticated;

alter table public.videos
    add column if not exists user_access text[] not null default '{}'::text[];

create or replace function public.is_admin()
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1
        from public.profiles
        where user_id = auth.uid() and admin_access = true
    );
$$;

revoke all on function public.is_admin() from public;
grant execute on function public.is_admin() to authenticated;

drop policy if exists "Authenticated users can read videos" on public.videos;
create policy "Users can read permitted videos"
    on public.videos for select to authenticated
    using (
        public.is_admin()
        or coalesce(cardinality(user_access), 0) = 0
        or lower(auth.jwt()->>'email') = any(user_access)
    );

drop policy if exists "Admins can update video access" on public.videos;
create policy "Admins can update video access"
    on public.videos for update to authenticated
    using (public.is_admin())
    with check (public.is_admin());

grant update on public.videos to authenticated;

-- auth.users is not directly readable by the authenticated role. This function
-- exposes only the fields needed by the admin panel after checking is_admin().
create or replace function public.admin_list_users()
returns table (user_id uuid, email text, admin_access boolean)
language sql
stable
security definer
set search_path = public, auth
as $$
    select u.id, u.email, coalesce(p.admin_access, false)
    from auth.users u
    left join public.profiles p on p.user_id = u.id
    where public.is_admin()
    order by lower(u.email);
$$;

revoke all on function public.admin_list_users() from public;
grant execute on function public.admin_list_users() to authenticated;
