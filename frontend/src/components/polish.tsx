import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../hooks";
import type { HookStyle, Polish, ProcessOptions, Recipe } from "../types";
import { Icon } from "./Icon";
import { Spinner, useDialogs, useToast } from "./ui";

export const DEFAULT_POLISH: Polish = {
  jumpcut: "light",
  fillers: true,
  zoom: "subtle",
  emphasis: true,
  emoji: false,
  progress: "off",
  hook_style: "box",
  cold_open: false,
  profanity: "off",
  denoise: "off",
};

export const HOOK_STYLES: { key: HookStyle; label: string; fg: string; bg: string; boxed: boolean }[] = [
  { key: "box", label: "White box", fg: "#111", bg: "#fff", boxed: true },
  { key: "dark", label: "Dark box", fg: "#fff", bg: "#111", boxed: true },
  { key: "yellow", label: "Yellow box", fg: "#111", bg: "#FFD300", boxed: true },
  { key: "banner", label: "Red banner", fg: "#fff", bg: "#E5242B", boxed: true },
  { key: "bold", label: "Bold outline", fg: "#FFE600", bg: "#000", boxed: false },
  { key: "outline", label: "Clean outline", fg: "#fff", bg: "#000", boxed: false },
];

/** Hook text templates: {title} is replaced with the clip's title. */
export const HOOK_TEMPLATES = [
  "Wait for it…",
  "Nobody expected this",
  "Watch till the end",
  "This changed everything",
  "POV: {title}",
  "I can't believe this happened",
  "The moment it all went wrong",
  "Don't make this mistake",
  "This is why {title}",
  "Here's the secret",
];

type Opt<T extends string> = { v: T; l: string };
const ROWS: { key: keyof Polish; label: string; hint: string; opts: Opt<string>[] }[] = [
  {
    key: "jumpcut",
    label: "Remove dead air",
    hint: "Cuts quiet pauses between sentences (jump cuts). Gameplay sound, music and reactions are never cut — only silence.",
    opts: [
      { v: "off", l: "Off" },
      { v: "light", l: "Light" },
      { v: "normal", l: "Normal" },
      { v: "tight", l: "Tight" },
    ],
  },
  {
    key: "zoom",
    label: "Punch-in zooms",
    hint: "Zooms in on strong words and every other jump cut (hides the cut). Not used on split/stream layouts.",
    opts: [
      { v: "off", l: "Off" },
      { v: "subtle", l: "Subtle" },
      { v: "punchy", l: "Punchy" },
    ],
  },
  {
    key: "progress",
    label: "Progress bar",
    hint: "A thin bar that fills as the video plays — keeps people watching to the end.",
    opts: [
      { v: "off", l: "Off" },
      { v: "top", l: "Top" },
      { v: "bottom", l: "Bottom" },
    ],
  },
  {
    key: "profanity",
    label: "Swear words",
    hint: "Beep or mute strong swear words and mask them in captions (f***). Useful for brand campaigns.",
    opts: [
      { v: "off", l: "Leave" },
      { v: "beep", l: "Beep" },
      { v: "mute", l: "Mute" },
    ],
  },
  {
    key: "denoise",
    label: "Speech denoise",
    hint: "Reduces steady background noise (fans, hum). Strong can sound slightly processed.",
    opts: [
      { v: "off", l: "Off" },
      { v: "light", l: "Light" },
      { v: "strong", l: "Strong" },
    ],
  },
];

