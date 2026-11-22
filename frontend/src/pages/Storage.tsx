import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Spinner, useToast } from "../components/ui";
import { fmtMB } from "../format";
import { usePoll } from "../hooks";

export default function StoragePage() {
  const toast = useToast();
  const q = usePoll(api.storage, []);
  const sys = usePoll(api.system, [], 5000).data;
  const [busy, setBusy] = useState<string | null>(null);
  const s = q.data;

  const clean = async (targets: string[], label: string) => {
    setBusy(label);
    try {
      const r = await api.cleanup(targets);
      toast(`Freed ${fmtMB(r.freed_mb)}`);
      q.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  };
  const dropSource = async (id: string) => {
    try {
      const r = await api.deleteSource(id);
      toast(`Source deleted · ${fmtMB(r.freed_mb)} freed`);
      q.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  if (!s) return <div className="skeleton" style={{ height: 300 }} />;
  const temp = s.temp_mb + s.partial_mb;
  const orphanMb = s.orphans.reduce((a, o) => a + o.mb, 0) + s.orphan_project_dirs.reduce((a, o) => a + o.mb, 0);

  return (
    <div className="stack lg" style={{ maxWidth: 980 }}>
      <div className="page-head">
        <div>
          <h1>Storage</h1>
          <p className="sub mono">{s.work_dir}</p>
        </div>
        <Link to="/settings" className="btn sm">
          <Icon name="folder" /> Change working directory
        </Link>
      </div>

      {sys && (
        <div className="card card-body stack">
          <div className="row between">
            <strong>Disk</strong>
            <span className="small muted">
              {fmtMB(sys.disk.free_mb)} free of {fmtMB(sys.disk.total_mb)} · keeps {fmtMB(sys.disk.min_free_mb)} reserved
            </span>
          </div>
          <Bar value={1 - sys.disk.free_mb / sys.disk.total_mb} tone={sys.disk.low ? "danger" : undefined} />
          {sys.disk.low && <Alert kind="warn">Disk space is low. Clean up below, delete downloaded sources you no longer need, or move the working directory to an external SSD.</Alert>}
        </div>
      )}

      <div className="grid cols-4">
        {(
          [
            ["Downloads", s.downloads_mb],
            ["Uploads", s.uploads_mb],
            ["Transcript cache", s.cache_mb],
            ["Temporary", temp],
          ] as const
        ).map(([l, v]) => (
          <div className="card stat" key={l}>
            <div className="label">{l}</div>
            <div className="value" style={{ fontSize: 22 }}>{fmtMB(v)}</div>
          </div>
        ))}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Clean up</h2>
          <span className="small muted">Never removes your original files or finished clips</span>
        </div>
        <div className="card-body stack">
          <CleanRow title="Temporary & partial files" desc="Extracted audio, failed renders and interrupted downloads." size={temp} busy={busy === "temp"} onClick={() => clean(["temp", "partial"], "temp")} />
          <CleanRow title="Unused downloads & uploads" desc={`${s.orphans.length + s.orphan_project_dirs.length} item(s) no project uses any more.`} size={orphanMb} busy={busy === "orphans"} onClick={() => clean(["orphans"], "orphans")} />
          <CleanRow title="Transcript cache" desc="Re-processing a video will need to transcribe it again." size={s.cache_mb} busy={busy === "cache"} onClick={() => clean(["cache"], "cache")} />
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>By project</h2>
        </div>
        <table className="table">
          <thead>
            <tr>
              <th>Project</th>
              <th>Clips</th>
              <th>Transcript & other</th>
              <th>Source</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {s.projects.map((p) => (
              <tr key={p.id}>
                <td>
                  <Link to={`/projects/${p.id}`} className="ellipsis" style={{ display: "block", maxWidth: 320 }}>
                    {p.name}
                  </Link>
                </td>
                <td>{fmtMB(p.clips_mb)}</td>
                <td>{fmtMB(p.other_mb)}</td>
                <td>{!p.source_exists ? <span className="muted">removed</span> : p.source_owned ? fmtMB(p.source_mb) : <span className="muted">your file (in place)</span>}</td>
                <td style={{ textAlign: "right" }}>
                  {p.source_owned && p.source_exists && (
                    <button className="btn sm" onClick={() => dropSource(p.id)} title="Clips stay; you won't be able to re-render or re-trim this project">
                      Delete source
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {s.projects.length === 0 && (
              <tr>
                <td colSpan={5} className="muted">
                  No projects yet.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CleanRow({ title, desc, size, busy, onClick }: { title: string; desc: string; size: number; busy: boolean; onClick: () => void }) {
  return (
    <div className="row between" style={{ padding: "6px 0" }}>
      <div className="stack sm" style={{ gap: 2 }}>
        <strong>{title}</strong>
        <span className="small muted">{desc}</span>
      </div>
      <div className="row">
        <span className="small muted nowrap">{fmtMB(size)}</span>
        <button className="btn sm" onClick={onClick} disabled={busy || size <= 0}>
          {busy ? <Spinner /> : <Icon name="trash" />} Clean
        </button>
      </div>
    </div>
  );
}
