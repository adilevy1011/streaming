create table if not exists public.video_previews (
    media_path text primary key,
    sprite_prefix text not null,
    sheets jsonb not null default '[]'::jsonb,
    duration_seconds double precision not null check (duration_seconds > 0),
    interval_seconds double precision not null default 5 check (interval_seconds > 0),
    columns integer not null default 10 check (columns > 0),
    rows integer not null default 10 check (rows > 0),
    thumbnail_width integer not null default 160 check (thumbnail_width > 0),
    thumbnail_height integer not null default 90 check (thumbnail_height > 0),
    updated_at timestamptz not null default timezone('utc', now())
);

comment on table public.video_previews is 'Pre-processed sprite-sheet metadata for instant library and timeline previews.';

alter table public.video_previews enable row level security;

drop policy if exists "Authenticated users can read video previews" on public.video_previews;
create policy "Authenticated users can read video previews"
    on public.video_previews for select
    using (auth.role() = 'authenticated');

grant select on public.video_previews to authenticated;
