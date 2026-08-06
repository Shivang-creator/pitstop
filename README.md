# Pitstop

**Your YouTube back catalog is rotting. Pitstop finds the rot, prices it, and fixes it.**

A creator four years in has 200 videos. Video #47 still pulls 400 views a day —
YouTube keeps recommending it. It also has a dead affiliate link, sits in no
playlist, has no chapters, and carries a title written for a trend that ended
two years ago. Multiply by 200.

Nobody fixes this, because fixing 200 videos by hand in YouTube Studio is a
week of clicking and it is boring. So the back catalog quietly leaks money and
watch time forever.

Pitstop scans an entire channel in one pass, scores what it finds, ranks the
findings by what they're actually costing, and — on a channel you own — applies
the repairs for real through the YouTube Data API.

```
$ pitstop scan @yourchannel

  Channel health  48/100  [F]  ██████████░░░░░░░░░░
  The back catalog needs serious work.

  Category       Score    Issues
  Money leaks       22        27    Revenue and links walking out the door
  Discovery         32       188    How findable the catalog is
  Hygiene           81        16    Consistency and housekeeping
  Risk              96         1    Ad-suitability and disclosure items to review

⛔ Dead link in description  ×18   ~28,338 views/mo affected   18 auto-fixable
⚠️  Video is in no playlist   ×30   ~30,010 views/mo affected   16 auto-fixable
⚠️  No chapters              ×21   ~39,057 views/mo affected

232 findings  ·  22 critical  ·  41 auto-fixable  ·  42 videos affected
Quota: 6 / 9,000 units used (0.1%)
```

---

## Why this and not another AI generator

Almost every tool in this space **generates** — titles, thumbnails, clips,
descriptions. Pitstop is an **auditor and a repairer**, aimed at the one part
of a channel nobody builds for: everything already published.

The nearest things that exist each cover one slice:

| Tool | What it does | What it doesn't |
|---|---|---|
| BrokenTube, Youfiliate | Find dead affiliate links | Only links. No score, no other checks, no other repairs. |
| TubeBuddy bulk find-replace | Rewrite text across videos | Fire-and-forget. No preview, no diff, no undo, no quota accounting. |
| YouTube Studio | Edit one video at a time | Not built for 200 at once. |

Nothing audits the whole catalog, scores it, ranks fixes by traffic-weighted
impact, and then applies them behind a reviewable diff.

**What Pitstop deliberately is not:** an idea generator. "Give me video ideas"
is the most crowded feature in this category and the output is unfalsifiable.
The closest Pitstop gets is `gap_topics` — words that demonstrably appear in
trending titles right now and demonstrably appear nowhere in your catalog. It's
a set difference over real data, and you can verify both halves yourself.

---

## Two creators, one engine

**If you have a catalog**, Pitstop audits and repairs it:

```bash
pitstop scan  <channel>   # read-only audit of ANY channel. no login.
pitstop plan  <channel>   # exactly what would change, as a diff. changes nothing.
pitstop apply <channel>   # actually change it. requires ownership.
```

**If you don't have one yet**, the same 19 checks point outward instead:

```bash
pitstop trending --category tech --region IN   # what's working right now, measured
pitstop benchmark @you --against @them,@other  # your habits vs channels you admire
pitstop draft --title "..." --description d.txt  # check a video before you upload it
```

That second group exists because a new creator's problem isn't "what should I
make" — it's **"I don't know what good looks like."** Pitstop already encodes
what good looks like. So rather than asking a language model to opine about
best practices, it runs the same checks against videos that are demonstrably
working right now and reports what they do:

```
  What they do                             Reference     You
  Videos with working chapters                  25 %       0    far behind
  Description length                       629 chars    1398
  Tags per video                           11.5 tags       0    far behind
  Links in description                     3.5 links       2    behind
```

Every number is measured from real trending videos. `trending` costs **one
quota unit** — `videos.list(chart="mostPopular")` is the cheapest useful
endpoint YouTube exposes.

`scan` works on any public channel with nothing but an API key, which is what
makes "paste a URL, get a score in 40 seconds" possible with zero setup. `plan`
and `apply` need OAuth, because they write.

There is also a web UI covering the same flow:

```bash
pitstop serve      # → http://127.0.0.1:8000
```

---

## What it checks

19 checks, each an independent plugin. The same set runs against your catalog, against trending videos, and against an unpublished draft. Adding one is a single file plus a
decorator; nothing else in the codebase changes.

**💸 Money leaks**
- `links.dead` — every URL in every description resolved for real. HEAD, then
  GET for hosts that reject HEAD, redirects followed, retried once before
  anything is called dead.
- `links.self_rot` — descriptions pointing at your own videos that went private
- `links.shortener` — shorteners whose destination can change under you
- `risk.disclosure` — sponsored content with no `#ad` / paid-promotion disclosure

**🔍 Discovery**
- `playlist.orphan` — videos in no playlist (a dead end; nothing of yours autoplays)
- `playlist.missing_series` — an obvious series with no playlist grouping it
- `description.no_chapters` — long videos with no timestamp list
- `description.broken_chapters` — timestamps YouTube **silently rejects**: fewer
  than three, not starting at 00:00, out of order, or under the 10s minimum.
  The creator thinks they have chapters. Viewers see none.
- `description.thin` — descriptions too short to be indexed usefully
- `metadata.tags`, `metadata.captions`, `metadata.long_title`, `metadata.category`

**🧹 Hygiene**
- `metadata.lazy_title` — `VID_20240417_final2.mp4` and friends
- `metadata.stale_winner` — videos over a year old **still pulling above-median
  traffic**. Every other problem on these compounds daily; they're ranked first.
- `playlist.broken_items` — playlists still containing private/deleted videos
- `description.footer` — your standard footer missing

