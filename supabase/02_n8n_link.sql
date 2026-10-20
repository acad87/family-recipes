-- =====================================================================
-- Family Recipes — link Supabase to your n8n (run AFTER schema.sql)
--
-- When the app (or a Telegram "Done" tap) marks an import job as
-- 'pending', Postgres calls your n8n webhook, which reads the recipe.
--
-- BEFORE RUNNING: replace YOUR-N8N-DOMAIN below with your n8n address,
-- e.g. https://n8n.example.co.uk
-- Safe to re-run (it updates the URL and keeps the secret).
-- =====================================================================

create extension if not exists pg_net with schema extensions;

create schema if not exists private;
revoke all on schema private from public, anon, authenticated;

create table if not exists private.settings (
  key   text primary key,
  value text not null
);

insert into private.settings (key, value) values
  ('n8n_job_webhook', 'https://YOUR-N8N-DOMAIN/webhook/recipes-job')
on conflict (key) do update set value = excluded.value;

-- Random shared secret, generated once. n8n checks it on every call.
insert into private.settings (key, value) values
  ('n8n_job_secret', encode(extensions.gen_random_bytes(24), 'hex'))
on conflict (key) do nothing;

create or replace function public.notify_job_pending()
returns trigger
language plpgsql security definer
set search_path = ''
as $$
declare
  v_url    text;
  v_secret text;
begin
  if new.status = 'pending'
     and (tg_op = 'INSERT' or old.status is distinct from 'pending') then
    select value into v_url    from private.settings where key = 'n8n_job_webhook';
    select value into v_secret from private.settings where key = 'n8n_job_secret';
    if v_url is not null and v_url not like '%YOUR-N8N-DOMAIN%' then
      perform net.http_post(
        url     := v_url,
        body    := jsonb_build_object('job_id', new.id),
        headers := jsonb_build_object('Content-Type', 'application/json',
                                      'x-recipes-secret', v_secret),
        timeout_milliseconds := 5000
      );
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists import_jobs_notify on public.import_jobs;
create trigger import_jobs_notify
  after insert or update of status on public.import_jobs
  for each row execute function public.notify_job_pending();

-- Show the secret so you can paste it into n8n (credential "Recipes job secret").
select value as paste_this_into_n8n from private.settings where key = 'n8n_job_secret';
