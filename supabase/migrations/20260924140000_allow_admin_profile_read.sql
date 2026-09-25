-- Allow administrators to read the limited profile fields needed by the
-- admin dashboard and the new-video access update fallback.

drop policy if exists "Admins can read profiles" on public.profiles;
create policy "Admins can read profiles"
    on public.profiles for select to authenticated
    using (public.is_admin());