export function PolishControls({ value, onChange, compact }: { value: Polish; onChange: (patch: Partial<Polish>) => void; compact?: boolean }) {
  return (
    <div className="stack">
      <div className={compact ? "stack" : "grid cols-2"}>
        {ROWS.map((r) => (
          <div className="field" key={r.key}>
            <label>{r.label}</label>
            <div className="seg">
              {r.opts.map((o) => (
                <button key={o.v} className={value[r.key] === o.v ? "active" : ""} onClick={() => onChange({ [r.key]: o.v } as Partial<Polish>)}>
                  {o.l}
                </button>
              ))}
            </div>
            <span className="hint">{r.hint}</span>
          </div>
        ))}
        <div className="field">
          <label>Hook title style</label>
          <HookStylePicker value={value.hook_style} onChange={(v) => onChange({ hook_style: v })} />
        </div>
      </div>
      <div className={compact ? "stack sm" : "grid cols-2"}>
        <label className="check">
          <input type="checkbox" checked={value.fillers} disabled={value.jumpcut === "off"} onChange={(e) => onChange({ fillers: e.target.checked })} />
          <span>
            Cut filler words (um, uh)
            <br />
            <span className="small muted">Only when dead-air removal is on.</span>
          </span>
        </label>
        <label className="check">
          <input type="checkbox" checked={value.emphasis} onChange={(e) => onChange({ emphasis: e.target.checked })} />
          <span>
            Keyword emphasis in captions
            <br />
            <span className="small muted">One strong word per caption gets a second colour and a slight size bump.</span>
          </span>
        </label>
        <label className="check">
          <input type="checkbox" checked={value.emoji} onChange={(e) => onChange({ emoji: e.target.checked })} />
          <span>
            Emoji on emphasised words
            <br />
            <span className="small muted">Up to 4 per clip (💰 🔥 😂 💀 …), above the captions.</span>
          </span>
        </label>
        <label className="check">
          <input type="checkbox" checked={value.cold_open} onChange={(e) => onChange({ cold_open: e.target.checked })} />
          <span>
            Cold open
            <br />
            <span className="small muted">Shows the strongest 2–3 seconds first as a teaser (with the hook title), then the full clip. Clips under 8 s are left as they are.</span>
          </span>
        </label>
      </div>
    </div>
  );
}

export function HookStylePicker({ value, onChange }: { value: HookStyle; onChange: (v: HookStyle) => void }) {
  return (
    <div className="hook-styles">
      {HOOK_STYLES.map((h) => (
        <button key={h.key} type="button" title={h.label} className={`hook-style ${value === h.key ? "active" : ""}`} onClick={() => onChange(h.key)}>
          <span
            className="hook-style-sample"
            style={h.boxed ? { background: h.bg, color: h.fg } : { color: h.fg, WebkitTextStroke: `1px ${h.bg}`, textShadow: `0 1px 0 ${h.bg}` }}
          >
            HOOK
          </span>
          <span className="small">{h.label}</span>
        </button>
      ))}
    </div>
  );
}

export function describePolish(p: Partial<Polish>): string {
  const bits: string[] = [];
  if (p.jumpcut && p.jumpcut !== "off") bits.push(`dead air: ${p.jumpcut}`);
  if (p.zoom && p.zoom !== "off") bits.push(`${p.zoom} zooms`);
  if (p.emphasis) bits.push("emphasis");
  if (p.emoji) bits.push("emoji");
  if (p.progress && p.progress !== "off") bits.push(`progress bar (${p.progress})`);
  if (p.cold_open) bits.push("cold open");
  if (p.profanity && p.profanity !== "off") bits.push(`${p.profanity} swears`);
  if (p.denoise && p.denoise !== "off") bits.push(`denoise: ${p.denoise}`);
  if (p.hook_style) bits.push(`hook: ${HOOK_STYLES.find((h) => h.key === p.hook_style)?.label ?? p.hook_style}`);
  return bits.join(" · ") || "no polish";
}

export function describeRecipe(r: Recipe): string {
  const bits: string[] = [];
  if (r.caption_preset) bits.push(`captions: ${r.caption_preset}`);
  if (r.layout) bits.push(`layout: ${r.layout}`);
  const p = describePolish(r.polish || {});
  return [...bits, p].join(" · ");
}

