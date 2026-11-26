import { useEffect, useState } from "react";
import { Link, useBlocker, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { DEFAULT_POLISH, PolishControls, RecipeManager } from "../components/polish";
import { Icon } from "../components/Icon";
import { Alert, Modal, Spinner, useToast } from "../components/ui";
import { fmtMB } from "../format";
import { usePoll } from "../hooks";
import type { Settings } from "../types";

const MODES = [
  { key: "low", title: "Low resource", badge: "Recommended for 8 GB", desc: "Speech model in 2-minute chunks, no local AI, 720p, one job at a time. Peak RAM about 3 GB." },
  { key: "balanced", title: "Balanced", badge: "", desc: "Larger transcription chunks (faster), optional local AI, sharper x264 encoding at 720p." },
  { key: "high", title: "High quality", badge: "Uses more RAM and disk", desc: "1080p output, up to 60 fps, slower high-quality encoding. Uses noticeably more memory and disk; close other apps first." },
] as const;

const CRITERIA: [string, string][] = [
  ["hook", "Hook strength"],
  ["context", "Context completeness"],
  ["emotion", "Emotional impact"],
  ["value", "Practical value"],
  ["story", "Storytelling"],
  ["visual", "Visual quality"],
  ["audio", "Audio quality"],
];

export default function SettingsPage() {
  const toast = useToast();
  const q = usePoll(api.settings, []);
  const models = usePoll(api.models, []).data;
  const [s, setS] = useState<Settings | null>(null);
  const [saving, setSaving] = useState(false);
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "advanced" ? "advanced" : "basic";

  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  const dirty = !!s && !!q.data && JSON.stringify(s) !== JSON.stringify(q.data);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
  if (!s) return <div className="skeleton" style={{ height: 300 }} />;

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  // send only what changed, so the server can apply a processing mode's own values (resolution, local AI)
  const changed = (): Partial<Settings> => {
    const out: Record<string, unknown> = {};
    const before = (q.data ?? {}) as unknown as Record<string, unknown>;
    for (const [k, v] of Object.entries(s)) if (JSON.stringify(v) !== JSON.stringify(before[k])) out[k] = v;
    return out as Partial<Settings>;
  };
  const save = async (patch: Partial<Settings> = changed()) => {
    setSaving(true);
    try {
      const n = await api.saveSettings(patch);
      q.setData(n);
      setS(n);
      toast("Settings saved");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setSaving(false);
    }
  };
  const ramGb = (models?.ram_total_mb ?? 8192) / 1024;
  const weightTotal = Object.values(s.weights).reduce((a, b) => a + b, 0);

  return (
    <div className="stack lg" style={{ maxWidth: 900 }}>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="sub">Everything runs on this Mac. Nothing is uploaded anywhere.</p>
        </div>
        <button className="btn primary" disabled={!dirty || saving} onClick={() => save()}>
          {saving ? <Spinner /> : <Icon name="check" />} Save changes
        </button>
      </div>
      {dirty && (
        <div className="save-bar" role="status">
          <span>
            <Icon name="edit" /> Unsaved changes
          </span>
          <span className="row">
            <button className="btn sm ghost" onClick={() => q.data && setS(q.data)}>
              Discard
            </button>
            <button className="btn sm primary" onClick={() => save()} disabled={saving}>
              {saving ? <Spinner /> : <Icon name="check" />} Save changes
            </button>
          </span>
        </div>
      )}
      <p className="small muted" style={{ margin: 0 }}>
        These are your defaults. A campaign can change them for its own videos (Change style, Requirements), and a video or clip can change them again.
      </p>
      {blocker.state === "blocked" && (
        <Modal
          title="Leave without saving?"
          onClose={() => blocker.reset()}
          actions={
            <>
              <button className="btn" onClick={() => blocker.reset()}>
                Stay
              </button>
              <button className="btn danger" onClick={() => blocker.proceed()}>
                Discard changes
              </button>
              <button className="btn primary" onClick={async () => (await save(), blocker.proceed())}>
                Save &amp; leave
              </button>
            </>
          }
        >
          <p className="muted">Your settings changes aren't saved yet.</p>
        </Modal>
      )}

      <div className="tabs" role="tablist" aria-label="Settings">
        {(
          [
            ["basic", "Basics"],
            ["advanced", "Advanced"],
          ] as const
        ).map(([k, l]) => (
          <button key={k} role="tab" aria-selected={view === k} className={view === k ? "active" : ""} onClick={() => setParams(k === "basic" ? {} : { view: k }, { replace: true })}>
            {l}
          </button>
        ))}
      </div>
      {view === "advanced" && <p className="small muted" style={{ marginTop: -8 }}>Fine-tuning for models, downloads, framing, detection and scoring. The defaults work well; change these only if you have a reason.</p>}

      {view === "basic" && (
      <section className="card">
        <div className="card-head">
          <h2>Processing mode</h2>
          <span className="small muted">{ramGb.toFixed(0)} GB RAM detected</span>
        </div>
        <div className="card-body stack">
          {MODES.map((m) => (
            <label key={m.key} className={`hook-option ${s.mode === m.key ? "active" : ""}`}>
              <input type="radio" name="mode" checked={s.mode === m.key} onChange={() => set("mode", m.key)} />
              <span className="stack sm" style={{ gap: 2 }}>
                <span className="row">
                  <strong>{m.title}</strong>
                  {m.badge && <span className={`badge ${m.key === "high" ? "warn" : "success"}`}>{m.badge}</span>}
                </span>
                <span className="small muted">{m.desc}</span>
              </span>
            </label>
          ))}
          {s.mode === "high" && ramGb <= 8 && <Alert kind="warn">High quality mode on an 8 GB Mac may slow things down. Close browsers and other heavy apps while processing.</Alert>}
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Models</h2>
        </div>
        <div className="card-body stack lg">
          <div className="field">
            <label>Transcription model</label>
            <select className="select" value={s.transcription_model} onChange={(e) => set("transcription_model", e.target.value)}>
              {models?.transcription.map((m) => (
                <option key={m.id} value={m.id} disabled={!m.installed}>
                  {m.label} · {m.disk_gb} GB disk · ~{m.ram_gb} GB RAM {m.installed ? "✓ installed" : "(not installed)"}
                </option>
              ))}
            </select>
            <span className="hint">Models are never downloaded automatically. See README → Model setup to add one (shows the size first).</span>
          </div>
          <div className="field">
            <label>Transcription chunk length: {s.transcription_chunk_seconds}s</label>
            <input type="range" min={30} max={240} step={30} value={s.transcription_chunk_seconds} onChange={(e) => set("transcription_chunk_seconds", +e.target.value)} />
            <span className="hint">Shorter chunks use less RAM. Lower this if transcription runs out of memory.</span>
          </div>
          <div className="divider" />
          <div className="stack sm">
            <label className="check">
              <input type="checkbox" checked={s.llm_enabled} onChange={(e) => set("llm_enabled", e.target.checked)} />
              <span>
                <strong>Optional local AI (Ollama)</strong> for titles, hooks and a second opinion on ranking
                <br />
                <span className="small muted">
                  Status:{" "}
                  {models?.llm.available ? (
                    <span style={{ color: "var(--success)" }}>available</span>
                  ) : (
                    <span>{models?.llm.status ?? "checking…"}</span>
                  )}
                  . Without it, built-in rules are used and everything still works.
                </span>
              </span>
            </label>
            <div className="grid cols-2">
              <div className="field">
                <label>Ollama URL (must be localhost)</label>
                <input className="input mono" value={s.llm_url} onChange={(e) => set("llm_url", e.target.value)} />
              </div>
              <div className="field">
                <label>Model</label>
                <input className="input mono" value={s.llm_model} onChange={(e) => set("llm_model", e.target.value)} />
                <span className="hint">qwen2.5:1.5b is ~1 GB on disk and ~1.5 GB RAM. It is unloaded after each request.</span>
              </div>
            </div>
          </div>
        </div>
      </section>
      )}

      {view === "basic" && (
      <section className="card">
        <div className="card-head">
          <h2>Clip defaults</h2>
        </div>
        <div className="card-body grid cols-2">
          <div className="field">
            <label>Clip length</label>
            <select className="select" value={s.duration_preset} onChange={(e) => set("duration_preset", e.target.value)}>
              {["15-30", "30-45", "30-60", "45-60", "60-90"].map((d) => (
                <option key={d} value={d}>
                  {d.replace("-", "–")} seconds
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label>Maximum clips per video: {s.max_clips}</label>
            <input type="range" min={1} max={30} value={s.max_clips} onChange={(e) => set("max_clips", +e.target.value)} />
          </div>
          <div className="field">
            <label>Minimum score to include: {s.min_score}</label>
            <input type="range" min={0} max={80} value={s.min_score} onChange={(e) => set("min_score", +e.target.value)} />
            <span className="hint">Clips scoring below this are left out instead of padding the list.</span>
          </div>
          <div className="field">
            <label>Output resolution</label>
            <select className="select" value={s.output_height} onChange={(e) => set("output_height", +e.target.value as 720 | 1080)}>
              <option value={720}>720p (recommended)</option>
              <option value={1080}>1080p</option>
            </select>
          </div>
          <div className="field">
            <label>Max download quality (video links)</label>
            <select className="select" value={s.max_download_height} onChange={(e) => set("max_download_height", +e.target.value as 720 | 1080)}>
              <option value={720}>720p (smaller downloads)</option>
              <option value={1080}>1080p</option>
            </select>
          </div>
          <label className="check">
            <input type="checkbox" checked={s.normalise_audio} onChange={(e) => set("normalise_audio", e.target.checked)} />
            <span>Normalise loudness (−14 LUFS, the usual level for social video)</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.auto_render} onChange={(e) => set("auto_render", e.target.checked)} />
            <span>Render clips automatically after analysis</span>
          </label>
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Downloads (video links)</h2>
        </div>
        <div className="card-body stack">
          {(
            [
              ["smart", "Smart: audio first, then only the parts clips need", "Downloads the audio (~1 MB/min), finds clips, then fetches just those sections of video (each clip ± 20 s for trimming). Uses a fraction of the disk. Falls back to the full video automatically if a site doesn't support it."],
              ["full", "Full video", "Downloads the whole video up front. Uses more disk, but you can add clips anywhere later without another download."],
            ] as const
          ).map(([k, l, d]) => (
            <label key={k} className={`hook-option ${s.download_strategy === k ? "active" : ""}`}>
              <input type="radio" name="dl" checked={s.download_strategy === k} onChange={() => set("download_strategy", k)} />
              <span>
                <strong>{l}</strong>
                <br />
                <span className="small muted">{d}</span>
              </span>
            </label>
          ))}
          <label className="check">
            <input type="checkbox" checked={s.use_heatmap} onChange={(e) => set("use_heatmap", e.target.checked)} />
            <span>
              Use YouTube's <strong>“Most replayed”</strong> graph when ranking clips
              <br />
              <span className="small muted">Real viewer behaviour; adds up to +10 points to parts people re-watch. Only some videos have it.</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.use_creator_subtitles} onChange={(e) => set("use_creator_subtitles", e.target.checked)} />
            <span>
              Use the uploader's own English subtitles instead of transcribing
              <br />
              <span className="small muted">Much faster and no speech model, but word timing is estimated (captions a bit less tight). Auto-generated subtitles are never used.</span>
            </span>
          </label>
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Framing (Auto)</h2>
        </div>
        <div className="card-body stack">
          <span className="label-text">When there's no big face to follow</span>
          <div className="seg">
            <button className={s.auto_fallback === "fill" ? "active" : ""} onClick={() => set("auto_fallback", "fill")}>
              Fill the frame (crop)
            </button>
            <button className={s.auto_fallback === "blur" ? "active" : ""} onClick={() => set("auto_fallback", "blur")}>
              Whole frame + blurred bars
            </button>
          </div>
          <label className="check">
            <input type="checkbox" checked={s.follow_action} onChange={(e) => set("follow_action", e.target.checked)} />
            <span>
              <strong>Follow the action</strong> in fill crops
              <br />
              <span className="small muted">The crop drifts slowly towards where the motion is (no faces needed), instead of staying in the centre.</span>
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.auto_stream} onChange={(e) => set("auto_stream", e.target.checked)} />
            <span>
              Detect a streamer's webcam and use <strong>webcam + gameplay</strong> stacking
              <br />
              <span className="small muted">A small face that stays in a corner is treated as a webcam overlay. Mark it exactly per project with Framing.</span>
            </span>
          </label>
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Gameplay &amp; action detection</h2>
        </div>
        <div className="card-body stack">
          <div className="field">
            <span className="label-text">Default content type</span>
            <div className="seg">
              {(
                [
                  ["auto", "Auto"],
                  ["talk", "Talk"],
                  ["gameplay", "Gameplay / action"],
                ] as const
              ).map(([k, l]) => (
                <button key={k} className={s.content_type === k ? "active" : ""} onClick={() => set("content_type", k)}>
                  {l}
                </button>
              ))}
            </div>
            <span className="hint">Auto treats videos with fewer than ~110 spoken words per minute as gameplay.</span>
          </div>
          <span className="label-text">What counts as an action moment</span>
          {(
            [
              ["audio", "Game audio spikes (gunfire, explosions, impacts)"],
              ["motion", "Fast on-screen action"],
              ["reaction", "Streamer reactions (shouting, laughing, “let's go”)"],
              ["hud", "Kill feed / HUD flashes (top-right corner)"],
            ] as const
          ).map(([k, l]) => (
            <div className="crit" key={k} style={{ gridTemplateColumns: "260px 1fr 40px" }}>
              <span>{l}</span>
              <input type="range" min={0} max={60} value={s.action_weights[k]} onChange={(e) => set("action_weights", { ...s.action_weights, [k]: +e.target.value })} />
              <span className="small" style={{ textAlign: "right" }}>{s.action_weights[k]}</span>
            </div>
          ))}
          <p className="small muted">Tune these after testing on your own streams. For example, raise “reactions” if your best clips are where you shout.</p>
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Finishing defaults</h2>
          <span className="small muted">For new projects; each clip can override. Campaigns can forbid music and effects.</span>
        </div>
        <div className="card-body grid cols-2">
          <label className="check">
            <input type="checkbox" checked={s.hook_overlay_default} onChange={(e) => set("hook_overlay_default", e.target.checked)} />
            <span>Hook title card at the start</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.sfx_default} onChange={(e) => set("sfx_default", e.target.checked)} />
            <span>Automatic sound effects</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.music_default === "auto"} onChange={(e) => set("music_default", e.target.checked ? "auto" : "none")} />
            <span>
              Background music from the Library (ducked under speech)
            </span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.blur_burned_default === "auto"} onChange={(e) => set("blur_burned_default", e.target.checked ? "auto" : "off")} />
            <span>Blur the source's burned-in captions</span>
          </label>
          <label className="check">
            <input type="checkbox" checked={s.voice_enhance} onChange={(e) => set("voice_enhance", e.target.checked)} />
            <span>
              Voice clean-up
              <br />
              <span className="small muted">Removes low rumble and evens out loud/quiet speech.</span>
            </span>
          </label>
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Editing details (defaults)</h2>
          <span className="small muted">Defaults for new projects. Each project, clip or recipe can change them.</span>
        </div>
        <div className="card-body">
          <PolishControls value={{ ...DEFAULT_POLISH, ...s.polish }} onChange={(p) => set("polish", { ...DEFAULT_POLISH, ...s.polish, ...p })} />
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Recipes</h2>
          <span className="small muted">Saved looks: caption style, framing, finishing and polish — applied in one click.</span>
        </div>
        <div className="card-body">
          <RecipeManager defaults={{ caption_preset: s.caption_preset, layout: s.layout, polish: { ...DEFAULT_POLISH, ...s.polish } }} />
        </div>
      </section>
      )}

      {view === "advanced" && (
      <section className="card">
        <div className="card-head">
          <h2>Scoring weights</h2>
          <span className={`small ${Math.round(weightTotal) === 100 ? "muted" : ""}`} style={Math.round(weightTotal) !== 100 ? { color: "var(--warn)" } : undefined}>
            Total {Math.round(weightTotal)}% (normalised automatically)
          </span>
        </div>
        <div className="card-body stack">
          {CRITERIA.map(([k, l]) => (
            <div className="crit" key={k} style={{ gridTemplateColumns: "170px 1fr 40px" }}>
              <span>{l}</span>
              <input type="range" min={0} max={50} value={s.weights[k]} onChange={(e) => set("weights", { ...s.weights, [k]: +e.target.value })} />
              <span className="small" style={{ textAlign: "right" }}>{s.weights[k]}</span>
            </div>
          ))}
          <p className="small muted">Scores are rule-based estimates for ranking clips. They are not predictions of views. Weights apply to new analyses.</p>
        </div>
      </section>
      )}

      {view === "basic" && (
      <section className="card">
        <div className="card-head">
          <h2>Storage</h2>
          <Link className="btn sm" to="/storage">
            <Icon name="disk" /> See what's using space
          </Link>
        </div>
        <div className="card-body stack lg">
          <div className="field">
            <label>Working directory</label>
            <input className="input mono" value={s.work_dir} placeholder="(default: the data/ folder inside the app)" onChange={(e) => set("work_dir", e.target.value)} />
            <span className="hint">Where downloads, uploads, transcripts and rendered clips go. Point this at an external SSD (e.g. /Volumes/MySSD/ClipStudio) to save internal disk space. Existing projects stay where they are.</span>
          </div>
          <div className="grid cols-2">
            <div className="field">
              <label>Always keep at least {s.min_free_gb} GB free</label>
              <input type="range" min={0.5} max={10} step={0.5} value={s.min_free_gb} onChange={(e) => set("min_free_gb", +e.target.value)} />
              <span className="hint">Jobs refuse to start if they'd leave less than this ({fmtMB(s.min_free_gb * 1024)}).</span>
            </div>
            <label className="check">
              <input type="checkbox" checked={s.keep_intermediates} onChange={(e) => set("keep_intermediates", e.target.checked)} />
              <span>
                Keep intermediate files
                <br />
                <span className="small muted">Extracted audio (~115 MB/hour). Off: deleted right after transcription.</span>
              </span>
            </label>
          </div>
        </div>
      </section>
      )}
    </div>
  );
}
