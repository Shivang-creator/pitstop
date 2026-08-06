# Architecture

## The pipeline

Everything is one direction, and each stage has exactly one job:

```
  channel ref
      │
      ▼
  ┌─────────┐   catalog.py + youtube.py
  │  FETCH  │   quota-accounted reads → Catalog
  └─────────┘
      │  Catalog (videos, playlists, channel)
      ▼
  ┌─────────┐   checks/*.py
  │  CHECK  │   19 independent plugins, read-only → [Finding]
  └─────────┘
      │  [Finding]  (each may carry a proposed Fix)
      ▼
  ┌─────────┐   score.py
  │  SCORE  │   severity × traffic → ranked, categorised
  └─────────┘
      │
      ▼
  ┌─────────┐   planner.py
  │  PLAN   │   compose fixes → minimum set of writes, priced
  └─────────┘
      │  Plan([Change])
      ▼
  ┌─────────┐   applier.py   ← the ONLY module that writes
  │  APPLY  │
  └─────────┘
```

The CLI and the web API are both thin shells over this. Nothing that decides
*what to do* lives in `cli.py` or `server.py`, which is why the two can never
disagree about what a scan found — verified by running both against the same
fixture and comparing totals.

---

## The five decisions worth explaining

### 1. Read and write are separated by the type system, not by discipline

A `Check` receives a `Catalog` and returns `Finding`s. It has no client, no
credentials, and no way to reach a write endpoint. A `Finding` may carry a
`Fix`, which is *data describing a proposed change* — not an action.

Only `applier.py` imports anything that can mutate. So "could a buggy new check
accidentally damage a channel?" has a structural answer rather than a
procedural one, and adding checks stays cheap and safe.

### 2. Fix composition — the hardest correctness problem here

One video can trip the dead-link check, the missing-footer check and the broken-
chapters check at once. All three propose a new `description`, and each computed
its proposal from the **original** text.

Applying them naively is last-writer-wins: two of the three repairs the user
reviewed in `plan` silently vanish. That's the worst possible failure mode,
because the plan promised all three.

So fixes on the same field are **composed**: each one's edit is re-derived as a
transformation and replayed against the running value. Three shapes cover
everything the checks emit —

| Shape | Detection | Replay |
|---|---|---|
| append | `proposed.startswith(original)` | append the suffix |
| prepend | `proposed.endswith(original)` | prepend the prefix |
| substring replace | common prefix/suffix trim | `.replace(removed, inserted, 1)` |

Anything that doesn't reduce to one of those is reported as a **conflict** and
dropped, never guessed at. A change the creator didn't review is a change
Pitstop doesn't make.

Grouping is sorted by `(video_id, field, check_id)`, so two runs over unchanged
input produce byte-identical plans. That determinism is what makes the diff
reviewable — and what would let a plan be diffed in CI.

Covered by seven tests in `tests/test_planner.py`.

### 3. Quota is a first-class design constraint

`videos.update` costs 50 units against a 10,000/day pool → **200 edits/day**.
A large repair genuinely cannot complete in one day.

Three consequences shaped the code:

- **The fetcher avoids `search.list`.** It walks the uploads playlist instead:
  1 unit per 50 videos versus 100 units per call, *plus* `search.list` carries
  a separate 100-calls/day cap. A 500-video scan costs ~25 units instead of
  ~1,000. This is the single reason free public scanning is viable.
- **`plan` prices the work before doing it**, and `split_by_budget` splits into
  today/deferred rather than starting something that cannot finish.
- **Writes are grouped per video.** `videos.update` is billed per *call*, not
  per field, so three field edits on one video cost 50 units, not 150. The
  planner and the budget splitter both model this.

`QuotaLedger.charge` raises **before** spending when a call would exceed budget.
A partially-applied plan is recoverable — re-running `plan` re-diffs against
live state and only proposes what's outstanding. A 403 mid-write with no
accounting is not.

### 4. Read-modify-write on `videos.update`, in exactly one place

The API replaces the entire `snippet` part. Omitting `categoryId` or `title`
on an update **erases them**. This is the most common way to destroy a channel
with this endpoint.

`YouTubeClient.update_video` re-reads the live snippet immediately before
writing and merges. It costs 1 extra unit and it is the only code path that
writes video metadata, so the mistake can be made in exactly one place — and
isn't.

### 5. Degrade to fewer checks, never to a wrong answer

