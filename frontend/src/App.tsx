import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { api } from "./api";
import { Icon, type IconName } from "./components/Icon";
import { Bar } from "./components/ui";
import { fmtMB } from "./format";
import { usePoll, useTheme } from "./hooks";
import CampaignView from "./pages/CampaignView";
import Campaigns from "./pages/Campaigns";
import ClipEditor from "./pages/ClipEditor";
import Dashboard from "./pages/Dashboard";
import LibraryPage from "./pages/Library";
import Create from "./pages/Create";
import NewMontage from "./pages/NewMontage";
import NewCampaign from "./pages/NewCampaign";
import NewProject from "./pages/NewProject";
import ProjectView from "./pages/ProjectView";
import Projects from "./pages/Projects";
import SettingsPage from "./pages/Settings";
import StoragePage from "./pages/Storage";

// R3: "+ Create" starts anything (clips, music edit, quick clip); Storage lives under the disk meter and Settings.
const NAV: { to: string; label: string; icon: IconName; end?: boolean; also?: string[] }[] = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/campaigns", label: "Campaigns", icon: "target" },
  { to: "/projects", label: "Videos", icon: "film", also: ["/clips/"] },
  { to: "/library", label: "Library", icon: "library" },
  { to: "/settings", label: "Settings", icon: "settings", also: ["/storage"] },
];

function SidebarStatus() {
  const { data } = usePoll(api.system, [], 5000);
  if (!data) return null;
  const used = 1 - data.disk.free_mb / data.disk.total_mb;
  const memUsed = 1 - data.memory.available_mb / data.memory.total_mb;
  return (
    <div className="stack" style={{ gap: 10 }}>
      <Link to="/storage" className="meter meter-link" title="Disk space: open Storage to free some up">
        <div className="row between tiny muted">
          <span style={{ color: data.disk.low ? "var(--danger)" : undefined }}>Disk · {fmtMB(data.disk.free_mb)} free</span>
          <span>{Math.round(used * 100)}% used</span>
        </div>
        <Bar value={used} tone={data.disk.low ? "danger" : used > 0.85 ? "warn" : undefined} />
      </Link>
      <div className="meter">
        <div className="row between tiny muted" title="The bar shows how much memory is in use">
          <span>Memory · {fmtMB(data.memory.available_mb)} free</span>
          <span>{Math.round(memUsed * 100)}% used</span>
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
            <span style={{ width: 7, height: 7, borderRadius: 9, background: "var(--success)" }} /> Idle · {data.mode === "low" ? "light" : data.mode} mode
          </>
        )}
      </div>
    </div>
  );
}

export default function App() {
  const { theme, setTheme } = useTheme();
  const { pathname } = useLocation();
  const next = theme === "dark" ? "light" : theme === "light" ? "system" : "dark";
  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">
            <Icon name="scissors" size={16} />
          </div>
          <span>ClipStudio</span>
        </div>
        <nav aria-label="Main" className="stack" style={{ gap: 2 }}>
        <Link to="/create" className={`btn primary nav-create ${["/create", "/new", "/montage/new"].includes(pathname) ? "on" : ""}`}>
          <Icon name="plus" /> Create
        </Link>
        {NAV.map((n) => (
          <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => `nav-link ${isActive || n.also?.some((a) => pathname.startsWith(a)) ? "active" : ""}`}>
            <Icon name={n.icon} />
            <span>{n.label}</span>
          </NavLink>
        ))}
        </nav>
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
      <main className="main" id="main" tabIndex={-1}>
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/create" element={<Create />} />
          <Route path="/new" element={<NewProject />} />
          <Route path="/projects" element={<Projects />} />
          <Route path="/projects/:id" element={<ProjectView />} />
          <Route path="/clips/:id" element={<ClipEditor />} />
          <Route path="/campaigns" element={<Campaigns />} />
          <Route path="/campaigns/new" element={<NewCampaign />} />
          <Route path="/campaigns/:id" element={<CampaignView />} />
          <Route path="/library" element={<LibraryPage />} />
          <Route path="/montage/new" element={<NewMontage />} />
          <Route path="/storage" element={<StoragePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<div className="empty">Page not found</div>} />
        </Routes>
      </main>
    </div>
  );
}
