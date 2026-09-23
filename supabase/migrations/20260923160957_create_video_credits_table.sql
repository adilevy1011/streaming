CREATE TABLE IF NOT EXISTS public.video_credits (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    media_path TEXT NOT NULL UNIQUE,
    credits_start_seconds NUMERIC NOT NULL CHECK (credits_start_seconds >= 0),
    credits_end_seconds NUMERIC CHECK (
        credits_end_seconds IS NULL OR credits_end_seconds >= credits_start_seconds
    ),
    confidence_score NUMERIC CHECK (confidence_score BETWEEN 0 AND 1),
    detected_via TEXT DEFAULT 'gemini-2.5-flash',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

ALTER TABLE public.video_credits ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Authenticated users can read video credits" ON public.video_credits;
CREATE POLICY "Authenticated users can read video credits"
    ON public.video_credits FOR SELECT
    USING (auth.role() = 'authenticated');

GRANT SELECT ON public.video_credits TO authenticated;

CREATE INDEX IF NOT EXISTS idx_video_credits_media_path ON public.video_credits(media_path);
