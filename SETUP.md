# Setup — click by click

**At any point, run this to see exactly where you are:**

```bash
cd ~/Projects/pitstop
.venv/bin/pitstop doctor
```

It tells you what's done, what's missing, and what the next step is.

---

## You need two things

Both come from the **same** Google Cloud project. They do different jobs:

| # | Thing | Unlocks | Where it goes |
|---|---|---|---|
| 1 | **API key** (a string) | Scanning **any** public channel | Pasted into `.env` |
| 2 | **OAuth client** (a `.json` file) | Repairing **your own** channel | Saved as `client_secret.json` |

Step 1 alone gets you most of the way. Do it first, confirm it works, then do step 2.

**Everything already works without either of them** — the offline demo runs the
full pipeline right now:

```bash
.venv/bin/pitstop scan demo --fixture demo
```

---

# STEP 0 — Create the project (2 min)

1. Go to **[console.cloud.google.com](https://console.cloud.google.com)** and
   sign in with `zingua816@gmail.com` (the account that owns your channel).
2. Top-left, click the **project dropdown** (says "Select a project" or shows a
   current project name) → **NEW PROJECT**.
3. Name: `pitstop` → **CREATE**. Wait ~10 seconds.
4. **Make sure the dropdown now says `pitstop`.** This is the #1 mistake —
   creating keys in the wrong project. Everything below assumes you're in it.

### Enable the two APIs

5. Left menu → **APIs & Services** → **Library**.
6. Search **`YouTube Data API v3`** → click it → **ENABLE**.
7. Back to Library, search **`YouTube Analytics API`** → click it → **ENABLE**.

> Data API = reading and writing video metadata. Analytics API = the retention
> numbers Pitstop uses to rank findings by traffic. Enable both now; step 2
> needs Analytics and you don't want to come back.

---

# STEP 1 — API key (2 min)

**This unlocks scanning any public channel — the whole demo opening.**

1. Left menu → **APIs & Services** → **Credentials**.
2. Top of page → **+ CREATE CREDENTIALS** → **API key**.
3. A box pops up with your key. Click the **copy icon**.
   It looks like `AIzaSyB1cD3fGhIjKlMnOpQrStUvWxYz1234567`.

### Where to put it

Open the file — it already exists and is waiting:

```bash
open -a TextEdit ~/Projects/pitstop/.env
```

or in VS Code:

```bash
code ~/Projects/pitstop/.env
```

Find this line near the top:

```
YOUTUBE_API_KEY=
```

Paste your key **immediately after the `=`**, no spaces, no quotes:

```
YOUTUBE_API_KEY=AIzaSyB1cD3fGhIjKlMnOpQrStUvWxYz1234567
```

Save the file.

> **Can't see `.env` in Finder?** macOS hides dotfiles. Press
> **⌘ + Shift + .** in any Finder window to toggle them. Or just use the
> `code`/`open` command above, which doesn't care.

### Verify

```bash
cd ~/Projects/pitstop
.venv/bin/pitstop doctor
```

The API key row should turn **✓**. Then try a real channel:

```bash
.venv/bin/pitstop scan @mkbhd --limit 60
```

If you get a real score on a real channel — step 1 is done.

### Optional but recommended: restrict the key

Back in Credentials, click your key → under **API restrictions** pick
**Restrict key** → tick **YouTube Data API v3** → **SAVE**. If the key ever
leaks, it can only do the one harmless thing.

---

# STEP 2 — OAuth client (5 min)

**This unlocks writing to your own channel — the `apply` money shot.**

Google's console is mid-redesign, so you may see either of two layouts. Both
are listed; use whichever you get.

### 2a. Configure the consent screen

**If you see "Google Auth Platform" in the left menu** (newer layout):

1. Left menu → **Google Auth Platform** → **Branding**.
2. Fill in **App name**: `Pitstop`, **User support email**: your email.
3. Scroll down → **Developer contact information**: your email → **SAVE**.
4. Left menu → **Audience** → under *User type* choose **External** → **SAVE**.
5. Still on **Audience**, find **Test users** → **+ ADD USERS** → type
   `zingua816@gmail.com` → **SAVE**.

**If you see "OAuth consent screen" under APIs & Services** (older layout):

1. **APIs & Services** → **OAuth consent screen**.
2. User type → **External** → **CREATE**.
3. App name `Pitstop`, support email = yours, developer contact = yours →
   **SAVE AND CONTINUE**.
4. Scopes page → **SAVE AND CONTINUE** (skip it, the app requests scopes itself).
5. Test users → **+ ADD USERS** → `zingua816@gmail.com` → **SAVE AND CONTINUE**.

> **Leave it in "Testing" mode. Do not click "Publish app".** Testing mode lets
> you use sensitive scopes immediately with no Google verification review.
> Publishing would trigger a review that takes weeks. The only cost of Testing
> is that the login token expires after 7 days — irrelevant, you just re-run
> `pitstop auth`.

### 2b. Create the client

6. Left menu → **APIs & Services** → **Credentials**.
7. **+ CREATE CREDENTIALS** → **OAuth client ID**.
8. **Application type** → **Desktop app**. ⚠️ This matters — pick **Desktop
   app**, not "Web application". A Web client cannot do the local loopback
   login Pitstop uses, and `pitstop doctor` will tell you if you picked wrong.
9. Name: `Pitstop CLI` → **CREATE**.
10. A dialog appears → click **DOWNLOAD JSON**. It lands in `~/Downloads` with
    a long name like `client_secret_8471...apps.googleusercontent.com.json`.

### Where to put it

One command moves and renames it correctly:

```bash
mv ~/Downloads/client_secret_*.json ~/Projects/pitstop/client_secret.json
```

### Sign in

```bash
cd ~/Projects/pitstop
.venv/bin/pitstop auth
```

Your browser opens. Sign in as `zingua816@gmail.com`.

> You'll hit a scary **"Google hasn't verified this app"** screen. That's
> expected in Testing mode — it's *your* app. Click **Advanced** → **Go to
> Pitstop (unsafe)** → then **Allow** on the permissions.

### Verify

```bash
.venv/bin/pitstop doctor
```

All five rows should be **✓**.

---

# You're done — try it on your channel

```bash
cd ~/Projects/pitstop

# 1. Audit your channel (read-only)
.venv/bin/pitstop scan @shivangshirodkar4518 --owner

# 2. See exactly what would change (changes nothing)
.venv/bin/pitstop plan @shivangshirodkar4518

# 3. Actually change it
.venv/bin/pitstop apply @shivangshirodkar4518
```

Or the web UI:

```bash
cd web && npm run build && cd ..
.venv/bin/pitstop serve      # → http://127.0.0.1:8000
```

---

# Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `No channel found for '@...'` | Key is in a different project than the enabled API | Check the project dropdown says `pitstop`; re-create the key inside it |
| `403 accessNotConfigured` | YouTube Data API v3 not enabled | Library → search it → **ENABLE**, wait 60s |
| `403 quotaExceeded` | Burned 10,000 units today | Resets midnight Pacific. Use `--limit 50` while testing |
| `access_denied` in browser | Your email isn't a test user | Audience → Test users → add `zingua816@gmail.com` |
| `redirect_uri_mismatch` | Client type is "Web application" | Create a new client, pick **Desktop app** |
| Token stopped working after a week | Testing-mode tokens expire in 7 days | `rm .pitstop/token.json && pitstop auth` |
| Can't see `.env` in Finder | macOS hides dotfiles | **⌘ + Shift + .** — or use `code ~/Projects/pitstop/.env` |

---

# Cost

**Free.** The YouTube Data API has no billing — you get 10,000 quota units per
day at no charge and there is no card to attach. Google Cloud will offer you a
free trial; you don't need it and can ignore it.

A full scan of a 500-video channel costs ~25 of those 10,000 units. Writes are
the expensive part at 50 units each, which is why `plan` prices the job before
`apply` runs it.
