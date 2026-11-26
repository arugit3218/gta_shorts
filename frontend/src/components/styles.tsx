/**
 * Style templates (R2): one visual choice instead of ~20 settings. A style bundles the caption look,
 * title text, sound effects and framing; "editing intensity" bundles the retention-polish switches.
 * Everything a style sets can still be changed under "Customize" (New project) or per clip (editor).
 */
import type { Polish, ProcessOptions } from "../types";
import { HelpTip } from "./help";

export type StyleKey = "talking" | "gaming" | "hype" | "clean";
export type Intensity = "clean" | "balanced" | "punchy";

export interface StyleTemplate {
  key: StyleKey;
  label: string;
  desc: string;
  bullets: string[];
  caption_preset: string;
  hook: boolean;
  sfx: "auto" | "off";
  intensity: Intensity;
  emoji: boolean;
  /** how the caption sample on the tile looks */
  sample: { text: [string, string]; font: string; color: string; hl: string; upper: boolean; box?: boolean };
}

export const STYLES: StyleTemplate[] = [
  {
    key: "talking",
    label: "Talking / Podcast",
    desc: "For podcasts, interviews and commentary.",
    bullets: ["Big captions, key words highlighted", "Title text at the start", "Pauses removed"],
    caption_preset: "hormozi",
    hook: true,
    sfx: "off",
    intensity: "balanced",
    emoji: false,
    sample: { text: ["THIS CHANGED", "EVERYTHING"], font: "'Montserrat', 'Arial Black', sans-serif", color: "#fff", hl: "#FFD300", upper: true },
  },
  {
    key: "gaming",
    label: "Gaming / Stream",
    desc: "For streams, gameplay and reactions.",
    bullets: ["Bold animated captions", "Webcam + gameplay framed automatically", "Punchy cuts and zooms"],
    caption_preset: "bold",
    hook: true,
    sfx: "auto",
    intensity: "punchy",
    emoji: false,
    sample: { text: ["HE HIT THE", "500X"], font: "'Montserrat', 'Arial Black', sans-serif", color: "#fff", hl: "#FFE600", upper: true },
  },
  {
    key: "hype",
    label: "Hype",
    desc: "Maximum energy for funny or wild moments.",
    bullets: ["Comic-style captions", "Emoji on key words", "Zooms and sound effects"],
    caption_preset: "beast",
    hook: true,
    sfx: "auto",
    intensity: "punchy",
    emoji: true,
    sample: { text: ["NO", "WAY 😂"], font: "'Bangers', 'Impact', sans-serif", color: "#fff", hl: "#FFE600", upper: true },
  },
  {
    key: "clean",
    label: "Clean",
    desc: "Calm and simple, for brands that want it subtle.",
    bullets: ["Simple boxed captions", "No title text or effects", "Light editing"],
    caption_preset: "minimal",
    hook: false,
    sfx: "off",
    intensity: "clean",
    emoji: false,
    sample: { text: ["Here's what", "happened next"], font: "'Helvetica Neue', Arial, sans-serif", color: "#fff", hl: "#fff", upper: false, box: true },
  },
];

export const INTENSITY: { key: Intensity; label: string; desc: string; polish: Partial<Polish> }[] = [
  { key: "clean", label: "Clean", desc: "Only long silences are cut. No zooms.", polish: { jumpcut: "light", fillers: false, zoom: "off", emphasis: false, progress: "off" } },
  { key: "balanced", label: "Balanced", desc: "Pauses and “um”s cut, gentle zooms, key words highlighted.", polish: { jumpcut: "normal", fillers: true, zoom: "subtle", emphasis: true, progress: "off" } },
  { key: "punchy", label: "Punchy", desc: "Tight cuts, strong zooms, progress bar. Keeps people watching.", polish: { jumpcut: "tight", fillers: true, zoom: "punchy", emphasis: true, progress: "bottom" } },
];

export const styleOf = (key?: string | null) => STYLES.find((s) => s.key === key) ?? STYLES[1];

/** Project options for a style + intensity (merged over the user's defaults by the caller). */
export function styleOptions(key: string | null | undefined, intensity?: Intensity | "" | null): ProcessOptions {
  const s = styleOf(key);
  const i = INTENSITY.find((x) => x.key === (intensity || s.intensity)) ?? INTENSITY[1];
  return {
    caption_preset: s.caption_preset,
    hook_overlay: s.hook,
    sfx: s.sfx,
    // only what the style/intensity decides; the backend fills every other editing switch from Settings
    polish: { ...i.polish, emoji: s.emoji },
  };
}

/** Style options plus a campaign's own framing / caption-look choices. */
export function campaignOptions(b: { style: string; intensity: string; layout: string; caption_preset: string }): ProcessOptions {
  const o = styleOptions(b.style || "gaming", (b.intensity || null) as Intensity | null);
  if (b.layout) o.layout = b.layout as ProcessOptions["layout"];
  if (b.caption_preset) o.caption_preset = b.caption_preset;
  return o;
}

export function describeStyle(key?: string | null, intensity?: string | null): string {
  const s = styleOf(key);
  const i = INTENSITY.find((x) => x.key === (intensity || s.intensity));
  return `${s.bullets.join(" · ")}${i ? ` · ${i.label.toLowerCase()} editing` : ""}`;
}

export function MiniPhone({ s, small }: { s: StyleTemplate; small?: boolean }) {
  const t = s.sample;
  return (
    <div className={`mini-phone ${small ? "sm" : ""}`} aria-hidden="true">
      {s.hook && <div className="mp-hook">Nobody expected this</div>}
      <div className={`mp-cap ${t.box ? "box" : ""}`} style={{ fontFamily: t.font, color: t.color }}>
        {t.text[0]} <i style={{ color: t.hl }}>{t.text[1]}</i>
      </div>
    </div>
  );
}

export function StylePicker({ value, onChange }: { value: string; onChange: (k: StyleKey) => void }) {
  return (
    <div className="style-grid" role="radiogroup" aria-label="Style">
      {STYLES.map((s) => (
        <button key={s.key} type="button" role="radio" aria-checked={value === s.key} className={`style-tile ${value === s.key ? "on" : ""}`} onClick={() => onChange(s.key)}>
          <MiniPhone s={s} />
          <span className="st-body">
            <strong>{s.label}</strong>
            <span className="small muted">{s.desc}</span>
            <ul>
              {s.bullets.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </span>
        </button>
      ))}
    </div>
  );
}

export function IntensityPicker({ value, onChange }: { value: Intensity | ""; onChange: (v: Intensity) => void }) {
  const cur = INTENSITY.find((i) => i.key === value);
  return (
    <div className="field">
      <label>
        Editing intensity <HelpTip topic="intensity" />
      </label>
      <div className="seg" role="radiogroup" aria-label="Editing intensity">
        {INTENSITY.map((i) => (
          <button key={i.key} type="button" role="radio" aria-checked={value === i.key} className={value === i.key ? "active" : ""} onClick={() => onChange(i.key)}>
            {i.label}
          </button>
        ))}
      </div>
      <span className="hint">{cur ? cur.desc : "Using your editing defaults from Settings. Pick one to change them."}</span>
    </div>
  );
}
