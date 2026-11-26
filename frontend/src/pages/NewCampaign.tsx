import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { HelpTip } from "../components/help";
import { Icon, type IconName } from "../components/Icon";
import { Alert, Spinner, useToast } from "../components/ui";
import type { CampaignType } from "../types";

export const TYPE_CARDS: { key: CampaignType; label: string; icon: IconName; desc: string; bullets: string[] }[] = [
  { key: "clipping", label: "Clips", icon: "scissors", desc: "Cut the best moments from long videos (streams, podcasts, gameplay).", bullets: ["Captions + title at the start", "Finds highlights for you"] },
  { key: "logo", label: "Logo", icon: "image", desc: "Clips with the brand's logo on screen the whole time.", bullets: ["Everything in Clips", "Logo placed where the brand wants it"] },
  { key: "music", label: "Music", icon: "music", desc: "Short edits cut to a song's beat, for promoting a track.", bullets: ["Beat-synced montage", "No captions, the song stays loud"] },
];

/**
 * Create a campaign in one step: type + pasted brief → the campaign page. What was read from the brief is
 * shown there (Overview), next to the settings it filled, so it can be checked and fixed in one place.
 */
export default function NewCampaign() {
  const nav = useNavigate();
  const toast = useToast();
  const [type, setType] = useState<CampaignType>("clipping");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const create = async (withBrief: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const label = TYPE_CARDS.find((x) => x.key === type)?.label ?? "New";
      const c = await api.createCampaign({ type, text: withBrief ? text.trim() : undefined, name: withBrief ? undefined : `${label} campaign` });
      toast(withBrief ? "Campaign created from the brief. Check what was read." : "Campaign created");
      nav(`/campaigns/${c.id}${withBrief ? "" : "?tab=requirements"}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="stack lg" style={{ maxWidth: 940 }}>
      <div className="page-head">
        <div>
          <h1>New campaign</h1>
          <p className="sub">Pick the type and paste the brief. Everything can be changed afterwards on the campaign page.</p>
        </div>
      </div>
      <div className="stack">
        <h2 className="step-title">
          1. What kind of campaign is it? <HelpTip topic="campaign_type" />
        </h2>
        <div className="type-cards" role="radiogroup" aria-label="Campaign type">
          {TYPE_CARDS.map((t) => (
            <button key={t.key} type="button" role="radio" aria-checked={type === t.key} className={`type-card ${type === t.key ? "on" : ""}`} onClick={() => setType(t.key)}>
              <span className="ico">
                <Icon name={t.icon} size={20} />
              </span>
              <strong className="tc-title">{t.label}</strong>
              <span className="small muted">{t.desc}</span>
              <ul>
                {t.bullets.map((b) => (
                  <li key={b}>{b}</li>
                ))}
              </ul>
            </button>
          ))}
        </div>
        <span className="small muted">UGC (your own on-camera videos) comes later.</span>
      </div>
      <div className="stack">
        <h2 className="step-title">2. Paste the brief</h2>
        <textarea
          className="textarea"
          rows={9}
          value={text}
          onChange={(e) => setText(e.target.value)}
          aria-label="Campaign brief"
          placeholder={"Copy everything from the campaign page: pay, length, logo and sound rules, what to put in the caption, do's and don'ts…"}
        />
        <span className="small muted">Read on this Mac with simple rules; nothing is sent anywhere. On the next page you'll see exactly what was read, with the line it came from, so you can check it.</span>
      </div>
      {error && <Alert kind="danger">{error}</Alert>}
      <div className="row between">
        <button className="link-btn" onClick={() => create(false)} disabled={busy}>
          No brief? Create an empty campaign and fill it in yourself
        </button>
        <button className="btn primary lg" onClick={() => create(true)} disabled={busy || !text.trim()}>
          {busy ? <Spinner /> : <Icon name="sparkles" />} Create campaign from the brief
        </button>
      </div>
    </div>
  );
}
