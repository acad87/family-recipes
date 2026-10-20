# Family Recipes — Phase 1 setup

Phase 1 gets recipes **into** the database. Once it's set up:

- Laura (or you) sends screenshots to a Telegram bot → the recipe appears in the database with status `inbox`.
- Your Instagram backlog goes into a Google Drive folder → one n8n run imports it in batches.

The iPad app (Phase 2) will read from this same database, so everything imported now will be there.

Time needed: about 45 minutes. Do the steps in order.

---

## 0. What you need

| Thing | Where | Cost |
|---|---|---|
| Supabase project | supabase.com | Free tier is enough |
| Anthropic API key + credit | console.anthropic.com | Pay per use; about 3–5p per recipe |
| Telegram bot | @BotFather in Telegram | Free |
| Your n8n | already on your VPS | — |
| Google Drive credential in n8n | Google Cloud OAuth client | Free |

Set a monthly spend limit in the Anthropic console (Settings → Limits). £10 is plenty.

---

## 1. Supabase

1. Create a new project. Pick region **London (eu-west-2)**. Save the database password in your password manager.
2. **SQL Editor → New query**. Paste all of `supabase/schema.sql` and click **Run**. It should finish with "Success. No rows returned".
3. **Authentication → Sign In / Providers**: turn **off** "Allow new users to sign up". Leave Email enabled.
4. **Authentication → Users → Add user → Create new user**. Create one user for you and one for Laura (email + password). Tick **Auto Confirm User**.
5. Back in the **SQL Editor**, run this with your real emails. It adds you both to the allow-list and the family list:

   ```sql
   insert into public.members (user_id, name)
   select id, case email when 'YOUR@EMAIL' then 'Aca' else 'Laura' end
   from auth.users where email in ('YOUR@EMAIL', 'LAURAS@EMAIL');

   insert into public.family (name, kind, sort) values
     ('Aca', 'adult', 1), ('Laura', 'adult', 2),
     ('Eldest', 'child', 3), ('Little one', 'child', 4);   -- rename the kids as you like
   ```

   Check: `select * from public.members;` should show 2 rows.

