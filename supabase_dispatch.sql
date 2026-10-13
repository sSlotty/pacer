-- Primary trigger for the daily workflow (CLAUDE.md §12).
-- GitHub's own cron runs hours late and skips most firings, so Supabase pg_cron dispatches
-- the workflow on time instead: every 15 minutes 05:00–06:45 Asia/Bangkok, only until
-- today's summary is in `notifications`. main.py still decides whether a run sends (§11.1).
--
-- Run once in the Supabase SQL Editor. Safe to re-run. If you forked the repo, change
-- `sSlotty/pacer` in the URL below to yours.
--
-- Before running, store a fine-grained GitHub PAT (this repo only, "Actions: Read and write")
-- in Vault. Run this line by itself with the real token, and never commit the token:
--
--   select vault.create_secret('<github_pat>', 'github_dispatch_token');
--
-- To rotate it later:
--   select vault.update_secret(id, '<new_github_pat>') from vault.secrets where name = 'github_dispatch_token';

create extension if not exists pg_cron;
create extension if not exists pg_net with schema extensions;

-- pg_cron runs in UTC: 22:00–23:45 UTC = 05:00–06:45 Asia/Bangkok.
select cron.schedule(
  'pacer-dispatch',
  '*/15 22-23 * * *',
  $$
  select net.http_post(
    url := 'https://api.github.com/repos/sSlotty/pacer/actions/workflows/daily.yml/dispatches',
    body := '{"ref": "main", "inputs": {"force": "false"}}'::jsonb,
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                                     where name = 'github_dispatch_token'),
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'pacer-pg-cron',
      'Content-Type', 'application/json'
    )
  )
  where not exists (
    select 1 from public.notifications
    where date = to_char(now() at time zone 'Asia/Bangkok', 'YYYY-MM-DD')
  );
  $$
);

-- Check it is working (GitHub answers 204 on success):
--   select * from cron.job_run_details where jobid = (select jobid from cron.job where jobname = 'pacer-dispatch') order by start_time desc limit 10;
--   select id, status_code, content, created from net._http_response order by created desc limit 10;
--
-- Remove:
--   select cron.unschedule('pacer-dispatch');
