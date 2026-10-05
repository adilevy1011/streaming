alter table public.video_progress
    add column if not exists started boolean not null default true;

-- Existing rows represent videos the user has already interacted with.
update public.video_progress
set started = true
where started is distinct from true;

comment on column public.video_progress.started is
    'Whether the user has started this video; false rows are TV episode placeholders.';
