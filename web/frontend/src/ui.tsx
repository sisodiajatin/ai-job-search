import { useEffect, type ReactNode } from "react";

export function missing(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  return String(value);
}

export function formatDate(value?: string | null): string {
  if (!value) return "-";
  const date = new Date(`${String(value).slice(0, 10)}T00:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/* ---------- Icons (inline SVG, no deps) ---------- */

const ICON_PATHS: Record<string, ReactNode> = {
  dashboard: (
    <>
      <rect x="3" y="3" width="7" height="9" rx="1.5" />
      <rect x="14" y="3" width="7" height="5" rx="1.5" />
      <rect x="14" y="12" width="7" height="9" rx="1.5" />
      <rect x="3" y="16" width="7" height="5" rx="1.5" />
    </>
  ),
  jobs: (
    <>
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
      <path d="M3 12h18" />
    </>
  ),
  pipeline: (
    <>
      <rect x="3" y="4" width="5" height="12" rx="1.5" />
      <rect x="10" y="4" width="5" height="8" rx="1.5" />
      <rect x="17" y="4" width="4" height="15" rx="1.5" />
    </>
  ),
  library: (
    <>
      <path d="M3 6a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V6Z" />
      <path d="M3 10h18" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </>
  ),
  radar: (
    <>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4.5" />
      <path d="M12 3v3M21 12h-3M12 21v-3M3 12h3" />
    </>
  ),
  queue: (
    <>
      <path d="M4 6h12M4 10h12M4 14h7" />
      <path d="M15 17h6M18 14v6" />
    </>
  ),
  send: <path d="M4 12 20 4l-4 16-4.5-6.5L4 12Z" />,
  doc: (
    <>
      <path d="M7 3h7l4 4v14H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
      <path d="M14 3v4h4" />
    </>
  ),
  external: (
    <>
      <path d="M14 4h6v6" />
      <path d="M20 4 11 13" />
      <path d="M18 13v5a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h5" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  check: <path d="m4.5 12.5 5 5 10-11" />,
  plus: <path d="M12 5v14M5 12h14" />,
  minus: <path d="M5 12h14" />,
  alert: (
    <>
      <path d="M12 3 2.5 20h19L12 3Z" />
      <path d="M12 10v4M12 17.2v.3" />
    </>
  ),
  copy: (
    <>
      <rect x="9" y="9" width="12" height="12" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  bird: (
    <>
      <path d="M4 17c6 1 12-2 14-9-4 1-6 3-7 5-1-2-3-3-6-3 2 1 3 3 3 4-2 1-3 2-4 3Z" />
      <path d="M8 20c3 0 5-1 7-3" />
    </>
  ),
};

export function Icon({ name, size = 16 }: { name: keyof typeof ICON_PATHS | string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICON_PATHS[name] ?? ICON_PATHS.doc}
    </svg>
  );
}

/* ---------- Stat card ---------- */

export function Stat({
  label,
  value,
  meta,
  icon,
  alert,
}: {
  label: string;
  value: string | number;
  meta?: string;
  icon?: string;
  alert?: boolean;
}) {
  return (
    <div className={`stat${alert ? " alert" : ""}`}>
      <div className="stat-head">
        <span className="stat-label">{label}</span>
        {icon && (
          <span className="stat-icon">
            <Icon name={icon} size={14} />
          </span>
        )}
      </div>
      <div>
        <b>{value}</b>
        {meta && <div className="stat-meta">{meta}</div>}
      </div>
    </div>
  );
}

/* ---------- Drawer ---------- */

export function Drawer({ title, onClose, children }: { title?: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);

  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} aria-hidden="true" />
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={title || "Details"}>
        {children}
      </aside>
    </>
  );
}

/* ---------- Empty / error states ---------- */

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="empty-panel">
      <p className="empty-title">{title}</p>
      {children && <div className="empty-body">{children}</div>}
    </div>
  );
}

export function LoadError({ children }: { children: ReactNode }) {
  return <div className="empty-panel flash error">{children}</div>;
}

export function PageSkeleton({ variant }: { variant: "dashboard" | "list" | "board" }) {
  return (
    <div className="skel-page" aria-busy="true" aria-live="polite">
      <span className="sr-only">Loading…</span>
      <span className="skel skel-title" />
      {variant === "dashboard" && (
        <>
          <div className="stat-grid">
            {Array.from({ length: 5 }, (_, i) => (
              <span className="skel skel-card" key={i} />
            ))}
          </div>
          <div className="grid-2">
            <span className="skel skel-tall" />
            <span className="skel skel-tall" />
          </div>
        </>
      )}
      {variant === "list" && <span className="skel skel-tall" />}
      {variant === "board" && (
        <div style={{ display: "flex", gap: "0.75rem" }}>
          {Array.from({ length: 4 }, (_, i) => (
            <span className="skel skel-tall" style={{ flex: 1 }} key={i} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ---------- Copy-to-clipboard helper ---------- */

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}