6. Collect two values (you'll paste them into n8n):
   - **Project URL**: click **Connect** at the top of the dashboard (looks like `https://abcdefgh.supabase.co`).
   - **Secret key**: **Project Settings → API Keys**. Create or reveal a **secret key** (starts with `sb_secret_`). Never put this key in the app or on GitHub.

---

## 2. Telegram bot

1. In Telegram, open **@BotFather** and send `/newbot`. Pick a name, for example "Cadinelli Recipes", and a username ending in `bot`. Copy the **token**.
2. Get your Telegram user IDs: you and Laura each send any message to **@userinfobot**. It replies with an `Id` number. Write both down.

---

## 3. n8n credentials

In n8n go to **Credentials → Add credential**. Use these **exact names**; the workflows look for them by name.

| Name (exact) | Type | Fields |
|---|---|---|
| `Supabase secret key` | Header Auth | Name: `apikey` · Value: your `sb_secret_…` key |
| `Anthropic API key` | Header Auth | Name: `x-api-key` · Value: your `sk-ant-…` key |
| `Recipes job secret` | Header Auth | Name: `x-recipes-secret` · Value: *(from step 5 below, leave for now)* |
| `Recipes Telegram bot` | Telegram API | Access Token: the BotFather token |
| `Google Drive` | Google Drive OAuth2 API | Reuse your existing one if you have it |

Leave "Recipes job secret" until step 5. You can create it then.

---

## 4. Import the workflows

For each file in `n8n/`, in this order: `1`, `5`, `4`, `3`, `2`:

**Workflows → Create workflow → ⋯ (top right) → Import from File** → pick the file → **Save**.

Then in each workflow:

1. Open the **Settings** node and paste your Supabase Project URL into `supabase_url` (no trailing slash).
2. Open every node with a red warning triangle and select the matching credential from step 3.
3. Re-link the sub-workflows. Importing gives each workflow a new ID, so these nodes lose their target:
   - Workflow **3 Process job** → node **Read recipe** → *From list* → **Recipes · 1 Extract recipe**
   - Workflow **5 Drive import (one recipe)** → node **Read recipe** → **Recipes · 1 Extract recipe**
   - Workflow **4 Drive import (run me)** → node **Import one recipe** → **Recipes · 5 Drive import (one recipe)**
4. Workflow **2 Telegram bot** → **Settings** node → `allowed_telegram_ids`: put your two IDs from step 2, comma-separated (e.g. `123456789,987654321`). Anyone else who finds the bot is ignored.
5. Workflow **4 Drive import** → **Settings** node → `drive_folder_id` (see step 7).

**Activate the workflows.** On n8n 2.x the button is **Publish**; on 1.x it's the **Active** toggle.

- Activate **2 Telegram bot** and **3 Process job**.
- On n8n 2.x, also publish **1 Extract recipe** and **5 Drive import (one recipe)**. Version 2 refuses to call a sub-workflow that isn't published; this caught me out in testing. On 1.x you can leave those two inactive.
- **4 Drive import** stays inactive. You run it by hand.

---

## 5. Link Supabase → n8n

1. Open `supabase/02_n8n_link.sql` and replace `YOUR-N8N-DOMAIN` with your n8n domain, e.g. `https://n8n.yourdomain.co.uk/webhook/recipes-job`.
2. Run it in the Supabase **SQL Editor**. The result shows a long random value under `paste_this_into_n8n`.
3. In n8n, create (or edit) the credential **Recipes job secret**: Header Auth, Name `x-recipes-secret`, Value = that random value.
4. Open workflow **3 Process job** → node **From Supabase** → check it uses that credential → save.

---

## 6. Test the Telegram bot (5 minutes)

1. Send `/start` to your bot. You should get the help message.
2. Send **2 screenshots** of one recipe. This is also the real test of Supabase Storage. If it fails, see "If nothing happens" below. After about 15 seconds the bot asks "Got 2 screenshots… tap Done".
3. Tap **✅ Done**. Within about a minute you should get "✅ Added "…" to the inbox".
4. In Supabase → **Table Editor → recipes**, the recipe should be there with status `inbox`.

If nothing happens:

- n8n → **Executions** shows which node failed.
- No reply at all: check `allowed_telegram_ids`, and check that n8n's public URL is HTTPS (Telegram requires it).
- **"Upload screenshot" fails with 401/403 or "Invalid JWT"**: this is the one thing I couldn't test without your real project. It's whether Supabase Storage accepts the new `sb_secret_` key on the `apikey` header alone. Send me the error text and I'll switch the credential setup.
- "On it!" but no result: check step 5. In Supabase run `select * from net._http_response order by created desc limit 5;` to see whether the call to n8n failed. A safety sweep also retries every 10 minutes.

**Tip for Laura:** if a screenshot has very small text and the recipe comes out wrong, send it as a **File** instead of a Photo (paperclip → File). Telegram shrinks photos; files keep full resolution.

---

## 7. Import your Instagram backlog via Google Drive

**Preparing the screenshots**

- On Instagram, open the post, tap **more** so the whole caption shows, and screenshot it. If the ingredients are in the carousel images instead, screenshot those slides.
- **Recipe that fits in 1 screenshot** → put it loose in the folder.
- **Recipe that needs 2+ screenshots** → make a **subfolder** for it (any name) and put its screenshots inside. They're read in filename order, which is the order you took them.
- iPhone screenshots are PNG, which works. HEIC *photos* are skipped, and the summary lists them.

**Upload**

1. In Google Drive, create a folder "Recipe screenshots" and upload into it. The Drive app works: **+ → Upload → Photos**.
2. Open the folder in a browser. The ID is the part of the URL after `/folders/`. Paste it into workflow **4 → Settings → `drive_folder_id`**.

**Run a test batch first**

1. `max_recipes` is set to **5**. Open workflow **4** and click **Execute workflow**.
2. When it finishes, click the **Summary** node: it shows `imported`, `failed`, titles, and any skipped files.
3. Check those 5 recipes in Supabase (or in the app's Inbox once Phase 2 is ready). If they look right, set `max_recipes` to **25** and run it again. Repeat until the summary shows 0 imported.

Running it again never imports the same file or subfolder twice. If "Only new ones" outputs 0 items, everything is already in.

If a Drive recipe failed (listed under `problems` in the Summary), it won't be retried automatically. To retry it, open Supabase → Table Editor → `import_jobs`, find the row with status `error`, and change `status` to `pending`. Supabase then hands it back to n8n. Each recipe is attempted at most 3 times, so a bad screenshot can't keep costing money.

Each recipe takes about 20–60 seconds, so a batch of 25 takes roughly 15–25 minutes.

---

## Costs (estimates)

- [Likely] Claude Sonnet 5.5 costs $2 per million input tokens and $10 per million output tokens. A recipe with 1–3 screenshots uses roughly 8–17k input tokens and 2k output tokens, so about **3–5p per recipe**.
- A 120-recipe backlog comes to about **£4–6**.
- Supabase free tier: 500 MB database and 1 GB file storage, which is thousands of screenshots.

---

## How it fits together

```
Telegram ──photos──▶ n8n "2 Telegram bot" ──▶ Supabase import_jobs (collecting)
                                   "Done" ──▶ status = pending
Supabase trigger (pg_net) ─────────────────▶ n8n "3 Process job"
                                                  └─▶ "1 Extract recipe" ──▶ Claude ──▶ recipes (inbox)
Google Drive ──▶ n8n "4 Drive import" ──▶ "5 one recipe" ──▶ "1 Extract recipe"
iPad app (Phase 2) ──▶ uploads + status = pending ──▶ same path as Telegram
```
