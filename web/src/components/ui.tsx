import type { ReactNode } from "react";
import type { Severity } from "../api";

export const SEVERITY: Record<
  Severity,
  { label: string; text: string; bg: string; ring: string; dot: string }
> = {
  critical: {
    label: "Critical",
    text: "text-[color:var(--color-critical)]",
    bg: "bg-[color:var(--color-critical-dim)]",
    ring: "ring-[color:var(--color-critical)]/25",
    dot: "bg-[color:var(--color-critical)]",
  },
  warning: {
    label: "Warning",
    text: "text-[color:var(--color-warning)]",
    bg: "bg-[color:var(--color-warning-dim)]",
    ring: "ring-[color:var(--color-warning)]/25",
    dot: "bg-[color:var(--color-warning)]",
  },
  notice: {
    label: "Notice",
    text: "text-[color:var(--color-notice)]",
    bg: "bg-[color:var(--color-notice-dim)]",
    ring: "ring-[color:var(--color-notice)]/25",
    dot: "bg-[color:var(--color-notice)]",
  },
};

export function scoreColor(score: number): string {
  if (score >= 80) return "var(--color-good)";
  if (score >= 60) return "var(--color-warning)";
  if (score >= 40) return "#ff8f4d";
  return "var(--color-critical)";
}

export function Card({
  children,
  className = "",
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={`relative rounded-xl border border-white/[0.06] bg-[color:var(--color-ink-900)]/70 backdrop-blur-sm ${className}`}
    >
      {children}
    </div>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "good" | "critical" | "warning";
}) {
  const tones = {
    neutral: "bg-white/[0.04] text-[color:var(--color-ink-300)] ring-white/[0.06]",
    good: "bg-[color:var(--color-good-dim)] text-[color:var(--color-good)] ring-[color:var(--color-good)]/20",
    critical:
      "bg-[color:var(--color-critical-dim)] text-[color:var(--color-critical)] ring-[color:var(--color-critical)]/20",
    warning:
      "bg-[color:var(--color-warning-dim)] text-[color:var(--color-warning)] ring-[color:var(--color-warning)]/20",
  };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-medium ring-1 ring-inset ${tones[tone]}`}
    >
      {children}
    </span>
  );
}

export function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  className = "",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "primary" | "ghost" | "danger";
  disabled?: boolean;
  className?: string;
}) {
  const variants = {
    primary:
      "bg-white text-[color:var(--color-ink-950)] hover:bg-white/90 disabled:bg-white/20 disabled:text-white/40",
    ghost:
      "bg-white/[0.04] text-[color:var(--color-ink-200)] hover:bg-white/[0.08] ring-1 ring-inset ring-white/[0.07]",
    danger:
      "bg-[color:var(--color-critical)] text-white hover:brightness-110 disabled:opacity-40",
  };
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-all duration-150 disabled:cursor-not-allowed ${variants[variant]} ${className}`}
    >
      {children}
    </button>
  );
}

/** Unified-diff renderer for description changes.
 *
 *  Computed client-side so the server never ships two full copies of every
 *  description over the wire — on a 200-video plan that is the difference
 *  between a ~200KB response and several megabytes. */
export function Diff({
  before,
  after,
  maxLines = 14,
}: {
  before: string;
  after: string;
  maxLines?: number;
}) {
  const rows = diffLines(before, after);
  const shown = rows.slice(0, maxLines);
  const hidden = rows.length - shown.length;

  return (
    <div className="overflow-x-auto rounded-lg border border-white/[0.06] bg-black/40">
      <pre className="min-w-full p-3 font-mono text-[11.5px] leading-[1.6]">
        {shown.map((row, i) => (
          <div
            key={i}
            className={
              row.kind === "add"
                ? "bg-[color:var(--color-good)]/[0.09] text-[color:var(--color-good)]"
                : row.kind === "del"
                  ? "bg-[color:var(--color-critical)]/[0.09] text-[color:var(--color-critical)]"
                  : "text-[color:var(--color-ink-400)]"
            }
          >
            <span className="mr-2 inline-block w-3 select-none opacity-50">
              {row.kind === "add" ? "+" : row.kind === "del" ? "−" : " "}
            </span>
            {row.text || " "}
          </div>
        ))}
        {hidden > 0 && (
          <div className="pt-1 text-[color:var(--color-ink-600)]">
            … {hidden} more line{hidden === 1 ? "" : "s"}
          </div>
        )}
      </pre>
    </div>
  );
}

type DiffRow = { kind: "add" | "del" | "ctx"; text: string };

/** Longest-common-subsequence line diff.
 *
 *  Descriptions are at most a few dozen lines, so the O(n·m) table is a few
 *  hundred cells — not worth pulling in a diff library for. */
function diffLines(before: string, after: string): DiffRow[] {
  const a = before.split("\n");
  const b = after.split("\n");
  const n = a.length;
  const m = b.length;

  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array(m + 1).fill(0),
  );
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j] ? lcs[i + 1][j + 1] + 1 : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }

  const rows: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      rows.push({ kind: "ctx", text: a[i] });
      i++;
      j++;
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      rows.push({ kind: "del", text: a[i] });
      i++;
    } else {
      rows.push({ kind: "add", text: b[j] });
      j++;
    }
  }
  while (i < n) rows.push({ kind: "del", text: a[i++] });
  while (j < m) rows.push({ kind: "add", text: b[j++] });

  // Collapse long unchanged runs — the reviewer cares about the edits, and a
  // 40-line untouched description pushes the actual change off screen.
  const out: DiffRow[] = [];
  let run = 0;
  for (let k = 0; k < rows.length; k++) {
    if (rows[k].kind === "ctx") {
      run++;
      const nearEdit =
        rows.slice(Math.max(0, k - 2), k + 3).some((r) => r.kind !== "ctx");
      if (nearEdit || run <= 2) out.push(rows[k]);
      else if (out.at(-1)?.text !== "⋯") out.push({ kind: "ctx", text: "⋯" });
    } else {
      run = 0;
      out.push(rows[k]);
    }
  }
  return out;
}
