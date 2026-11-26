import { Link, useSearchParams } from "react-router-dom";
import { Icon, type IconName } from "../components/Icon";

const CHOICES: { to: string; icon: IconName; title: string; desc: string; bullets: string[] }[] = [
  {
    to: "/new",
    icon: "scissors",
    title: "Clips from a long video",
    desc: "A stream, podcast or gameplay video. ClipStudio finds the best moments.",
    bullets: ["Upload files or paste links", "Captions, title text, framing done for you", "Already-short video? Tick “Quick clip” on the next page"],
  },
  {
    to: "/montage/new",
    icon: "music",
    title: "Music edit",
    desc: "Many short shots cut to a song's beat, strongest shot on the drop.",
    bullets: ["For music campaigns and edits", "Several variants at once"],
  },
];

/** "+ Create": one place to start anything (R2). Passes a campaign through when opened from one. */
export default function Create() {
  const [params] = useSearchParams();
  const campaign = params.get("campaign");
  const withCampaign = (to: string) => (campaign ? `${to}${to.includes("?") ? "&" : "?"}campaign=${campaign}` : to);
  return (
    <div className="stack lg" style={{ maxWidth: 940 }}>
      <div className="page-head">
        <div>
          <h1>What do you want to make?</h1>
          <p className="sub">Pick one. You can add a campaign's requirements on the next screen.</p>
        </div>
      </div>
      <div className="type-cards two">
        {CHOICES.map((c) => (
          <Link key={c.to} to={withCampaign(c.to)} className="type-card" style={{ textDecoration: "none" }}>
            <span className="ico">
              <Icon name={c.icon} size={20} />
            </span>
            <strong className="tc-title">{c.title}</strong>
            <span className="small muted">{c.desc}</span>
            <ul>
              {c.bullets.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </Link>
        ))}
      </div>
      <div className="card card-body row between wrap">
        <div className="stack sm" style={{ gap: 2 }}>
          <strong>Making videos for a brand's campaign?</strong>
          <span className="small muted">Set up the campaign first: paste its brief, and every video you add to it follows the brand's rules.</span>
        </div>
        <Link className="btn" to="/campaigns/new">
          <Icon name="target" /> New campaign
        </Link>
      </div>
    </div>
  );
}