/** Project options from a recipe (New project). */
export function recipeToOptions(r: Recipe): ProcessOptions {
  const o: ProcessOptions = { polish: r.polish };
  if (r.caption_preset) o.caption_preset = r.caption_preset;
  if (r.caption_style) o.caption_style = r.caption_style;
  if (r.layout) o.layout = r.layout;
  if (r.aspect_ratio) o.aspect_ratio = r.aspect_ratio;
  if (r.output_height) o.output_height = r.output_height;
  if (r.music) o.music = r.music;
  if (r.sfx) o.sfx = r.sfx;
  if (r.blur_burned) o.blur_burned = r.blur_burned;
  if (r.hook_overlay != null) o.hook_overlay = r.hook_overlay;
  return o;
}

export function RecipeSelect({ value, onChange, placeholder = "No recipe" }: { value: string; onChange: (id: string, recipe: Recipe | null) => void; placeholder?: string }) {
  const q = usePoll(api.recipes, []);
  const list = q.data ?? [];
  return (
    <select
      className="select"
      value={value}
      onChange={(e) => {
        const r = list.find((x) => x.id === e.target.value) ?? null;
        onChange(e.target.value, r);
      }}
    >
      <option value="">{list.length ? placeholder : "No recipes yet"}</option>
      {list.map((r) => (
        <option key={r.id} value={r.id}>
          {r.name}
        </option>
      ))}
    </select>
  );
}

/** Settings page: list, rename and delete recipes; save the current defaults as a new recipe. */
export function RecipeManager({ defaults }: { defaults: { caption_preset: string; layout: string; polish: Polish } }) {
  const toast = useToast();
  const { confirm, prompt } = useDialogs();
  const q = usePoll(api.recipes, []);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const create = async () => {
    if (!name.trim()) return;
    setBusy(true);
    try {
      await api.createRecipe({ name: name.trim(), caption_preset: defaults.caption_preset, layout: defaults.layout as Recipe["layout"], polish: defaults.polish });
      setName("");
      await q.reload();
      toast("Recipe saved");
    } catch (e) {
      toast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  };
  const remove = async (r: Recipe) => {
    if (!(await confirm({ title: `Delete the recipe “${r.name}”?`, body: "Clips that used it keep their settings.", confirm: "Delete", danger: true }))) return;
    await api.deleteRecipe(r.id);
    await q.reload();
  };
  const rename = async (r: Recipe) => {
    const n = await prompt({ title: "Rename recipe", label: "Name", initial: r.name, confirm: "Rename" });
    if (!n || !n.trim() || n === r.name) return;
    const { id: _id, created_at: _c, updated_at: _u, ...body } = r;
    await api.updateRecipe(r.id, { ...body, name: n.trim() });
    await q.reload();
  };
  const list = q.data ?? [];
  return (
    <div className="stack">
      {list.length === 0 ? (
        <p className="small muted">No recipes yet. Save one here from the defaults above, or from any clip (Clip editor → Polish → “Save as recipe”).</p>
      ) : (
        <div className="stack sm">
          {list.map((r) => (
            <div key={r.id} className="list-row row" style={{ justifyContent: "space-between" }}>
              <span style={{ minWidth: 0 }}>
                <strong>{r.name}</strong>
                <br />
                <span className="small muted">{describeRecipe(r)}</span>
              </span>
              <span className="row nowrap">
                <button className="btn sm ghost" onClick={() => rename(r)}>
                  Rename
                </button>
                <button className="btn sm ghost" onClick={() => remove(r)} aria-label={`Delete recipe ${r.name}`}>
                  <Icon name="trash" />
                </button>
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="row">
        <input className="input" style={{ maxWidth: 280 }} placeholder="New recipe name, e.g. Podcast punchy" value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" onClick={create} disabled={!name.trim() || busy}>
          {busy ? <Spinner /> : <Icon name="plus" />} Save defaults as recipe
        </button>
      </div>
    </div>
  );
}
