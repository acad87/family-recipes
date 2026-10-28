# Putting the app on the iPad and iPhone

## 1. Turn on GitHub Pages (once, 2 minutes)
1. github.com/acad87/family-recipes → **Settings** → **Pages** (left menu).
2. **Source:** Deploy from a branch · **Branch:** `main` · folder `/ (root)` → **Save**.
3. Wait 1–2 minutes. The app lives at **https://acad87.github.io/family-recipes/app/**

The code is public; the recipes are not. Without a Daddy or Mummy login the database returns nothing.

## 2. On each iPad / iPhone
1. Open the link above in **Safari**.
2. Log in with the email and password from Supabase → Authentication → Users.
3. Tap **Share → Add to Home Screen → Add**. Open it from the home-screen icon from now on (it remembers the login).

## Updating
New versions load automatically the next time the app is opened with internet. If something looks stale, close the app fully and reopen it.

## If something goes wrong
- "This login isn't on the family list yet" → that user isn't in `public.members` (Supabase SQL).
- Recipes don't appear after Add → check the Inbox for a "Couldn't read" card with Retry, and n8n → Executions.
