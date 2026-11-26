import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Spinner, useDialogs, useToast } from "../components/ui";
import { fmtMB } from "../format";
import { usePoll } from "../hooks";

export default function StoragePage() {
  const toast = useToast();
  const { confirm } = useDialogs();
  const q = usePoll(api.storage, []);
  const sys = usePoll(api.system, [], 5000).data;
  const [busy, setBusy] = useState<string | null>(null);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const s = q.data;

  const clean = async (targets: string[], label: string) => {
    if (targets.includes("cache") && !(await confirm({ title: "Clear the cache?", body: "Transcripts and analysis are kept here. Videos that need them again will re-transcribe, which takes a few minutes per video.", confirm: "Clear cache", danger: true }))) return;
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
    const ok = await confirm({
      title: "Delete the source video?",
      body: "Frees the most space, but clips that aren't rendered yet can't be rendered or re-rendered any more (finished MP4s stay). Videos from links can be downloaded again.",
      confirm: "Delete source",
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await api.deleteSource(id);
      toast(`Source deleted · ${fmtMB(r.freed_mb)} freed`);
      q.reload();
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };

  const toggle = (id: string) => setPicked((x) => {
    const n = new Set(x);
    if (n.has(id)) n.delete(id);
    else n.add(id);
    return n;
  });
  if (!s) return <div className="skeleton" style={{ height: 300 }} />;
  const rows = [...s.projects].sort((a, b) => b.source_mb + b.clips_mb + b.other_mb - (a.source_mb + a.clips_mb + a.other_mb));
  const pickedRows = rows.filter((r) => picked.has(r.id));
  const pickedWithSource = pickedRows.filter((r) => r.source_owned && r.source_exists);
  const pickedSourceMb = pickedWithSource.reduce((a, r) => a + r.source_mb, 0);
  const dropSources = async () => {
    if (!(await confirm({ title: `Delete ${pickedWithSource.length} source file${pickedWithSource.length === 1 ? "" : "s"}?`, body: `Frees ${fmtMB(pickedSourceMb)}. Finished MP4s stay, but these videos can't be rendered, re-trimmed or re-framed any more. Videos from links can be downloaded again. Files you used in place are never deleted.`, confirm: "Delete source files", danger: true }))) return;
    setBusy("sources");
    let freed = 0;
    for (const r of pickedWithSource) {
      try {
        freed += (await api.deleteSource(r.id)).freed_mb;
      } catch (e) {
        toast(`${r.name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(null);
    setPicked(new Set());
    toast(`Freed ${fmtMB(freed)}`);
    q.reload();
  };
  const dropVideos = async () => {
    if (!(await confirm({ title: `Delete ${pickedRows.length} video${pickedRows.length === 1 ? "" : "s"} entirely?`, body: "Removes the videos, their clips (including rendered MP4s) and downloaded or uploaded source files. Exported folders and files you used in place are kept. This can't be undone.", confirm: "Delete videos", danger: true }))) return;
    setBusy("videos");
    let freed = 0;
    for (const r of pickedRows) {
      try {
        freed += (await api.deleteProject(r.id, { delete_exports: true, delete_source: true })).freed_mb;
      } catch (e) {
        toast(`${r.name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(null);
    setPicked(new Set());
    toast(`Deleted · ${fmtMB(freed)} freed`);
    q.reload();
  };
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
            ["Library (music & effects)", s.library_mb],
            ["Exports", s.exports_mb],
            ["Campaign files", s.campaigns_mb],
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
          <h2>By video</h2>
          <span className="small muted">Largest first · tick several to free space in one go</span>
        </div>
        {picked.size > 0 && (
          <div className="select-bar" style={{ margin: "10px 16px 0" }}>
            <strong>{picked.size} selected</strong>
            <span className="small muted">{fmtMB(pickedSourceMb)} of source files</span>
            <span className="spacer" />
            <button className="btn sm" onClick={dropSources} disabled={!pickedWithSource.length || !!busy}>
              {busy === "sources" ? <Spinner /> : <Icon name="trash" />} Delete source files ({pickedWithSource.length})
            </button>
            <button className="btn sm danger" onClick={dropVideos} disabled={!!busy}>
              {busy === "videos" ? <Spinner /> : <Icon name="trash" />} Delete videos entirely
            </button>
            <button className="btn sm ghost" onClick={() => setPicked(new Set())}>
              Clear
            </button>
          </div>
        )}
        <table className="table">
          <thead>
            <tr>
              <th style={{ width: 32 }}>
                <input type="checkbox" aria-label="Select all" checked={picked.size > 0 && picked.size === rows.length} onChange={(e) => setPicked(e.target.checked ? new Set(rows.map((r) => r.id)) : new Set())} />
              </th>
              <th>Video</th>
              <th>Clips</th>
              <th>Transcript & other</th>
              <th>Source</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((p) => (
              <tr key={p.id} className={picked.has(p.id) ? "picked" : ""}>
                <td>
                  <input type="checkbox" aria-label={`Select ${p.name}`} checked={picked.has(p.id)} onChange={() => toggle(p.id)} />
                </td>
                <td>
                  <Link to={`/projects/${p.id}`} className="ellipsis" style={{ display: "block", maxWidth: 320 }}>
                    {p.name}
                  </Link>
                  {p.stage === "not_started" && <span className="badge warn" title="Uploaded but never processed">not started</span>}
                  {p.stage === "footage" && <span className="badge" title="Only used as a music edit source">music edit source</span>}
                </td>
                <td>{fmtMB(p.clips_mb)}</td>
                <td>{fmtMB(p.other_mb)}</td>
                <td>{!p.source_exists ? <span className="muted">removed</span> : p.source_owned ? <>{fmtMB(p.source_mb)}{p.smart && <span className="badge info" style={{ marginLeft: 6 }}>audio + sections</span>}</> : <span className="muted">your file (in place)</span>}</td>
                <td style={{ textAlign: "right" }}>
                  {p.source_owned && p.source_exists && (
                    <button className="btn sm" onClick={() => dropSource(p.id)} title="Clips stay; you won't be able to re-render or re-frame this video">
                      Delete source
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {rows.length === 0 && (
              <tr>
                <td colSpan={6} className="muted">
                  No videos yet.
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
