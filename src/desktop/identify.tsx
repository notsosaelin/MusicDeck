/**
 * "Look up details" in desktop Steam: the records an album may be (every catalogue's, weighed against its
 * files: identify.py) on the left, most likely first; the one chosen on the right, its cover large, how sure
 * it is, what would change and which file is which track (with both lengths, the sign it's the right
 * record). A search by other names when the tags are too far off to find it. In a resizable window of its
 * own (dialog.ts), so its styles are its own.
 */
import { ModalRoot } from "@decky/ui";
import { CSSProperties, FC, useEffect, useState } from "react";
import { FaCompactDisc, FaSearch } from "react-icons/fa";
import * as api from "../api";
import { Collection, LookupChanges, Release } from "../api";
import { confidenceColour, lookupOpener } from "../components/TagEditor";
import { invalidateBrowserCache } from "../nav";
import { notify } from "../notify";
import { relayUrl } from "../store";
import { desktopDialog } from "./dialog";

const SIZE = { title: "Look up details", width: 1040, height: 720 };
const dim: CSSProperties = { color: "#8b929a" };
const input: CSSProperties = { flex: 1, minWidth: 0, height: 32, padding: "0 10px", borderRadius: 3, border: "1px solid #3d4450",
                               background: "#1b2129", color: "#fff", font: "inherit", fontSize: 14 };
const button: CSSProperties = { height: 32, padding: "0 16px", border: "none", borderRadius: 2, background: "#3d4450", color: "#fff",
                                font: "inherit", fontSize: 14, cursor: "pointer", display: "flex", alignItems: "center", gap: 8 };

