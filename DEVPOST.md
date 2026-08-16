# Pitstop — Social Media Automation Hackathon submission

## Project name
```
Pitstop
```

## Elevator pitch
```
Your best video from two years ago is still pulling 400 views a day, still has a dead affiliate link, and nobody's touched it since upload. Pitstop scans your whole back catalog, finds what's costing you money, and shows you — no login required. 223 tests.
```

## Built With
```
python, fastapi, youtube-data-api, vercel, pytest
```

---

## About the project

### The tool exists for a video nobody's opened in two years

A creator four years in has 200 videos. Video #47 still pulls 400 views a day — YouTube keeps recommending it. It also has a dead affiliate link, sits in no playlist, has no chapters, and carries a title written for a trend that ended two years ago. Multiply by 200.

Every optimization tool on the market — TubeBuddy, VidIQ — points at the video you're about to upload: better titles, better thumbnails, better tags for *next time*. **Nobody looks backward.** The back catalog just sits there, quietly leaking watch time and revenue, because auditing 200 videos by hand in YouTube Studio is a week of clicking nobody has.

Pitstop is the tool that looks backward. Paste a channel, no login, and it scans the entire public catalog, scores what it finds, and ranks the findings by what they're actually costing you.

### Real scans, on real channels, right now

```
https://pitstop-lime.vercel.app
```

Paste any public channel — this isn't a demo dataset:

| Channel | Health Score | Scope |
|---|---|---|
| MKBHD | **78 (C)** | 150 of 1,841 videos, 23 playlists |
| Veritasium | **74 (C)** | 150 of 526 videos, 13 playlists |
| Kurzgesagt | **79 (C)** | 150 of 377 videos |
| TED | **86 (B)** | 150 of 5,768 videos, 60 playlists |

Every one of those numbers is on-screen in under 25 seconds, from a cold start, on a channel we don't own.

### Read-only, public, no OAuth — that's the entire design decision

Public YouTube data (titles, descriptions, view counts, playlists) needs nothing but an API key. So the public web page **audits**, and requires no login at all — anyone, including a judge, can try it on their own channel or anyone else's in the time it takes to paste a URL.

**The CLI repairs** — and repairs genuinely need OAuth, because you're mutating a channel you own. That split is deliberate: the free, zero-friction layer is the one that proves the tool is real; the authenticated layer is the one that does the work.

```bash
pitstop scan @yourchannel
pitstop connect          # guided OAuth, only when you're ready to fix things
```

### What it checks

19 checks across four categories: dead and expiring affiliate/sponsor links, orphaned videos with no playlist, missing chapters, stale metadata, thumbnail issues, SEO gaps. Every finding is priced — the interface shows what it's costing you, not just that something's wrong.

### Honest about what it didn't do

The web page has a dedicated **"What this scan did not do"** section, not a footnote:

- **Partial catalog, stated as a number.** *"Scored the 150 most recent videos of 1,841"* — never implies full coverage when it wasn't.
- **Playlist checks past 60 switch themselves off** rather than silently reporting videos as playlist-less when the scan simply never reached that far.
- **Link resolution is bounded and disclosed** — *"resolved 399 of 1,016 unique links"*, checked highest-traffic-first so the budget spends on what matters, and an unreached link is reported as unknown, never as dead.
- **2 of 19 checks need channel ownership** (tags, broken playlist items) — YouTube only returns those to the owning account, and the scan says so rather than pretending they ran.

### Built to be scriptable, not just clickable

```bash
pitstop scan @yourchannel --json -
```

`--json` now accepts `-` for stdout, so it pipes cleanly: `pitstop scan @x --json - | jq`. Human-readable output routes to stderr so the two never collide.

### 223 tests

`pytest` — 223 tests, 19 of them added for the public scanner specifically: that a link the quota budget never reached is never reported dead, that truncation always surfaces as a stated limit rather than silent coverage, that an API key can never leak into a returned error message, and that the deployed public-scan path has no import-graph route to the OAuth writer at all — asserted by importing it in a clean interpreter and checking what actually loaded.

### The bug the public launch found

Building the public endpoint surfaced something real: the Google API client embeds the full request URL — including the API key — inside its own error objects, and that string was on its way back to anonymous callers by default. Found it, fixed it with a `redact()` function that every exception-derived message now passes through, and wrote a regression test so it can't come back quietly.

I'm stating this because a tool whose whole pitch is "audit what's actually wrong" should hold itself to the same standard.

### Where it came from

Built for the original YouTube Automation Hackathon, which went dark mid-event and reappeared as this one. All prior work — the CLI, the 19 checks, the 204 original tests — predates this weekend, and I'm saying so plainly rather than letting a judge assume otherwise. What's new *this weekend* is the public web scanner: the read-only endpoint, the honest-limits UI, the redaction fix, and 19 new tests. That's the part built for Social Media Automation specifically.

### Honest limits

- Structure and cost, not truth — it can tell you a link is unreachable, not what should replace it.
- Public scans are bounded by quota; a full audit of a very large channel needs the CLI's owner-authenticated path.
- English-language findings only; no localization yet.

---

## Try it out
```
https://pitstop-lime.vercel.app
```
```
https://github.com/Shivang-creator/pitstop
```
