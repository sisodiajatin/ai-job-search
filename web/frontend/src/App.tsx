import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { fetchToday } from "./api";
import { Icon } from "./ui";
import Dashboard from "./pages/Dashboard";
import Jobs from "./pages/Jobs";
import Pipeline from "./pages/Pipeline";
import Library from "./pages/Library";
import Today from "./pages/Today";

const NAV = [
  { to: "/", label: "Dashboard", icon: "dashboard", end: true },
  { to: "/today", label: "Today", icon: "clock" },
  { to: "/jobs", label: "Jobs", icon: "jobs" },
  { to: "/pipeline", label: "Pipeline", icon: "pipeline" },
  { to: "/library", label: "Library", icon: "library" },
];

export default function App() {
  const [followups, setFollowups] = useState(0);
  const [todayBadge, setTodayBadge] = useState(0);

  useEffect(() => {
    let cancelled = false;
    function load() {
      fetchToday()
        .then((t) => {
          if (cancelled) return;
          setFollowups(t.counts.followups);
          setTodayBadge(t.counts.badge);
        })
        .catch(() => {
          /* sidebar badge is optional */
        });
    }
    load();
    const timer = window.setInterval(load, 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <Icon name="bird" size={15} />
          </span>
          <div>
            <span className="brand-name">Job desk</span>
            <span className="brand-sub">AI JOB SEARCH</span>
          </div>
        </div>
        <nav className="side-nav" aria-label="Pages">
          <p className="side-label">Search</p>
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}>
              <Icon name={item.icon} />
              {item.label}
              {item.to === "/pipeline" && followups > 0 && <span className="nav-badge">{followups}</span>}
              {item.to === "/today" && todayBadge > 0 && <span className="nav-badge">{todayBadge}</span>}
            </NavLink>
          ))}
        </nav>
        <div className="side-foot">
          <p>
            <span className="dot">●</span>Local · 127.0.0.1
          </p>
          <p>Chat does the applying</p>
        </div>
      </aside>
      <div className="main">
        <main className="content">
          <Routes>
            <Route path="/" element={<Dashboard />} />
            <Route path="/today" element={<Today />} />
            <Route path="/jobs" element={<Jobs />} />
            <Route path="/pipeline" element={<Pipeline />} />
            <Route path="/library" element={<Library />} />
            <Route path="*" element={<Dashboard />} />
          </Routes>
        </main>
      </div>
    </div>
  );
}
