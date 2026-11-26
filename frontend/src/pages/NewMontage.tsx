import { useEffect, useMemo, useRef, useState } from "react";
import { useBlocker, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { CampaignSelect } from "../components/campaign";
import { HelpTip } from "../components/help";
import { Icon } from "../components/Icon";
import { Alert, Modal, Spinner, plural, useToast } from "../components/ui";
import { fmtDuration } from "../format";
import { usePoll } from "../hooks";
import type { MontageOptions, Project } from "../types";

const STYLES = [
  { key: "chill", label: "Chill", desc: "Calm: a cut every bar while it builds, every 2 beats after the drop." },
  { key: "normal", label: "Normal", desc: "Balanced: every 2 beats while it builds, every beat after the drop." },
  { key: "hype", label: "Hype", desc: "Fast: like Normal, plus quick stutter cuts right after the drop." },
] as const;

const STEPS = ["Song", "Videos", "Style"] as const;

/** Music edit wizard (R2): song → videos → style. */
export default function NewMontage() {
  const nav = useNavigate();
  const toast = useToast();
  const [params] = useSearchParams();
  const projects = usePoll(api.projects, []);
  const [step, setStep] = useState(0);
  const [campaignId, setCampaignId] = useState(params.get("campaign") ?? "");
  const campaign = usePoll(() => (campaignId ? api.campaign(campaignId) : Promise.resolve(null)), [campaignId]).data;
  const [name, setName] = useState("Music edit");
  const [sources, setSources] = useState<string[]>([]);
  const [songFile, setSongFile] = useState<File | null>(null);
  const [songKind, setSongKind] = useState<"upload" | "campaign">("upload");
  const [soundId, setSoundId] = useState<string>("");
  const [useCut, setUseCut] = useState(false);
  const [opts, setOpts] = useState<Omit<MontageOptions, "sources" | "song">>({ length: 15, drop_at: 0.4, style: "normal", pov: "", variants: 3, audio: "song", flash: true });
  const [uploading, setUploading] = useState<string | null>(null);
  const [links, setLinks] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const [busy, setBusy] = useState(false);
  const songInput = useRef<HTMLInputElement>(null);
  // videos uploaded or linked during this visit: leaving without making the edit asks whether to keep them
  const [added, setAdded] = useState<string[]>([]);
  const started = useRef(false);
  const [leaveBusy, setLeaveBusy] = useState(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => added.length > 0 && !started.current && currentLocation.pathname !== nextLocation.pathname);
  const videoInput = useRef<HTMLInputElement>(null);

  // Links that aren't downloaded yet are fine: the montage downloads them when it starts.
  const usable = useMemo(() => (projects.data ?? []).filter((p) => p.source_type !== "montage" && (p.source_available || p.source_type === "url")), [projects.data]);
  const linkList = links.split(/\s+/).map((u) => u.trim()).filter((u) => /^https?:\/\//i.test(u));
  const campaignSounds = campaign?.brief.sounds.filter((s) => s.has_file) ?? [];
  const set = <K extends keyof typeof opts>(k: K, v: (typeof opts)[K]) => setOpts((o) => ({ ...o, [k]: v }));
  const minLen = campaign?.brief.min_duration ?? 0;
  const maxLen = campaign?.brief.max_duration ?? 45;
  // a campaign with its own sound file: use it by default
  useEffect(() => {
    if (campaignSounds.length) setSongKind("campaign");
    // the official sound is added in the app when posting: render silent so it isn't heard twice
    if (campaign?.brief.sound_rule.mode === "in_app") set("audio", "silent");
    // length from the campaign: just above its minimum, within its maximum
    if (minLen || campaign?.brief.max_duration) set("length", Math.min(maxLen, Math.max(Math.ceil(minLen) + 1, minLen ? 0 : 15)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [campaign?.id, campaignSounds.length]);

  const addVideos = async (files: File[]) => {
    for (const f of files) {
      setUploading(f.name);
      try {
        const p: Project = await api.uploadProject(f, undefined, undefined, campaignId || null);
        setSources((s) => [...s, p.id]);
        setAdded((a) => [...a, p.id]);
        await projects.reload();
      } catch (e) {
        toast(`${f.name}: ${(e as Error).message}`, "error");
      }
    }
    setUploading(null);
  };

  const addLinks = async () => {
    if (!linkList.length) return;
    setLinkBusy(true);
    try {
      const r = await api.addMontageLinks(linkList, campaignId);
      setSources((s) => [...s, ...r.created.map((p) => p.id).filter((id) => !s.includes(id))]);
      setAdded((a) => [...a, ...r.created.map((p) => p.id)]);
      await projects.reload();
      if (r.created.length) toast(`Added ${plural(r.created.length, "link")}. They download when you make the edit.`);
      for (const e of r.errors) toast(`${e.url}: ${e.error}`, "error");
      setLinks(r.errors.map((e) => e.url).join("\n"));
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setLinkBusy(false);
    }
  };

  const toDownload = usable.filter((p) => sources.includes(p.id) && p.video_mode === "missing").length;
  const songReady = songKind === "upload" ? !!songFile : !!(soundId || campaignSounds[0]);
  const canStep = [songReady, sources.length > 0, true];

  const start = async () => {
    setBusy(true);
    started.current = true;
    try {
      const song = songKind === "campaign" ? { kind: "campaign" as const, sound_id: soundId || campaignSounds[0]?.id, use_cut: useCut } : { kind: "upload" as const };
      const p = await api.createMontage({ name: name.trim() || "Music edit", campaign_id: campaignId || null, options: { ...opts, sources, song }, start: false });
      try {
        if (songKind === "upload" && songFile) await api.uploadSong(p.id, songFile);
        await api.process(p.id, {});
      } catch (e) {
        await api.deleteProject(p.id, { delete_exports: true, delete_source: false }).catch(() => undefined);
        throw e;
      }
      toast("Music edit started");
      nav(`/projects/${p.id}`);
    } catch (e) {
      started.current = false;
      toast((e as Error).message, "error");
      setBusy(false);
    }
  };

  const leaveModal = blocker.state === "blocked" && (
    <Modal
      title={`Keep the ${added.length === 1 ? "video" : `${added.length} videos`} you added?`}
      onClose={() => blocker.reset()}
      actions={
        <>
          <button className="btn" onClick={() => blocker.reset()} disabled={leaveBusy}>
            Stay
          </button>
          <button
            className="btn danger"
            disabled={leaveBusy}
            onClick={async () => {
              setLeaveBusy(true);
              for (const id of added) {
                try {
                  await api.deleteProject(id, { delete_exports: true, delete_source: true });
                } catch {
                  /* already gone */
                }
              }
              blocker.proceed();
            }}
          >
            {leaveBusy ? <Spinner /> : <Icon name="trash" />} Delete them
          </button>
          <button className="btn primary" onClick={() => blocker.proceed()} disabled={leaveBusy}>
            Keep for later
          </button>
        </>
      }
    >
      <p className="muted">You haven't made the music edit yet. Keep the videos for later (they wait under Videos → Not started), or delete them to free disk space.</p>
    </Modal>
  );

  return (
    <div className="stack lg" style={{ maxWidth: 860 }}>
      {leaveModal}
      <div className="page-head">
        <div>
          <h1>Music edit</h1>
          <p className="sub">Short shots from your videos, cut on the song's beat, with the strongest shot on the drop.</p>
        </div>
      </div>

      <ol className="stepper" aria-label="Steps">
        {STEPS.map((l, i) => (
          <li key={l} className={i === step ? "on" : i < step ? "done" : ""}>
            <button onClick={() => i <= step || canStep.slice(0, i).every(Boolean) ? setStep(i) : undefined} disabled={i > step && !canStep.slice(0, i).every(Boolean)} aria-current={i === step ? "step" : undefined}>
              <span className="n">{i < step ? <Icon name="check" size={12} /> : i + 1}</span> {l}
            </button>
          </li>
        ))}
      </ol>

      {step === 0 && (
        <section className="card">
          <div className="card-head">
            <h2>Which song?</h2>
          </div>
          <div className="card-body stack">
            <div className="grid cols-2">
              <div className="field">
                <label>Campaign</label>
                <CampaignSelect value={campaignId} onChange={setCampaignId} />
                <span className="hint">Optional. A music campaign brings its sound and rules.</span>
              </div>
              <div className="field">
                <label>Name</label>
                <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
              </div>
            </div>
            <div className="sound-cards" style={{ gridTemplateColumns: "1fr 1fr" }} role="radiogroup" aria-label="Song">
              <button type="button" role="radio" aria-checked={songKind === "campaign"} className={`sound-card ${songKind === "campaign" ? "on" : ""}`} onClick={() => setSongKind("campaign")} disabled={!campaignSounds.length}>
                <b>Use the campaign's sound</b>
                {campaignSounds.length ? `${plural(campaignSounds.length, "sound")} uploaded on the campaign.` : campaign ? "This campaign has no sound file. Upload it on the campaign's Requirements tab, or upload the song here." : "Pick a campaign first."}
              </button>
              <button type="button" role="radio" aria-checked={songKind === "upload"} className={`sound-card ${songKind === "upload" ? "on" : ""}`} onClick={() => setSongKind("upload")}>
                <b>Upload the song</b>
                MP3, M4A, WAV… Used for the beat. For campaigns where you add the official sound in the app, it's only used for timing.
              </button>
            </div>
            {songKind === "upload" ? (
              <div className="row">
                <button className={`btn ${songFile ? "" : "primary"}`} onClick={() => songInput.current?.click()}>
                  <Icon name="music" /> {songFile ? "Change song" : "Choose song file"}
                </button>
                {songFile && <span className="small">{songFile.name}</span>}
                <input ref={songInput} type="file" accept="audio/*" hidden onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (f) setSongFile(f);
                  }} />
              </div>
            ) : (
              campaignSounds.length > 0 && (
                <div className="stack sm">
                  <select className="select" value={soundId || campaignSounds[0]?.id || ""} onChange={(e) => setSoundId(e.target.value)} aria-label="Campaign sound">
                    {campaignSounds.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.name}
                        {s.start ? ` (cut from ${s.start}s)` : ""}
                      </option>
                    ))}
                  </select>
                  <label className="check small">
                    <input type="checkbox" checked={useCut} onChange={(e) => setUseCut(e.target.checked)} /> Start exactly where the brand's cut starts (otherwise ClipStudio picks the best part of the song)
                  </label>
                </div>
              )
            )}
          </div>
        </section>
      )}

      {step === 1 && (
        <section className="card">
          <div className="card-head">
            <h2>Videos to cut from</h2>
            <span className="small muted">{sources.length} selected</span>
          </div>
          <div className="card-body stack">
            <div className="row wrap">
              <button className="btn" onClick={() => videoInput.current?.click()} disabled={!!uploading}>
                {uploading ? <Spinner /> : <Icon name="upload" />} {uploading ? `Uploading ${uploading}…` : "Upload videos"}
              </button>
              <span className="small muted">Sports, movie or gameplay clips all work. Two or more give more variety.</span>
              <input ref={videoInput} type="file" accept="video/*" multiple hidden onChange={(e) => {
                const fs = e.target.files ? Array.from(e.target.files) : [];
                e.target.value = "";
                if (fs.length) addVideos(fs);
              }} />
            </div>
            <div className="field">
              <label>Or paste links (YouTube, Twitch, Instagram, TikTok…)</label>
              <textarea className="textarea" rows={2} value={links} placeholder="One link per line" onChange={(e) => setLinks(e.target.value)} />
              <div className="row">
                <button className="btn sm" onClick={addLinks} disabled={!linkList.length || linkBusy}>
                  {linkBusy ? <Spinner /> : <Icon name="link" />} {linkBusy ? "Checking links…" : linkList.length > 1 ? `Add ${linkList.length} links` : "Add link"}
                </button>
                <span className="hint">Downloaded in full when you make the edit (shots can come from anywhere in them).</span>
              </div>
            </div>
            {usable.length > 0 && (
              <div className="field">
                <span className="label-text">Your videos</span>
                <div className="stack sm" style={{ maxHeight: 280, overflowY: "auto" }}>
                  {usable.map((p) => (
                    <label key={p.id} className="check list-row">
                      <input type="checkbox" checked={sources.includes(p.id)} onChange={(e) => setSources((s) => (e.target.checked ? [...s, p.id] : s.filter((x) => x !== p.id)))} />
                      <span className="ellipsis" style={{ flex: 1 }}>
                        {p.name}
                      </span>
                      <span className="small muted nowrap">
                        {fmtDuration(p.duration)}
                        {p.video_mode === "sections" ? " · sections only" : p.video_mode === "missing" ? " · link, downloads on start" : ""}
                      </span>
                    </label>
                  ))}
                </div>
              </div>
            )}
            {toDownload > 0 && <Alert kind="info">{plural(toDownload, "linked video")} will be downloaded first (needs disk space for the full video).</Alert>}
          </div>
        </section>
      )}

      {step === 2 && (
        <section className="card">
          <div className="card-head">
            <h2>Style</h2>
          </div>
          <div className="card-body stack">
            <div className="sound-cards" role="radiogroup" aria-label="Cut speed">
              {STYLES.map((s) => (
                <button key={s.key} type="button" role="radio" aria-checked={opts.style === s.key} className={`sound-card ${opts.style === s.key ? "on" : ""}`} onClick={() => set("style", s.key)}>
                  <b>{s.label}</b>
                  {s.desc}
                </button>
              ))}
            </div>
            <div className="field">
              <label>Length: {opts.length}s{campaign && (minLen || campaign.brief.max_duration) ? " (from the campaign)" : ""}</label>
              <input type="range" min={Math.max(6, Math.ceil(minLen))} max={Math.max(Math.ceil(minLen) + 1, maxLen)} step={1} value={Math.max(opts.length, Math.ceil(minLen))} onChange={(e) => set("length", +e.target.value)} aria-label="Length in seconds" />
              {campaign && (minLen > 0 || campaign.brief.max_duration) ? <span className="hint">Set from the campaign ({minLen || "any"}–{campaign.brief.max_duration ?? "any"} s). Change it only if you want a different length.</span> : null}
            </div>
            <div className="field">
              <label>Text on screen (optional)</label>
              <input className="input" value={opts.pov} maxLength={200} placeholder="POV: you finally hit the shot you practised all week" onChange={(e) => set("pov", e.target.value)} />
              <span className="hint">Shown for the whole video, e.g. a “POV:” line.</span>
            </div>
            <details className="adv">
              <summary>More options: variants, the drop, audio</summary>
              <div className="stack" style={{ marginTop: 12 }}>
                <div className="grid cols-2">
                  <div className="field">
                    <label>Variants: {opts.variants}</label>
                    <input type="range" min={1} max={6} value={opts.variants} onChange={(e) => set("variants", +e.target.value)} />
                    <span className="hint">Different shots in each, so every post is unique.</span>
                  </div>
                  <div className="field">
                    <label>
                      The drop lands at {Math.round(opts.drop_at * 100)}% of the video <HelpTip topic="montage_drop" />
                    </label>
                    <input type="range" min={0.15} max={0.7} step={0.05} value={opts.drop_at} onChange={(e) => set("drop_at", +e.target.value)} />
                  </div>
                </div>
                <div className="field">
                  <span className="label-text">Audio in the video</span>
                  <div className="seg">
                    <button className={opts.audio === "song" ? "active" : ""} onClick={() => set("audio", "song")}>
                      The song
                    </button>
                    <button className={opts.audio === "silent" ? "active" : ""} onClick={() => set("audio", "silent")}>
                      Silent (I'll add the sound in the app)
                    </button>
                  </div>
                </div>
                <label className="check">
                  <input type="checkbox" checked={opts.flash} onChange={(e) => set("flash", e.target.checked)} /> White flash on the drop
                </label>
              </div>
            </details>
          </div>
        </section>
      )}

      <div className="row between">
        {step > 0 ? (
          <button className="btn ghost" onClick={() => setStep(step - 1)}>
            <Icon name="chevronLeft" /> Back
          </button>
        ) : (
          <span />
        )}
        {step < 2 ? (
          <button className="btn primary" onClick={() => setStep(step + 1)} disabled={!canStep[step]}>
            Next: {STEPS[step + 1]} <Icon name="chevronRight" />
          </button>
        ) : (
          <button className="btn primary lg" onClick={start} disabled={busy || !songReady || !sources.length}>
            {busy ? <Spinner /> : <Icon name="zap" />} Make {opts.variants > 1 ? `${opts.variants} variants` : "the edit"}
          </button>
        )}
      </div>
    </div>
  );
}
