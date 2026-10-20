-- =====================================================================
-- Family Recipes — Supabase schema (Phase 1)
-- Run once in Supabase: Dashboard → SQL Editor → New query → paste → Run.
-- Safe to re-run: every object is created with IF NOT EXISTS / OR REPLACE,
-- and seed rows use ON CONFLICT DO NOTHING.
-- =====================================================================

create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------
-- 1. Household members (allow-list) and access helper
-- ---------------------------------------------------------------------
-- Only users listed here can read or write anything. Sign-ups are also
-- disabled in Auth settings (see docs/SETUP.md), so this is a second lock.
create table if not exists public.members (
  user_id     uuid primary key references auth.users(id) on delete cascade,
  name        text not null,
  created_at  timestamptz not null default now()
);

create or replace function public.is_member()
returns boolean
language sql stable security definer
set search_path = ''
as $$
  select exists (select 1 from public.members m where m.user_id = auth.uid());
$$;
revoke all on function public.is_member() from public, anon;
grant execute on function public.is_member() to authenticated, service_role;

-- Generic updated_at trigger
create or replace function public.touch_updated_at()
returns trigger language plpgsql
set search_path = ''
as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- ---------------------------------------------------------------------
-- 2. Reference lists (editable; the app reads labels and order from here)
-- ---------------------------------------------------------------------
-- kind: section | nutrient | allergen | equipment | reheat
create table if not exists public.tags (
  kind   text not null check (kind in ('section','nutrient','allergen','equipment','reheat')),
  slug   text not null,
  label  text not null,
  sort   int  not null default 100,
  primary key (kind, slug)
);

insert into public.tags (kind, slug, label, sort) values
  -- Sections (a recipe can be in several)
  ('section','packed_lunch','Packed lunches',10),
  ('section','family_dinner','Family dinners',20),
  ('section','kids_dinner','Kids'' dinners',30),
  ('section','breakfast','Breakfasts',40),
  ('section','snack','Snacks and lunchbox fillers',50),
  ('section','batch_freeze','Batch cook and freeze',60),
  ('section','quick','20-minute weeknight',70),
  ('section','weekend','Weekend projects',80),
  ('section','cake_bake','Cakes and bakes',90),
  ('section','dessert','Desserts',100),
  ('section','basics','Sauces and basics',110),
  ('section','brazilian','Brazilian comfort food',120),
  ('section','baby','Baby and finger foods',130),
  -- Nutrient badges
  ('nutrient','protein','High protein',10),
  ('nutrient','iron','Iron-rich',20),
  ('nutrient','fibre','High fibre',30),
  ('nutrient','calcium','Calcium',40),
  ('nutrient','omega3','Omega-3',50),
  ('nutrient','vit_c','Vitamin C',60),
  ('nutrient','two_veg','2+ veg',70),
  ('nutrient','wholegrain','Wholegrain',80),
  ('nutrient','low_sugar','Low sugar',90),
  -- UK's 14 regulated allergens
  ('allergen','celery','Celery',10),
  ('allergen','gluten','Gluten',20),
  ('allergen','crustaceans','Crustaceans',30),
  ('allergen','eggs','Eggs',40),
  ('allergen','fish','Fish',50),
  ('allergen','lupin','Lupin',60),
  ('allergen','milk','Milk',70),
  ('allergen','molluscs','Molluscs',80),
  ('allergen','mustard','Mustard',90),
  ('allergen','peanuts','Peanuts',100),
  ('allergen','sesame','Sesame',110),
  ('allergen','soya','Soya',120),
  ('allergen','sulphites','Sulphites',130),
  ('allergen','tree_nuts','Tree nuts',140),
  -- Equipment
  ('equipment','oven','Oven',10),
  ('equipment','hob','Hob',20),
  ('equipment','air_fryer','Air fryer',30),
  ('equipment','slow_cooker','Slow cooker',40),
  ('equipment','pressure_cooker','Pressure cooker',50),
  ('equipment','blender','Blender',60),
  ('equipment','food_processor','Food processor',70),
  ('equipment','microwave','Microwave',80),
  -- Reheat methods
  ('reheat','microwave','Microwave',10),
  ('reheat','oven','Oven',20),
  ('reheat','hob','Hob',30),
  ('reheat','cold','Eat cold',40)
