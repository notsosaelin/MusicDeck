/**
 * Your listening (stats.py's play log, every source's): for the last week, month or year, or all time. The
 * totals as tiles, top artists and albums as cards (those from the live source open), top songs as a list, and
 * when you listen: by hour of the day and day of the week, as plain bar charts (one series, one blue; each bar
 * says its number under the mouse).
 */
import { FC, useEffect, useState } from "react";
import * as api from "../api";
import { ListeningStats, StatsItem } from "../api";
import { mediaUrl, pluginState, useStore } from "../store";
import { go } from "./data";
import { ArtOrIcon } from "./library";

const PERIODS: [ListeningStats["period"], string][] = [
  ["week", "Last 7 days"],
  ["month", "Last 30 days"],
  ["year", "Last 12 months"],
  ["all", "All time"],
];
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const hours = (seconds: number) =>
  seconds >= 36000 ? `${Math.round(seconds / 3600)}` : seconds >= 3600 ? (seconds / 3600).toFixed(1) : `${Math.round(seconds / 60)}`;
const hourLabel = (h: number) => (h === 0 ? "12am" : h < 12 ? `${h}am` : h === 12 ? "12pm" : `${h - 12}pm`);

const Tile: FC<{ value: string; label: string }> = ({ value, label }) => (
  <div className="st-tile">
    <div className="st-value">{value}</div>
    <div className="st-label">{label}</div>
  </div>
);

