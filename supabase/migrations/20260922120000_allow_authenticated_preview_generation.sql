-- Allow the authenticated preview worker fallback to manage generated previews.
-- The worker authenticates through /api/auth/login, which enforces the
-- application's ALLOWED_EMAILS list before issuing the user access token.

drop policy if exists "Authenticated users can write video previews" on public.video_previews;
create policy "Authenticated users can write video previews"
    on public.video_previews for all
    to authenticated
    using (auth.role() = 'authenticated')
    with check (auth.role() = 'authenticated');

grant insert, update, delete on public.video_previews to authenticated;

drop policy if exists "Allow authenticated users to upload preview objects" on storage.objects;
create policy "Allow authenticated users to upload preview objects"
    on storage.objects for insert
    to authenticated
    with check (bucket_id = 'media' and name ~ '^__previews/');

drop policy if exists "Allow authenticated users to update preview objects" on storage.objects;
create policy "Allow authenticated users to update preview objects"
    on storage.objects for update
    to authenticated
    using (bucket_id = 'media' and name ~ '^__previews/')
    with check (bucket_id = 'media' and name ~ '^__previews/');

drop policy if exists "Allow authenticated users to delete preview objects" on storage.objects;
create policy "Allow authenticated users to delete preview objects"
    on storage.objects for delete
    to authenticated
    using (bucket_id = 'media' and name ~ '^__previews/');
