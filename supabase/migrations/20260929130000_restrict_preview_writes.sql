-- Preview generation is a trusted service-role job. Authenticated users may
-- read preview data through the existing policies, but may not create,
-- modify, or delete preview manifests or preview storage objects.

drop policy if exists "Authenticated users can write video previews" on public.video_previews;
revoke insert, update, delete on public.video_previews from authenticated;

drop policy if exists "Allow authenticated users to upload preview objects" on storage.objects;
drop policy if exists "Allow authenticated users to update preview objects" on storage.objects;
drop policy if exists "Allow authenticated users to delete preview objects" on storage.objects;
revoke insert, update, delete on storage.objects from authenticated;