/** One series as bars: thin, rounded at the top, anchored to the baseline, 2px apart; its value on hover. */
const Bars: FC<{ values: number[]; labels: string[]; every: number; title: string }> = ({ values, labels, every, title }) => {
  const top = Math.max(1, ...values);
  const [hover, setHover] = useState<number | null>(null);
  return (
    <figure className="st-chart">
      <figcaption>{title}</figcaption>
      <div className="st-bars" onMouseLeave={() => setHover(null)} role="img"
           aria-label={`${title}: ${values.map((v, i) => `${labels[i]} ${v}`).join(", ")}`}>
        {values.map((v, i) => (
          // the whole column takes the mouse, not just the bar: short bars are easy to point at
          <div key={i} className="st-col" onMouseEnter={() => setHover(i)}>
            <div className="st-bar" data-on={hover === i ? "" : undefined} style={{ height: `${(v / top) * 100}%` }} />
            {hover === i && (
              <div className="st-tip">
                <b>{v}</b> {v === 1 ? "listen" : "listens"} · {labels[i]}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="st-axis">
        {labels.map((l, i) => (
          <span key={i}>{i % every === 0 ? l : ""}</span>
        ))}
      </div>
    </figure>
  );
};

const Card: FC<{ item: StatsItem; kind: "artist" | "album"; live: string }> = ({ item, kind, live }) => {
  const art = (item.art && item.src && mediaUrl("art", item.src, item.art)) || "";
  const opens = item.src === live && !!item.id;
  return (
    <div
      className="md-card st-card"
      data-opens={opens ? "" : undefined}
      onClick={() =>
        opens && go({ type: "collection", collection: { id: item.id!, kind, title: item.name, subtitle: item.artist ?? "", art: item.art ?? null } })
      }
    >
      <div className="md-card-art" style={{ borderRadius: kind === "artist" ? "50%" : 4 }}>
        <ArtOrIcon url={art} kind={kind} />
      </div>
      <div className="md-card-title">{item.name}</div>
      <div className="md-card-sub">
        {kind === "album" ? `${item.artist} · ` : ""}
        {item.plays} {item.plays === 1 ? "listen" : "listens"}
      </div>
    </div>
  );
};

export const StatsPage: FC = () => {
  const [period, setPeriod] = useState<ListeningStats["period"]>("month");
  const [stats, setStats] = useState<ListeningStats | null>(null);
  const [error, setError] = useState("");
  const live = useStore(pluginState)?.active_service ?? "";
  useEffect(() => {
    let current = true;
    setError("");
    api
      .listeningStats(period)
      .then((r) => current && (r.ok ? setStats(r.data) : setError(r.error)))
      .catch((e) => current && setError(String(e)));
    return () => {
      current = false;
    };
  }, [period]);
  const s = stats?.period === period ? stats : null;
  return (
    <>
      <div className="st-head">
        <h1 className="md-title">Your listening</h1>
        <div className="st-periods" role="tablist">
          {PERIODS.map(([id, label]) => (
            <button key={id} role="tab" aria-selected={period === id} data-on={period === id ? "" : undefined} onClick={() => setPeriod(id)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {error && <div className="md-dim">{error}</div>}
      {!s && !error && <div className="md-dim">Adding it up…</div>}
      {s && !s.plays && (
        <div className="md-dim st-empty">
          Nothing yet for this time. Every song you listen to past its first half (or four minutes), from any source, counts here.
        </div>
      )}
      {s && !!s.plays && (
        <>
          <div className="st-tiles">
            <Tile value={s.plays.toLocaleString()} label={s.plays === 1 ? "listen" : "listens"} />
            <Tile value={hours(s.seconds)} label={s.seconds >= 3600 ? "hours" : "minutes"} />
            <Tile value={s.songs.toLocaleString()} label={s.songs === 1 ? "song" : "different songs"} />
            <Tile value={s.artists_count.toLocaleString()} label={s.artists_count === 1 ? "artist" : "artists"} />
            <Tile value={`${s.streak}`} label={s.streak === 1 ? "day in a row" : "days in a row"} />
          </div>
          <h2 className="md-heading">Top artists</h2>
          <div className="md-grid">
            {s.top_artists.slice(0, 6).map((a) => (
              <Card key={a.name} item={a} kind="artist" live={live} />
            ))}
          </div>
          <h2 className="md-heading">Top albums</h2>
          <div className="md-grid">
            {s.top_albums.slice(0, 6).map((a) => (
              <Card key={`${a.name}-${a.artist}`} item={a} kind="album" live={live} />
            ))}
          </div>
          <h2 className="md-heading">Top songs</h2>
          <div className="st-songs">
            {s.top_songs.map((t, i) => (
              <div key={`${t.name}-${t.artist}`} className="st-song">
                <span className="st-rank">{i + 1}</span>
                <span className="st-song-art">
                  <ArtOrIcon url={(t.art && t.src && mediaUrl("art", t.src, t.art)) || ""} size={40} />
                </span>
                <span className="st-song-name">
                  <span>{t.name}</span>
                  <span className="md-dim">{t.artist}</span>
                </span>
                <span className="md-dim">
                  {t.plays} {t.plays === 1 ? "listen" : "listens"}
                </span>
              </div>
            ))}
          </div>
          <h2 className="md-heading">When you listen</h2>
          <div className="st-charts">
            <Bars title="By hour of the day" values={s.hours} labels={s.hours.map((_, h) => hourLabel(h))} every={6} />
            <Bars title="By day of the week" values={s.weekdays} labels={DAYS.map((d) => d.slice(0, 3))} every={1} />
          </div>
        </>
      )}
    </>
  );
};

export const STATS_CSS = `
.st-head { display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap; margin: 24px 0 20px; }
.st-head .md-title { margin: 0; }
.st-periods { display: flex; gap: 2px; padding: 3px; border-radius: 18px; background: rgba(0,0,0,0.3); }
.st-periods button { border: none; background: none; color: #b8bcbf; height: 30px; padding: 0 14px; border-radius: 15px; font: inherit;
  font-size: 13px; cursor: pointer; }
.st-periods button:hover { color: #fff; }
.st-periods button[data-on] { background: #dcdedf; color: #10141a; font-weight: 600; }
.st-empty { max-width: 520px; line-height: 1.6; }
.st-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 12px; margin-bottom: 8px; }
.st-tile { padding: 16px 18px; border-radius: 6px; background: rgba(255,255,255,0.05); }
.st-value { font-size: 32px; font-weight: 700; color: #fff; line-height: 1.1; font-variant-numeric: tabular-nums; }
.st-label { font-size: 13px; color: #8b929a; margin-top: 4px; }
.st-card:not([data-opens]) { cursor: default; }
.st-songs { display: flex; flex-direction: column; max-width: 760px; }
.st-song { display: flex; align-items: center; gap: 14px; padding: 6px 8px; border-radius: 4px; }
.st-song:hover { background: rgba(255,255,255,0.05); }
.st-rank { width: 22px; text-align: right; color: #8b929a; font-variant-numeric: tabular-nums; }
.st-song-art { width: 40px; height: 40px; border-radius: 3px; overflow: hidden; flex-shrink: 0; }
.st-song-name { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.st-song-name > span { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.st-charts { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 28px; margin-bottom: 32px; }
.st-chart { margin: 0; }
.st-chart figcaption { font-size: 13px; color: #b8bcbf; margin-bottom: 10px; }
.st-bars { position: relative; display: flex; align-items: flex-end; gap: 2px; height: 140px;
  border-bottom: 1px solid rgba(255,255,255,0.12); }
.st-col { position: relative; flex: 1; height: 100%; display: flex; align-items: flex-end; justify-content: center; }
/* thin marks: a bar never wider than 28px, however few there are */
.st-bar { width: 100%; max-width: 28px; min-height: 2px; border-radius: 4px 4px 0 0; background: #1a9fff; opacity: 0.85; }
.st-bar[data-on] { opacity: 1; background: #47b4ff; }
.st-tip { position: absolute; bottom: calc(100% + 6px); left: 50%; transform: translateX(-50%); z-index: 2; white-space: nowrap;
  padding: 5px 9px; border-radius: 4px; background: #3d4450; color: #dcdedf; font-size: 12px; box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  pointer-events: none; }
.st-tip b { color: #fff; }
.st-axis { display: flex; gap: 2px; margin-top: 6px; font-size: 11px; color: #8b929a; }
.st-axis span { flex: 1; white-space: nowrap; text-align: center; }
.st-chart[data-few] .st-axis span { text-align: center; }
`;
