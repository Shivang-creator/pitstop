import { useEffect, useState } from "react";
import type { Capabilities, Progress } from "../api";
import { Button } from "./ui";

const EXAMPLES = [
  { label: "@mkbhd", ref: "https://www.youtube.com/@mkbhd" },
  { label: "@fireship", ref: "https://www.youtube.com/@fireship" },
  { label: "@veritasium", ref: "https://www.youtube.com/@veritasium" },
];

export function Landing({
  onScan,
  onDiscover,
  onDraft,
  capabilities,
  error,
}: {
  onScan: (channel: string, fixture: string | null, limit: number | null) => void;
  onDiscover: () => void;
  onDraft: () => void;
  capabilities: Capabilities | null;
  error: string | null;
}) {
  const [value, setValue] = useState("");
  const canScanLive = capabilities?.has_api_key ?? false;

  return (
    <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-3xl flex-col items-center justify-center px-6 py-20">
      <div className="rise w-full text-center">
        <div className="mb-5 inline-flex items-center gap-2 rounded-full border border-white/[0.07] bg-white/[0.03] px-3 py-1 text-[11px] text-[color:var(--color-ink-300)]">
          <span className="h-1.5 w-1.5 rounded-full bg-[color:var(--color-good)]" />
          {capabilities?.check_count ?? "—"} checks · read-only · no login
        </div>

        <h1 className="text-[clamp(2.4rem,7vw,3.6rem)] font-semibold leading-[1.05] tracking-tight text-white">
          Your back catalog
          <br />
          <span className="text-[color:var(--color-ink-400)]">is rotting.</span>
        </h1>

        <p className="mx-auto mt-5 max-w-xl text-[15px] leading-relaxed text-[color:var(--color-ink-300)]">
          Dead affiliate links. Videos in no playlist. Chapters that silently
          don't render. Pitstop scans an entire channel, scores what it finds,
          and — on a channel you own — fixes it for real.
        </p>
      </div>

      <div className="rise mt-9 w-full" style={{ animationDelay: "80ms" }}>
        <div className="flex flex-col gap-2 sm:flex-row">
          <div className="relative flex-1">
            <input
              value={value}
              onChange={(e) => setValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && value.trim())
                  onScan(value.trim(), null, null);
              }}
              placeholder="youtube.com/@channel  ·  @handle  ·  UC…"
              spellCheck={false}
              autoFocus
              className="w-full rounded-lg border border-white/[0.08] bg-[color:var(--color-ink-900)] px-4 py-3 font-mono text-[13.5px] text-white outline-none transition-colors placeholder:text-[color:var(--color-ink-600)] focus:border-[color:var(--color-notice)]/50 focus:ring-2 focus:ring-[color:var(--color-notice)]/15"
            />
          </div>
          <Button
            onClick={() => value.trim() && onScan(value.trim(), null, null)}
            disabled={!value.trim() || !canScanLive}
            className="sm:w-32"
          >
            Scan
          </Button>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-[12px]">
          <span className="text-[color:var(--color-ink-600)]">Try</span>
          {EXAMPLES.map((example) => (
            <button
              key={example.ref}
              onClick={() => {
                setValue(example.ref);
                if (canScanLive) onScan(example.ref, null, 120);
              }}
              disabled={!canScanLive}
              className="font-mono text-[color:var(--color-ink-400)] underline decoration-dotted underline-offset-4 transition-colors hover:text-[color:var(--color-notice)] disabled:opacity-40"
            >
              {example.label}
            </button>
          ))}

          {capabilities?.fixtures.includes("demo") && (
            <>
              <span className="text-[color:var(--color-ink-700)]">|</span>
              <button
                onClick={() => onScan("demo", "demo", null)}
                className="font-mono text-[color:var(--color-good)] underline decoration-dotted underline-offset-4"
              >
                demo fixture (offline)
              </button>
            </>
          )}
        </div>

        {!canScanLive && (
          <div className="mt-5 rounded-lg border border-[color:var(--color-warning)]/20 bg-[color:var(--color-warning-dim)]/50 p-4 text-[12.5px] leading-relaxed text-[color:var(--color-ink-300)]">
            <strong className="text-[color:var(--color-warning)]">
              No API key configured.
            </strong>{" "}
            Live scanning needs a YouTube Data API key in{" "}
            <code className="font-mono text-[color:var(--color-ink-200)]">.env</code>
            . The demo fixture runs fully offline in the meantime — every check,
            the score, the plan diff, all of it.
          </div>
        )}

        {error && (
          <div className="mt-5 rounded-lg border border-[color:var(--color-critical)]/25 bg-[color:var(--color-critical-dim)]/50 p-4 text-[12.5px] text-[color:var(--color-critical)]">
            {error}
          </div>
        )}
      </div>

      {/* Haven't published anything yet? The audit above needs a catalog.
          These two don't. */}
      <div
        className="rise mt-12 w-full border-t border-white/[0.05] pt-8"
        style={{ animationDelay: "140ms" }}
      >
        <p className="text-center text-[11.5px] text-[color:var(--color-ink-600)]">
          No catalog yet? Start here instead.
        </p>
        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <button
            onClick={onDiscover}
            disabled={!canScanLive}
            className="group rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 text-left transition-colors hover:border-white/[0.14] hover:bg-white/[0.04] disabled:opacity-40"
          >
            <div className="text-[13px] font-medium text-white">
              What's working in my niche →
            </div>
            <div className="mt-1 text-[11.5px] leading-snug text-[color:var(--color-ink-400)]">
              Real trending videos, measured. What they do that you don't —
              chapters, tags, description length. One quota unit.
            </div>
          </button>

          <button
            onClick={onDraft}
            className="group rounded-xl border border-white/[0.07] bg-white/[0.015] p-4 text-left transition-colors hover:border-white/[0.14] hover:bg-white/[0.04]"
          >
            <div className="text-[13px] font-medium text-white">
              Check a video before I publish →
            </div>
            <div className="mt-1 text-[11.5px] leading-snug text-[color:var(--color-ink-400)]">
              Paste a draft title and description. Same checks, run before the
              mistakes are permanent. No channel, no login.
            </div>
          </button>
        </div>
      </div>

      <div
        className="rise mt-10 grid w-full grid-cols-2 gap-x-8 gap-y-5 border-t border-white/[0.05] pt-8 sm:grid-cols-4"
        style={{ animationDelay: "200ms" }}
      >
        {[
          ["Money leaks", "Dead links, broken affiliate URLs, undisclosed sponsorships"],
          ["Discovery", "Orphaned videos, missing chapters, thin metadata"],
          ["Hygiene", "Placeholder titles, footer drift, stale winners"],
          ["Repair", "Preview every change, then apply it for real"],
        ].map(([title, body]) => (
          <div key={title}>
            <div className="text-[12px] font-medium text-[color:var(--color-ink-200)]">
              {title}
            </div>
            <div className="mt-1 text-[11.5px] leading-snug text-[color:var(--color-ink-400)]">
              {body}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function Scanning({ progress }: { progress: Progress | null }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(timer);
  }, []);

  const pct =
    progress?.total && progress.total > 0
      ? Math.min(100, Math.round((progress.done / progress.total) * 100))
      : null;

  return (
    <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-md flex-col items-center justify-center px-6">
      <div className="w-full text-center">
        <div className="relative mx-auto mb-8 h-16 w-16">
          <div className="pulse-ring absolute inset-0 rounded-full border-2 border-[color:var(--color-notice)]/30" />
          <div className="absolute inset-2 rounded-full border-2 border-t-[color:var(--color-notice)] border-r-transparent border-b-transparent border-l-transparent [animation:spin_1s_linear_infinite]" />
        </div>

        <div className="font-mono text-[13px] text-[color:var(--color-ink-200)]">
          {progress?.stage ?? "Starting…"}
        </div>

        <div className="mt-4 h-1 overflow-hidden rounded-full bg-white/[0.05]">
          {pct !== null ? (
            <div
              className="h-full rounded-full bg-[color:var(--color-notice)] transition-[width] duration-300"
              style={{ width: `${pct}%` }}
            />
          ) : (
            <div className="sweep h-full w-1/4 rounded-full bg-[color:var(--color-notice)]" />
          )}
        </div>

        <div className="mt-3 flex justify-between font-mono text-[11px] text-[color:var(--color-ink-600)]">
          <span>
            {progress?.phase === "checks" ? "running checks" : "fetching catalog"}
          </span>
          <span className="tabular-nums">
            {pct !== null && `${progress!.done}/${progress!.total} · `}
            {elapsed}s
          </span>
        </div>

        <p className="mt-8 text-[11.5px] leading-relaxed text-[color:var(--color-ink-600)]">
          Every URL in every description is resolved for real. On a large
          catalog that is the slow part.
        </p>
      </div>
    </div>
  );
}
