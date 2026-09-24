-- Create a default profile whenever Supabase Auth creates a user.
-- The SECURITY DEFINER function is required because auth.users is managed by
-- Supabase and the inserting session may not have direct public-table grants.
create or replace function public.handle_new_user_profile()
returns trigger
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.profiles (user_id)
    values (new.id)
    on conflict (user_id) do nothing;

    return new;
end;
$$;

comment on function public.handle_new_user_profile() is
    'Creates a default public.profiles row after a new Supabase Auth user is created.';

drop trigger if exists on_auth_user_created on auth.users;

create trigger on_auth_user_created
    after insert on auth.users
    for each row
    execute function public.handle_new_user_profile();
