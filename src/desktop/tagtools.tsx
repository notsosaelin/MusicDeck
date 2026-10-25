/**
 * Tag tools and Organize files, as desktop dialogs: for the whole library (Library tools), an album or a
 * selection of songs. Both show exactly what would change (retag.py's and organize.py's plans) before anything is
 * written, and both can be undone (each file's tag edit; the last organize).
 */
import { Menu, MenuItem, ModalRoot, showContextMenu } from "@decky/ui";
import { CSSProperties, FC, useEffect, useState } from "react";
import * as api from "../api";
import { invalidateBrowserCache } from "../nav";
import { notify } from "../notify";
import { libraryTools } from "../components/TagEditor";
import { SIZES, desktopDialog } from "./dialog";

const input: CSSProperties = { height: 32, padding: "0 10px", borderRadius: 3, border: "1px solid #3d4450", background: "#1b2129",
                               color: "#fff", font: "inherit", fontSize: 14, minWidth: 0 };
const button: CSSProperties = { height: 32, padding: "0 16px", border: "none", borderRadius: 2, background: "#3d4450", color: "#fff",
                                font: "inherit", fontSize: 14, cursor: "pointer" };
const primary: CSSProperties = { ...button, background: "linear-gradient(to right, #47bfff 5%, #1a44c2 60%)" };
const dim: CSSProperties = { color: "#8b929a" };

type Op = "from_names" | "case" | "renumber" | "replace";
const OPS: [Op, string, string][] = [
  ["from_names", "Tags from file names", "Fill in fields from how the files are named."],
  ["case", "Fix capitalisation", "Title Case (small words like “of” and “the” stay small) or Sentence case."],
  ["renumber", "Renumber tracks", "1, 2, 3… in file-name order, on each disc."],
  ["replace", "Find and replace", "Change text in one field, in every song chosen."],
];
const FIELD_NAMES: Record<string, string> = { title: "Title", artist: "Artist", album: "Album", albumartist: "Album artist", track: "Track",
                                              disc: "Disc", year: "Year", genre: "Genre", composer: "Composer", label: "Label" };

const TagTools: FC<{ instanceId: string; ids: string[]; what: string; closeModal?: () => void }> = ({ instanceId, ids, what, closeModal }) => {
  const [op, setOp] = useState<Op>("case");
  const [pattern, setPattern] = useState("{track} - {title}");
  const [style, setStyle] = useState<"title" | "sentence">("title");
  const [fields, setFields] = useState<string[]>(["title", "artist", "album", "albumartist"]);
  const [field, setField] = useState("title");
  const [find, setFind] = useState("");
  const [to, setTo] = useState("");
  const [preview, setPreview] = useState<{ changes: { id: string; file: string; title: string; fields: Record<string, [string | number, string | number]> }[]; count: number } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const args = op === "from_names" ? { pattern } : op === "case" ? { style, fields } : op === "replace" ? { field, find, to } : {};
  const key = JSON.stringify([op, args]);
  // the preview follows what's typed, a moment after the typing stops
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      api.retagPreview(instanceId, ids, op, args).then((r) => {
        if (!live) return;
        if (r.ok) {
          setPreview(r.data);
          setError("");
        } else {
          setPreview(null);
          setError(r.error);
        }
      });
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [key]);
  const apply = async () => {
    setBusy(true);
    const r = await api.retagApply(instanceId, ids, op, args).catch((e) => ({ ok: false as const, error: String(e) }));
    setBusy(false);
    if (!r.ok) return setError(r.error);
    invalidateBrowserCache();
    notify("Tag tools", `${r.data.changed} ${r.data.changed === 1 ? "song" : "songs"} changed${r.data.failed ? `, ${r.data.failed} couldn't be` : ""}`);
    closeModal?.();
  };
  return (
    <ModalRoot bAllowFullSize closeModal={closeModal} onCancel={closeModal}>
      <div className="tt-box">
        <div style={dim}>{what}</div>
        <div className="tt-row">
          <select style={input} value={op} onChange={(e) => setOp(e.target.value as Op)}>
            {OPS.map(([id, label]) => (
              <option key={id} value={id}>{label}</option>
            ))}
          </select>
          <span style={dim}>{OPS.find(([id]) => id === op)?.[2]}</span>
        </div>
        {op === "from_names" && (
          <div className="tt-row">
            <input style={{ ...input, flex: 1 }} value={pattern} onChange={(e) => setPattern(e.target.value)} spellCheck={false} />
            <span style={{ ...dim, fontSize: 12 }}>{"{track} {artist} {title} {album} {year} {disc} {genre}"}</span>
          </div>
        )}
        {op === "case" && (
          <div className="tt-row">
            <select style={input} value={style} onChange={(e) => setStyle(e.target.value as "title" | "sentence")}>
              <option value="title">Title Case</option>
              <option value="sentence">Sentence case</option>
            </select>
            {["title", "artist", "album", "albumartist"].map((f) => (
              <label key={f} className="tt-check">
                <input type="checkbox" checked={fields.includes(f)} onChange={(e) => setFields(e.target.checked ? [...fields, f] : fields.filter((x) => x !== f))} />
                {FIELD_NAMES[f]}
              </label>
            ))}
          </div>
        )}
        {op === "replace" && (
          <div className="tt-row">
            <select style={input} value={field} onChange={(e) => setField(e.target.value)}>
              {["title", "artist", "album", "albumartist", "genre", "composer", "label"].map((f) => (
                <option key={f} value={f}>{FIELD_NAMES[f]}</option>
              ))}
            </select>
            <input style={{ ...input, flex: 1 }} value={find} onChange={(e) => setFind(e.target.value)} placeholder="Find" />
            <input style={{ ...input, flex: 1 }} value={to} onChange={(e) => setTo(e.target.value)} placeholder="Replace with" />
          </div>
        )}
        <div className="tt-preview">
          {error && <div style={{ color: "#e88" }}>{error}</div>}
          {!error && preview && !preview.count && <div style={dim}>Nothing would change.</div>}
          {preview?.changes.map((c) => (
            <div key={c.id} className="tt-change">
              <div className="tt-file">{c.file}</div>
              {Object.entries(c.fields).map(([f, [before, after]]) => (
                <div key={f} className="tt-field">
                  <span style={{ ...dim, width: 100 }}>{FIELD_NAMES[f] ?? f}</span>
                  <span className="tt-before">{String(before) || "empty"}</span>
                  <span style={dim}>→</span>
                  <span>{String(after) || "empty"}</span>
                </div>
              ))}
            </div>
          ))}
          {preview && preview.count > preview.changes.length && <div style={dim}>and {preview.count - preview.changes.length} more</div>}
        </div>
        <div className="tt-row" style={{ justifyContent: "flex-end" }}>
          <span style={{ ...dim, marginRight: "auto" }}>Each file's change can be undone from its Edit tags.</span>
          <button style={button} onClick={closeModal}>Cancel</button>
          <button style={preview?.count ? primary : button} disabled={!preview?.count || busy} onClick={apply}>
            {busy ? "Writing…" : preview?.count ? `Change ${preview.count} ${preview.count === 1 ? "song" : "songs"}` : "Change"}
          </button>
        </div>
      </div>
    </ModalRoot>
  );
};

