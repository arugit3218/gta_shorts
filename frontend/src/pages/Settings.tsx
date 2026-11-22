import { useEffect, useState } from "react";
import { api } from "../api";
import { Icon } from "../components/Icon";
import { Alert, Spinner, useToast } from "../components/ui";
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
  useEffect(() => {
    if (q.data) setS(q.data);
  }, [q.data]);
  if (!s) return <div className="skeleton" style={{ height: 300 }} />;

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setS({ ...s, [k]: v });
  const dirty = JSON.stringify(s) !== JSON.stringify(q.data);
  const save = async (patch: Partial<Settings> = s) => {
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

      <section className="card">
        <div className="card-head">
          <h2>Storage</h2>
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
    </div>
  );
}
