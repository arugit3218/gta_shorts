import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { TYPE_LABEL } from "../components/campaign";
import { Icon } from "../components/Icon";
import { Empty, Spinner, plural, useDialogs, useToast } from "../components/ui";
import { usePoll } from "../hooks";
import type { Campaign } from "../types";

export default function Campaigns() {
  const toast = useToast();
  const { confirm } = useDialogs();
  const [archived, setArchived] = useState(false);
  const q = usePoll(() => api.campaigns(archived), [archived]);
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);
  const list = q.data ?? [];
  const sel = list.filter((c) => picked.has(c.id));
  const toggle = (id: string) =>
    setPicked((x) => {
      const n = new Set(x);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  const setStatus = async (items: Campaign[], status: "active" | "archived") => {
    setBusy(true);
    for (const c of items) {
      try {
        await api.patchCampaign(c.id, { status });
      } catch (e) {
        toast(`${c.name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(false);
    setPicked(new Set());
    toast(status === "archived" ? `Archived ${plural(items.length, "campaign")}. Tick “Show archived” to see them.` : `Restored ${plural(items.length, "campaign")}`);
    q.reload();
  };
  const remove = async (items: Campaign[]) => {
    const ok = await confirm({
      title: items.length === 1 ? `Delete “${items[0].name}”?` : `Delete ${items.length} campaigns?`,
      body: "Removes the campaign, its logo and sound files. Its videos, clips and exported folders are kept (they just won't belong to a campaign). Archive instead if you may need it again.",
      confirm: items.length === 1 ? "Delete campaign" : "Delete campaigns",
      danger: true,
    });
    if (!ok) return;
    setBusy(true);
    for (const c of items) {
      try {
        await api.deleteCampaign(c.id);
      } catch (e) {
        toast(`${c.name}: ${(e as Error).message}`, "error");
      }
    }
    setBusy(false);
    setPicked(new Set());
    toast(`Deleted ${plural(items.length, "campaign")}`);
    q.reload();
  };

  return (
    <div className="stack lg" style={{ maxWidth: 1100 }}>
      <div className="page-head">
        <div>
          <h1>Campaigns</h1>
          <p className="sub">A campaign holds what a brand requires (logo, sound, caption, length). Videos you add to it are made to match, and checked before you post.</p>
        </div>
        <div className="row">
          <label className="check small">
            <input type="checkbox" checked={archived} onChange={(e) => (setArchived(e.target.checked), setPicked(new Set()))} /> Show archived
          </label>
          <Link className="btn primary" to="/campaigns/new">
            <Icon name="plus" /> New campaign
          </Link>
        </div>
      </div>

      {sel.length > 0 && (
        <div className="select-bar active">
          <strong>{sel.length} selected</strong>
          <span className="spacer" />
          {sel.some((c) => c.status !== "archived") && (
            <button className="btn sm" disabled={busy} onClick={() => setStatus(sel.filter((c) => c.status !== "archived"), "archived")}>
              <Icon name="package" /> Archive
            </button>
          )}
          {sel.some((c) => c.status === "archived") && (
            <button className="btn sm" disabled={busy} onClick={() => setStatus(sel.filter((c) => c.status === "archived"), "active")}>
              <Icon name="refresh" /> Unarchive
            </button>
          )}
          <button className="btn sm danger" disabled={busy} onClick={() => remove(sel)}>
            {busy ? <Spinner /> : <Icon name="trash" />} Delete
          </button>
          <button className="btn sm ghost" onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </div>
      )}

      {!q.data ? (
        <div className="skeleton" style={{ height: 160 }} />
      ) : list.length === 0 ? (
        <Empty icon="target" title={archived ? "No campaigns" : "No campaigns yet"}>
          <p className="muted small" style={{ maxWidth: 460, margin: "0 auto 12px" }}>
            Pick the campaign type and paste the brief from the campaign site. ClipStudio reads the requirements for you.
          </p>
          <Link className="btn primary" to="/campaigns/new">
            <Icon name="plus" /> New campaign
          </Link>
        </Empty>
      ) : (
        <div className="grid cols-3">
          {list.map((c) => (
            <CampaignCard
              key={c.id}
              c={c}
              picked={picked.has(c.id)}
              onPick={() => toggle(c.id)}
              onArchive={() => setStatus([c], c.status === "archived" ? "active" : "archived")}
              onDelete={() => remove([c])}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function CampaignCard({ c, picked, onPick, onArchive, onDelete }: { c: Campaign; picked: boolean; onPick: () => void; onArchive: () => void; onDelete: () => void }) {
  const b = c.brief;
  return (
    <div className={`card camp-card ${picked ? "picked" : ""}`}>
      <div className="row between">
        <span className="row" style={{ gap: 8 }}>
          <input type="checkbox" checked={picked} onChange={onPick} aria-label={`Select ${c.name}`} />
          <span className="badge accent">{TYPE_LABEL[b.type]}</span>
        </span>
        {c.status === "archived" ? <span className="badge">archived</span> : b.source && <span className="badge">{b.source}</span>}
      </div>
      <Link to={`/campaigns/${c.id}`} className="camp-link">
        <strong style={{ fontSize: 15.5 }} className="ellipsis">
          {c.name}
        </strong>
        <span className="row wrap small muted" style={{ gap: 10 }}>
          {b.cpm != null && <span>${b.cpm} / 1K views</span>}
          {(b.min_duration != null || b.max_duration != null) && (
            <span>
              {b.min_duration ?? "any"}–{b.max_duration ?? "any"} s
            </span>
          )}
          {b.logo.mode !== "none" && <span>{b.logo.has_file ? "Logo ✓" : "Logo missing"}</span>}
          {b.sound_rule.mode !== "none" && <span>Sound: {b.sound_rule.mode === "in_app" ? "in app" : "in video"}</span>}
        </span>
        <span className="row small" style={{ gap: 14 }}>
          <span className="muted">{plural(c.projects, "video")}</span>
          <span>
            <strong>{c.approved}</strong> <span className="muted">approved</span>
          </span>
          <span>
            <strong>{c.exported}</strong> <span className="muted">exported</span>
          </span>
        </span>
      </Link>
      <div className="camp-actions">
        <Link to={`/campaigns/${c.id}`} className="btn sm">
          Open
        </Link>
        <span className="spacer" />
        <button className="btn sm ghost" onClick={onArchive} title={c.status === "archived" ? "Bring back to the active list" : "Hide from the list; keeps everything"}>
          {c.status === "archived" ? "Unarchive" : "Archive"}
        </button>
        <button className="btn sm ghost icon danger" onClick={onDelete} aria-label={`Delete ${c.name}`} title="Delete campaign">
          <Icon name="trash" />
        </button>
      </div>
    </div>
  );
}
