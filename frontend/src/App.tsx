import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import { Icon, type IconName } from "./components/Icon";
import { Bar } from "./components/ui";
import { fmtMB } from "./format";
import { usePoll, useTheme } from "./hooks";
import ClipEditor from "./pages/ClipEditor";
import Dashboard from "./pages/Dashboard";
import NewProject from "./pages/NewProject";
import ProjectView from "./pages/ProjectView";
import Projects from "./pages/Projects";
import SettingsPage from "./pages/Settings";
import StoragePage from "./pages/Storage";

const NAV: { to: string; label: string; icon: IconName; end?: boolean }[] = [
  { to: "/", label: "Dashboard", icon: "home", end: true },
  { to: "/new", label: "New project", icon: "plus" },
  { to: "/projects", label: "Projects", icon: "film" },
  { to: "/storage", label: "Storage", icon: "disk" },
  { to: "/settings", label: "Settings", icon: "settings" },
];

function SidebarStatus() {
  const { data } = usePoll(api.system, [], 5000);
  if (!data) return null;
  const used = 1 - data.disk.free_mb / data.disk.total_mb;
  const memUsed = 1 - data.memory.available_mb / data.memory.total_mb;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <div className="meter">
        <div className="row between tiny muted">
          <span>Disk free</span>
          <span style={{ color: data.disk.low ? "var(--danger)" : undefined }}>{fmtMB(data.disk.free_mb)}</span>
        </div>
        <Bar value={used} tone={data.disk.low ? "danger" : used > 0.85 ? "warn" : undefined} />
      </div>
      <div className="meter">
        <div className="row between tiny muted">
          <span>Memory free</span>
          <span>{fmtMB(data.memory.available_mb)}</span>
        </div>
        <Bar value={memUsed} tone={memUsed > 0.85 ? "warn" : undefined} />
      </div>
      <div className="row tiny muted">
        {data.worker.current_job ? (
          <>
            <span className="pulse" style={{ width: 7, height: 7, borderRadius: 9, background: "var(--accent)" }} /> Processing
            {data.worker.queued ? ` · ${data.worker.queued} queued` : ""}
          </>
        ) : (
          <>
            <span style={{ width: 7, height: 7, borderRadius: 9, background: "var(--success)" }} /> Idle · {data.mode} mode
          </>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const { theme, setTheme } = useTheme();
  const next = theme === "dark" ? "light" : theme === "light" ? "system" : "dark";
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="scissors" size={16} />
          </div>
          <span>ClipStudio</span>
        </div>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link ${isActive ? "active" : ""}`}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </NavLink>
        ))}
        <div className="sidebar-foot">
          <SidebarStatus />
          <button className="btn ghost sm" onClick={() => setTheme(next)} title="Toggle theme">
            <Icon name={theme === "dark" ? "moon" : theme === "light" ? "sun" : "cpu"} />
            Theme: {theme}
          </button>
          <div className="row tiny muted">
            <Icon name="shield" size={12} /> Local only · no telemetry
          </div>
        </div>
      </aside>
      <main className="main">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<ProjectView />} />
          <Route path="/clips/:id" element={<ClipEditor />} />
          <Route path="/storage" element={<StoragePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="empty">Page not found</div>} />
        </Routes>
      </main>
    </div>
  );
}
