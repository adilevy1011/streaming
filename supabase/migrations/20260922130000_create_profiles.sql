create table if not exists public.profiles (
    user_id uuid primary key references auth.users(id) on delete cascade,
    subtitles_enabled boolean not null default false
);

comment on table public.profiles is 'Per-user preferences and profile data.';

alter table public.profiles enable row level security;

drop policy if exists "Users can read their own profile" on public.profiles;
create policy "Users can read their own profile"
    on public.profiles for select
    using (auth.uid() = user_id);

drop policy if exists "Users can insert their own profile" on public.profiles;
create policy "Users can insert their own profile"
    on public.profiles for insert
    with check (auth.uid() = user_id);

drop policy if exists "Users can update their own profile" on public.profiles;
create policy "Users can update their own profile"
    on public.profiles for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

grant select, insert, update on public.profiles to authenticated;

insert into public.profiles (user_id)
select id
from auth.users
on conflict (user_id) do nothing;