on conflict (kind, slug) do nothing;

-- Keep only slugs that exist in public.tags for a given kind.
create or replace function public.clean_tags(p_kind text, p_values text[])
returns text[]
language sql stable
set search_path = ''
as $$
  select coalesce(array_agg(distinct v order by v), '{}')
  from unnest(coalesce(p_values, '{}')) as v
  where exists (select 1 from public.tags t where t.kind = p_kind and t.slug = v);
$$;

-- ---------------------------------------------------------------------
-- 3. Shops and shopping categories
-- ---------------------------------------------------------------------
create table if not exists public.shops (
  id    text primary key,
  name  text not null,
  sort  int  not null default 100
);
insert into public.shops (id, name, sort) values
  ('unicorn','Unicorn Grocery',10),
  ('farm','Farm shop',20),
  ('sainsburys','Sainsbury''s',30)
on conflict (id) do nothing;

-- Which shop each ingredient category goes to by default (tap-to-move in the app)
create table if not exists public.shop_categories (
  category  text primary key,
  label     text not null,
  shop_id   text not null references public.shops(id),
  sort      int  not null default 100
);
insert into public.shop_categories (category, label, shop_id, sort) values
  ('veg','Vegetables','unicorn',10),
  ('fruit','Fruit','unicorn',20),
  ('herbs','Fresh herbs','unicorn',30),
  ('meat','Meat','farm',40),
  ('eggs','Eggs','farm',50),
  ('fish','Fish','sainsburys',60),
  ('dairy','Dairy','sainsburys',70),
  ('bakery','Bread and bakery','sainsburys',80),
  ('dry_goods','Pasta, rice and dry goods','sainsburys',90),
  ('tins_jars','Tins and jars','sainsburys',100),
  ('spices','Herbs and spices (dried)','sainsburys',110),
  ('oils_sauces','Oils, vinegars and sauces','sainsburys',120),
  ('baking','Baking','sainsburys',130),
  ('frozen','Frozen','sainsburys',140),
  ('drinks','Drinks','sainsburys',150),
  ('household','Household','sainsburys',160),
  ('other','Other','sainsburys',170)
on conflict (category) do nothing;

-- ---------------------------------------------------------------------
-- 4. Recipes
-- ---------------------------------------------------------------------
create table if not exists public.recipes (
  id                uuid primary key default gen_random_uuid(),
  status            text not null default 'inbox' check (status in ('inbox','approved','archived')),
  title             text not null,
  description       text,
  source_name       text,                 -- e.g. Instagram handle
  source_url        text,

  sections          text[] not null default '{}',   -- tags(kind='section')
  servings          int check (servings is null or servings between 1 and 60),
  batch_cook        boolean generated always as (coalesce(servings, 0) >= 6) stored,
  slow_cook         boolean not null default false,

  hands_on_min      int check (hands_on_min is null or hands_on_min >= 0),
  total_min         int check (total_min is null or total_min >= 0),
  effort            text check (effort in ('weeknight','weekend')),
  cost              text check (cost in ('£','££','£££')),

  fridge_days       int check (fridge_days is null or fridge_days between 0 and 14),
  freezer_months    int check (freezer_months is null or freezer_months between 0 and 12), -- 0 = do not freeze
  reheat            text[] not null default '{}',   -- tags(kind='reheat')
  equipment         text[] not null default '{}',   -- tags(kind='equipment')

  lunchbox          text check (lunchbox in ('ambient','ice_pack','no')), -- null = unknown
  lunchbox_notes    text,
  toddler_ok        boolean,              -- can a portion be taken out before salt/chilli/etc.
  toddler_notes     text,
  hidden_veg        int not null default 0 check (hidden_veg >= 0),

  nutrients         text[] not null default '{}',   -- tags(kind='nutrient')
  allergens         text[] not null default '{}',   -- tags(kind='allergen')
  nut_free          boolean generated always as (not (allergens && array['peanuts','tree_nuts']::text[])) stored,

  -- [{group, qty, unit, item, display, category, optional}]
  ingredients       jsonb not null default '[]' check (jsonb_typeof(ingredients) = 'array'),
  -- ["Step one…", "Step two…"]
  steps             jsonb not null default '[]' check (jsonb_typeof(steps) = 'array'),
  tips              text,

  images            text[] not null default '{}',   -- storage paths of the source screenshots
  ai_warnings       text[] not null default '{}',   -- things the reviewer should double-check
  ai_model          text,
  import_job_id     uuid,

  created_by        uuid default auth.uid() references auth.users(id) on delete set null,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),
  approved_at       timestamptz
);
create index if not exists recipes_status_idx on public.recipes (status);
drop trigger if exists recipes_touch on public.recipes;
create trigger recipes_touch before update on public.recipes
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------
-- 5. Family members (who eats) and cooking log with ratings
-- ---------------------------------------------------------------------
create table if not exists public.family (
  id       uuid primary key default gen_random_uuid(),
  name     text not null,
  kind     text not null check (kind in ('adult','child')),
  sort     int  not null default 100
);

