create table if not exists public.video_progress (
    user_id uuid not null references auth.users(id) on delete cascade,
    media_path text not null,
    position_seconds double precision not null default 0 check (position_seconds >= 0),
    duration_seconds double precision check (duration_seconds is null or duration_seconds > 0),
    completed boolean not null default false,
    updated_at timestamptz not null default timezone('utc', now()),
    primary key (user_id, media_path)
);

comment on table public.video_progress is 'Per-user playback checkpoints for resumable video streaming.';

alter table public.video_progress enable row level security;

create policy "Users can read their own video progress"
    on public.video_progress for select
    using (auth.uid() = user_id);

create policy "Users can insert their own video progress"
    on public.video_progress for insert
    with check (auth.uid() = user_id);

create policy "Users can update their own video progress"
    on public.video_progress for update
    using (auth.uid() = user_id)
    with check (auth.uid() = user_id);

create policy "Users can delete their own video progress"
    on public.video_progress for delete
    using (auth.uid() = user_id);

grant select, insert, update, delete on public.video_progress to authenticated;
