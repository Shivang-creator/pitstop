import { useMemo, useState } from "react";
import { fmt, type ApplyResult, type PlanResult, type Progress, type ScanResult } from "../api";
import { Badge, Button, Card, Diff } from "./ui";

/** The plan review screen.
 *
 *  This is the safety surface. Everything the tool is about to do to a real
 *  channel is shown here, grouped per video, with a real diff — and nothing
 *  has happened yet. The apply button is deliberately the only destructive
 *  affordance on the page and it names the exact count it will change. */
export function PlanView({
  scan,
  plan,
  onApply,
  onBack,
  applying,
  progress,
  result,
}: {
  scan: ScanResult;
  plan: PlanResult;
  onApply: (dryRun: boolean) => void;
  onBack: () => void;
  applying: boolean;
  progress: Progress | null;
  result: ApplyResult | null;
}) {
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const byVideo = useMemo(() => {
    const map = new Map<string, typeof plan.changes>();
    for (const change of plan.changes) {
      const list = map.get(change.video_id) ?? [];
      list.push(change);
      map.set(change.video_id, list);
    }
    return [...map.entries()];
  }, [plan.changes]);

  const toggle = (id: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  if (result) return <ApplyDone result={result} scan={scan} onBack={onBack} />;

  return (
    <div className="relative z-10 mx-auto w-full max-w-4xl px-6 py-10">
      <div className="rise flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-[20px] font-semibold text-white">Plan</h1>
          <p className="mt-0.5 text-[12.5px] text-[color:var(--color-ink-400)]">
            Everything below is what <span className="font-mono">apply</span>{" "}
            would change on{" "}
            <span className="text-[color:var(--color-ink-200)]">
              {scan.channel.title}
            </span>
            . Nothing has changed yet.
          </p>
        </div>
        <Button variant="ghost" onClick={onBack} disabled={applying}>
          ← Back to report
        </Button>
      </div>

      {/* summary bar */}
      <Card className="rise mt-5 p-5" >
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          <Metric value={fmt.int(plan.changes.length)} label="edits" />
          <Metric value={fmt.int(plan.affected_videos)} label="videos" />
          <Metric
            value={`${fmt.int(plan.quota_cost)}`}
            label={`of ${fmt.int(plan.quota_budget)} quota units`}
          />
          {plan.manual_only > 0 && (
            <Metric value={fmt.int(plan.manual_only)} label="need a human" dim />
          )}
        </div>

        <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
          <div
            className="h-full rounded-full bg-[color:var(--color-notice)]"
            style={{
              width: `${Math.min(100, (plan.quota_cost / plan.quota_budget) * 100)}%`,
            }}
          />
        </div>

        {plan.deferred > 0 && (
          <p className="mt-3 text-[11.5px] leading-relaxed text-[color:var(--color-warning)]">
            {plan.deferred} further change{plan.deferred === 1 ? "" : "s"} exceed
            today's quota budget and are deferred.{" "}
            <span className="text-[color:var(--color-ink-400)]">
              Expected on large catalogs — a metadata write costs 50 units
              against a 10,000/day pool. Re-run tomorrow; the plan re-diffs
              against live state, so nothing is applied twice.
            </span>
          </p>
        )}

        {plan.conflicts.length > 0 && (
          <p className="mt-2 text-[11.5px] text-[color:var(--color-warning)]">
            {plan.conflicts.length} fix
            {plan.conflicts.length === 1 ? "" : "es"} dropped as unmergeable —
            two checks wanted to edit the same text in incompatible ways, so
            neither was guessed at.
          </p>
        )}
      </Card>

      {/* apply bar */}
      <Card className="rise mt-4 border-white/[0.1] p-5">
        {applying ? (
          <div>
            <div className="flex items-center justify-between font-mono text-[12px]">
              <span className="text-[color:var(--color-ink-200)]">
                {progress?.stage ?? "Applying…"}
              </span>
              <span className="tabular-nums text-[color:var(--color-ink-400)]">
                {progress ? `${progress.done}/${progress.total}` : ""}
              </span>
            </div>
            <div className="mt-2.5 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
              <div
                className="h-full rounded-full bg-[color:var(--color-good)] transition-[width] duration-200"
                style={{
                  width: progress?.total
                    ? `${(progress.done / progress.total) * 100}%`
                    : "8%",
                }}
              />
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-[12.5px] text-[color:var(--color-ink-300)]">
              {scan.is_owner ? (
                <>
                  This will change{" "}
                  <strong className="text-white">
                    {plan.affected_videos} real videos
                  </strong>{" "}
                  on YouTube.
                </>
              ) : (
                <>
                  This scan was read-only.{" "}
                  <span className="text-[color:var(--color-ink-400)]">
                    Applying needs owner access — run{" "}
                    <code className="font-mono">pitstop auth</code>, then
                    re-scan.
                  </span>
                </>
              )}
            </div>
            <div className="flex gap-2">
              <Button variant="ghost" onClick={() => onApply(true)}>
                Dry run
              </Button>
              <Button
                variant="danger"
                onClick={() => onApply(false)}
                disabled={!scan.is_owner}
              >
                Apply {plan.changes.length} changes
              </Button>
            </div>
          </div>
        )}
      </Card>

      {/* per-video diffs */}
      <div className="mt-6 space-y-2.5">
        {byVideo.map(([videoId, changes]) => {
          const isOpen = expanded.has(videoId);
          const description = changes.find((c) => c.field === "description");

          return (
            <Card key={videoId} className="overflow-hidden">
              <button
                onClick={() => toggle(videoId)}
                className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-white/[0.02]"
              >
                <span className="font-mono text-[color:var(--color-warning)]">
                  ~
                </span>
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-1 text-[13px] font-medium text-white">
                    {changes[0].video_title}
                  </div>
                  <div className="mt-1 flex flex-wrap gap-1.5">
                    {changes.map((change, i) => (
                      <Badge key={i}>
                        {change.field} · {change.note || "update"}
                      </Badge>
                    ))}
                  </div>
                </div>
                <span
                  className={`shrink-0 text-[color:var(--color-ink-600)] transition-transform ${isOpen ? "rotate-90" : ""}`}
                >
                  ›
                </span>
              </button>

              {isOpen && (
                <div className="space-y-3 border-t border-white/[0.06] p-4">
                  {description && (
                    <Diff
                      before={String(description.current ?? "")}
                      after={String(description.proposed ?? "")}
                    />
                  )}
                  {changes
                    .filter((c) => c.field !== "description")
                    .map((change, i) => (
                      <div
                        key={i}
                        className="rounded-lg border border-white/[0.06] bg-black/30 p-3 font-mono text-[11.5px]"
                      >
                        <div className="text-[color:var(--color-ink-400)]">
                          {change.field}
                        </div>
                        <div className="mt-1 text-[color:var(--color-good)]">
                          + {JSON.stringify(change.proposed)}
                        </div>
                      </div>
                    ))}
                  <a
                    href={changes[0].video_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-block font-mono text-[11px] text-[color:var(--color-notice)] hover:underline"
                  >
                    open on youtube ↗
                  </a>
                </div>
              )}
            </Card>
          );
        })}
      </div>
    </div>
  );
}

function ApplyDone({
  result,
  scan,
  onBack,
}: {
  result: ApplyResult;
  scan: ScanResult;
  onBack: () => void;
}) {
  return (
    <div className="relative z-10 mx-auto flex min-h-screen w-full max-w-lg flex-col items-center justify-center px-6 text-center">
      <div className="rise w-full">
        <div className="mx-auto mb-6 flex h-14 w-14 items-center justify-center rounded-full bg-[color:var(--color-good-dim)] text-2xl ring-1 ring-[color:var(--color-good)]/25">
          ✓
        </div>

        <h1 className="text-[24px] font-semibold text-white">
          {result.dry_run ? "Dry run complete" : "Channel updated"}
        </h1>

        <p className="mt-2 text-[13.5px] leading-relaxed text-[color:var(--color-ink-300)]">
          {result.dry_run ? "Would have changed " : "Changed "}
          <strong className="text-white">{result.applied} fields</strong> across{" "}
          <strong className="text-white">{result.videos} videos</strong>
          {result.dry_run && " — nothing was written."}
        </p>

        {!result.dry_run && (
          <a
            href={result.channel_url}
            target="_blank"
            rel="noreferrer"
            className="mt-6 inline-flex items-center gap-2 rounded-lg bg-white px-5 py-2.5 text-[13px] font-medium text-[color:var(--color-ink-950)] transition-colors hover:bg-white/90"
          >
            Verify on YouTube ↗
          </a>
        )}

        {result.failed.length > 0 && (
          <div className="mt-6 rounded-lg border border-[color:var(--color-critical)]/25 bg-[color:var(--color-critical-dim)]/40 p-4 text-left">
            <div className="text-[12px] font-medium text-[color:var(--color-critical)]">
              {result.failed.length} change
              {result.failed.length === 1 ? "" : "s"} failed
            </div>
            <div className="mt-2 space-y-1">
              {result.failed.slice(0, 5).map((failure, i) => (
                <div
                  key={i}
                  className="font-mono text-[10.5px] leading-relaxed text-[color:var(--color-ink-400)]"
                >
                  {failure.video_id} · {failure.field} — {failure.error.slice(0, 90)}
                </div>
              ))}
            </div>
          </div>
        )}

        {result.stopped_early && (
          <p className="mt-4 text-[11.5px] text-[color:var(--color-warning)]">
            {result.stopped_early}
          </p>
        )}

        <div className="mt-8 font-mono text-[11px] text-[color:var(--color-ink-600)]">
          {fmt.int(result.quota_spent)} quota units spent · {scan.channel.title}
        </div>

        <button
          onClick={onBack}
          className="mt-6 text-[12px] text-[color:var(--color-ink-400)] underline decoration-dotted underline-offset-4 hover:text-white"
        >
          Re-scan to confirm the score moved
        </button>
      </div>
    </div>
  );
}

function Metric({
  value,
  label,
  dim,
}: {
  value: string;
  label: string;
  dim?: boolean;
}) {
  return (
    <div>
      <div
        className={`font-mono text-[21px] font-semibold tabular-nums ${dim ? "text-[color:var(--color-ink-400)]" : "text-white"}`}
      >
        {value}
      </div>
      <div className="mt-0.5 text-[11px] text-[color:var(--color-ink-400)]">
        {label}
      </div>
    </div>
  );
}
