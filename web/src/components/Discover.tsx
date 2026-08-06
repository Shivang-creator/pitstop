import { useState } from "react";
import {
  checkDraft,
  fetchTrending,
  fmt,
  type DraftResult,
  type TrendingResult,
} from "../api";
import { Badge, Button, Card, SEVERITY } from "./ui";

const CATEGORIES = [
  ["tech", "Tech & Science"],
  ["education", "Education"],
  ["gaming", "Gaming"],
  ["howto", "How-to & Style"],
  ["entertainment", "Entertainment"],
  ["music", "Music"],
  ["comedy", "Comedy"],
  ["sports", "Sports"],
] as const;

const REGIONS = ["US", "IN", "GB", "CA", "AU", "DE", "BR", "JP"] as const;

/** "What's working in my niche" — real trending data, one quota unit.
 *
 *  Deliberately not an idea generator. Every number here is measured from
 *  videos that are trending right now, and every suggested topic is a word
 *  that demonstrably appears in those titles and demonstrably does not appear
 *  in the user's catalog. Both halves are checkable. */
export function Discover({ onBack }: { onBack: () => void }) {
  const [category, setCategory] = useState<string>("tech");
  const [region, setRegion] = useState<string>("US");
  const [compareTo, setCompareTo] = useState("");
  const [result, setResult] = useState<TrendingResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setLoading(true);
    setError(null);
    try {
      setResult(
        await fetchTrending({
          category,
          region,
          compare_to: compareTo.trim() || null,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative z-10 mx-auto w-full max-w-5xl px-6 py-10">
      <Header
        title="What's working right now"
        sub="Real trending videos, measured against your habits. Costs one quota unit."
        onBack={onBack}
      />

      <Card className="rise mt-6 p-5">
        <div className="grid gap-4 sm:grid-cols-[1fr_auto_auto]">
          <div>
            <Label>Niche</Label>
            <div className="mt-1.5 flex flex-wrap gap-1.5">
              {CATEGORIES.map(([key, label]) => (
                <button
                  key={key}
                  onClick={() => setCategory(key)}
                  className={`rounded-md px-2.5 py-1 text-[11.5px] transition-colors ${
                    category === key
                      ? "bg-white/[0.1] text-white ring-1 ring-inset ring-white/15"
                      : "bg-white/[0.03] text-[color:var(--color-ink-400)] hover:text-[color:var(--color-ink-200)]"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <Label>Region</Label>
            <select
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              className="mt-1.5 rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 font-mono text-[12.5px] text-white outline-none focus:border-[color:var(--color-notice)]/50"
            >
              {REGIONS.map((r) => (
                <option key={r} value={r}>
                  {r}
                </option>
              ))}
            </select>
          </div>

          <div className="flex items-end">
            <Button onClick={run} disabled={loading}>
              {loading ? "Fetching…" : "Show me"}
            </Button>
          </div>
        </div>

        <div className="mt-4 border-t border-white/[0.06] pt-4">
          <Label>Compare with your channel (optional)</Label>
          <input
            value={compareTo}
            onChange={(e) => setCompareTo(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && !loading && run()}
            placeholder="youtube.com/@yourchannel"
            spellCheck={false}
            className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 font-mono text-[12.5px] text-white outline-none placeholder:text-[color:var(--color-ink-600)] focus:border-[color:var(--color-notice)]/50"
          />
        </div>

        {error && (
          <p className="mt-4 text-[12px] text-[color:var(--color-critical)]">
            {error}
          </p>
        )}
      </Card>

      {result && <TrendingReport result={result} />}
    </div>
  );
}

function TrendingReport({ result }: { result: TrendingResult }) {
  const scored = result.practices.filter((p) => p.yours !== null);
  const behind = scored.filter((p) => p.verdict !== "ok");

  return (
    <div className="rise mt-6 space-y-6">
      <Card className="p-5">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-[15px] font-semibold text-white">
            {result.source}
          </h2>
          <span className="font-mono text-[11.5px] text-[color:var(--color-ink-400)]">
            {result.sample_size} long-form
            {result.shorts_excluded > 0 &&
              ` · ${result.shorts_excluded} Shorts excluded`}
          </span>
        </div>

        {result.caveat ? (
          <p className="mt-2 text-[11.5px] leading-relaxed text-[color:var(--color-warning)]">
            {result.caveat}
          </p>
        ) : (
          result.shorts_excluded > 0 && (
            <p className="mt-2 text-[11.5px] leading-relaxed text-[color:var(--color-ink-400)]">
              Shorts are excluded — their conventions would drag these numbers
              down and make long-form habits look unnecessary.
            </p>
          )
        )}

        <div className="mt-5 space-y-2.5">
          {result.practices.map((practice) => (
            <div key={practice.key} className="flex items-center gap-3">
              <span className="min-w-0 flex-1 truncate text-[12.5px] text-[color:var(--color-ink-200)]">
                {practice.label}
              </span>
              <span className="shrink-0 font-mono text-[12px] tabular-nums text-[color:var(--color-ink-300)]">
                {practice.reference}
                <span className="text-[color:var(--color-ink-600)]">
                  {practice.unit}
                </span>
              </span>
              {practice.yours !== null ? (
                <span
                  className={`w-24 shrink-0 text-right font-mono text-[12px] tabular-nums ${
                    practice.verdict === "ok"
                      ? "text-[color:var(--color-good)]"
                      : practice.verdict === "behind"
                        ? "text-[color:var(--color-warning)]"
                        : "text-[color:var(--color-critical)]"
                  }`}
                >
                  you {practice.yours}
                </span>
              ) : (
                <span className="w-24 shrink-0 text-right text-[11px] text-[color:var(--color-ink-700)]">
                  —
                </span>
              )}
            </div>
          ))}
        </div>

        {behind.length > 0 && (
          <div className="mt-5 rounded-lg border border-[color:var(--color-warning)]/20 bg-[color:var(--color-warning-dim)]/40 p-3.5">
            <div className="text-[12px] font-medium text-[color:var(--color-warning)]">
              Your biggest gaps
            </div>
            <ul className="mt-1.5 space-y-1">
              {behind.slice(0, 4).map((practice) => (
                <li
                  key={practice.key}
                  className="text-[11.5px] text-[color:var(--color-ink-300)]"
                >
                  <strong className="text-[color:var(--color-ink-200)]">
                    {practice.label}
                  </strong>{" "}
                  — they {practice.reference}
                  {practice.unit}, you {practice.yours}
                  {practice.unit}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>

      {result.gaps.length > 0 && (
        <Card className="p-5">
          <h3 className="text-[13.5px] font-semibold text-white">
            {result.your_channel
              ? "Trending now, absent from your catalog"
              : "What this niche is talking about"}
          </h3>
          <p className="mt-1 text-[11.5px] text-[color:var(--color-ink-400)]">
            {result.your_channel
              ? "Words in trending titles right now that appear nowhere in your videos. Verifiable both ways — not generated suggestions."
              : "Word frequency across trending titles. A count, not an opinion."}
          </p>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {result.gaps.slice(0, 14).map(([word, count]) => (
              <span
                key={word}
                className="rounded-md bg-[color:var(--color-notice-dim)] px-2 py-1 font-mono text-[11.5px] text-[color:var(--color-notice)] ring-1 ring-inset ring-[color:var(--color-notice)]/20"
              >
                {word}
                <span className="ml-1 opacity-50">×{count}</span>
              </span>
            ))}
          </div>
        </Card>
      )}

      {result.examples.length > 0 && (
        <Card className="p-5">
          <h3 className="text-[13.5px] font-semibold text-white">
            What they look like
          </h3>
          <div className="mt-3 space-y-1.5">
            {result.examples.slice(0, 6).map((example) => (
              <a
                key={example.video_id}
                href={example.url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center gap-3 rounded-lg px-2 py-1.5 transition-colors hover:bg-white/[0.03]"
              >
                <span className="min-w-0 flex-1 truncate text-[12.5px] text-[color:var(--color-ink-200)]">
                  {example.title}
                </span>
                {example.has_chapters && <Badge tone="good">chapters</Badge>}
                <span className="shrink-0 font-mono text-[11.5px] tabular-nums text-[color:var(--color-ink-400)]">
                  {fmt.compact(example.views)}
                </span>
              </a>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/** Pre-publish check. Needs no channel and no login — the entry point for
 *  someone who hasn't uploaded anything yet. */
export function Draft({ onBack }: { onBack: () => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [minutes, setMinutes] = useState(8);
  const [result, setResult] = useState<DraftResult | null>(null);
  const [loading, setLoading] = useState(false);

  async function run() {
    setLoading(true);
    try {
      setResult(
        await checkDraft({
          title,
          description,
          tags: tags
            .split(",")
            .map((t) => t.trim())
            .filter(Boolean),
          duration_seconds: Math.round(minutes * 60),
        }),
      );
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="relative z-10 mx-auto w-full max-w-3xl px-6 py-10">
      <Header
        title="Check it before you publish"
        sub="The same checks, run against a video you haven't uploaded yet. Fixing things now is free."
        onBack={onBack}
      />

      <Card className="rise mt-6 p-5">
        <Label>Title</Label>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="The title you're planning to use"
          className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 text-[13px] text-white outline-none placeholder:text-[color:var(--color-ink-600)] focus:border-[color:var(--color-notice)]/50"
        />
        <p className="mt-1 text-right font-mono text-[10.5px] text-[color:var(--color-ink-600)]">
          {title.length} chars {title.length > 70 && "· will be truncated"}
        </p>

        <Label className="mt-3">Description</Label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          rows={7}
          placeholder="Paste your draft description, chapters and links"
          className="mt-1.5 w-full resize-y rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 font-mono text-[12px] leading-relaxed text-white outline-none placeholder:text-[color:var(--color-ink-600)] focus:border-[color:var(--color-notice)]/50"
        />
        <p className="mt-1 text-right font-mono text-[10.5px] text-[color:var(--color-ink-600)]">
          {description.length} chars
        </p>

        <div className="mt-3 grid gap-3 sm:grid-cols-[1fr_140px]">
          <div>
            <Label>Tags (comma separated)</Label>
            <input
              value={tags}
              onChange={(e) => setTags(e.target.value)}
              placeholder="leave empty and Pitstop will suggest some"
              className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 font-mono text-[12px] text-white outline-none placeholder:text-[color:var(--color-ink-600)] focus:border-[color:var(--color-notice)]/50"
            />
          </div>
          <div>
            <Label>Length (min)</Label>
            <input
              type="number"
              min={0}
              value={minutes}
              onChange={(e) => setMinutes(Number(e.target.value))}
              className="mt-1.5 w-full rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-3 py-2 font-mono text-[12px] text-white outline-none focus:border-[color:var(--color-notice)]/50"
            />
          </div>
        </div>

        <div className="mt-4 flex justify-end">
          <Button onClick={run} disabled={loading || !title.trim()}>
            {loading ? "Checking…" : "Check this draft"}
          </Button>
        </div>
      </Card>

      {result && (
        <Card className="rise mt-5 p-5">
          {result.findings.length === 0 ? (
            <p className="text-[13px] text-[color:var(--color-good)]">
              ✓ Nothing to fix. Ship it.
            </p>
          ) : (
            <>
              <div className="flex items-baseline justify-between">
                <h3 className="text-[13.5px] font-semibold text-white">
                  {result.findings.length} to fix before you publish
                </h3>
                {result.critical > 0 && (
                  <Badge tone="critical">{result.critical} critical</Badge>
                )}
              </div>

              <div className="mt-3 space-y-2.5">
                {result.findings.map((finding, i) => (
                  <div key={i} className="flex items-start gap-2.5">
                    <span
                      className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${SEVERITY[finding.severity].dot}`}
                    />
                    <div className="min-w-0">
                      <div className="text-[12.5px] text-[color:var(--color-ink-200)]">
                        {finding.title}
                      </div>
                      <div className="text-[11.5px] text-[color:var(--color-ink-400)]">
                        {finding.detail}
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {result.suggested_tags.length > 0 && (
                <div className="mt-4 rounded-lg border border-white/[0.06] bg-black/25 p-3.5">
                  <div className="flex items-center justify-between">
                    <span className="text-[12px] font-medium text-[color:var(--color-good)]">
                      Suggested tags
                    </span>
                    <button
                      onClick={() => setTags(result.suggested_tags.join(", "))}
                      className="text-[11px] text-[color:var(--color-notice)] underline decoration-dotted underline-offset-2"
                    >
                      use these
                    </button>
                  </div>
                  <p className="mt-1 text-[10.5px] text-[color:var(--color-ink-600)]">
                    Derived from your own title and description — nothing
                    invented.
                  </p>
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {result.suggested_tags.map((tag) => (
                      <span
                        key={tag}
                        className="rounded bg-white/[0.05] px-2 py-0.5 font-mono text-[11px] text-[color:var(--color-ink-200)]"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </>
          )}
        </Card>
      )}
    </div>
  );
}

function Header({
  title,
  sub,
  onBack,
}: {
  title: string;
  sub: string;
  onBack: () => void;
}) {
  return (
    <div className="rise flex flex-wrap items-start justify-between gap-3">
      <div>
        <h1 className="text-[20px] font-semibold text-white">{title}</h1>
        <p className="mt-0.5 max-w-xl text-[12.5px] leading-relaxed text-[color:var(--color-ink-400)]">
          {sub}
        </p>
      </div>
      <Button variant="ghost" onClick={onBack}>
        ← Back
      </Button>
    </div>
  );
}

function Label({
  children,
  className = "",
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`text-[11px] uppercase tracking-wider text-[color:var(--color-ink-400)] ${className}`}
    >
      {children}
    </div>
  );
}
