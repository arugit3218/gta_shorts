import { useEffect, useRef, useState } from "react";
import { Link, useBlocker, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { DEFAULT_POLISH, PolishControls, RecipeSelect, recipeToOptions } from "../components/polish";
import { CampaignSelect } from "../components/campaign";
import { HelpTip } from "../components/help";
import { INTENSITY, IntensityPicker, StylePicker, campaignOptions, styleOf, styleOptions, type Intensity, type StyleKey } from "../components/styles";
import { LAYOUT_OPTIONS } from "../components/framing";
import { Icon } from "../components/Icon";
import { Alert, Bar, Modal, Spinner, useToast } from "../components/ui";
import { fmtDuration, fmtMB } from "../format";
import { usePoll } from "../hooks";
import type { ProcessOptions, Project, Settings, UrlInfo } from "../types";

type Source = "upload" | "url" | "path";

export default function NewProject() {
  const toast = useToast();
  const [params] = useSearchParams();
  const [tab, setTab] = useState<Source>("upload");
  const [project, setProject] = useState<Project | null>(null);
  const [urlInfo, setUrlInfo] = useState<UrlInfo | null>(null);
  const [bulk, setBulk] = useState<string[] | null>(null);
  const [uploads, setUploads] = useState<Project[] | null>(null);
  const [campaignId, setCampaignId] = useState<string>(params.get("campaign") ?? "");
  const settings = usePoll(api.settings, []).data;
  const campaigns = usePoll(() => api.campaigns(), []).data;
  const musicCampaign = !!campaignId && campaigns?.find((c) => c.id === campaignId)?.brief.type === "music";
  // videos already added to the chosen campaign, so they aren't added twice
  const campDetail = usePoll(() => (campaignId ? api.campaign(campaignId) : Promise.resolve(null)), [campaignId]).data;
  const campVideos = (campDetail?.project_list ?? []).filter((p) => p.source_type !== "montage");
  // "Re-process with new settings" from a project page: same form, pre-filled with that project's options
  const reprocessId = params.get("reprocess");
  useEffect(() => {
    if (!reprocessId) return;
    api.project(reprocessId).then((p) => {
      setProject(p);
      setCampaignId(p.campaign_id ?? "");
    });
  }, [reprocessId]);
  // "Not started" videos (Projects page) open here to choose settings
  const setupId = params.get("setup");
  const quickMode = params.get("quick") === "1";
  useEffect(() => {
    if (!setupId) return;
    api.project(setupId).then((p) => {
      setProject(p);
      setCampaignId(p.campaign_id ?? "");
    });
  }, [setupId]);

  const reset = () => {
    setProject(null);
    setUrlInfo(null);
    setBulk(null);
    setUploads(null);
  };

  // Uploaded (or added in place) but not started yet. Leaving asks whether to keep them for later or delete them.
  const pending = reprocessId ? [] : (uploads ?? (project && project.status === "new" ? [project] : []));
  const started = useRef(false);
  const [leaving, setLeaving] = useState<"back" | null>(null);
  const [deleting, setDeleting] = useState(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => pending.length > 0 && !started.current && currentLocation.pathname + currentLocation.search !== nextLocation.pathname + nextLocation.search);
  useEffect(() => {
    if (!pending.length) return;
    const warn = (e: BeforeUnloadEvent) => {
      if (started.current) return;
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [pending.length]);
  const leaveOpen = blocker.state === "blocked" || leaving !== null;
  const closeLeave = () => (blocker.state === "blocked" ? blocker.reset() : setLeaving(null));
  const finishLeave = () => {
    if (blocker.state === "blocked") blocker.proceed();
    else {
      setLeaving(null);
      reset();
    }
  };
  const deletePending = async () => {
    setDeleting(true);
    let freed = 0;
    for (const x of pending) {
      try {
        freed += (await api.deleteProject(x.id, { delete_exports: true, delete_source: true })).freed_mb;
      } catch {
        /* already gone */
      }
    }
    setDeleting(false);
    toast(freed ? `Deleted · ${freed} MB freed` : "Deleted");
    finishLeave();
  };
  const back = () => (pending.length ? setLeaving("back") : reset());

  return (
    <div className="stack lg" style={{ maxWidth: 880 }}>
      <div className="page-head">
        <div>
          <h1>{reprocessId ? "Re-process with new settings" : quickMode ? "Quick clip" : "Clips from a video"}</h1>
          <p className="sub">
            {reprocessId
              ? "Change the settings, then find clips again. The download and transcript are reused."
              : setupId
                ? "This video was added but never started. Choose its settings, then start."
                : quickMode
                  ? "For a video that's already short, or when you know where the clip is. It still gets captions, campaign logo, checks and export."
                  : "Add a long video. ClipStudio finds the best moments and turns them into clips."}
          </p>
        </div>
      </div>

      {musicCampaign && !reprocessId && (
        <Alert kind="info">
          This is a music campaign. For a beat-synced edit (many short shots cut to the song), use{" "}
          <Link to={`/montage/new?campaign=${campaignId}`}>Music edit</Link> instead. It takes links and files too. This page finds clips inside one long video.
        </Alert>
      )}

      {!project && !urlInfo && !bulk && !uploads && !reprocessId && (
        <div className="card">
          <div className="card-body">
            <div className="field" style={{ marginBottom: 14 }}>
              <label>Campaign (optional)</label>
              <CampaignSelect value={campaignId} onChange={setCampaignId} />
              <span className="hint">Clips get the campaign's logo, sound and text rules, and are checked against its brief before export.</span>
            </div>
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
            {tab === "upload" && <UploadSource onDone={setProject} onMany={setUploads} campaignId={campaignId} />}
            {tab === "url" && <UrlSource onDone={setUrlInfo} onBulk={setBulk} />}
            {tab === "path" && <PathSource onDone={setProject} campaignId={campaignId} />}
          </div>
        </div>
      )}

      {!project && !urlInfo && !bulk && !uploads && !reprocessId && campVideos.length > 0 && (
        <section className="card">
          <div className="card-head">
            <h2>Already in {campDetail?.name}</h2>
            <span className="small muted">No need to add these again</span>
          </div>
          <div className="card-body tight">
            {campVideos.map((p) => (
              <div className="list-row row" key={p.id} style={{ justifyContent: "space-between" }}>
                <span className="ellipsis" style={{ minWidth: 0 }}>
                  {p.name}
                </span>
                <span className="row nowrap">
                  <span className="small muted">{p.stage === "not_started" ? "not started" : `${p.clip_count} clip${p.clip_count === 1 ? "" : "s"} · ${p.status}`}</span>
                  {p.stage === "not_started" ? (
                    <Link className="btn sm primary" to={`/new?setup=${p.id}`}>
                      Start
                    </Link>
                  ) : (
                    <Link className="btn sm" to={`/projects/${p.id}`}>
                      Open
                    </Link>
                  )}
                </span>
              </div>
            ))}
          </div>
        </section>
      )}

      {(project || urlInfo || bulk || uploads) && settings && <Configure project={project} urlInfo={urlInfo} bulk={bulk} uploads={uploads} settings={settings} onBack={back} onStarted={(v) => (started.current = v)} initialQuick={quickMode} campaignId={campaignId} setCampaignId={setCampaignId} reprocess={!!reprocessId} />}

      {leaveOpen && (
        <Modal
          title={pending.length > 1 ? `Keep these ${pending.length} videos for later?` : "Keep this video for later?"}
          onClose={closeLeave}
          actions={
            <>
              <button className="btn" onClick={closeLeave} disabled={deleting}>
                Stay
              </button>
              <button className="btn danger" onClick={deletePending} disabled={deleting}>
                {deleting ? <Spinner /> : <Icon name="trash" />} Delete
              </button>
              <button className="btn primary" onClick={finishLeave} disabled={deleting}>
                Keep for later
              </button>
            </>
          }
        >
          <p className="muted">
            {pending.length > 1 ? "They haven't" : "It hasn't"} been started. <strong>Keep for later</strong> puts {pending.length > 1 ? "them" : "it"} under <strong>Videos → Not started</strong>, where you can choose settings any time.{" "}
            <strong>Delete</strong> removes the uploaded copy to free disk space. Files you used in place are never deleted.
          </p>
        </Modal>
      )}
    </div>
  );
}

function UploadSource({ onDone, onMany, campaignId }: { onDone: (p: Project) => void; onMany: (ps: Project[]) => void; campaignId: string }) {
  const [over, setOver] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [count, setCount] = useState<{ k: number; n: number } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const abort = useRef<AbortController | null>(null);

  // One file → its own settings page. Several → uploaded one after another, then one settings page for all.
  const start = async (list: FileList | File[]) => {
    const files = Array.from(list);
    if (!files.length) return;
    setError(null);
    abort.current = new AbortController();
    const done: Project[] = [];
    const failed: string[] = [];
    for (let k = 0; k < files.length; k++) {
      const f = files[k];
      setFile(f);
      setCount(files.length > 1 ? { k: k + 1, n: files.length } : null);
      setProgress(0);
      try {
        done.push(await api.uploadProject(f, setProgress, abort.current.signal, campaignId));
      } catch (e) {
        if (abort.current.signal.aborted) break;
        failed.push(`${f.name}: ${(e as Error).message}`);
      }
    }
    setProgress(null);
    setCount(null);
    if (failed.length) setError(failed.join("\n"));
    if (done.length === 1 && files.length === 1) onDone(done[0]);
    else if (done.length) onMany(done);
  };

  if (progress !== null && file) {
    return (
      <div className="stack" style={{ padding: "20px 0" }}>
        <div className="row between">
          <strong className="ellipsis">
            {count ? `${count.k} of ${count.n} · ` : ""}
            {file.name}
          </strong>
          <span className="muted small">
            {fmtMB((file.size / 1048576) * progress)} / {fmtMB(file.size / 1048576)}
          </span>
        </div>
        <Bar value={progress} />
        <div className="row between">
          <span className="small muted">{progress >= 1 ? "Reading video metadata…" : "Uploading to your local working folder…"}</span>
          {progress < 1 && (
            <button className="btn sm" onClick={() => abort.current?.abort()}>
              {count ? "Cancel the rest" : "Cancel"}
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
          if (e.dataTransfer.files.length) start(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <div className="big-icon">
          <Icon name="upload" size={22} />
        </div>
        <h3>Drop videos here or click to browse</h3>
        <p className="muted small" style={{ marginTop: 6 }}>
          One or several files · MP4, MOV, MKV, WebM and other FFmpeg-supported formats
        </p>
        <input
          ref={input}
          type="file"
          accept="video/*,.mkv,.mts,.m2ts"
          multiple
          hidden
          onChange={(e) => {
            if (e.target.files?.length) start(e.target.files);
            e.target.value = "";
          }}
        />
      </div>
      <p className="small muted">
        Uploading copies the file into ClipStudio's working folder. To avoid a duplicate copy on a full disk, use <strong>Use file in place</strong> instead.
      </p>
      {error && (
        <Alert kind="danger" title="Upload failed">
          <span style={{ whiteSpace: "pre-line" }}>{error}</span>
        </Alert>
      )}
    </div>
  );
}

function UrlSource({ onDone, onBulk }: { onDone: (i: UrlInfo) => void; onBulk: (urls: string[]) => void }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const sys = usePoll(api.system, []).data;
  const urls = [...new Set(url.split(/[\s,]+/).map((u) => u.trim()).filter((u) => /^https?:\/\//i.test(u)))];
  const go = async () => {
    if (urls.length > 1) return onBulk(urls);
    setBusy(true);
    setError(null);
    try {
      onDone(await api.inspectUrl(urls[0] ?? url.trim()));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="stack">
      <div className="field">
        <label htmlFor="url">Video link(s)</label>
        <textarea id="url" className="textarea" rows={urls.length > 1 ? 6 : 2} placeholder={"https://…\nPaste one link, or several (one per line) to queue them all"} value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && !e.shiftKey && urls.length === 1 && (e.preventDefault(), go())} autoFocus />
        <div className="row between">
          <span className="hint">{urls.length > 1 ? `${urls.length} links: they'll be processed one after another.` : "Shift+Enter for a new line."}</span>
          <button className="btn primary" disabled={!urls.length || busy} onClick={go}>
            {busy ? <Spinner /> : <Icon name="link" />} {urls.length > 1 ? `Queue ${urls.length} links` : "Check link"}
          </button>
        </div>
      </div>
      {sys && !sys.downloads.youtube_ready && (
        <Alert kind="warn" title="YouTube downloads may fail">
          YouTube now needs a JavaScript runtime for downloads. Install Node.js or Deno (e.g. <span className="mono">brew install deno</span>), then restart ClipStudio.
        </Alert>
      )}
      <Alert kind="info">
        Only public videos you're allowed to download are supported (via yt-dlp). Login-only, private and DRM-protected videos aren't. Checking a link contacts the video site; nothing else leaves your Mac.
      </Alert>
      {error && <Alert kind="danger" title="Can't use this link">{error}</Alert>}
    </div>
  );
}

function PathSource({ onDone, campaignId }: { onDone: (p: Project) => void; campaignId: string }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      onDone(await api.importPath(path, campaignId));
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

function Configure({
  project,
  urlInfo,
  bulk,
  uploads = null,
  settings,
  onBack,
  onStarted,
  initialQuick = false,
  campaignId,
  setCampaignId,
  reprocess = false,
}: {
  onStarted?: (started: boolean) => void;
  initialQuick?: boolean;
  reprocess?: boolean;
  project: Project | null;
  urlInfo: UrlInfo | null;
  bulk: string[] | null;
  uploads?: Project[] | null;
  settings: Settings;
  onBack: () => void;
  campaignId: string;
  setCampaignId: (id: string) => void;
}) {
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
    music: settings.music_default,
    sfx: settings.sfx_default ? "auto" : "off",
    blur_burned: settings.blur_burned_default,
    hook_overlay: settings.hook_overlay_default,
    quick: initialQuick,
    polish: { ...DEFAULT_POLISH, ...settings.polish },
    ...(reprocess && project ? { ...project.options, quick: false, polish: { ...DEFAULT_POLISH, ...settings.polish, ...project.options.polish } } : {}),
  });
  const [keepClips, setKeepClips] = useState(true);
  const camps = usePoll(() => api.campaigns(), []).data;
  const camp = camps?.find((c) => c.id === campaignId) ?? null;
  const [style, setStyle] = useState<StyleKey | "">("");
  const [intensity, setIntensity] = useState<Intensity | "">("");
  const pickStyle = (k: StyleKey, i: Intensity) => {
    setStyle(k);
    setIntensity(i);
    setRecipeId("");
    const so = styleOptions(k, i);
    setOpts((o) => ({ ...o, ...so, polish: { ...DEFAULT_POLISH, ...settings.polish, ...o.polish, ...so.polish } }));
  };
  // a campaign's chosen style (and clips per video) become the starting point
  const campKey = camp?.id;
  useEffect(() => {
    if (!camp || reprocess) return;
    // exactly what the campaign page uses when you add videos there
    const k = (camp.brief.style || "gaming") as StyleKey;
    setStyle(k);
    setIntensity((camp.brief.intensity || styleOf(k).intensity) as Intensity);
    const co = campaignOptions(camp.brief);
    setOpts((o) => ({ ...o, ...co, max_clips: camp.brief.max_clips ?? settings.max_clips, polish: { ...DEFAULT_POLISH, ...settings.polish, ...o.polish, ...co.polish } }));
    setOverride(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campKey]);
  // with a campaign, its settings are used and the style/clip controls stay folded away unless you ask
  const [override, setOverride] = useState(false);
  const useCamp = !!camp && !reprocess && !override;
  const campSummary = camp
    ? [
        camp.brief.min_duration || camp.brief.max_duration ? `${camp.brief.min_duration ?? "any"}–${camp.brief.max_duration ?? "any"} s clips` : null,
        `up to ${opts.max_clips} clips`,
        `${styleOf(camp.brief.style || "gaming").label}, ${(camp.brief.intensity || styleOf(camp.brief.style || "gaming").intensity)} editing`,
        `${LAYOUT_OPTIONS.find((o) => o.key === opts.layout)?.label.replace(" (recommended)", "") ?? opts.layout} framing`,
        `${presets?.[opts.caption_preset ?? ""]?.name ?? opts.caption_preset} captions`,
      ].filter(Boolean)
    : [];
  const [recipeId, setRecipeId] = useState("");
  const polish = { ...DEFAULT_POLISH, ...settings.polish, ...opts.polish };
  const [estimate, setEstimate] = useState<{ total_mb: number; free_mb: number; ok: boolean } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (uploads)
      Promise.all(uploads.map((u) => api.estimate(u.id))).then((es) => {
        const total = es.reduce((a, e) => a + e.estimate.total_mb, 0);
        const free = es[0]?.free_mb ?? 0;
        setEstimate({ total_mb: total, free_mb: free, ok: free - total > settings.min_free_gb * 1024 });
      });
    else if (project) api.estimate(project.id).then((e) => setEstimate({ total_mb: e.estimate.total_mb, free_mb: e.free_mb, ok: e.ok }));
    else if (urlInfo) {
      // Smart downloads fetch audio + short sections instead of the whole video.
      const total = settings.download_strategy === "smart" ? urlInfo.estimate.total_mb - urlInfo.estimate.source_mb * 0.85 : urlInfo.estimate.total_mb;
      setEstimate({ total_mb: total, free_mb: urlInfo.free_mb, ok: urlInfo.free_mb - total > settings.min_free_gb * 1024 });
    }
  }, [project, urlInfo, uploads, settings.min_free_gb, settings.download_strategy]);

  const set = <K extends keyof ProcessOptions>(k: K, v: ProcessOptions[K]) => setOpts((o) => ({ ...o, [k]: v }));
  const many = bulk ?? uploads;
  const name = project?.name ?? urlInfo?.title ?? (bulk ? `${bulk.length} links` : uploads ? `${uploads.length} videos` : "");
  const duration = project?.duration ?? urlInfo?.duration ?? 0;
  const res = project ? `${project.width}×${project.height}` : urlInfo?.height ? `${urlInfo.width ?? "?"}×${urlInfo.height}` : "–";

  const start = async () => {
    setBusy(true);
    setError(null);
    onStarted?.(true);
    try {
      if (project) {
        if ((project.campaign_id ?? "") !== campaignId) await api.patchProject(project.id, { campaign_id: campaignId });
        await api.process(project.id, opts, reprocess && keepClips);
        nav(`/projects/${project.id}`);
      } else if (urlInfo) {
        const p = await api.createUrlProject(urlInfo.url, opts, urlInfo.title, campaignId);
        nav(`/projects/${p.id}`);
      } else if (uploads) {
        for (const u of uploads) {
          if ((u.campaign_id ?? "") !== campaignId) await api.patchProject(u.id, { campaign_id: campaignId });
          await api.process(u.id, opts);
        }
        nav(campaignId ? `/campaigns/${campaignId}` : "/projects");
      } else if (bulk) {
        const r = await api.createBulk(bulk, opts, campaignId);
        if (r.errors.length) toast(`${r.errors.length} link(s) skipped: ${r.errors[0].error}`, "error");
        nav(campaignId ? `/campaigns/${campaignId}` : "/projects");
      }
      toast("Processing started");
    } catch (e) {
      onStarted?.(false);
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
              <Icon name={urlInfo || bulk ? "link" : "film"} size={22} />
            </div>
            <div className="stack sm" style={{ minWidth: 0, gap: 2 }}>
              <strong className="ellipsis" style={{ fontSize: 16 }}>{name}</strong>
              <span className="muted small">
                {many ? (
                  <>
                    Processed one after another with the settings below
                    {uploads ? ` · ${fmtDuration(uploads.reduce((a, u) => a + (u.duration ?? 0), 0))} in total` : ""}
                  </>
                ) : (
                  <>
                    {fmtDuration(duration)} · {res}
                  </>
                )}
                {!many && project?.fps ? ` · ${Math.round(project.fps)} fps` : ""}
                {urlInfo?.extractor ? ` · ${urlInfo.extractor}` : ""}
                {urlInfo?.uploader ? ` · ${urlInfo.uploader}` : ""}
              </span>
            </div>
          </div>
          {reprocess && project ? (
            <button className="btn ghost sm" onClick={() => nav(`/projects/${project.id}`)}>
              <Icon name="chevronLeft" /> Back to the project
            </button>
          ) : (
            <button className="btn ghost sm" onClick={onBack}>
              <Icon name="chevronLeft" /> {many ? "Choose other videos" : "Choose another video"}
            </button>
          )}
        </div>
        {reprocess && project && project.clip_count > 0 && (
          <label className="check" style={{ marginTop: 14 }}>
            <input type="checkbox" checked={keepClips} onChange={(e) => setKeepClips(e.target.checked)} />
            <span>
              <strong>Keep approved and hand-made clips</strong>
              <br />
              <span className="small muted">
                Clips you approved, added by hand or imported as AI picks stay; the rest are replaced by the new results. Unchecked: the other clips are deleted too, including approved ones. Clips marked as posted always stay.
              </span>
            </span>
          </label>
        )}
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
          <h2>Campaign &amp; clips</h2>
        </div>
        <div className="card-body stack">
          <div className="grid cols-2">
            <div className="field">
              <label>Campaign</label>
              <CampaignSelect value={campaignId} onChange={setCampaignId} />
              <span className="hint">{camp ? "Clips follow its requirements (length, logo, sound, caption)." : "Optional. Pick one to apply a brand's requirements."}</span>
            </div>
            {!opts.quick && !useCamp && (
              <div className="field">
                <label htmlFor="maxc">
                  Up to {opts.max_clips} clips {many ? "per video" : ""} <HelpTip topic="clips_per_video" />
                </label>
                <input id="maxc" type="range" min={1} max={30} value={opts.max_clips} onChange={(e) => set("max_clips", +e.target.value)} />
              </div>
            )}
          </div>
          {useCamp && !opts.quick && (
            <div className="camp-uses">
              <div>
                <b>Using the campaign's settings</b>
                <span className="small">{campSummary.join(" · ")}</span>
                <span className="tiny muted">Set on the campaign (Overview → Change style, Requirements). Editing details start from your defaults in Settings.</span>
              </div>
              <button className="btn sm" onClick={() => setOverride(true)}>
                Change for this video
              </button>
            </div>
          )}
          {camp && override && !reprocess && (
            <Alert kind="info">
              Changes here apply to {many ? "these videos" : "this video"} only; the campaign keeps its settings.{" "}
              <button className="link-btn" onClick={() => setOverride(false)}>
                Use the campaign's settings again
              </button>
            </Alert>
          )}
          {!opts.quick && !useCamp && (
            <div className="field">
              <span className="label-text">
                Clip length <HelpTip topic="clip_length" />
              </span>
              {camp && (camp.brief.min_duration || camp.brief.max_duration) ? (
                <span className="small">
                  <b>
                    {camp.brief.min_duration ?? "any"}–{camp.brief.max_duration ?? "any"} seconds
                  </b>{" "}
                  <span className="muted">from the campaign</span>
                </span>
              ) : (
                <div className="seg">
                  {DURATIONS.map(([k, l]) => (
                    <button key={k} className={opts.duration_preset === k ? "active" : ""} onClick={() => set("duration_preset", k)}>
                      {l}
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
          {!bulk && (
            <label className="check">
              <input type="checkbox" checked={!!opts.quick} onChange={(e) => set("quick", e.target.checked)} />
              <span>
                <strong>Quick clip</strong>: I know where the clip is <HelpTip topic="quick_clip" />
                <br />
                <span className="small muted">Skip finding highlights; use your own start/end, or the whole file for an already-short video.</span>
              </span>
            </label>
          )}
          {opts.quick && (
            <div className="grid cols-2">
              <div className="field">
                <label>Start (seconds)</label>
                <input className="input" type="number" min={0} step={0.1} value={opts.quick_start ?? 0} onChange={(e) => set("quick_start", Math.max(0, +e.target.value))} />
              </div>
              <div className="field">
                <label>End (seconds, blank = end of video)</label>
                <input className="input" type="number" min={0} step={0.1} value={opts.quick_end ?? ""} onChange={(e) => set("quick_end", e.target.value === "" ? undefined : +e.target.value)} />
              </div>
            </div>
          )}
          {opts.quick && opts.quick_end != null && opts.quick_end <= (opts.quick_start ?? 0) && (
            <Alert kind="warn">The end must be after the start.</Alert>
          )}
          {opts.quick && uploads && <span className="hint">The same start and end apply to every video. Leave them at 0 and blank to use each whole file.</span>}
        </div>
      </div>

      {!useCamp && (
      <div className="card">
        <div className="card-head">
          <h2>
            Style <HelpTip topic="style" />
          </h2>
          <span className="small muted">{style ? "Change anything per clip later in the editor" : "Using your defaults from Settings. Pick a style to change them."}</span>
        </div>
        <div className="card-body stack">
          <StylePicker value={style} onChange={(k) => pickStyle(k, styleOf(k).intensity)} />
          <IntensityPicker
            value={intensity}
            onChange={(i) => {
              if (style) return pickStyle(style, i);
              // no style picked: change only the editing switches, keep everything else from Settings
              setIntensity(i);
              const pol = INTENSITY.find((x) => x.key === i)?.polish ?? {};
              setOpts((o) => ({ ...o, polish: { ...DEFAULT_POLISH, ...settings.polish, ...o.polish, ...pol } }));
            }}
          />
        </div>
      </div>
      )}

      <details className="adv">
        <summary>{useCamp ? "Customize everything for this video (framing, captions, extras, editing details)" : "Customize everything (framing, captions, extras, editing details)"}</summary>
        <div className="stack lg" style={{ marginTop: 14 }}>
          <div className="grid cols-2">
            <div className="field">
              <span className="label-text">Content type</span>
              <div className="seg">
                {(
                  [
                    ["auto", "Auto"],
                    ["talk", "Talk"],
                    ["gameplay", "Gameplay / action"],
                  ] as const
                ).map(([k, l]) => (
                  <button key={k} className={(opts.content_type ?? settings.content_type) === k ? "active" : ""} onClick={() => set("content_type", k)}>
                    {l}
                  </button>
                ))}
              </div>
              <span className="hint">Talk: podcasts and interviews (finds clips from what's said). Gameplay: streams, games, sports (also uses gunfire, explosions, reactions and on-screen action). Auto decides from how much is said.</span>
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
              <div className="seg" role="radiogroup" aria-label="Framing">
                {LAYOUT_OPTIONS.map((o) => (
                  <button key={o.key} role="radio" aria-checked={opts.layout === o.key} className={opts.layout === o.key ? "active" : ""} onClick={() => set("layout", o.key)}>
                    {o.label.replace(" (recommended)", "")}
                  </button>
                ))}
              </div>
              <span className="hint">
                {LAYOUT_OPTIONS.find((o) => o.key === opts.layout)?.desc}
                {opts.layout === "stream" ? " You can mark the webcam exactly later with Framing on the project page." : ""}
              </span>
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
          <div className="divider" />
          <div className="row" style={{ justifyContent: "space-between" }}>
            <span className="section-title">Recipe</span>
            <span className="small muted">Saved looks from Settings or the clip editor</span>
          </div>
          <RecipeSelect
            value={recipeId}
            placeholder="Use these settings (no recipe)"
            onChange={(id, r) => {
              setRecipeId(id);
              if (r) {
                setOpts((o) => ({ ...o, ...recipeToOptions(r), polish: { ...DEFAULT_POLISH, ...settings.polish, ...r.polish } }));
                toast(`Recipe “${r.name}” applied`);
              }
            }}
          />
          <div className="divider" />
          <span className="section-title">Extras</span>
          <div className="grid cols-2">
            <label className="check">
              <input type="checkbox" checked={opts.hook_overlay !== false} onChange={(e) => set("hook_overlay", e.target.checked)} />
              <span>
                <strong>Hook title</strong> at the start
                <br />
                <span className="small muted">Bold title card for the first seconds.</span>
              </span>
            </label>
            <label className="check">
              <input type="checkbox" checked={opts.sfx === "auto"} onChange={(e) => set("sfx", e.target.checked ? "auto" : "off")} />
              <span>
                <strong>Sound effects</strong>
                <br />
                <span className="small muted">Pop, whoosh, ding… placed automatically (2–4 per clip).</span>
              </span>
            </label>
            <label className="check">
              <input type="checkbox" checked={opts.music === "auto"} onChange={(e) => set("music", e.target.checked ? "auto" : "none")} />
              <span>
                <strong>Background music</strong> from your library
                <br />
                <span className="small muted">Ducked under speech. Needs tracks in Library.</span>
              </span>
            </label>
            <label className="check">
              <input type="checkbox" checked={opts.blur_burned !== "off"} onChange={(e) => set("blur_burned", e.target.checked ? "auto" : "off")} />
              <span>
                <strong>Blur burned-in captions</strong>
                <br />
                <span className="small muted">Hides the source's own subtitles when found.</span>
              </span>
            </label>
          </div>
          <div className="divider" />
          <span className="section-title">Editing details</span>
          <span className="hint" style={{ marginTop: -8 }}>Set by the editing intensity above; fine-tune here.</span>
          <PolishControls value={polish} onChange={(p) => setOpts((o) => ({ ...o, polish: { ...polish, ...p } }))} />
          <div className="divider" />
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
      </details>

      {estimate && (
        <Alert kind={estimate.ok ? "info" : "danger"} title={estimate.ok ? "Storage estimate" : "Not enough disk space"}>
          Needs about <strong>{fmtMB(estimate.total_mb)}</strong> (including temporary files, which are deleted afterwards). {fmtMB(estimate.free_mb)} free.
          {!estimate.ok && " Free some space or choose an external working folder in Settings."}
        </Alert>
      )}
      {error && <Alert kind="danger" title="Couldn't start">{error}</Alert>}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn primary lg" onClick={start} disabled={busy || (estimate ? !estimate.ok : false) || (!!opts.quick && opts.quick_end != null && opts.quick_end <= (opts.quick_start ?? 0))}>
          {busy ? <Spinner /> : <Icon name="zap" />} {opts.quick ? (uploads ? `Make ${uploads.length} clips` : "Make clip") : many ? `Queue ${many.length} videos` : "Find clips"}
        </button>
      </div>
    </div>
  );
}