create table if not exists public.cook_log (
  id         uuid primary key default gen_random_uuid(),
  recipe_id  uuid not null references public.recipes(id) on delete cascade,
  cooked_on  date not null default current_date,
  note       text,
  logged_by  uuid default auth.uid() references auth.users(id) on delete set null,
  created_at timestamptz not null default now()
);
create index if not exists cook_log_recipe_idx on public.cook_log (recipe_id, cooked_on desc);

create table if not exists public.cook_ratings (
  cook_log_id  uuid not null references public.cook_log(id) on delete cascade,
  family_id    uuid not null references public.family(id) on delete cascade,
  score        text not null check (score in ('loved','ate','refused')),
  primary key (cook_log_id, family_id)
);

-- One row per recipe: how often cooked, when last, and rating counts.
create or replace view public.recipe_stats
with (security_invoker = true) as
select
  r.id as recipe_id,
  count(distinct l.id)                                         as times_cooked,
  max(l.cooked_on)                                             as last_cooked,
  count(*) filter (where cr.score = 'loved')                   as loved,
  count(*) filter (where cr.score = 'ate')                     as ate,
  count(*) filter (where cr.score = 'refused')                 as refused,
  count(*) filter (where cr.score = 'loved'   and f.kind = 'child') as kids_loved,
  count(*) filter (where cr.score = 'refused' and f.kind = 'child') as kids_refused
from public.recipes r
left join public.cook_log l      on l.recipe_id = r.id
left join public.cook_ratings cr on cr.cook_log_id = l.id
left join public.family f        on f.id = cr.family_id
group by r.id;

