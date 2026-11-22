import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Bar, Spinner, useToast } from "../components/ui";
import { fmtDuration, fmtMB } from "../format";
import { usePoll } from "../hooks";
import type { ProcessOptions, Project, Settings, UrlInfo } from "../types";

type Source = "upload" | "url" | "path";

export default function NewProject() {
  const [tab, setTab] = useState<Source>("upload");
  const [project, setProject] = useState<Project | null>(null);
  const [urlInfo, setUrlInfo] = useState<UrlInfo | null>(null);
  const settings = usePoll(api.settings, []).data;

  const reset = () => {
    setProject(null);
    setUrlInfo(null);
  };

  return (
    <div className="stack lg" style={{ maxWidth: 880 }}>
      <div className="page-head">
        <div>
          <h1>New project</h1>
          <p className="sub">Add a video, choose your clip settings, then start processing.</p>
        </div>
      </div>

      {!project && !urlInfo && (
        <div className="card">
          <div className="card-body">
            <div className="tabs" role="tablist">
              {(
                [
                  ["upload", "Upload file", "upload"],
                  ["url", "Video link", "link"],
                  ["path", "Use file in place", "folder"],
                ] as const
              ).map(([k, label, icon]) => (
                <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "active" : ""} onClick={() => setTab(k)}>
                  <span className="row" style={{ gap: 6 }}>
                    <Icon name={icon} size={14} /> {label}
                  </span>
                </button>
              ))}
            </div>
            {tab === "upload" && <UploadSource onDone={setProject} />}
            {tab === "url" && <UrlSource onDone={setUrlInfo} />}
            {tab === "path" && <PathSource onDone={setProject} />}
          </div>
        </div>
      )}

      {(project || urlInfo) && settings && <Configure project={project} urlInfo={urlInfo} settings={settings} onBack={reset} />}
    </div>
  );
}

function UploadSource({ onDone }: { onDone: (p: Project) => void }) {
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);

  const start = async (f: File) => {
    setFile(f);
    setError(null);
    setProgress(0);
    abort.current = new AbortController();
    try {
      const p = await api.uploadProject(f, setProgress, abort.current.signal);
      onDone(p);
    } catch (e) {
      setError((e as Error).message);
      setProgress(null);
    }
  };

  if (progress !== null && file) {
    return (
      <div className="stack" style={{ padding: "20px 0" }}>
        <div className="row between">
          <strong className="ellipsis">{file.name}</strong>
          <span className="muted small">
            {fmtMB((file.size / 1048576) * progress)} / {fmtMB(file.size / 1048576)}
          </span>
        </div>
        <Bar value={progress} />
        <div className="row between">
          <span className="small muted">{progress >= 1 ? "Reading video metadata…" : "Uploading to your local working folder…"}</span>
          {progress < 1 && (
            <button className="btn sm" onClick={() => abort.current?.abort()}>
              Cancel
            </button>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="stack">
      <div
        className={`dropzone ${over ? "over" : ""}`}
        onClick={() => input.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const f = e.dataTransfer.files[0];
          if (f) start(f);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <div className="big-icon">
          <Icon name="upload" size={22} />
        </div>
        <h3>Drop a video here or click to browse</h3>
        <p className="muted small" style={{ marginTop: 6 }}>
          MP4, MOV, MKV, WebM and other FFmpeg-supported formats
        </p>
        <input ref={input} type="file" accept="video/*,.mkv,.mts,.m2ts" hidden onChange={(e) => e.target.files?.[0] && start(e.target.files[0])} />
      </div>
      <p className="small muted">
        Uploading copies the file into ClipStudio's working folder. To avoid a duplicate copy on a full disk, use <strong>Use file in place</strong> instead.
      </p>
      {error && <Alert kind="danger" title="Upload failed">{error}</Alert>}
    </div>
  );
}

function UrlSource({ onDone }: { onDone: (i: UrlInfo) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.inspectUrl(url));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <div className="field">
        <label htmlFor="url">Video URL</label>
        <div className="row">
          <input id="url" className="input" placeholder="https://…" value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && url && go()} autoFocus />
          <button className="btn primary" disabled={!url || busy} onClick={go}>
            {busy ? <Spinner /> : <Icon name="link" />} Check link
          </button>
        </div>
      </div>
      <Alert kind="info">
        Only public videos you're allowed to download are supported (via yt-dlp). Login-only, private and DRM-protected videos aren't. Checking a link contacts the video site; nothing else leaves your Mac.
      </Alert>
      {error && <Alert kind="danger" title="Can't use this link">{error}</Alert>}
    </div>
  );
}

function PathSource({ onDone }: { onDone: (p: Project) => void }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.importPath(path));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <div className="field">
        <label htmlFor="path">Full path to a video on this Mac or an external drive</label>
        <div className="row">
          <input id="path" className="input mono" placeholder="/Users/you/Movies/podcast.mp4" value={path} onChange={(e) => setPath(e.target.value)} onKeyDown={(e) => e.key === "Enter" && path && go()} autoFocus />
          <button className="btn primary" disabled={!path || busy} onClick={go}>
            {busy ? <Spinner /> : <Icon name="folder" />} Use file
          </button>
        </div>
        <span className="hint">Tip: in Finder, select the file and press ⌥⌘C to copy its path. The file is read in place (no copy) and ClipStudio never deletes it.</span>
      </div>
      {error && <Alert kind="danger">{error}</Alert>}
    </div>
  );
}

