import { useEffect, useRef, useState } from "react";

/** One-paragraph, plain-language explanations shown by the "?" buttons next to settings (R4). */
export const HELP: Record<string, { title: string; body: string }> = {
  campaign_type: {
    title: "Campaign type",
    body: "Decides the sensible defaults. Clips: cut highlights from long videos. Logo: the same, with the brand's logo on screen. Music: short edits cut to a song's beat. You can change any requirement afterwards.",
  },
  safe_zones: {
    title: "Areas covered by app buttons",
    body: "Instagram and YouTube draw their own buttons, username and caption over your video. Anything in the red areas may be hidden, so brands don't want their logo there. Light red: partly covered. Dark red: always covered, never place the logo here.",
  },
  logo_mode: {
    title: "Logo",
    body: "“Brand's template file” is a full-screen transparent image (often called LOGO_PRE_PLACED) that goes over the whole video exactly as supplied: it can't be moved. “Place a logo image” is a normal logo you drag onto the frame.",
  },
  clip_length: {
    title: "Clip length",
    body: "Clips are found and cut to fit this range. Dead-air cuts make clips a little shorter, so ClipStudio aims slightly above the minimum. The check before posting confirms the final length.",
  },
  sound: {
    title: "Campaign sound",
    body: "Music campaigns usually want their song on the post. “Add the sound when posting”: you pick the official sound inside Instagram/YouTube (apps don't allow adding it by code); the checklist reminds you and says where to start it. “Put the sound in the video”: you upload the song and it's mixed into every clip.",
  },
  extras: {
    title: "Background music and sound effects",
    body: "ClipStudio can add quiet background music and small effects (whoosh, pop) to make clips livelier. Many brands forbid this, e.g. music campaigns where only their song may play. “No” turns them off for every clip in the campaign.",
  },
  hook_title: {
    title: "Title text at the start",
    body: "A short line like “Nobody expected this” shown at the top for the first few seconds. It tells viewers why to keep watching and usually raises watch time. It's written for each clip and editable in the clip editor.",
  },
  platforms: {
    title: "Where you'll post",
    body: "Used to keep the logo and captions out of each app's buttons, and to keep hashtags within each app's limit (Instagram allows 5).",
  },
  required_text: {
    title: "Required caption text",
    body: "Some brands (often betting or finance) require a line in every post caption, word for word. It's added to every caption automatically, and the check before posting fails if it's missing or changed.",
  },
  checklist: {
    title: "Before you post",
    body: "Everything the brand requires, in one list. ClipStudio checks the first group itself on every clip. The second group are things only you can do (like keeping the post live), shown on each clip's Post screen so nothing is forgotten.",
  },
  reference: {
    title: "For your reference",
    body: "Pay and targets don't change how videos are made. They're kept here so you can see them later, and will be used for earnings tracking.",
  },
  ai_notes: {
    title: "Notes for AI picks",
    body: "Used only when you ask an AI chat (ChatGPT, Claude…) to pick clips with the “AI picks” button on a video. Describe what the brand likes, e.g. “big-win reactions, no losses”.",
  },
  style: {
    title: "Style",
    body: "How clips look: caption design, title text, sound effects and framing. Pick the closest one; you can still change anything per clip in the editor.",
  },
  intensity: {
    title: "Editing intensity",
    body: "How much ClipStudio edits each clip to keep people watching: cutting silences and “um”s, zooming in on key moments, highlighting key words, a progress bar. Clean = light touch, Punchy = fast and energetic.",
  },
  clips_per_video: {
    title: "Clips per video",
    body: "The most clips to make from each video. Only moments that score well are kept, so you may get fewer.",
  },
  quick_clip: {
    title: "Quick clip",
    body: "For a video that's already short, or when you know exactly where the clip is: skips finding highlights and uses your start/end (or the whole file). Still gets captions, logo, checks and export.",
  },
  montage_drop: {
    title: "The drop",
    body: "The moment in a song where the beat hits hardest. Montages put their strongest shot exactly on it, and cut faster after it.",
  },
};

export function HelpTip({ topic, label }: { topic: keyof typeof HELP | string; label?: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const h = HELP[topic];
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !ref.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);
  if (!h) return null;
  return (
    <span className="help-tip" ref={ref}>
      <button
        type="button"
        className={label ? "help-link" : "help-q"}
        aria-label={label ? undefined : `What is “${h.title}”?`}
        aria-expanded={open}
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setOpen((o) => !o);
        }}
      >
        {label ?? "?"}
      </button>
      {open && (
        <span className="help-pop" role="dialog" aria-label={h.title}>
          <strong>{h.title}</strong>
          <span>{h.body}</span>
        </span>
      )}
    </span>
  );
}
