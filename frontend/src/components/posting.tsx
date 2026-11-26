import { useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { api, media } from "../api";
import { fmtDuration } from "../format";
import type { Campaign, Platform, PostedMap, ReadyClip } from "../types";
import { CopyButton, PLATFORM_LABEL } from "./campaign";
import { Icon } from "./Icon";
import { Spinner, attempt, plural, useToast } from "./ui";

export const PLATFORM_SHORT: Record<Platform, string> = { reels: "Instagram", shorts: "YouTube" };
const ALL: Platform[] = ["reels", "shorts"];

/** How far a clip is through posting: posted on every platform its campaign needs = done. */
export function postedState(posted: PostedMap | undefined, platforms: Platform[] | undefined) {
  const need = platforms?.length ? platforms : ALL;
  const n = need.filter((p) => posted?.[p]).length;
  return { done: n === need.length, n, total: need.length, need };
}

const day = (ts: number) => new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short" });

/** "Posted" badge for clip cards: full or per platform. Nothing when not posted anywhere. */
export function PostedBadge({ posted, platforms }: { posted?: PostedMap; platforms?: Platform[] }) {
  const s = postedState(posted, platforms);
  if (!s.n) return null;
  const where = s.need.filter((p) => posted?.[p]).map((p) => PLATFORM_SHORT[p]).join(" + ");
  return s.done ? (
    <span className="badge success" title={`Posted on ${where}`}>
      <Icon name="check" size={11} /> posted
    </span>
  ) : (
    <span className="badge info" title={`Posted on ${where}; ${s.total - s.n} to go`}>
      <Icon name="check" size={11} /> {where}
    </span>
  );
}

/**
 * Mark one platform as posted: one click marks it (today), the link to the live post can be added after.
 * Undo removes the mark (and its link).
 */
export function PostedControl({ clipId, platform, posted, onChange, compact = false }: { clipId: string; platform: Platform; posted: PostedMap; onChange: (p: PostedMap) => void; compact?: boolean }) {
  const toast = useToast();
  const mine = posted[platform];
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [url, setUrl] = useState("");
  const run = (fn: () => Promise<PostedMap>, after?: () => void) =>
    attempt(toast, async () => {
      setBusy(true);
      try {
        onChange(await fn());
        after?.();
      } finally {
        setBusy(false);
      }
    });
  const saveLink = () => {
    const u = url.trim();
    if (u && !/^https?:\/\/\S+$/.test(u)) {
      toast("Paste the full link, starting with https://", "error");
      return;
    }
    run(async () => (await api.markPosted(clipId, platform, u)).posted, () => setEditing(false));
  };

  if (editing)
    return (
      <span className="pq-link-edit">
        <input
          className="input"
          autoFocus
          value={url}
          placeholder={`Link to the ${PLATFORM_SHORT[platform]} post (optional)`}
          aria-label={`Link to the ${PLATFORM_LABEL[platform]} post`}
          onChange={(e) => setUrl(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") saveLink();
            if (e.key === "Escape") {
              e.stopPropagation();
              setEditing(false);
            }
          }}
        />
        <button className="btn sm primary" disabled={busy} onClick={saveLink}>
          {busy ? <Spinner /> : null} Save
        </button>
        <button className="btn sm ghost" onClick={() => setEditing(false)}>
          Cancel
        </button>
      </span>
    );

  if (!mine)
    return (
      <button
        className="btn sm"
        disabled={busy}
        title={`Mark as posted on ${PLATFORM_LABEL[platform]}. You can add the post's link after.`}
        onClick={() => run(async () => (await api.markPosted(clipId, platform)).posted)}
      >
        {busy ? <Spinner /> : <Icon name="check" />} {compact ? PLATFORM_SHORT[platform] : "Mark posted"}
      </button>
    );

  return (
    <span className="pq-posted">
      <span className="badge success">
        <Icon name="check" size={11} /> {compact ? `${PLATFORM_SHORT[platform]} · ` : "Posted "}
        {day(mine.at)}
      </span>
      {mine.url && (
        <a className="btn sm ghost icon" href={mine.url} target="_blank" rel="noreferrer" title={mine.url} aria-label="Open the post">
          <Icon name="link" />
        </a>
      )}
      <button
        className="link-btn small"
        onClick={() => {
          setUrl(mine.url ?? "");
          setEditing(true);
        }}
      >
        {mine.url ? "Edit link" : "Add link"}
      </button>
      <button className="link-btn small muted-link" disabled={busy} title="Not posted after all: remove the mark" onClick={() => run(async () => (await api.unmarkPosted(clipId, platform)).posted)}>
        Undo
      </button>
    </span>
  );
}

const VERDICT_TEXT = (r: ReadyClip) =>
  r.verdict === "pass" ? "All checks passed" : r.verdict === "warn" ? "Check warnings" : r.verdict === "fail" ? `${plural(r.fails, "problem")} to fix` : "Not checked";

/**
 * The campaign's post queue: every approved, rendered clip still to post, with what you need to post it
 * (download, caption per platform, mark posted), and the clips already posted with their links.
 */
export function PostQueue({ camp, onRefresh, goPosting }: { camp: Campaign; onRefresh: () => Promise<void>; goPosting: () => void }) {
  const toast = useToast();
  const [params] = useSearchParams();
  const [view, setView] = useState<"todo" | "done">(params.get("show") === "posted" ? "done" : "todo");
  // local copy of posted marks so a click shows at once; the campaign reload then moves finished rows
  const [local, setLocal] = useState<Record<string, PostedMap>>({});
  const seq = useRef<Record<string, number>>({}); // latest click per row: only its refresh may clear the local copy
  const platforms = camp.post_platforms?.length ? camp.post_platforms : ALL;
  const todo = camp.ready ?? [];
  const done = camp.posted_clips ?? [];
  const rows = view === "todo" ? todo : done;
  const postedOf = (r: ReadyClip) => local[r.id] ?? r.posted;
  const changed = async (r: ReadyClip, p: PostedMap) => {
    const n = (seq.current[r.id] = (seq.current[r.id] ?? 0) + 1);
    const was = postedState(postedOf(r), platforms).done;
    setLocal((m) => ({ ...m, [r.id]: p }));
    const now = postedState(p, platforms).done;
    if (now && !was) toast(`“${r.title || "Clip"}” posted everywhere: moved to Posted`);
    if (!now && was && view === "done") toast(`“${r.title || "Clip"}” marked as not posted on that platform`);
    await onRefresh();
    if (seq.current[r.id] !== n) return; // a newer click on this row is still refreshing
    setLocal((m) => {
      const n = { ...m };
      delete n[r.id];
      return n;
    });
  };

  return (
    <section className="card">
      <div className="card-head wrap" style={{ gap: 10 }}>
        <div className="stack" style={{ gap: 2 }}>
          <h2>Post queue</h2>
          <span className="small muted">
            Posting to {platforms.map((p) => PLATFORM_LABEL[p]).join(" and ")} ·{" "}
            <button className="link-btn" onClick={goPosting}>
              change
            </button>
          </span>
        </div>
        <div className="seg" role="tablist" aria-label="Show">
          <button role="tab" aria-selected={view === "todo"} className={view === "todo" ? "active" : ""} onClick={() => setView("todo")}>
            To post <span className="count">{camp.ready_total ?? todo.length}</span>
          </button>
          <button role="tab" aria-selected={view === "done"} className={view === "done" ? "active" : ""} onClick={() => setView("done")}>
            Posted <span className="count">{camp.posted_total ?? done.length}</span>
          </button>
        </div>
      </div>
      <div className="card-body tight">
        {view === "todo" && todo.length > 0 && (
          <p className="small muted" style={{ margin: "10px 0 2px" }}>
            For each clip: <b>1</b> download the video, <b>2</b> copy the caption and post it in the app, <b>3</b> press <b>Mark posted</b>. Once it's marked on every platform it moves to Posted.
          </p>
        )}
        {rows.length === 0 && (
          <p className="small muted" style={{ padding: "14px 0" }}>
            {view === "todo"
              ? (camp.outdated ?? 0) > 0
                ? "Nothing to post right now. Some approved clips need re-rendering first (see the note at the top of Overview)."
                : "Nothing to post. Approve clips on a video's page and render them: they show up here."
              : "Nothing posted yet. Clips you mark as posted show here, with their links."}
          </p>
        )}
        {rows.map((r) => {
          const posted = postedOf(r);
          return (
            <div className="pq-row" key={r.id}>
              <Link to={`/clips/${r.id}?tab=post`} className="pq-thumb" title="Open in the editor">
                <img src={media.thumb({ id: r.id, version: Math.round(r.updated_at) })} alt="" onError={(e) => (e.currentTarget.style.visibility = "hidden")} />
              </Link>
              <div className="pq-body">
                <div className="pq-head">
                  <div style={{ minWidth: 0 }}>
                    <b className="ellipsis" style={{ display: "block" }}>
                      {r.title || "Untitled clip"}
                    </b>
                    <span className="tiny muted">
                      {fmtDuration(r.duration)} · {r.project_name}
                      {view === "todo" && (
                        <>
                          {" · "}
                          <span className={r.verdict === "fail" ? "danger-text" : r.verdict === "warn" ? "warn-text" : ""}>{VERDICT_TEXT(r)}</span>
                        </>
                      )}
                    </span>
                  </div>
                  <div className="row wrap" style={{ gap: 6, flex: "none" }}>
                    {view === "todo" && r.verdict === "fail" && (
                      <Link className="btn sm" to={`/clips/${r.id}?tab=post`}>
                        <Icon name="alert" /> Fix
                      </Link>
                    )}
                    {view === "todo" && r.render_status !== "done" ? (
                      <span className="badge warn" title={r.render_status === "failed" ? "The last render failed. Open it to see why." : "Its video is out of date: re-render it (Overview note or the video's page) before posting the rest."}>
                        {r.render_status === "failed" ? "render failed" : "needs re-render"}
                      </span>
                    ) : r.has_video ? (
                      <a className="btn sm" href={`/api/clips/${r.id}/download`} title="Download the MP4" download>
                        <Icon name="download" /> MP4
                      </a>
                    ) : null}
                    <Link className="btn sm icon" to={`/clips/${r.id}?tab=post`} title="Open in the editor" aria-label="Open in the editor">
                      <Icon name="edit" />
                    </Link>
                  </div>
                </div>
                {view === "todo" ? (
                  platforms.map((p) => {
                    const cap = r.captions?.[p];
                    return (
                      <div className={`pq-plat ${posted[p] ? "is-done" : ""}`} key={p}>
                        <div className="pq-plat-text">
                          <span className="section-title">{PLATFORM_LABEL[p]}</span>
                          {cap && p === "shorts" && cap.title && (
                            <span className="small">
                              <span className="muted">Title:</span> {cap.title}
                            </span>
                          )}
                          {cap && <Caption text={cap.caption} />}
                          {cap?.warnings.map((w) => (
                            <span key={w} className="tiny warn-text">
                              {w}
                            </span>
                          ))}
                        </div>
                        <div className="pq-plat-actions">
                          {cap && p === "shorts" && cap.title && !posted[p] && <CopyButton text={cap.title} label="Copy title" />}
                          {cap && !posted[p] && <CopyButton text={cap.caption} label="Copy caption" />}
                          <PostedControl clipId={r.id} platform={p} posted={posted} onChange={(m) => changed(r, m)} />
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="row wrap" style={{ gap: 8, marginTop: 6 }}>
                    {platforms.map((p) => (
                      <PostedControl key={p} compact clipId={r.id} platform={p} posted={posted} onChange={(m) => changed(r, m)} />
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {rows.length < (view === "todo" ? camp.ready_total ?? 0 : camp.posted_total ?? 0) && (
          <p className="tiny muted" style={{ padding: "8px 0" }}>
            Showing the latest {rows.length}. Post or mark these to see the rest.
          </p>
        )}
      </div>
    </section>
  );
}

/** A caption, two lines until "Show all". */
function Caption({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  const long = text.length > 120;
  return (
    <span className="stack" style={{ gap: 2 }}>
      {/* collapsed: line breaks shown as "·" so the hashtags and required text are visible in two lines */}
      <span className={`pq-caption ${open ? "open" : ""}`}>{!text ? <i className="muted">Empty caption</i> : open ? text : text.trim().replace(/\s*\n+\s*/g, " · ")}</span>
      {long && (
        <button className="link-btn tiny" style={{ alignSelf: "flex-start" }} onClick={() => setOpen(!open)}>
          {open ? "Show less" : "Show all"}
        </button>
      )}
    </span>
  );
}
