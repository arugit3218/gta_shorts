import { Link } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Empty } from "../components/ui";
import { fmtMB } from "../format";
import { usePoll } from "../hooks";
import { ProjectList } from "./Projects";

export default function Dashboard() {
  const { data, error } = usePoll(api.dashboard, [], 3000);
  const sys = usePoll(api.system, [], 10000).data;

  return (
    <div className="stack lg">
      <div className="page-head">
        <div>
          <h1>Dashboard</h1>
          <p className="sub">Turn long videos into ranked, captioned vertical clips, all on this Mac.</p>
        </div>
        <Link to="/new" className="btn primary lg">
          <Icon name="plus" /> New project
        </Link>
      </div>

      {error && <Alert kind="danger" title="Can't reach the ClipStudio backend">{error}</Alert>}
      {sys && sys.disk.low && (
        <Alert kind="warn" title="Disk space is low">
          Only {fmtMB(sys.disk.free_mb)} free. Run <Link to="/storage"><u>Storage → Clean up</u></Link> or set an external drive as the working directory in <Link to="/settings"><u>Settings</u></Link>.
        </Alert>
      )}

      <div className="grid cols-4">
        <Stat icon="film" label="Projects" value={data?.projects} hint={`${data?.videos_processed ?? 0} processed`} />
        <Stat icon="clock" label="Video processed" value={data ? `${data.minutes_processed} min` : undefined} hint="total source length" />
        <Stat icon="scissors" label="Clips generated" value={data?.clips} hint={`${data?.clips_rendered ?? 0} rendered · ${data?.clips_approved ?? 0} approved`} />
        <div className="card stat">
          <div className="label">
            <Icon name="disk" size={13} /> Disk available
          </div>
          <div className="value">{sys ? fmtMB(sys.disk.free_mb) : "–"}</div>
          {sys && <Bar value={1 - sys.disk.free_mb / sys.disk.total_mb} tone={sys.disk.low ? "danger" : undefined} />}
        </div>
      </div>

      {data && data.active_jobs.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2>Processing now</h2>
            <span className="badge accent">{data.active_jobs.length} active</span>
          </div>
          <div className="card-body stack">
            {data.active_jobs.map((j) => {
              const p = data.recent.find((r) => r.id === j.project_id);
              const stage = j.stage ? j.stages[j.stage]?.label : null;
              return (
                <Link key={j.id} to={`/projects/${j.project_id}`} className="stack sm">
                  <div className="row between">
                    <strong className="ellipsis">{p?.name ?? "Project"}</strong>
                    <span className="small muted">
                      {j.status === "queued" ? "Queued" : `${stage ?? "Working"} · ${Math.round(j.progress * 100)}%`}
                    </span>
                  </div>
                  <Bar value={j.progress} />
                </Link>
              );
            })}
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2>Recent projects</h2>
          <Link to="/projects" className="btn ghost sm">
            View all
          </Link>
        </div>
        {data && data.recent.length === 0 ? (
          <Empty icon="film" title="No projects yet">
            <Link to="/new" className="btn primary" style={{ marginTop: 8 }}>
              <Icon name="plus" /> Create your first project
            </Link>
          </Empty>
        ) : (
          <ProjectList projects={data?.recent ?? null} />
        )}
      </div>
    </div>
  );
}

function Stat({ icon, label, value, hint }: { icon: Parameters<typeof Icon>[0]["name"]; label: string; value: number | string | undefined; hint?: string }) {
  return (
    <div className="card stat">
      <div className="label">
        <Icon name={icon} size={13} /> {label}
      </div>
      <div className="value">{value ?? "–"}</div>
      {hint && <div className="hint">{hint}</div>}
    </div>
  );
}
