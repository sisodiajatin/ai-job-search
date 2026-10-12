import { formatDate } from "./ui";

/* All charts are CSS/SVG-only — no chart library, keeping the repo dependency-free. */

export type FunnelStage = { label: string; count: number; muted?: boolean };

export function Funnel({ stages }: { stages: FunnelStage[] }) {
  const max = Math.max(1, ...stages.map((s) => s.count));
  return (
    <div className="funnel">
      {stages.map((stage) => (
        <div className="funnel-row" key={stage.label}>
          <span className="funnel-label" title={stage.label}>
            {stage.label}
          </span>
          <div className="funnel-track">
            <div
              className={`funnel-bar${stage.muted ? " dim" : ""}`}
              style={{ width: `${Math.max(stage.count > 0 ? 3 : 0, (stage.count / max) * 100)}%` }}
            />
          </div>
          <span className="funnel-num">{stage.count}</span>
        </div>
      ))}
    </div>
  );
}

export type DonutSlice = { label: string; count: number; color: string };

export function Donut({ slices, caption }: { slices: DonutSlice[]; caption: string }) {
  const total = slices.reduce((sum, s) => sum + s.count, 0);
  const radius = 52;
  const circumference = 2 * Math.PI * radius;
  let offset = 0;

  return (
    <div className="donut-wrap">
      <div className="donut">
        <svg viewBox="0 0 132 132" width="132" height="132" role="img" aria-label={`${caption}: ${total}`}>
          <circle cx="66" cy="66" r={radius} fill="none" stroke="var(--surface-3)" strokeWidth="14" />
          {total > 0 &&
            slices
              .filter((s) => s.count > 0)
              .map((slice) => {
                const length = (slice.count / total) * circumference;
                const dash = `${length} ${circumference - length}`;
                const rotation = (offset / circumference) * 360 - 90;
                offset += length;
                return (
                  <circle
                    key={slice.label}
                    cx="66"
                    cy="66"
                    r={radius}
                    fill="none"
                    stroke={slice.color}
                    strokeWidth="14"
                    strokeDasharray={dash}
                    strokeLinecap="butt"
                    transform={`rotate(${rotation} 66 66)`}
                  />
                );
              })}
        </svg>
        <div className="donut-center">
          <div>
            <b>{total}</b>
            <span>{caption}</span>
          </div>
        </div>
      </div>
      <div className="legend">
        {slices.map((slice) => (
          <div className="legend-row" key={slice.label}>
            <span className="legend-swatch" style={{ background: slice.color }} />
            {slice.label}
            <span className="n">{slice.count}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/* Group ISO dates into the last `weeks` calendar weeks (Mon-Sun). */
export function weeklyBuckets(dates: string[], weeks: number): { label: string; count: number; start: string }[] {
  const now = new Date();
  const today = new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
  const day = (today.getUTCDay() + 6) % 7; // 0 = Monday
  const monday = new Date(today);
  monday.setUTCDate(today.getUTCDate() - day - (weeks - 1) * 7);

  const buckets: { label: string; count: number; start: Date }[] = [];
  for (let i = 0; i < weeks; i += 1) {
    const start = new Date(monday);
    start.setUTCDate(monday.getUTCDate() + i * 7);
    buckets.push({ label: "", count: 0, start });
  }
  for (const raw of dates) {
    if (!raw) continue;
    const date = new Date(`${raw.slice(0, 10)}T00:00:00Z`);
    if (Number.isNaN(date.getTime())) continue;
    for (const bucket of buckets) {
      const end = new Date(bucket.start);
      end.setUTCDate(bucket.start.getUTCDate() + 7);
      if (date >= bucket.start && date < end) {
        bucket.count += 1;
        break;
      }
    }
  }
  return buckets.map((bucket) => ({
    label: formatDate(bucket.start.toISOString().slice(0, 10)),
    count: bucket.count,
    start: bucket.start.toISOString().slice(0, 10),
  }));
}

export function Trend({ buckets }: { buckets: { label: string; count: number }[] }) {
  const max = Math.max(1, ...buckets.map((b) => b.count));
  return (
    <div className="trend">
      {buckets.map((bucket, index) => (
        <div className="trend-col" key={`${bucket.label}-${index}`}>
          <span className="trend-num">{bucket.count > 0 ? bucket.count : ""}</span>
          <div
            className={`trend-bar${bucket.count === 0 ? " zero" : ""}`}
            style={{ height: `${(bucket.count / max) * 100}%` }}
            title={`${bucket.label}: ${bucket.count}`}
          />
          <span className="trend-x">{bucket.label}</span>
        </div>
      ))}
    </div>
  );
}