**⚠️ Risk**
- `risk.ad_suitability` — text conflicting with a published advertiser-friendly
  guideline. Every flag cites the guideline. **This is not a prediction** — see
  Limitations.

**📋 Custom** — `custom.rules` runs your own conventions from `pitstop.yaml`.

---

## Setup

```bash
git clone <repo> && cd pitstop
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cp .env.example .env
```

**Not sure what's configured?** `pitstop doctor` checks your setup and
names the next step. Click-by-click credential walkthrough: [SETUP.md](SETUP.md).

**Try it immediately, no credentials:**

```bash
.venv/bin/python scripts/make_fixture.py
.venv/bin/pitstop scan demo --fixture demo
```

That runs every check, the scoring model and the planner against a synthetic
42-video channel, entirely offline.

**Scan real channels** — needs a YouTube Data API key:

> [console.cloud.google.com](https://console.cloud.google.com) → new project →
> APIs & Services → Library → enable **YouTube Data API v3** → Credentials →
> Create Credentials → **API key**

Put it in `.env` as `YOUTUBE_API_KEY`.

**Repair your own channel** — needs OAuth:

> Same project → also enable **YouTube Analytics API** → OAuth consent screen →
> External, stay in **Testing**, add yourself as a test user → Scopes:
> `youtube.force-ssl` + `yt-analytics.readonly` → Credentials → Create OAuth
> client ID → **Desktop app** → download JSON → save as `client_secret.json`

Then `pitstop auth` once.

Staying in Testing mode means no Google verification review. Refresh tokens
expire after 7 days, which is irrelevant for personal use.

**Web UI:**

```bash
cd web && npm install && npm run build && cd ..
.venv/bin/pitstop serve
```

---

## Custom rules

```bash
pitstop init      # writes a starter pitstop.yaml
```

```yaml
description_footer: |
  ── ── ──
  Subscribe: https://youtube.com/@yourhandle?sub_confirmation=1

rules:
  - id: tutorials-tagged
    name: Tutorial videos carry the "tutorial" tag
    when:
      title_matches: "(?i)tutorial|how to"
    require:
      has_tag: tutorial
    severity: warning
    fix: add_tag          # omit for report-only
```

`when` selects which videos a rule applies to; `require` is what must hold.
13 predicates are available (`pitstop checks` lists them). Validation is strict
and errors name the offending rule — a config that silently does nothing is
worse than one that refuses to load.

---

## Limitations

Stated plainly, because a tool that overclaims about someone's income is worse
than one that says less.

**Thumbnail CTR is not available.** `impressionClickThroughRate` is not exposed
by the public YouTube Analytics API at all. Pitstop therefore makes **no claim
whatsoever** about thumbnail performance. Ranking uses `averageViewPercentage`
and view-rate trends instead.

**Cards and end screens cannot be written.** The Data API has no endpoint for
them ([open feature request](https://issuetracker.google.com/issues/387277988)).
Pitstop can flag them; it cannot fix them.

**Comments cannot be pinned** via the API — only their moderation status set.

**Monetization settings** need the Content Owner API, not available here.

**`risk.ad_suitability` is an audit, not a prediction.** It does not and cannot
know what YouTube's classifier will decide. Every flag names the published
guideline it maps to and quotes the triggering text, so a human can judge. It
reads title and description only — not audio, not video. The summary never
renders an all-clear.

**Dead-link detection is conservative by design.** Only unambiguous 4xx/5xx or
a connection failure that reproduces on retry counts as dead. Timeouts are
reported as unverified. Telling someone to edit 40 descriptions because their
own network hiccuped would be worse than useless.

**Dead links are flagged, not silently deleted.** Pitstop can't know the correct
replacement URL, so the proposed fix marks the link for the creator to edit
during `plan` review.

**LLM suggestions are grounded, not invented.** Tag suggestions derive from the
creator's own title and description; the model categorises and rephrases, it
does not add claims. Everything goes through `plan` as a reviewable diff, is
length- and count-capped, and degrades to *no suggestion* on any error rather
than to a bad one. Chapter generation requires a real timestamped transcript —
chapters are claims about *when* things happen, and there is no honest way to
guess that from a title, so Pitstop refuses to try.

**What genuinely is auto-fixable:** title, description, tags, category, privacy,
scheduled publish time, thumbnail, playlist membership, captions.

---

## Quota

The Data API bills in units against 10,000/day, and the costs are uneven:
reading a video costs 1, writing one costs **50**. So:

> **10,000 ÷ 50 = 200 metadata edits per day. Hard ceiling.**

A 300-video repair genuinely does not fit in one day. Pitstop refuses to
discover that halfway through: `plan` prices the work up front, and the applier
stops cleanly at the budget with a resumable remainder rather than dying on a
403 with half the channel modified.

Scanning is cheap by comparison — a 500-video catalog costs ~25 units, because
the fetcher walks the uploads playlist (1 unit per 50 videos) instead of paging
`search.list` (100 units per call, *and* separately capped at 100 calls/day).
That choice is why "scan any channel, free, no login" is viable at all.

---

## Development

```bash
.venv/bin/python -m pytest        # 204 tests
.venv/bin/pitstop checks          # list every check and what it needs
cd web && npm run dev             # UI on :5173, proxies /api to :8000
```

Architecture and design decisions: [ARCHITECTURE.md](ARCHITECTURE.md).

**Stack** — Python 3.11+, `google-api-python-client`, `httpx` (async link
checking), Typer + Rich (CLI), FastAPI + SSE (API), React 18 + Vite +
Tailwind v4 (UI). No database; scan results live in process.

---

## License

MIT.

*Independent project. Not affiliated with YouTube or Google. Uses the official
YouTube Data and Analytics APIs within their terms of service — no scraping.*
