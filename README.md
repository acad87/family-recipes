# Family Recipes

A private recipe library and weekly planner for our family: packed lunches, family dinners, batch cooking, the freezer and a shopping list split by shop.

- **Where we are / next steps:** [`docs/NEXT-STEPS.md`](docs/NEXT-STEPS.md)
- **Phase 1:** database + recipe import from screenshots (Telegram bot and Google Drive bulk import). See [`docs/SETUP.md`](docs/SETUP.md).
- Phase 2: iPad app (library, search, recipe page, cook mode, add recipe, inbox review).
- Phase 3: weekly plan, suggestions, shopping list.
- Phase 4: freezer inventory and ratings.

## Folders

| Path | What |
|---|---|
| `supabase/schema.sql` | All tables, access rules and import functions. Safe to re-run. |
| `supabase/02_n8n_link.sql` | Makes Supabase call n8n when a recipe is ready to read. |
| `n8n/*.json` | The five n8n workflows, ready to import. |
| `n8n/build.py` | Generates those JSON files. Edit this, then run `python3 n8n/build.py`. |

No secrets are stored in this repo. Keys live in n8n credentials and Supabase.