const DURATIONS = [
  ["15-30", "15–30s"],
  ["30-45", "30–45s"],
  ["30-60", "30–60s"],
  ["45-60", "45–60s"],
  ["60-90", "60–90s"],
] as const;

function Configure({ project, urlInfo, settings, onBack }: { project: Project | null; urlInfo: UrlInfo | null; settings: Settings; onBack: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const presets = usePoll(api.captionPresets, []).data;
  const models = usePoll(api.models, []).data;
  const [opts, setOpts] = useState<ProcessOptions>({
    duration_preset: settings.duration_preset,
    max_clips: settings.max_clips,
    aspect_ratio: settings.aspect_ratio,
    layout: settings.layout,
    caption_preset: settings.caption_preset,
    output_height: settings.output_height,
    auto_render: settings.auto_render,
    llm_enabled: settings.llm_enabled,
  });
  const [estimate, setEstimate] = useState<{ total_mb: number; free_mb: number; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (project) api.estimate(project.id).then((e) => setEstimate({ total_mb: e.estimate.total_mb, free_mb: e.free_mb, ok: e.ok }));
    else if (urlInfo) setEstimate({ total_mb: urlInfo.estimate.total_mb, free_mb: urlInfo.free_mb, ok: urlInfo.free_mb - urlInfo.estimate.total_mb > settings.min_free_gb * 1024 });
  }, [project, urlInfo, settings.min_free_gb]);

  const set = <K extends keyof ProcessOptions>(k: K, v: ProcessOptions[K]) => setOpts((o) => ({ ...o, [k]: v }));
  const name = project?.name ?? urlInfo?.title ?? "";
  const duration = project?.duration ?? urlInfo?.duration ?? 0;
  const res = project ? `${project.width}×${project.height}` : urlInfo?.height ? `${urlInfo.width ?? "?"}×${urlInfo.height}` : "–";

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      if (project) {
        await api.process(project.id, opts);
        nav(`/projects/${project.id}`);
      } else if (urlInfo) {
        const p = await api.createUrlProject(urlInfo.url, opts, urlInfo.title);
        nav(`/projects/${p.id}`);
      }
      toast("Processing started");
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="stack lg">
      <div className="card card-body">
        <div className="row between wrap" style={{ gap: 16 }}>
          <div className="row" style={{ gap: 14, minWidth: 0 }}>
            <div className="proj-thumb" style={{ width: 56, height: 56 }}>
              <Icon name={urlInfo ? "link" : "film"} size={22} />
            </div>
            <div className="stack sm" style={{ minWidth: 0, gap: 2 }}>
              <strong className="ellipsis" style={{ fontSize: 16 }}>{name}</strong>
              <span className="muted small">
                {fmtDuration(duration)} · {res}
                {project?.fps ? ` · ${Math.round(project.fps)} fps` : ""}
                {urlInfo?.extractor ? ` · ${urlInfo.extractor}` : ""}
                {urlInfo?.uploader ? ` · ${urlInfo.uploader}` : ""}
              </span>
            </div>
          </div>
          <button className="btn ghost sm" onClick={onBack}>
            <Icon name="chevronLeft" /> Choose another video
          </button>
        </div>
        {project && project.has_audio === false && (
          <div style={{ marginTop: 14 }}>
            <Alert kind="warn" title="No audio track">This video has no audio, so clips can't be found from speech.</Alert>
          </div>
        )}
        {urlInfo?.existing_project && (
          <div style={{ marginTop: 14 }}>
            <Alert kind="info">You already have a project for this link. The downloaded file will be reused, not downloaded again.</Alert>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Clip settings</h2>
        </div>
        <div className="card-body stack lg">
          <div className="grid cols-2">
            <div className="field">
              <span className="label-text">Clip length</span>
              <div className="seg">
                {DURATIONS.map(([k, l]) => (
                  <button key={k} className={opts.duration_preset === k ? "active" : ""} onClick={() => set("duration_preset", k)}>
                    {l}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <label htmlFor="maxc">Maximum clips: {opts.max_clips}</label>
              <input id="maxc" type="range" min={1} max={20} value={opts.max_clips} onChange={(e) => set("max_clips", +e.target.value)} />
              <span className="hint">About one clip per 4 minutes of video. Weak moments are never added just to reach this number.</span>
            </div>
            <div className="field">
              <span className="label-text">Aspect ratio</span>
              <div className="seg">
                {(["9:16", "1:1", "16:9"] as const).map((a) => (
                  <button key={a} className={opts.aspect_ratio === a ? "active" : ""} onClick={() => set("aspect_ratio", a)}>
                    {a}
                  </button>
                ))}
              </div>
            </div>
            <div className="field">
              <span className="label-text">Framing</span>
              <div className="seg">
                {(
                  [
                    ["auto", "Auto"],
                    ["center", "Centre crop"],
                    ["blur", "Blurred fill"],
                    ["split", "Split (2 people)"],
                  ] as const
                ).map(([k, l]) => (
                  <button key={k} className={opts.layout === k ? "active" : ""} onClick={() => set("layout", k)}>
                    {l}
                  </button>
                ))}
              </div>
              <span className="hint">Auto tracks faces when there are any, otherwise keeps the whole frame on a blurred background.</span>
            </div>
            <div className="field">
              <span className="label-text">Resolution</span>
              <div className="seg">
                {([720, 1080] as const).map((h) => (
                  <button key={h} className={opts.output_height === h ? "active" : ""} onClick={() => set("output_height", h)}>
                    {h}p
                  </button>
                ))}
              </div>
              {opts.output_height === 1080 && settings.mode === "low" && <span className="hint">1080p uses more memory and disk than 720p.</span>}
            </div>
            <div className="field">
              <span className="label-text">Caption style</span>
              <select className="select" value={opts.caption_preset} onChange={(e) => set("caption_preset", e.target.value)}>
                {presets && Object.entries(presets).map(([k, p]) => <option key={k} value={k}>{p.name}</option>)}
              </select>
            </div>
          </div>
          <div className="stack sm">
            <label className="check">
              <input type="checkbox" checked={!!opts.auto_render} onChange={(e) => set("auto_render", e.target.checked)} />
              <span>
                <strong>Render clips automatically</strong>
                <br />
                <span className="small muted">Turn off to review candidates first and render only the ones you like (saves time and disk).</span>
              </span>
            </label>
            <label className="check" style={{ opacity: models?.llm.available ? 1 : 0.6 }}>
              <input type="checkbox" checked={!!opts.llm_enabled} disabled={!models?.llm.available} onChange={(e) => set("llm_enabled", e.target.checked)} />
              <span>
                <strong>Use local AI for titles &amp; hooks</strong> <span className="badge">optional</span>
                <br />
                <span className="small muted">{models?.llm.available ? `Runs ${models.llm.model} through Ollama on this Mac.` : `Not available: ${models?.llm.status ?? "checking…"}. Built-in rules are used instead (titles and hooks taken from the speaker's own words).`}</span>
              </span>
            </label>
          </div>
        </div>
      </div>

      {estimate && (
        <Alert kind={estimate.ok ? "info" : "danger"} title={estimate.ok ? "Storage estimate" : "Not enough disk space"}>
          Needs about <strong>{fmtMB(estimate.total_mb)}</strong> (including temporary files, which are deleted afterwards). {fmtMB(estimate.free_mb)} free.
          {!estimate.ok && " Free some space or choose an external working folder in Settings."}
        </Alert>
      )}
      {error && <Alert kind="danger" title="Couldn't start">{error}</Alert>}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn primary lg" onClick={start} disabled={busy || (estimate ? !estimate.ok : false)}>
          {busy ? <Spinner /> : <Icon name="zap" />} Find clips
        </button>
      </div>
    </div>
  );
}
