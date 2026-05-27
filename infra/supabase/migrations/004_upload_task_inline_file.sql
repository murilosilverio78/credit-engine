-- MVP fallback for certificate files when Cloudflare R2 is unavailable.
ALTER TABLE upload_tasks
  ADD COLUMN IF NOT EXISTS file_content TEXT;
