import { useMemo, useState } from "react";
import { fmt, type ScanResult, type Severity } from "../api";
import { CategoryBar, ScoreRing } from "./ScoreRing";
import { Badge, Button, Card, SEVERITY } from "./ui";

export function Report({
  scan,
  onPlan,
  onReset,
  planning,
}: {
  scan: ScanResult;
  onPlan: () => void;
  onReset: () => void;
  planning: boolean;
}) {
  const [filter, setFilter] = useState<Severity | "all" | "fixable">("all");
  const [open, setOpen] = useState<string | null>(null);

  const groups = useMemo(() => {
    if (filter === "all") return scan.groups;
    if (filter === "fixable") return scan.groups.filter((g) => g.auto_fixable > 0);
    return scan.groups.filter((g) => g.severity === filter);
  }, [scan.groups, filter]);

  const { score } = scan;
  const channelUrl = scan.channel.handle
    ? `https://www.youtube.com/@${scan.channel.handle}`
    : `https://www.youtube.com/channel/${scan.channel.id}`;

  return (
    <div className="relative z-10 mx-auto w-full max-w-6xl px-6 py-10">
      {/* ── channel header ─────────────────────────────────────────── */}
      <div className="rise flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3.5">
          {scan.channel.thumbnail_url && (
            <img
              src={scan.channel.thumbnail_url}
              alt=""
              className="h-11 w-11 rounded-full ring-1 ring-white/10"
              onError={(e) => (e.currentTarget.style.display = "none")}
            />
          )}
          <div>
            <a
              href={channelUrl}
              target="_blank"
              rel="noreferrer"
              className="text-[17px] font-semibold text-white hover:underline"
            >
              {scan.channel.title}
            </a>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 font-mono text-[11.5px] text-[color:var(--color-ink-400)]">
              <span>{fmt.int(scan.video_count)} videos scanned</span>
              <span className="text-[color:var(--color-ink-700)]">·</span>
              <span>{fmt.compact(scan.channel.subscriber_count)} subs</span>
              <span className="text-[color:var(--color-ink-700)]">·</span>
              <span>{fmt.compact(scan.channel.view_count)} views</span>
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <Badge tone={scan.is_owner ? "good" : "neutral"}>
            {scan.mode === "FIXTURE"
              ? "fixture · offline"
              : scan.is_owner
                ? "owner · can repair"
                : "public · read-only"}
          </Badge>
          <Button variant="ghost" onClick={onReset}>
            New scan
          </Button>
        </div>
      </div>

      {/* ── score ──────────────────────────────────────────────────── */}
      <Card className="rise mt-6 p-7" >
        <div className="flex flex-col items-center gap-9 lg:flex-row lg:items-center">
          <ScoreRing score={score.score} grade={score.grade} />

          <div className="min-w-0 flex-1">
            <p className="text-[19px] font-medium leading-snug text-white">
              {score.headline}
            </p>

            <div className="mt-4 flex flex-wrap gap-x-6 gap-y-2 font-mono text-[12px]">
              <Stat value={score.total_findings} label="findings" />
              <Stat value={score.critical} label="critical" tone="critical" />
              <Stat value={score.auto_fixable} label="auto-fixable" tone="good" />
              <Stat value={score.affected_videos} label="videos affected" />
            </div>

            <div className="mt-6 grid gap-4 sm:grid-cols-2">
              {score.categories.map((category) => (
                <CategoryBar
                  key={category.key}
                  label={category.label}
                  description={category.description}
                  score={category.score}
                  findings={category.findings}
                />
              ))}
            </div>
          </div>
        </div>

        {score.auto_fixable > 0 && (
          <div className="mt-7 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.06] pt-5">
            <p className="text-[12.5px] text-[color:var(--color-ink-300)]">
              <strong className="text-white">{score.auto_fixable}</strong> of
              these can be repaired automatically.{" "}
              {!scan.is_owner && (
                <span className="text-[color:var(--color-ink-400)]">
                  Previewing is free — applying needs owner access.
                </span>
              )}
            </p>
            <Button onClick={onPlan} disabled={planning}>
              {planning ? "Building plan…" : "Preview the repairs →"}
            </Button>
          </div>
        )}
      </Card>

      {/* ── priority videos ────────────────────────────────────────── */}
      {score.priority_videos.length > 0 && (
        <section className="rise mt-8" style={{ animationDelay: "60ms" }}>
          <SectionHeading
            title="Fix these first"
            sub="Ranked by severity weighted against how much traffic the video actually gets"
          />
          <div className="mt-3 grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
            {score.priority_videos.slice(0, 6).map((video, index) => (
              <a
                key={video.video_id}
                href={video.url}
                target="_blank"
                rel="noreferrer"
                className="group flex gap-3 rounded-lg border border-white/[0.06] bg-white/[0.015] p-2.5 transition-colors hover:border-white/[0.12] hover:bg-white/[0.04]"
              >
                <div className="relative h-[52px] w-[92px] shrink-0 overflow-hidden rounded bg-[color:var(--color-ink-850)]">
                  {video.thumbnail_url && (
                    <img
                      src={video.thumbnail_url}
                      alt=""
                      loading="lazy"
                      className="h-full w-full object-cover"
                      onError={(e) => (e.currentTarget.style.opacity = "0")}
                    />
                  )}
                  <span className="absolute left-1 top-1 rounded bg-black/75 px-1 font-mono text-[10px] text-white">
                    {index + 1}
                  </span>
                </div>
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 text-[12.5px] leading-snug text-[color:var(--color-ink-200)] group-hover:text-white">
                    {video.title}
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-[color:var(--color-ink-400)]">
                    {video.issues} issues · {fmt.compact(video.views_per_month)}/mo
                  </div>
                </div>
              </a>
            ))}
          </div>
        </section>
      )}

      {/* ── findings ───────────────────────────────────────────────── */}
      <section className="rise mt-9" style={{ animationDelay: "110ms" }}>
        <div className="flex flex-wrap items-end justify-between gap-3">
          <SectionHeading
            title="Findings"
            sub={`${scan.groups.length} distinct problems across the catalog`}
          />
          <div className="flex gap-1 rounded-lg bg-white/[0.03] p-1">
            {(
              [
                ["all", `All ${score.total_findings}`],
                ["critical", `Critical ${score.critical}`],
                ["warning", `Warning ${score.warning}`],
                ["fixable", `Fixable ${score.auto_fixable}`],
              ] as const
            ).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                className={`rounded px-2.5 py-1 text-[11.5px] font-medium transition-colors ${
                  filter === key
                    ? "bg-white/[0.09] text-white"
                    : "text-[color:var(--color-ink-400)] hover:text-[color:var(--color-ink-200)]"
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>

        <div className="mt-3 space-y-2">
          {groups.map((group) => {
            const key = `${group.check_id}|${group.title}`;
            const isOpen = open === key;
            const sev = SEVERITY[group.severity];

            return (
              <Card key={key} className="overflow-hidden">
                <button
                  onClick={() => setOpen(isOpen ? null : key)}
                  className="flex w-full items-center gap-3 p-4 text-left transition-colors hover:bg-white/[0.02]"
                >
                  <span className={`h-2 w-2 shrink-0 rounded-full ${sev.dot}`} />

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-baseline gap-2">
                      <span className="text-[13.5px] font-medium text-white">
                        {group.title}
                      </span>
                      <span className="font-mono text-[11.5px] text-[color:var(--color-ink-400)]">
                        ×{group.count}
                      </span>
                      {group.auto_fixable > 0 && (
                        <Badge tone="good">{group.auto_fixable} fixable</Badge>
                      )}
                    </div>
                    <p className="mt-0.5 line-clamp-1 text-[11.5px] text-[color:var(--color-ink-400)]">
                      {group.description}
                    </p>
                  </div>

                  {group.impact_views > 0 && (
                    <div className="hidden shrink-0 text-right sm:block">
                      <div className="font-mono text-[13px] tabular-nums text-[color:var(--color-ink-200)]">
                        {fmt.compact(group.impact_views)}
                      </div>
                      <div className="text-[10px] uppercase tracking-wider text-[color:var(--color-ink-600)]">
                        views/mo
                      </div>
                    </div>
                  )}

                  <span
                    className={`shrink-0 text-[color:var(--color-ink-600)] transition-transform ${isOpen ? "rotate-90" : ""}`}
                  >
                    ›
                  </span>
                </button>

                {isOpen && (
                  <div className="border-t border-white/[0.06] bg-black/20">
                    {group.instances.map((instance, i) => (
                      <div
                        key={`${instance.video_id}-${i}`}
                        className="flex items-start gap-3 border-b border-white/[0.03] px-4 py-2.5 last:border-0"
                      >
                        <div className="min-w-0 flex-1">
                          {instance.video_url ? (
                            <a
                              href={instance.video_url}
                              target="_blank"
                              rel="noreferrer"
                              className="line-clamp-1 text-[12px] text-[color:var(--color-ink-200)] hover:text-[color:var(--color-notice)] hover:underline"
                            >
                              {instance.video_title}
                            </a>
                          ) : (
                            <span className="text-[12px] text-[color:var(--color-ink-300)]">
                              channel-wide
                            </span>
                          )}
                          <p className="mt-0.5 break-all font-mono text-[11px] leading-relaxed text-[color:var(--color-ink-400)]">
                            {instance.detail}
                          </p>
                        </div>
                        {instance.auto_fixable && (
                          <span className="mt-0.5 shrink-0 text-[10px] text-[color:var(--color-good)]">
                            ✓ fixable
                          </span>
                        )}
                      </div>
                    ))}
                    {group.count > group.instances.length && (
                      <div className="px-4 py-2.5 text-[11px] text-[color:var(--color-ink-600)]">
                        … and {group.count - group.instances.length} more
                      </div>
                    )}
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      </section>

      {/* ── what didn't run ────────────────────────────────────────── */}
      {scan.skipped.length > 0 && (
        <section className="mt-8 rounded-lg border border-white/[0.05] bg-white/[0.012] p-4">
          <div className="text-[12px] font-medium text-[color:var(--color-ink-300)]">
            {scan.skipped.length} check
            {scan.skipped.length === 1 ? "" : "s"} didn't run
          </div>
          <div className="mt-2 space-y-1">
            {scan.skipped.map((item) => (
              <div
                key={item.check_id}
                className="font-mono text-[11px] text-[color:var(--color-ink-600)]"
              >
                <span className="text-[color:var(--color-ink-400)]">
                  {item.check_id}
                </span>{" "}
                — {item.reason}
              </div>
            ))}
          </div>
        </section>
      )}

      {/* ── founding access ────────────────────────────────────────── */}
      <div className="rise mt-10 rounded-2xl border border-white/[0.08] bg-white/[0.03] p-6">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="max-w-xl">
            <h2 className="text-[15px] font-semibold text-white">
              {score.auto_fixable > 0
                ? `${fmt.int(score.auto_fixable)} of these can be repaired automatically.`
                : "Keep this catalog from rotting again."}
            </h2>
            <p className="mt-1.5 text-[12.5px] leading-relaxed text-[color:var(--color-ink-400)]">
              The scan is free and always will be. Pitstop Pro applies the
              repairs through your own YouTube account and re-checks the
              catalog monthly, so a dead link never leaks for two years again.
              It isn't on sale yet — founding creators get it first, and set
              the price with us.
            </p>
          </div>
          <Button
            onClick={() => {
              const subject = encodeURIComponent(
                `Pitstop Pro founding access — ${scan.channel.title}`,
              );
              const body = encodeURIComponent(
                `Channel: ${channelUrl}\nVideos: ${scan.video_count}\n\nWhat would make this worth paying for, for you?\n`,
              );
              window.location.href = `mailto:zingua816@gmail.com?subject=${subject}&body=${body}`;
            }}
          >
            Ask for founding access
          </Button>
        </div>
        <p className="mt-3 font-mono text-[10.5px] text-[color:var(--color-ink-600)]">
          One email, no list, no spam. You're telling us this is worth building
          — that's all it does.
        </p>
      </div>

      <footer className="mt-8 flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.05] pt-5 font-mono text-[11px] text-[color:var(--color-ink-600)]">
        <span>
          quota {fmt.int(scan.quota.spent)}/{fmt.int(scan.quota.budget)} units ·{" "}
          {fmt.int(scan.quota.remaining)} remaining today
        </span>
        <span>
          Thumbnail CTR is not exposed by the YouTube Analytics API — Pitstop
          makes no claim about it.
        </span>
      </footer>
    </div>
  );
}

function Stat({
  value,
  label,
  tone,
}: {
  value: number;
  label: string;
  tone?: "critical" | "good";
}) {
  const color =
    tone === "critical"
      ? "text-[color:var(--color-critical)]"
      : tone === "good"
        ? "text-[color:var(--color-good)]"
        : "text-white";
  return (
    <span className="flex items-baseline gap-1.5">
      <span className={`text-[17px] font-semibold tabular-nums ${color}`}>
        {fmt.int(value)}
      </span>
      <span className="text-[color:var(--color-ink-400)]">{label}</span>
    </span>
  );
}

function SectionHeading({ title, sub }: { title: string; sub: string }) {
  return (
    <div>
      <h2 className="text-[15px] font-semibold text-white">{title}</h2>
      <p className="mt-0.5 text-[11.5px] text-[color:var(--color-ink-400)]">
        {sub}
      </p>
    </div>
  );
}
