# Where we are and what's next

_Last updated: 28 Oct 2026_

## Done ✅

- Supabase project `family-recipes` (London): tables, access rules, logins for Daddy and Mummy, family list (Daddy, Mummy, Sisi, Tata). RLS confirmed on all 13 tables.
- n8n updated from 2.8.3 to **2.44.4**, pinned on purpose (the version all workflows are tested on). A backup was taken first: `/home/node/.n8n/backup-2026-10-27-*.json`.
- n8n credentials: `Supabase secret key`, `Anthropic API key`, `Recipes Telegram bot`, `Recipes job secret`.
- Supabase → n8n link (`02_n8n_link.sql`) pointing at `https://n8n.sitalabs.co.uk/webhook/recipes-job`.
- Workflows 1 (Extract), 2 (Telegram bot) and 3 (Process job) imported and published.
- **First live recipe imported from Telegram** ("Mustard chicken with smashed roast potatoes").
- Quality checked; prompt tuned (Brazilian tag only for Brazilian dishes, pack sizes → g/ml, UK product names, toddler alcohol/salt rule).
- Recipe pictures (screenshot + crop) live and confirmed.
- Saturated fat estimate per serving + heart-friendly swaps: installed (v5) and confirmed live.
- Google Drive credential (Google Cloud project `family-recipes-n8n`, OAuth in Testing mode — reconnect every ~7 days if Drive calls fail).
- Workflows 4 and 5 imported; **first Drive batch of 5 recipes imported successfully** (28 Oct).
- Original-post links: link files (.txt / Google Doc) in Drive subfolders, links pasted in Telegram → `recipes.source_url`. Confirmed on all 5 recipes.
- Clickable iPad mockup (design A, landscape) reviewed: "seems ok".

## Next session, in this order

### 0. ~~Install saturated fat (v5)~~ ✅
1. Re-run `supabase/schema.sql` in SQL Editor (adds `sat_fat_g`, `heart_swaps`).
2. Paste v5 `build-claude-request.js` into workflow 1 → Build Claude request (Cmd+F `sat_fat_g: estimate` = 1 match) → Save → Publish.
3. Resend one recipe and check `select title, sat_fat_g, heart_swaps from recipes order by created_at desc limit 1;`

### 1. ~~Check the first recipe's quality~~ ✅
Run the query from the chat in Supabase SQL Editor (title, ingredients, steps, allergens, lunchbox). Report anything wrong so the instructions can be tuned **before** the bulk import.

### 2. ~~Add recipe pictures~~ ✅ (kept for reference)
Claude picks the screenshot that shows the finished dish and the area to crop. Nothing to crop by hand.

1. Supabase **SQL Editor**: paste and run the whole of `supabase/schema.sql` again. It's safe to re-run; it adds the two picture fields (`cover_image`, `cover_crop`) and updates the save function.
2. n8n workflow **1 Extract recipe** → node **Build Claude request** → replace all the code with the latest `build-claude-request.js`. Check: Cmd+F `PICTURE` finds 1 match. Then **Save → Publish**.
3. Test: send the bot a recipe including the screenshot that shows the dish, tap Done. In Supabase, `select title, cover_image, cover_crop from recipes order by created_at desc limit 1;` should show a screenshot path and a crop.

**Links to the original post:** put a Google Doc or .txt with the Instagram link in the recipe's subfolder (Drive), or paste the link as a message (Telegram). The first web address found is saved as the recipe's link.

**How to screenshot from now on:** include the screenshot showing the dish photo (or a paused Reel frame) **with** the recipe screenshots, in the same Telegram batch or the same Drive subfolder.

### 3. Add Laura to the bot (5 min)
1. Laura opens the bot link, taps **Start**, sends `hi`. The bot ignores her for now.
2. n8n → Executions → **Recipes · 2 Telegram bot** → latest run → node **Understand message** → copy `user_id`.
3. Workflow 2 → **Settings** node → `allowed_telegram_ids` = `8178499856,HER_ID` → **Save → Publish**.

### 4. Google Drive backlog import (30 min + running time)
1. Create the **Google Drive** credential in n8n (OAuth). Guided step by step next session.
2. Import workflows **5** then **4**, set credentials (watch out for the gotcha below), re-link **Read recipe** and **Import one recipe**, publish **5**.
3. Screenshot the Instagram saved posts into a Drive folder: one loose screenshot per single-screen recipe, one **subfolder** per multi-screen recipe (with the dish photo inside it).
4. Run workflow 4 with `max_recipes = 5`, check them, then 25 at a time.

### 5. Phase 2: the iPad app
Design A (kitchen notebook): login, Inbox to review and approve, library with filters and plain-English search, recipe page with picture, cook mode, add recipe from screenshots.

## Lessons from setup (keep for future changes)

- **Importing a workflow silently assigns credentials.** n8n fills every Header Auth node with the most recently created Header Auth credential, without showing a warning. After any import, open each node and check its credential.
- **n8n 2.x runs the published version.** After editing a node: Save **and** Publish.
- **Claude Sonnet 5.5 API changes:** no forced `tool_choice` (use `auto` + `strict: true`), and strict schemas reject `enum` combined with a type array (use `anyOf`). Both are handled in the current code. If Anthropic ever says "Schema is too complex", set `const STRICT = false;` in Build Claude request.
- **Strict schema budget:** 14 of 16 nullable ("union") fields are used. New nullable fields must replace one or be made non-nullable.
- **Sub-workflows must be published and re-linked before the first run.** If a run fails after creating import records, they block re-runs (duplicate protection): reset them to `pending` (retries via workflow 3) or delete them.
- **Drive import stops at "Only new ones" with 0 items** = everything in the folder is already imported.
- **Re-running `schema.sql` is safe** and is how database changes are applied.
- **Never share screenshots of EasyPanel → Environment** (it shows the database password and encryption key).