const clock = (s: number) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s) % 60).padStart(2, "0")}` : "–");

const Cover: FC<{ url?: string; size: number }> = ({ url, size }) => {
  const [failed, setFailed] = useState(false);
  const src = relayUrl(url);
  const box: CSSProperties = { width: size, height: size, minWidth: size, borderRadius: 3, overflow: "hidden",
                               background: "linear-gradient(135deg, #2a3240, #1b2129)", display: "flex", alignItems: "center", justifyContent: "center" };
  return (
    <div style={box}>
      {src && !failed ? <img src={src} onError={() => setFailed(true)} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
        : <FaCompactDisc style={{ width: "34%", height: "34%", color: "rgba(255,255,255,0.3)" }} />}
    </div>
  );
};

const Sure: FC<{ n: number }> = ({ n }) => (
  <span style={{ padding: "2px 8px", borderRadius: 10, fontSize: 12, fontWeight: 700, background: confidenceColour(n), color: "#10141a" }}>
    {n}%
  </span>
);

export const IdentifyDialog: FC<{ instanceId: string; album: Collection; files: number; closeModal?: () => void }> = ({
  instanceId,
  album,
  files,
  closeModal,
}) => {
  const [artist, setArtist] = useState(album.subtitle.split(" · ")[0] ?? "");
  const [title, setTitle] = useState(album.title);
  const [records, setRecords] = useState<Release[] | null>(null);
  const [error, setError] = useState("");
  const [chosen, setChosen] = useState<Release | null>(null);
  const [changes, setChanges] = useState<LookupChanges | null>(null);
  const [busy, setBusy] = useState(false);

  const search = (byName = false) => {
    setRecords(null);
    setError("");
    setChosen(null);
    api
      .lookupAlbum(instanceId, album.id, byName ? artist : "", byName ? title : "")
      .then((r) => {
        if (!r.ok) return setError(r.error);
        setRecords(r.data);
        setChosen(r.data[0] ?? null);
      })
      .catch((e) => setError(String(e)));
  };
  useEffect(() => search(), []);
  useEffect(() => {
    setChanges(null);
    if (!chosen) return;
    let live = true;
    api.lookupChanges(instanceId, album.id, chosen.id).then((r) => live && (r.ok ? setChanges(r.data) : setError(r.error)));
    return () => {
      live = false;
    };
  }, [chosen?.id]);

  const apply = async () => {
    if (!chosen) return;
    setBusy(true);
    const r = await api.applyLookup(instanceId, album.id, chosen.id).catch(() => null);
    setBusy(false);
    if (r?.ok) {
      invalidateBrowserCache();
      notify("MusicDeck", `${chosen.title}: ${r.data.changed} files updated${r.data.art ? ", cover found" : ""}`);
      closeModal?.();
    } else setError(r && !r.ok ? r.error : "Couldn't write the tags");
  };

  const sure = chosen ? chosen.confidence ?? chosen.score : 0;
  const nothingToDo = changes && !changes.album.length && !changes.tracks.length;
  return (
    <ModalRoot bAllowFullSize closeModal={closeModal} onCancel={closeModal}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14, height: "calc(100vh - 120px)", minHeight: 420, color: "#dcdedf" }}>
        <form
          style={{ display: "flex", gap: 8 }}
          onSubmit={(e) => {
            e.preventDefault();
            search(true);
          }}
        >
          <input style={input} value={artist} onChange={(e) => setArtist(e.target.value)} placeholder="Artist" />
          <input style={input} value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Album" />
          <button type="submit" style={button}>
            <FaSearch /> Search
          </button>
        </form>
        <div style={{ display: "flex", gap: 18, flex: 1, minHeight: 0 }}>
          <div style={{ width: 320, minWidth: 320, overflowY: "auto", display: "flex", flexDirection: "column", gap: 4 }}>
            {error && <div style={{ color: "#e88" }}>{error}</div>}
            {!records && !error && <div style={dim}>Asking MusicBrainz, Apple Music and Deezer…</div>}
            {records?.length === 0 && <div style={dim}>Nothing found by those names. Try other words above.</div>}
            {records?.map((r) => (
              <div
                key={r.id}
                onClick={() => setChosen(r)}
                style={{ display: "flex", gap: 10, padding: 8, borderRadius: 3, cursor: "pointer", alignItems: "center",
                         background: chosen?.id === r.id ? "rgba(26,159,255,0.22)" : "rgba(255,255,255,0.03)" }}
              >
                <Cover url={r.cover} size={52} />
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{r.title}</div>
                  <div style={{ ...dim, fontSize: 12 }}>{[r.artist, r.year, `${r.tracks} tracks`].filter(Boolean).join(" · ")}</div>
                  <div style={{ ...dim, fontSize: 11 }}>{r.source}</div>
                </div>
                <Sure n={r.confidence ?? r.score} />
              </div>
            ))}
          </div>
          <div style={{ flex: 1, minWidth: 0, overflowY: "auto" }}>
            {chosen && (
              <>
                <div style={{ display: "flex", gap: 20, alignItems: "flex-end" }}>
                  <Cover url={chosen.cover} size={180} />
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 26, color: "#fff" }}>{chosen.title}</div>
                    <div style={dim}>{[chosen.artist, chosen.year, chosen.label, (chosen.genres ?? []).join(", ")].filter(Boolean).join(" · ")}</div>
                    <div style={{ ...dim, fontSize: 12, marginTop: 4 }}>From {chosen.source}</div>
                    <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: 12 }}>
                      <div style={{ width: 200, height: 6, borderRadius: 3, background: "rgba(255,255,255,0.1)" }}>
                        <div style={{ width: `${sure}%`, height: "100%", borderRadius: 3, background: confidenceColour(sure) }} />
                      </div>
                      <span style={{ fontSize: 13 }}>
                        {sure >= 90 ? "Sure match" : sure >= 70 ? "Likely" : "Unlikely"} ({sure}%){chosen.tracks !== files ? ` · ${chosen.tracks} tracks, yours ${files}` : ""}
                      </span>
                    </div>
                  </div>
                </div>
                {!changes && <div style={{ ...dim, marginTop: 16 }}>Comparing…</div>}
                {nothingToDo && <div style={{ marginTop: 16 }}>Your files already say all of this.</div>}
                {!!changes?.album.length && (
                  <>
                    <div style={{ ...dim, fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", margin: "18px 0 6px" }}>Album</div>
                    {changes.album.map(([label, before, after]) => (
                      <div key={label} style={{ display: "flex", gap: 10, padding: "4px 0", fontSize: 14 }}>
                        <span style={{ ...dim, width: 110 }}>{label}</span>
                        <span style={{ ...dim, textDecoration: before ? "line-through" : undefined, flex: 1 }}>{before || "empty"}</span>
                        <span style={{ flex: 1.4 }}>{after}</span>
                      </div>
                    ))}
                  </>
                )}
                {!!changes?.pairs?.length && (
                  <>
                    <div style={{ ...dim, fontSize: 11, letterSpacing: "0.08em", textTransform: "uppercase", margin: "18px 0 6px" }}>
                      Tracks{changes.matched ? "" : " (not every file found its track: those keep their own details)"}
                    </div>
                    {changes.pairs.map(([file, length, track, trackLength]) => {
                      const off = track && length && trackLength ? Math.abs(length - trackLength) : null;
                      const ok = off !== null && off <= 3;
                      return (
                        <div key={file} style={{ display: "flex", gap: 10, padding: "3px 0", fontSize: 13, alignItems: "center" }}>
                          <span style={{ flex: 1, minWidth: 0, ...dim, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{file}</span>
                          <span style={{ width: 44, textAlign: "right", ...dim }}>{clock(length)}</span>
                          <span style={{ width: 18, textAlign: "center", color: !track ? "#e88" : ok ? "#59bf40" : off === null ? "#8b929a" : "#d7a640" }}>
                            {!track ? "✕" : ok ? "✓" : "~"}
                          </span>
                          <span style={{ flex: 1.2, minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                            {track || "no track of this record"}
                          </span>
                          <span style={{ width: 44, textAlign: "right", ...dim }}>{track ? clock(trackLength) : ""}</span>
                        </div>
                      );
                    })}
                  </>
                )}
              </>
            )}
          </div>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 10 }}>
          <button style={button} onClick={closeModal}>
            Cancel
          </button>
          <button
            style={{ ...button, background: chosen && !nothingToDo ? "linear-gradient(to right, #47bfff 5%, #1a44c2 60%)" : "#3d4450" }}
            disabled={!chosen || busy || !!nothingToDo}
            onClick={apply}
          >
            {busy ? "Writing…" : "Use these details"}
          </button>
        </div>
      </div>
    </ModalRoot>
  );
};

/** While desktop Steam shows: "Look up details" (the album menus, the album page) opens this. Returns the teardown. */
export function installDesktopLookup(): () => void {
  const gameMode = lookupOpener.open;
  lookupOpener.open = (instanceId, album, files) => {
    desktopDialog(<IdentifyDialog instanceId={instanceId} album={album} files={files} />, SIZE);
  };
  return () => {
    lookupOpener.open = gameMode;
  };
}