const Organize: FC<{ instanceId: string; ids?: string[]; what: string; closeModal?: () => void }> = ({ instanceId, ids, what, closeModal }) => {
  const [pattern, setPattern] = useState(() => localStorage.getItem("musicdeck:organize-pattern") || "{albumartist}/{album}/{disc-}{track} {title}");
  const [preview, setPreview] = useState<api.OrganizePreview | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    const timer = window.setTimeout(() => {
      api.organizePreview(instanceId, pattern, ids ?? null).then((r) => {
        if (!live) return;
        r.ok ? (setPreview(r.data), setError("")) : (setPreview(null), setError(r.error));
      });
    }, 300);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
  }, [pattern]);
  const act = async (work: () => Promise<api.Result<{ moved: number; failed: number; error: string }>>, done: string) => {
    setBusy(true);
    const r = await work().catch((e) => ({ ok: false as const, error: String(e) }));
    setBusy(false);
    if (!r.ok) return setError(r.error);
    invalidateBrowserCache();
    notify("Organize files", `${r.data.moved} ${r.data.moved === 1 ? "song" : "songs"} ${done}${r.data.failed ? `; ${r.data.failed} couldn't be (${r.data.error})` : ""}`);
    closeModal?.();
  };
  return (
    <ModalRoot bAllowFullSize closeModal={closeModal} onCancel={closeModal}>
      <div className="tt-box">
        <div style={dim}>{what}: each song moved to where its tags say, inside the library folder it's in. Its lyrics file comes along, and a folder's cover when all its songs move together.</div>
        <div className="tt-row">
          <input style={{ ...input, flex: 1 }} value={pattern} spellCheck={false}
                 onChange={(e) => {
                   setPattern(e.target.value);
                   try {
                     localStorage.setItem("musicdeck:organize-pattern", e.target.value);
                   } catch {}
                 }} />
        </div>
        <div style={{ ...dim, fontSize: 12 }}>{"{albumartist} {artist} {album} {disc-} {track} {title} {year} {genre}  ·  / makes a folder"}</div>
        <div className="tt-preview">
          {error && <div style={{ color: "#e88" }}>{error}</div>}
          {preview && (
            <div style={{ ...dim, marginBottom: 8 }}>
              {preview.count} to move · {preview.same} already in place
              {preview.clash_count ? ` · ${preview.clash_count} left where they are (they'd get the same name as another song)` : ""}
              {preview.untagged ? ` · ${preview.untagged} without an artist or album left where they are (tag them first)` : ""}
            </div>
          )}
          {preview?.moves.map((m) => (
            <div key={m.id} className="tt-change">
              <div className="tt-file">{m.title}</div>
              <div className="tt-field">
                <span className="tt-before">{m.from}</span>
              </div>
              <div className="tt-field">
                <span style={dim}>→</span>
                <span>{m.to}</span>
              </div>
            </div>
          ))}
          {preview && preview.count > preview.moves.length && <div style={dim}>and {preview.count - preview.moves.length} more</div>}
        </div>
        <div className="tt-row" style={{ justifyContent: "flex-end" }}>
          {preview?.undo && (
            <button style={{ ...button, marginRight: "auto" }} disabled={busy} onClick={() => act(() => api.organizeUndo(instanceId), "put back")}>
              Undo the last organize
            </button>
          )}
          <button style={button} onClick={closeModal}>Cancel</button>
          <button style={preview?.count ? primary : button} disabled={!preview?.count || busy}
                  onClick={() => act(() => api.organizeApply(instanceId, pattern, ids ?? null), "moved")}>
            {busy ? "Moving…" : preview?.count ? `Move ${preview.count} ${preview.count === 1 ? "song" : "songs"}` : "Move"}
          </button>
        </div>
      </div>
    </ModalRoot>
  );
};

