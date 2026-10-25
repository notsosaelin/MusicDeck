/**
 * Library tools (Local files): the clean-up report (health.py) with each problem's fix beside it, and the
 * library-wide jobs (tidying tags and files, levelling loudness, moving in from another player). The report is
 * asked for when the page opens and again after each fix.
 */
import { FileSelectionType, openFilePicker } from "@decky/api";
import { FC, ReactNode, useEffect, useState } from "react";
import { FaCheck } from "react-icons/fa";
import * as api from "../api";
import { Collection } from "../api";
import { editTags } from "../components/TagEditor";
import { invalidateBrowserCache } from "../nav";
import { notify } from "../notify";
import { pluginState } from "../store";
import { go } from "./data";
import { ArtOrIcon } from "./library";
import { showOrganize, showTagTools } from "./tagtools";

type Song = api.Track & { folder: string; file: string; quality: string; size: string };
export interface HealthReport {
  tracks: number;
  duplicates: Song[][];
  duplicate_count: number;
  split: { album: string; folder: string; path: string; suggest: string; parts: { album_id: string; albumartist: string; tracks: number }[] }[];
  split_count: number;
  no_art: Collection[];
  no_art_count: number;
  untagged: (Song & { missing: string[] })[];
  untagged_count: number;
  missing_files: number;
  no_gain: number;
  loudness: { running: boolean; done: number; total: number; written: number };
}

/** A part of the page; `count` -1: a tool, not a problem (no count, always open). */
const Section: FC<{ id: string; title: string; count: number; action?: ReactNode; note: string; children?: ReactNode }> = ({
  id,
  title,
  count,
  action,
  note,
  children,
}) => (
  <section className="tl-section" id={`tl-${id}`}>
    <div className="tl-section-head">
      <div>
        <h2 className="tl-title">
          {title}
          {count >= 0 && <span className="tl-count" data-zero={count ? undefined : ""}>{count ? count.toLocaleString() : <FaCheck />}</span>}
        </h2>
        {count >= 0 && <div className="md-dim tl-note">{count ? note : "Nothing to fix."}</div>}
      </div>
      {!!count && action}
    </div>
    {!!count && children}
  </section>
);

/** Picks a library file and brings it in; says what came across. */
async function importFrom(id: string) {
  let path: string | null = null;
  try {
    path = (await openFilePicker(FileSelectionType.FILE, pluginState.get()?.user_home || "/", true, true, undefined, ["xml"])).realpath;
  } catch {
    return; // cancelled
  }
  const r = await api.importLibrary(id, path).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) return notify("Import", r.error);
  const d = r.data;
  notify(
    "Import",
    `${d.matched} of ${d.songs} songs found here: ${d.plays} play counts, ${d.ratings} ratings, ${d.loved} loved, ${d.playlists} playlists`,
  );
}

