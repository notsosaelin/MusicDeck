/**
 * The column browser (iTunes's, MusicBee's): Genres, Artists, Albums side by side, each narrowing the ones to its
 * right, and the chosen songs below in the usual table (sortable, multi-select, right-click). For Local files.
 */
import { FC, useEffect, useState } from "react";
import * as api from "../api";
import { Collection } from "../api";
import { TrackTable } from "./library";

export interface Columns {
  genres: [string, number][];
  artists: [string, number][];
  albums: [string, string, string, number][];
  tracks: api.Track[];
  count: number;
}

const Pane: FC<{ title: string; items: { key: string; name: string; sub?: string; n: number }[]; chosen: string; onChoose: (key: string) => void }> = ({
  title,
  items,
  chosen,
  onChoose,
}) => (
  <div className="cb-pane">
    <div className="cb-head">{title}</div>
    <div className="cb-list">
      <div className="cb-item" data-on={!chosen ? "" : undefined} onClick={() => onChoose("")}>
        <span>All ({items.length})</span>
      </div>
      {items.map((x) => (
        <div key={x.key} className="cb-item" data-on={chosen === x.key ? "" : undefined} onClick={() => onChoose(x.key)} title={x.sub ? `${x.name} · ${x.sub}` : x.name}>
          <span className="cb-name">
            {x.name}
            {x.sub && <span className="md-dim"> · {x.sub}</span>}
          </span>
          <span className="cb-n">{x.n}</span>
        </div>
      ))}
    </div>
  </div>
);

export const ColumnsPage: FC<{ id: string }> = ({ id }) => {
  const [genre, setGenre] = useState("");
  const [artist, setArtist] = useState("");
  const [album, setAlbum] = useState("");
  const [data, setData] = useState<Columns | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    api
      .libraryColumns(id, genre, artist, album)
      .then((r) => live && (r.ok ? (setData(r.data), setError("")) : setError(r.error)))
      .catch((e) => live && setError(String(e)));
    return () => {
      live = false;
    };
  }, [id, genre, artist, album]);
  const from: Collection = { id: `columns:${genre}:${artist}:${album}`, kind: "list", title: album ? "Album" : artist || genre || "All songs", subtitle: "", art: null };
  return (
    <>
      <h1 className="md-title">Column browser</h1>
      {error && <div className="md-dim">{error}</div>}
      {data && (
        <>
          <div className="cb-panes">
            <Pane title="Genres" chosen={genre} items={data.genres.map(([g, n]) => ({ key: g, name: g, n }))}
                  onChoose={(g) => { setGenre(g); setArtist(""); setAlbum(""); }} />
            <Pane title="Artists" chosen={artist} items={data.artists.map(([a, n]) => ({ key: a, name: a, n }))}
                  onChoose={(a) => { setArtist(a); setAlbum(""); }} />
            <Pane title="Albums" chosen={album} items={data.albums.map(([key, title, owner, n]) => ({ key, name: title, sub: artist ? undefined : owner, n }))}
                  onChoose={setAlbum} />
          </div>
          <div className="md-dim cb-count">
            {data.count.toLocaleString()} {data.count === 1 ? "song" : "songs"}
            {data.count > data.tracks.length ? ` (the first ${data.tracks.length.toLocaleString()} shown: choose a genre or artist to narrow it)` : ""}
          </div>
          <TrackTable id={id} tracks={data.tracks} from={from} />
        </>
      )}
    </>
  );
};

export const COLUMNS_CSS = `
.cb-panes { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
.cb-pane { display: flex; flex-direction: column; min-width: 0; border-radius: 6px; background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.06); }
.cb-head { padding: 8px 12px; font-size: 12px; letter-spacing: 0.06em; text-transform: uppercase; color: #8b929a;
  border-bottom: 1px solid rgba(255,255,255,0.06); }
.cb-list { height: 230px; overflow-y: auto; padding: 4px; scrollbar-width: thin; scrollbar-color: rgba(255,255,255,0.18) transparent; }
.cb-item { display: flex; align-items: center; gap: 8px; padding: 5px 8px; border-radius: 3px; font-size: 13px; color: #dcdedf; cursor: default; }
.cb-item:hover { background: rgba(255,255,255,0.06); }
.cb-item[data-on] { background: rgba(26,159,255,0.25); color: #fff; }
.cb-name { flex: 1; min-width: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.cb-n { font-size: 11px; color: #8b929a; font-variant-numeric: tabular-nums; }
.cb-count { margin-bottom: 6px; font-size: 13px; }
`;