export const showTagTools = (instanceId: string, ids: string[], what: string) =>
  desktopDialog(<TagTools instanceId={instanceId} ids={ids} what={what} />, SIZES.tools, TAG_TOOLS_CSS);
export const showOrganize = (instanceId: string, ids: string[] | undefined, what: string) =>
  desktopDialog(<Organize instanceId={instanceId} ids={ids} what={what} />, { ...SIZES.tools, title: "Organize files" }, TAG_TOOLS_CSS);

export const TAG_TOOLS_CSS = `
.tt-box { display: flex; flex-direction: column; gap: 12px; height: calc(100vh - 96px); min-height: 360px; color: #dcdedf; }
.tt-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.tt-check { display: flex; align-items: center; gap: 6px; font-size: 14px; }
.tt-preview { flex: 1; min-height: 0; overflow-y: auto; padding: 10px; border-radius: 4px; background: rgba(0,0,0,0.25); }
.tt-change { padding: 8px 4px; border-bottom: 1px solid rgba(255,255,255,0.05); }
.tt-file { font-size: 13px; color: #fff; margin-bottom: 4px; }
.tt-field { display: flex; gap: 10px; font-size: 13px; padding: 1px 0; }
.tt-before { color: #8b929a; text-decoration: line-through; }
`;

/** While desktop Steam shows: an album's menu offers Tag tools and Organize files. Returns the teardown. */
export function installTagTools(): () => void {
  const songs = async (instanceId: string, album: api.Collection) => {
    const r = await api.openCollection(instanceId, album.kind, album.id);
    return r.ok ? r.data.tracks.map((t) => t.id) : [];
  };
  libraryTools.tagTools = async (instanceId, album) => showTagTools(instanceId, await songs(instanceId, album), album.title);
  libraryTools.organize = async (instanceId, album) => showOrganize(instanceId, await songs(instanceId, album), album.title);
  libraryTools.convert = async (instanceId, album) => showConvert(instanceId, await songs(instanceId, album), window);
  return () => {
    delete libraryTools.tagTools;
    delete libraryTools.organize;
    delete libraryTools.convert;
  };
}

const CONVERT_FORMATS: [string, string][] = [
  ["mp3", "MP3 (320 kbps): plays everywhere"],
  ["aac", "AAC (256 kbps, M4A): phones, iPods"],
  ["opus", "Opus (160 kbps): smallest, as good"],
  ["flac", "FLAC: nothing lost"],
];

/** Convert: the format, then the songs are converted in the background; a toast says where they went. */
export const showConvert = (instanceId: string, ids: string[], target: EventTarget) =>
  showContextMenu(
    <Menu label={`Convert ${ids.length} ${ids.length === 1 ? "song" : "songs"} to`}>
      {CONVERT_FORMATS.map(([fmt, label]) => (
        <MenuItem key={fmt} onSelected={async () => {
          const r = await api.convertSongs(instanceId, ids, fmt).catch((e) => ({ ok: false as const, error: String(e) }));
          notify("Convert", r.ok ? `Converting ${r.data.count} ${r.data.count === 1 ? "song" : "songs"} into ${r.data.folder}` : r.error);
        }}>
          {label}
        </MenuItem>
      ))}
    </Menu>,
    target,
  );