export const ToolsPage: FC<{ id: string }> = ({ id }) => {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const refresh = () =>
    api
      .libraryHealth(id)
      .then((r) => (r.ok ? (setReport(r.data), setError("")) : setError(r.error)))
      .catch((e) => setError(String(e)));
  useEffect(() => {
    setReport(null);
    refresh();
  }, [id]);
  // while loudness is measured, its count and progress follow it (a light call, not the whole report)
  useEffect(() => {
    if (!report?.loudness.running) return;
    const timer = window.setTimeout(async () => {
      const s = await api.loudnessStatus(id).catch(() => null);
      if (s?.ok) setReport((r) => r && { ...r, no_gain: s.data.no_gain, loudness: s.data });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [report]);
  const run = async (label: string, work: () => Promise<unknown>) => {
    setBusy(label);
    try {
      await work();
    } finally {
      invalidateBrowserCache();
      await refresh();
      setBusy("");
    }
  };
  const removeCopies = (groups: Song[][]) =>
    run("copies", async () => {
      let removed = 0;
      for (const group of groups)
        for (const copy of group.slice(1)) {
          const r = await api.deleteFromLibrary(id, "track", copy.id).catch(() => null);
          if (r?.ok) removed++;
        }
      notify("Duplicates", `${removed} ${removed === 1 ? "copy" : "copies"} moved to Recently deleted`);
    });
  const r = report;
  return (
    <>
      <h1 className="md-title">Library tools</h1>
      {error && <div className="md-dim">{error}</div>}
      {!r && !error && <div className="md-dim">Looking through the library…</div>}
      {r && (
        <>
          <div className="md-dim" style={{ marginBottom: 18 }}>
            {r.tracks.toLocaleString()} songs looked through.{" "}
            {r.duplicate_count + r.split_count + r.no_art_count + r.untagged_count + r.missing_files === 0 ? "All tidy." : ""}
          </div>

          <Section
            id="duplicates"
            title="Duplicates"
            count={r.duplicate_count}
            note="The same song more than once. The best copy (lossless first, then the highest quality) is kept; the others go to Recently deleted, where they can be put back."
            action={
              <button className="md-button md-wide" disabled={!!busy} onClick={() => removeCopies(r.duplicates)}>
                {busy === "copies" ? "Removing…" : "Keep the best of each"}
              </button>
            }
          >
            {r.duplicates.map((group) => (
              <div key={group[0].id} className="tl-group">
                {group.map((s, i) => (
                  <div key={s.id} className="tl-row" data-keep={i === 0 ? "" : undefined}>
                    <span className="tl-badge">{i === 0 ? "Keep" : "Copy"}</span>
                    <span className="tl-main">
                      <span>{s.title}</span>
                      <span className="md-dim">{s.artist}</span>
                    </span>
                    <span className="tl-file md-dim" title={`${s.folder}/${s.file}`}>
                      {s.folder} › {s.file}
                    </span>
                    <span className="tl-quality md-dim">
                      {s.quality} · {s.size}
                    </span>
                  </div>
                ))}
              </div>
            ))}
          </Section>

          <Section
            id="split"
            title="Albums split in two"
            count={r.split_count}
            note="One folder's songs with one album name, shown as several albums because their album artists differ. Give them one."
          >
            {r.split.map((s) => (
              <SplitRow key={`${s.path}-${s.album}`} id={id} split={s} disabled={!!busy} onDone={() => run("split", async () => {})} />
            ))}
          </Section>

          <Section
            id="art"
            title="Albums without a cover"
            count={r.no_art_count}
            note="Nothing in the files or the folder. MusicDeck can look for each on the Cover Art Archive, Apple Music and Deezer."
            action={
              <button
                className="md-button md-wide"
                disabled={!!busy}
                onClick={() =>
                  run("art", async () => {
                    const res = await api.findMissingArt(id);
                    if (res.ok) notify("Covers", "Looking for them in the background; they appear as they're found");
                  })
                }
              >
                Find covers
              </button>
            }
          >
            <div className="tl-covers">
              {r.no_art.slice(0, 24).map((a) => (
                <div key={a.id} className="tl-cover" onClick={() => go({ type: "collection", collection: a })} title={`${a.title} · ${a.subtitle}`}>
                  <ArtOrIcon url="" />
                  <span>{a.title}</span>
                </div>
              ))}
            </div>
          </Section>

          <Section
            id="untagged"
            title="Songs missing details"
            count={r.untagged_count}
            note="No artist, album or track number, so they sort and group badly. Click one to edit it, or open its album and use Look up details."
          >
            {r.untagged.slice(0, 60).map((s) => (
              <div key={s.id} className="tl-row tl-click" onClick={() => editTags(id, "track", s.id, s.title)}>
                <span className="tl-main">
                  <span>{s.title}</span>
                  <span className="md-dim">{s.folder} › {s.file}</span>
                </span>
                <span className="tl-missing">No {s.missing.join(", no ")}</span>
              </div>
            ))}
            {r.untagged_count > 60 && <div className="md-dim tl-more">and {r.untagged_count - 60} more</div>}
          </Section>

          <Section
            id="loudness"
            title="Songs without loudness info"
            count={r.loudness.running ? Math.max(r.no_gain, 1) : r.no_gain}
            note="Volume levelling can't even them out. MusicDeck can measure each (with ffmpeg) and write ReplayGain tags into the files, as foobar2000 does."
            action={
              <button
                className="md-button md-wide"
                disabled={r.loudness.running}
                onClick={() =>
                  api.scanLoudness(id).then((x) => {
                    if (!x.ok) notify("Measuring loudness", x.error);
                    refresh();
                  })
                }
              >
                {r.loudness.running ? `Measuring ${r.loudness.done} of ${r.loudness.total}…` : "Measure"}
              </button>
            }
          />

          <Section
            id="tidy"
            title="Tidy the whole library"
            count={-1}
            note=""
            action={
              <div style={{ display: "flex", gap: 8 }}>
                <button className="md-button md-wide" onClick={() => showTagTools(id, ["*"], "Every song in the library")}>Tag tools…</button>
                <button className="md-button md-wide" onClick={() => showOrganize(id, undefined, "Every song in the library")}>Organize files…</button>
              </div>
            }
          >
            <div className="md-dim tl-note">
              Tag tools: tags from file names, capitalisation, track numbers, find and replace. Organize files: every song moved
              into folders by its tags. Both show what they'd do first. (Right-click songs or an album for the same, for just those.)
            </div>
          </Section>

          <Section
            id="move"
            title="Moving in, and out"
            count={-1}
            note=""
            action={
              <div style={{ display: "flex", gap: 8 }}>
                <button className="md-button md-wide" disabled={!!busy} onClick={() => run("import", () => importFrom(id))}>
                  {busy === "import" ? "Bringing it in…" : "Import from iTunes or MusicBee…"}
                </button>
                <button
                  className="md-button md-wide"
                  onClick={async () => {
                    const r = await api.exportPlaylists(id).catch((e) => ({ ok: false as const, error: String(e) }));
                    notify("Export playlists", r.ok ? `${r.data.count} playlists saved as .m3u8 in ${r.data.folder}` : r.error);
                  }}
                >
                  Export playlists
                </button>
              </div>
            }
          >
            <div className="md-dim tl-note">
              Import: the library file iTunes exports (File › Library › Export Library) or MusicBee writes (Preferences › Library ›
              iTunes compatible XML). Play counts, ratings, loved songs and playlists come across for the songs that are here.
              Export: your playlists as .m3u8 files any player opens. (A backup of everything MusicDeck keeps is in Settings.)
            </div>
          </Section>

          <Section
            id="missing"
            title="Files gone"
            count={r.missing_files}
            note="In the library, but their files were moved or deleted outside MusicDeck. A rescan finds where things are now."
            action={
              <button className="md-button md-wide" disabled={!!busy} onClick={() => run("rescan", () => api.rescan(id))}>
                Rescan
              </button>
            }
          />
        </>
      )}
    </>
  );
};

const SplitRow: FC<{ id: string; split: HealthReport["split"][number]; disabled: boolean; onDone: () => void }> = ({
  id,
  split,
  disabled,
  onDone,
}) => {
  const [artist, setArtist] = useState(split.suggest);
  const [working, setWorking] = useState(false);
  return (
    <div className="tl-row tl-split">
      <span className="tl-main">
        <span>{split.album}</span>
        <span className="md-dim">
          {split.folder} · now {split.parts.map((p) => `${p.albumartist || "no album artist"} (${p.tracks})`).join(", ")}
        </span>
      </span>
      <input className="tl-input" value={artist} onChange={(e) => setArtist(e.target.value)} placeholder="Album artist" />
      <button
        className="md-button md-wide"
        disabled={disabled || working || !artist.trim()}
        onClick={async () => {
          setWorking(true);
          const r = await api.fixSplitAlbum(id, split.path, split.album, artist.trim()).catch((e) => ({ ok: false as const, error: String(e) }));
          setWorking(false);
          if (!r.ok) notify("Couldn't join it", r.error);
          onDone();
        }}
      >
        {working ? "Joining…" : "Join"}
      </button>
    </div>
  );
};

export const TOOLS_CSS = `
.tl-section { padding: 18px 0; border-top: 1px solid rgba(255,255,255,0.07); }
.tl-section-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; margin-bottom: 10px; }
.tl-title { margin: 0; font-size: 18px; font-weight: 600; color: #fff; display: flex; align-items: center; gap: 10px; }
.tl-count { font-size: 12px; font-weight: 700; padding: 2px 9px; border-radius: 10px; background: #d7a640; color: #10141a; }
.tl-count[data-zero] { background: rgba(89,191,64,0.2); color: #59bf40; }
.tl-note { font-size: 13px; margin-top: 4px; max-width: 640px; line-height: 1.5; }
.tl-group { margin: 8px 0 12px; border-radius: 6px; background: rgba(255,255,255,0.03); padding: 4px 0; }
.tl-row { display: flex; align-items: center; gap: 14px; padding: 7px 12px; font-size: 13px; border-radius: 4px; }
.tl-click { cursor: pointer; }
.tl-click:hover { background: rgba(255,255,255,0.06); }
.tl-badge { width: 44px; text-align: center; font-size: 11px; font-weight: 700; padding: 2px 0; border-radius: 3px;
  background: rgba(255,255,255,0.08); color: #8b929a; flex-shrink: 0; }
.tl-row[data-keep] .tl-badge { background: rgba(89,191,64,0.2); color: #59bf40; }
.tl-main { flex: 1.2; min-width: 0; display: flex; flex-direction: column; }
.tl-main > span, .tl-file { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tl-file { flex: 1.4; min-width: 0; }
.tl-quality { width: 170px; text-align: right; flex-shrink: 0; }
.tl-missing { color: #d7a640; font-size: 12px; flex-shrink: 0; }
.tl-input { width: 220px; height: 30px; padding: 0 10px; border-radius: 3px; border: 1px solid #3d4450; background: #1b2129; color: #fff; font: inherit; }
.tl-covers { display: grid; grid-template-columns: repeat(auto-fill, minmax(110px, 1fr)); gap: 12px; }
.tl-cover { cursor: pointer; font-size: 12px; color: #b8bcbf; }
.tl-cover > div, .tl-cover > img { aspect-ratio: 1; border-radius: 4px; overflow: hidden; }
.tl-cover > span { display: block; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tl-more { padding: 6px 12px; font-size: 12px; }
`;
