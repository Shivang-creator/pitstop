import { useEffect, useState } from "react";
import { scoreColor } from "./ui";

/** Animated score ring.
 *
 *  The number counts up and the arc sweeps in over ~1.1s. That timing is
 *  deliberate: it is the moment the report lands, and a value that simply
 *  appears reads as a static mockup. A value that animates reads as something
 *  that was just computed. */
export function ScoreRing({
  score,
  grade,
  size = 184,
}: {
  score: number;
  grade: string;
  size?: number;
}) {
  const [shown, setShown] = useState(0);
  const color = scoreColor(score);
  const stroke = 11;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;

  useEffect(() => {
    let frame = 0;
    const start = performance.now();
    const duration = 1100;

    const tick = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      // easeOutExpo — fast start, long settle. Reads as "resolving".
      const eased = t === 1 ? 1 : 1 - Math.pow(2, -10 * t);
      setShown(Math.round(score * eased));
      if (t < 1) frame = requestAnimationFrame(tick);
    };

    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [score]);

  const offset = circumference * (1 - shown / 100);

  return (
    <div
      className="relative shrink-0"
      style={{ width: size, height: size }}
      role="img"
      aria-label={`Channel health score ${score} out of 100, grade ${grade}`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke="currentColor"
          strokeWidth={stroke}
          className="text-white/[0.05]"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          style={{ filter: `drop-shadow(0 0 12px ${color}55)` }}
        />
      </svg>

      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <div
          className="font-mono text-[46px] font-semibold leading-none tabular-nums"
          style={{ color }}
        >
          {shown}
        </div>
        <div className="mt-1.5 text-[11px] uppercase tracking-[0.16em] text-[color:var(--color-ink-400)]">
          Health
        </div>
        <div
          className="mt-1 rounded px-1.5 text-[13px] font-semibold"
          style={{ color, background: `${color}18` }}
        >
          {grade}
        </div>
      </div>
    </div>
  );
}

/** Horizontal category meter used in the score breakdown. */
export function CategoryBar({
  label,
  description,
  score,
  findings,
}: {
  label: string;
  description: string;
  score: number;
  findings: number;
}) {
  const [width, setWidth] = useState(0);
  const color = scoreColor(score);

  useEffect(() => {
    const timer = setTimeout(() => setWidth(score), 120);
    return () => clearTimeout(timer);
  }, [score]);

  return (
    <div className="group">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-[13px] font-medium text-[color:var(--color-ink-200)]">
          {label}
        </span>
        <span className="flex items-baseline gap-2">
          {findings > 0 && (
            <span className="text-[11px] tabular-nums text-[color:var(--color-ink-400)]">
              {findings} issue{findings === 1 ? "" : "s"}
            </span>
          )}
          <span
            className="font-mono text-[13px] font-semibold tabular-nums"
            style={{ color }}
          >
            {score}
          </span>
        </span>
      </div>
      <div className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
        <div
          className="h-full rounded-full transition-[width] duration-[900ms] ease-out"
          style={{ width: `${width}%`, background: color }}
        />
      </div>
      <p className="mt-1 text-[11px] leading-snug text-[color:var(--color-ink-400)] opacity-0 transition-opacity group-hover:opacity-100">
        {description}
      </p>
    </div>
  );
}