Each check declares `requires_owner` / `requires_network` / `requires_llm`. The
runner skips unavailable ones **with a stated reason**, surfaced in both the CLI
and the UI. A public scan of someone else's channel runs fewer checks; it never
guesses.

The same principle governs unknown data: `caption_available` is `None` (not
`False`) on a public scan, and the captions check skips those videos rather than
reporting a missing caption track it cannot see. A check that throws is caught
and reported as skipped, so one bad plugin can't kill a scan.

---

## Three client modes

| Mode | Credentials | Can do |
|---|---|---|
| `PUBLIC` | API key | Read any channel. Zero-friction entry point. |
| `OWNER` | OAuth | Adds tags, captions, analytics, and **writes**. |
| `FIXTURE` | none | Replays recorded responses. No network. |

`FIXTURE` isn't only a test seam. It's offline development, and it's a demo
path that cannot fail live. The ledger charges in fixture mode too, so quota
estimates are verifiable without spending a real unit.

---

## The score

```
penalty(finding) = severity.weight × traffic_multiplier
score            = 100 × (1 − min(1, Σpenalty / videos / ZERO_POINT))
```

Two properties were designed for deliberately:

**Size independence.** The traffic multiplier is relative to the channel's
**median** video, not to total channel traffic. An earlier version used
share-of-total, which made every video on a 10-video channel look like 10% of
the channel and every video on a 200-video channel look like 0.5% — so identical
problems scored differently purely because of catalog size. A test now pins
10-video and 200-video channels with the same *rate* of problems to within 3
points.

**A visible tuning constant.** `ZERO_POINT = 45` penalty-per-video. Since a
critical finding at median traffic is worth ~13, that means a channel whose
average video carries three or four critical problems scores 0. An earlier
version normalised against "every video fails every check", a denominator so
pessimistic that genuinely broken channels scored in the high 80s and the
number carried no information.

Both constants are in `score.py`, in the open. No hidden terms — every score
expands into the findings that produced it.

---

## Bugs the test suite caught

Worth recording, since all three were silent:

1. **`\b#ad\b` never matched.** `\b` asserts a word/non-word transition; at the
   start of a string followed by `#`, there is none. Blanket-wrapping every
   pattern in `\b` broke the single most important disclosure marker there is —
   so every properly-disclosed sponsorship was being reported as undisclosed.
   `_p()` now adds boundaries only where they can match.
2. **`rank()` silently degraded.** It read `Finding.impact_views`, which was
   populated as a *side effect* of `compute()`. Any caller that ranked without
   scoring first got arbitrary order. Both now call an idempotent
   `_hydrate_impact()`.
3. **The score scaled with channel size** — see above.

---

## Layout

```
pitstop/
  models.py      domain types. Catalog, Finding, Fix, Change, Plan.
  config.py      env config; every knob has a working default
  quota.py       cost table + ledger. Costs verified against Google's docs.
  youtube.py     API wrapper. 3 modes. The only module that writes.
  catalog.py     fetch orchestration, quota-shaped ordering
  checks/
    base.py      plugin contract + registry + runner
    links.py     dead links, self-rot, shorteners
    playlists.py orphans, broken items, missing series
    description.py chapters, thin text, footer
    metadata.py  tags, category, titles, captions, stale winners
    risk.py      ad-suitability audit, disclosure
    custom.py    pitstop.yaml rule engine
  score.py       scoring + ranking
  planner.py     fix composition, budget splitting
  applier.py     the only writer
  rules.py       pitstop.yaml load + strict validation
  cli.py         Typer + Rich
  server.py      FastAPI + SSE
web/             React 18 + Vite + Tailwind v4
fixtures/        recorded API shapes for offline work
scripts/         fixture generator
tests/           58 tests
```

---

## What I'd build next

- **Transcript-driven chapter generation.** Currently `description.no_chapters`
  reports but can't repair — good chapters need the transcript. The `Fix` slot
  is already there; it needs a transcription pass and an LLM step behind the
  existing `requires_llm` flag.
- **Thumbnail legibility audit.** Render at real mobile size, check contrast and
  text size, detect collision with the duration badge. Every AI thumbnail tool
  *generates*; none *audit*. And it needs no CTR data, so the API gap doesn't
  block it.
- **A GitHub Action.** `plan` output is already deterministic, so a PR that
  edits `pitstop.yaml` could get the diff posted as a comment and merging could
  apply it. CI/CD for a channel.