-- ---------------------------------------------------------------------
-- 6. Freezer
-- ---------------------------------------------------------------------
create table if not exists public.freezer_items (
  id          uuid primary key default gen_random_uuid(),
  recipe_id   uuid references public.recipes(id) on delete set null,
  label       text not null,
  portions    int  not null default 1 check (portions >= 0),
  frozen_on   date not null default current_date,
  use_by      date,
  status      text not null default 'in' check (status in ('in','used','binned')),
  note        text,
  created_by  uuid default auth.uid() references auth.users(id) on delete set null,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists freezer_status_idx on public.freezer_items (status, use_by);

-- Fill use_by from the recipe's freezer life (default 3 months) when not given.
create or replace function public.freezer_default_use_by()
returns trigger language plpgsql
set search_path = ''
as $$
declare m int;
begin
  if new.use_by is null then
    select r.freezer_months into m from public.recipes r where r.id = new.recipe_id;
    new.use_by := new.frozen_on + make_interval(months => coalesce(nullif(m, 0), 3));
  end if;
  return new;
end;
$$;
drop trigger if exists freezer_use_by on public.freezer_items;
create trigger freezer_use_by before insert on public.freezer_items
  for each row execute function public.freezer_default_use_by();
drop trigger if exists freezer_touch on public.freezer_items;
create trigger freezer_touch before update on public.freezer_items
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------
-- 7. Weekly plan
-- ---------------------------------------------------------------------
-- kind: cook (make the recipe) | freezer (eat a freezer item) |
--       leftovers (from another plan entry) | note (free text, e.g. "eating out")
create table if not exists public.plan_entries (
  id               uuid primary key default gen_random_uuid(),
  day              date not null,
  slot             text not null check (slot in ('dinner','lunchbox')),
  position         int  not null default 0,
  kind             text not null default 'cook' check (kind in ('cook','freezer','leftovers','note')),
  recipe_id        uuid references public.recipes(id) on delete set null,
  freezer_item_id  uuid references public.freezer_items(id) on delete set null,
  from_entry_id    uuid references public.plan_entries(id) on delete set null,
  servings         int check (servings is null or servings between 1 and 60),
  note             text,
  done             boolean not null default false,
  created_by       uuid default auth.uid() references auth.users(id) on delete set null,
  updated_at       timestamptz not null default now(),
  unique (day, slot, position)
);
create index if not exists plan_day_idx on public.plan_entries (day);
drop trigger if exists plan_touch on public.plan_entries;
create trigger plan_touch before update on public.plan_entries
  for each row execute function public.touch_updated_at();

-- ---------------------------------------------------------------------
-- 8. Shopping list and pantry staples
-- ---------------------------------------------------------------------
create table if not exists public.shopping_items (
  id           uuid primary key default gen_random_uuid(),
  week_start   date not null,                  -- Monday of the planned week
  item_key     text,                           -- canonical "item|unit" for merging; null for manual items
  name         text not null,
  qty_text     text,                           -- e.g. "5", "750 g", "2 tins"
  category     text references public.shop_categories(category),
  shop_id      text references public.shops(id),
  from_recipes uuid[] not null default '{}',
  is_manual    boolean not null default false,
  checked      boolean not null default false,
  sort         int not null default 100,
  updated_by   uuid default auth.uid() references auth.users(id) on delete set null,
  updated_at   timestamptz not null default now()
);
create index if not exists shopping_week_idx on public.shopping_items (week_start);
drop trigger if exists shopping_touch on public.shopping_items;
create trigger shopping_touch before update on public.shopping_items
  for each row execute function public.touch_updated_at();

-- Staples are always pinned at the top of the list. needed = false means
-- "we have it". Tap to mark needed → it shows in that shop's list; tick it
-- in the shop → needed goes back to false.
create table if not exists public.staples (
  id         uuid primary key default gen_random_uuid(),
  name       text not null unique,
  aliases    text[] not null default '{}',   -- other ingredient names that count as this staple
  category   text not null default 'other' references public.shop_categories(category),
  shop_id    text references public.shops(id),  -- null = use the category's shop
  needed     boolean not null default false,
  sort       int not null default 100,
  updated_at timestamptz not null default now()
);
drop trigger if exists staples_touch on public.staples;
create trigger staples_touch before update on public.staples
  for each row execute function public.touch_updated_at();

insert into public.staples (name, aliases, category, sort) values
  ('salt', array['sea salt','table salt','flaky salt','salt and pepper'], 'spices', 10),
  ('black pepper', array['pepper','ground black pepper','salt and pepper'], 'spices', 20),
  ('olive oil', array['extra virgin olive oil','light olive oil'], 'oils_sauces', 30),
  ('vegetable oil', array['sunflower oil','rapeseed oil','cooking oil','neutral oil'], 'oils_sauces', 40),
  ('rice', array['white rice','basmati rice','long grain rice'], 'dry_goods', 50),
  ('pasta', array['spaghetti','penne','fusilli'], 'dry_goods', 60),
  ('plain flour', array['flour','all-purpose flour'], 'baking', 70),
  ('sugar', array['caster sugar','granulated sugar','white sugar'], 'baking', 80),
  ('stock cubes', array['stock cube','chicken stock cube','vegetable stock cube'], 'dry_goods', 90),
  ('garlic', array['garlic clove','garlic cloves'], 'veg', 100),
  ('onion', array['onions','brown onion','yellow onion'], 'veg', 110)
on conflict (name) do nothing;

-- ---------------------------------------------------------------------
-- 9. Import jobs (screenshots → recipe)
-- ---------------------------------------------------------------------
-- collecting: Telegram is still receiving screenshots for this recipe
-- pending:    ready to process
-- processing: n8n is reading it
-- done/error/cancelled
create table if not exists public.import_jobs (
  id           uuid primary key default gen_random_uuid(),
  source       text not null check (source in ('app','telegram','drive')),
  status       text not null default 'pending'
               check (status in ('collecting','pending','processing','done','error','cancelled')),
  images       text[] not null default '{}',   -- storage paths, in order
  texts        text[] not null default '{}',   -- pasted captions / notes, in order
  source_ref   text unique,                    -- Drive file or folder id (prevents double import)
  tg_chat_id   bigint,
  tg_user_id   bigint,
  recipe_id    uuid references public.recipes(id) on delete set null,
  error        text,
  attempts     int not null default 0,
  last_part_at timestamptz not null default now(),
  created_by   uuid default auth.uid() references auth.users(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
-- At most one "collecting" job per Telegram chat
create unique index if not exists import_jobs_one_collecting
  on public.import_jobs (tg_chat_id) where status = 'collecting';
create index if not exists import_jobs_status_idx on public.import_jobs (status, created_at desc);
drop trigger if exists import_jobs_touch on public.import_jobs;
create trigger import_jobs_touch before update on public.import_jobs
  for each row execute function public.touch_updated_at();

-- Telegram: add one screenshot (or text) to the chat's collecting job.
-- Atomic, so photos arriving in parallel never create two jobs.
create or replace function public.tg_add_part(
  p_chat_id bigint, p_user_id bigint, p_image text default null, p_text text default null)
returns table (job_id uuid, image_count int, text_count int)
language plpgsql security definer
set search_path = ''
as $$
begin
  return query
  insert into public.import_jobs as j (source, status, tg_chat_id, tg_user_id, images, texts)
  values ('telegram', 'collecting', p_chat_id, p_user_id,
          case when p_image is null then '{}' else array[p_image] end,
          case when nullif(btrim(p_text), '') is null then '{}' else array[p_text] end)
  on conflict (tg_chat_id) where status = 'collecting'
  do update set
    images       = j.images || case when p_image is null then '{}'::text[] else array[p_image] end,
    texts        = j.texts  || case when nullif(btrim(p_text), '') is null then '{}'::text[] else array[p_text] end,
    last_part_at = now()
  returning j.id, cardinality(j.images), cardinality(j.texts);
end;
$$;

-- Move a job into 'processing' only if it is currently in one of p_from.
-- Returns the job row when claimed, nothing otherwise (prevents double processing).
create or replace function public.claim_job(p_job_id uuid, p_from text[] default array['pending','collecting','error'])
returns setof public.import_jobs
language sql security definer
set search_path = ''
as $$
  update public.import_jobs
     set status = 'processing', attempts = attempts + 1, error = null
   where id = p_job_id and status = any(p_from)
     and (cardinality(images) > 0 or cardinality(texts) > 0)
  returning *;
$$;

-- Save the AI result as an inbox recipe and close the job. Unknown tag
-- values are dropped instead of failing the import.
create or replace function public.complete_job(p_job_id uuid, p_recipe jsonb, p_model text default null)
returns table (recipe_id uuid, title text)
language plpgsql security definer
set search_path = ''
as $$
declare
  j  public.import_jobs;
  r  jsonb := coalesce(p_recipe, '{}'::jsonb);
  new_id uuid;
begin
  select * into j from public.import_jobs where id = p_job_id for update;
  if not found then
    raise exception 'import job % not found', p_job_id;
  end if;
  if j.status = 'done' and j.recipe_id is not null then
    -- already completed (e.g. retried) — return the existing recipe
    return query select rc.id, rc.title from public.recipes rc where rc.id = j.recipe_id;
    return;
  end if;
  if coalesce(btrim(r->>'title'), '') = '' then
    raise exception 'recipe has no title';
  end if;

  insert into public.recipes (
    status, title, description, source_name, source_url, sections, servings, slow_cook,
    hands_on_min, total_min, effort, cost, fridge_days, freezer_months, reheat, equipment,
    lunchbox, lunchbox_notes, toddler_ok, toddler_notes, hidden_veg, nutrients, allergens,
    ingredients, steps, tips, images, ai_warnings, ai_model, import_job_id, created_by)
  values (
    'inbox',
    left(btrim(r->>'title'), 200),
    nullif(btrim(r->>'description'), ''),
    nullif(btrim(r->>'source_name'), ''),
    nullif(btrim(r->>'source_url'), ''),
    public.clean_tags('section',   array(select jsonb_array_elements_text(coalesce(r->'sections','[]')))),
    case when (r->>'servings') ~ '^\d+$' and (r->>'servings')::int between 1 and 60 then (r->>'servings')::int end,
    coalesce(r->>'slow_cook' = 'true', false),
    case when (r->>'hands_on_min') ~ '^\d+$' then (r->>'hands_on_min')::int end,
    case when (r->>'total_min') ~ '^\d+$' then (r->>'total_min')::int end,
    case when r->>'effort' in ('weeknight','weekend') then r->>'effort' end,
    case when r->>'cost' in ('£','££','£££') then r->>'cost' end,
    case when (r->>'fridge_days') ~ '^\d+$' and (r->>'fridge_days')::int <= 14 then (r->>'fridge_days')::int end,
    case when (r->>'freezer_months') ~ '^\d+$' and (r->>'freezer_months')::int <= 12 then (r->>'freezer_months')::int end,
    public.clean_tags('reheat',    array(select jsonb_array_elements_text(coalesce(r->'reheat','[]')))),
    public.clean_tags('equipment', array(select jsonb_array_elements_text(coalesce(r->'equipment','[]')))),
    case when r->>'lunchbox' in ('ambient','ice_pack','no') then r->>'lunchbox' end,
    nullif(btrim(r->>'lunchbox_notes'), ''),
    case when r->>'toddler_ok' in ('true','false') then (r->>'toddler_ok')::boolean end,
    nullif(btrim(r->>'toddler_notes'), ''),
    case when (r->>'hidden_veg') ~ '^\d+$' then least((r->>'hidden_veg')::int, 20) else 0 end,
    public.clean_tags('nutrient',  array(select jsonb_array_elements_text(coalesce(r->'nutrients','[]')))),
    public.clean_tags('allergen',  array(select jsonb_array_elements_text(coalesce(r->'allergens','[]')))),
    case when jsonb_typeof(r->'ingredients') = 'array' then r->'ingredients' else '[]'::jsonb end,
    case when jsonb_typeof(r->'steps') = 'array' then r->'steps' else '[]'::jsonb end,
    nullif(btrim(r->>'tips'), ''),
    j.images,
    array(select jsonb_array_elements_text(case when jsonb_typeof(r->'warnings') = 'array' then r->'warnings' else '[]'::jsonb end)),
    p_model,
    j.id,
    j.created_by
  ) returning id into new_id;

  update public.import_jobs set status = 'done', recipe_id = new_id, error = null where id = j.id;
  return query select rc.id, rc.title from public.recipes rc where rc.id = new_id;
end;
$$;

create or replace function public.fail_job(p_job_id uuid, p_error text)
returns void
language sql security definer
set search_path = ''
as $$
  update public.import_jobs set status = 'error', error = left(p_error, 2000) where id = p_job_id;
$$;

-- Safety sweep (n8n runs it every 10 minutes):
--  * jobs stuck in 'processing' for 20+ minutes (n8n restarted mid-way) become 'error' so Retry shows
--  * returns 'pending' jobs that were never picked up (e.g. n8n was offline when Supabase called it)
create or replace function public.sweep_jobs()
returns table (id uuid)
language plpgsql security definer
set search_path = ''
as $$
begin
  update public.import_jobs j
     set status = 'error', error = 'This took too long. Tap Retry to try again.'
   where j.status = 'processing' and j.updated_at < now() - interval '20 minutes';
  return query
    select j.id from public.import_jobs j
     where j.status = 'pending' and j.updated_at < now() - interval '2 minutes'
     order by j.created_at
     limit 5;
end;
$$;

-- Drive import: which of these source refs were already imported?
drop function if exists public.known_source_refs(text[]);
create or replace function public.known_source_refs(p_refs text[])
returns table (source_ref text)
language sql stable security definer
set search_path = ''
as $$
  select j.source_ref from public.import_jobs j where j.source_ref = any(p_refs);
$$;

-- The pipeline functions are for n8n (service role) only.
revoke all on function public.tg_add_part(bigint, bigint, text, text)  from public, anon, authenticated;
revoke all on function public.claim_job(uuid, text[])                  from public, anon, authenticated;
revoke all on function public.complete_job(uuid, jsonb, text)          from public, anon, authenticated;
revoke all on function public.fail_job(uuid, text)                     from public, anon, authenticated;
revoke all on function public.known_source_refs(text[])                from public, anon, authenticated;
revoke all on function public.sweep_jobs()                             from public, anon, authenticated;
grant execute on function public.tg_add_part(bigint, bigint, text, text) to service_role;
grant execute on function public.claim_job(uuid, text[])                 to service_role;
grant execute on function public.complete_job(uuid, jsonb, text)         to service_role;
grant execute on function public.fail_job(uuid, text)                    to service_role;
grant execute on function public.known_source_refs(text[])               to service_role;
grant execute on function public.sweep_jobs()                            to service_role;
grant execute on function public.clean_tags(text, text[])                to authenticated, service_role;

-- ---------------------------------------------------------------------
-- 10. Row level security: members only
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array[
    'members','tags','shops','shop_categories','recipes','family','cook_log','cook_ratings',
    'freezer_items','plan_entries','shopping_items','staples','import_jobs']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists members_all on public.%I', t);
    execute format(
      'create policy members_all on public.%I for all to authenticated
         using ((select public.is_member())) with check ((select public.is_member()))', t);
    execute format('revoke all on public.%I from anon', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

-- A member can see the members list but only service role / SQL editor adds members.
drop policy if exists members_all on public.members;
drop policy if exists members_read on public.members;
create policy members_read on public.members for select to authenticated
  using ((select public.is_member()));
revoke insert, update, delete on public.members from authenticated;

revoke all on public.recipe_stats from anon;
grant select on public.recipe_stats to authenticated;

-- ---------------------------------------------------------------------
-- 11. Storage bucket for screenshots (private)
-- ---------------------------------------------------------------------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('recipe-images', 'recipe-images', false, 15728640,
        array['image/jpeg','image/png','image/heic','image/heif','image/webp'])
on conflict (id) do nothing;

drop policy if exists recipe_images_member_select on storage.objects;
drop policy if exists recipe_images_member_insert on storage.objects;
drop policy if exists recipe_images_member_update on storage.objects;
drop policy if exists recipe_images_member_delete on storage.objects;
create policy recipe_images_member_select on storage.objects for select to authenticated
  using (bucket_id = 'recipe-images' and (select public.is_member()));
create policy recipe_images_member_insert on storage.objects for insert to authenticated
  with check (bucket_id = 'recipe-images' and (select public.is_member()));
create policy recipe_images_member_update on storage.objects for update to authenticated
  using (bucket_id = 'recipe-images' and (select public.is_member()));
create policy recipe_images_member_delete on storage.objects for delete to authenticated
  using (bucket_id = 'recipe-images' and (select public.is_member()));

-- ---------------------------------------------------------------------
-- 12. Live sync between the two iPads
-- ---------------------------------------------------------------------
do $$
declare t text;
begin
  foreach t in array array['recipes','plan_entries','shopping_items','staples','freezer_items','import_jobs']
  loop
    if not exists (
      select 1 from pg_publication_tables
      where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = t
    ) then
      execute format('alter publication supabase_realtime add table public.%I', t);
    end if;
  end loop;
end $$;