- **Ad-suitability on actual content** — transcription plus keyframe vision,
  rather than title and description only. Scoped and honest about being unbuilt.

---

## Serving new creators without becoming an idea generator

Pitstop originally only worked for people who already had a catalog. That is a
real gap — but the obvious fix ("add AI video ideas") is the most crowded
feature in this category, and its output is unfalsifiable.

The reframing: a new creator's problem is not *what should I make*. It is
**"I don't know what good looks like."** Pitstop already encodes what good
looks like, in 19 checks. So the same checks point outward:

| Command | Reference set | Costs |
|---|---|---|
| `trending` | Real trending videos in a category/region | **1 unit** |
| `benchmark` | Specific channels you name | ~1 unit per 50 videos |
| `draft` | Nothing — checks unpublished metadata | 0 units |

`videos.list(chart="mostPopular")` returns 50 full snippets for one quota unit.
The alternative — `search.list(order=viewCount)` — costs 100 units per call,
draws on a *separate* 100-calls/day allowance, and returns only ids, so you pay
again to hydrate them. That 100× difference is why "measure what's working" is
a free feature rather than a premium one.

### Shorts pollute every reference set

YouTube's trending chart is majority Shorts in most regions. Left in, they
dragged median video length to 151 seconds and made every long-form creator
look "far behind" on chapters — a convention Shorts never use. Benchmarking a
12-minute explainer against 30-second phone-sale clips produces confident,
wrong advice.

So `split_shorts` excludes sub-90-second videos from the reference set by
default, excludes them from *your* side too (like-for-like), reports how many
were dropped, and emits a caveat when the entire niche is Shorts rather than
silently producing meaningless numbers. Unknown duration counts as long-form —
missing data must not silently shrink the sample.

Two practices — title length and video length — are reported but never scored.
Telling someone their 6-minute video is "behind" a 22-minute one is advice, not
measurement.

### The one place suggestions are generated

`enrich.py` is the only module that invents content, and it is the most
dangerous code in the repo: a wrong dead-link report wastes a minute, while a
hallucinated tag on 200 videos is the creator's channel now saying something
they didn't say. Its rules:

1. **Grounded** — suggestions derive from the creator's own title and
   description. The model categorises and rephrases; it does not add claims.
2. **Reviewable** — output goes through `plan` as a diff like any other fix.
3. **Degrades to nothing** — no key, a timeout, malformed JSON: the check
   reports the problem *without* a fix. A missing suggestion is fine; a bad one
   is not.
4. **Bounded** — tags are sanitised, de-duplicated, length-capped per tag and
   capped against YouTube's ~500-character total (exceeding it fails the whole
   `videos.update` with an error that looks unrelated to tags).

Chapter generation requires a real timestamped transcript and refuses without
one. Chapters are claims about *when* things happen; there is no honest way to
infer that from a title. The generated list is then validated against YouTube's
actual rules (first at 00:00, 3+ chapters, 10s minimum gaps, ascending, within
duration) rather than trusted — a list violating them renders as *no* chapters,
which would look like the fix silently did nothing.

Only the highest-traffic 25 videos get suggestions. Spending 500 LLM calls on a
500-video catalog is slow and pointless when the creator reviews the first
twenty and stops.

---

## Bugs found by fuzzing, after the unit tests were green

`tests/test_robustness.py` runs every check against 21 pathological catalogs
(empty, single-video, all-private, zero-view, future-dated, emoji, RTL, CJK,
5000-character descriptions, duplicate ids, playlists referencing missing
videos). A check that throws is *caught by the runner*, so a crash is silent
data loss rather than a visible error — which makes these the cheapest tests
here to justify.

That sweep plus a targeted review found three more:

1. **Fixtures over 50 videos silently lost everything after #50.** The catalog
   fetcher had a leftover `break` in fixture mode from an earlier design where
   `hydrate_videos` didn't filter by id.
2. **The default footer marker was the footer's first line** — which, for the
   most common footer shape, is a decorative rule like `── ── ──`. Any
   description containing a separator looked like it already had the footer, so
   the check silently passed on every video it should have flagged. Now prefers
   the first line containing a URL.
3. **`captions.list` was being called for data already in hand.**
   `contentDetails.caption` arrives free with `videos.list`; the dedicated
   endpoint costs 50 units *per video*, which would have turned a 25-unit scan
   of a 500-video channel into a 25,000-unit one — two and a half days of quota
   for a boolean we already had.
