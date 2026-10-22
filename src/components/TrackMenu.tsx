import { openFilePicker } from "@decky/api";
import { ConfirmModal, Menu, MenuItem, MenuSeparator, showContextMenu, showModal } from "@decky/ui";
import {
  Collection,
  Track,
  addToPlaylist,
  createPlaylist,
  getUserPlaylists,
  isLocalPlaylist,
  localPlaylists,
  playlistAdd,
  playlistClearArt,
  playlistDelete,
  playlistRemove,
  playlistRename,
  playlistSetArt,
  saveAsPlaylist,
  setPlayed,
  star,
} from "../api";
import { invalidateBrowserCache, navigation, openInLibrary, popView } from "../nav";
import { notify } from "../notify";
import { SleepChoice, setSleepTimer, sleepTimerLabel } from "../sleep";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../games";
import { hasFeature, player } from "../player";
import { pluginState } from "../store";
import { NameModal } from "./NameModal";

type Item = { instanceId: string; track: Track };

export async function toggleStar(instanceId: string, track: Track) {
  const starred = !track.starred;
  const r = await star(instanceId, track.id, starred);
  if (!r.ok) {
    notify("Couldn't update favourites", r.error, { instanceId, track });
    return;
  }
  player.updateTrack(instanceId, track.id, { starred });
  // lists showing this track (and the Favourites list) are now stale
  invalidateBrowserCache();
  notify(starred ? "Added to favourites" : "Removed from favourites", track.title, { instanceId, track });
}

function newPlaylist(item: Item) {
  showModal(
    <NameModal
      title="New playlist"
      action="Create"
      onSubmit={async (name) => {
        await createPlaylist(item.instanceId, name, [item.track]);
        invalidateBrowserCache();
        notify(`Added to ${name}`, item.track.title, item);
      }}
    />,
  );
}

/**
 * Sources with server-side playlists (Navidrome) add there; everything else uses MusicDeck's own
 * playlists, kept on the device.
 */
async function showPlaylistPicker(item: Item, parent: EventTarget) {
  const server = hasFeature(item.instanceId, "playlists");
  let playlists: Collection[];
  if (server) {
    const r = await getUserPlaylists(item.instanceId);
    if (!r.ok) {
      notify("Couldn't load playlists", r.error, item);
      return;
    }
    playlists = r.data;
  } else {
    playlists = await localPlaylists(item.instanceId);
  }
  showContextMenu(
    <Menu label="Add to playlist">
      {!server && <MenuItem onSelected={() => newPlaylist(item)}>New playlist…</MenuItem>}
      {!server && playlists.length > 0 && <MenuSeparator />}
      {playlists.map((p) => (
        <MenuItem
          key={p.id}
          onSelected={async () => {
            if (server) {
              const res = await addToPlaylist(item.instanceId, p.id, [item.track.id]);
              if (!res.ok) return notify("Couldn't add to playlist", res.error, item);
            } else {
              const added = await playlistAdd(p.id, [item.track]);
              if (!added) return notify(`Already in ${p.title}`, item.track.title, item);
            }
            invalidateBrowserCache();
            notify(`Added to ${p.title}`, item.track.title, item);
          }}
        >
          {p.title}
        </MenuItem>
      ))}
    </Menu>,
    parent,
  );
}

/** Episodes and books: "Played" shows in their second line (see podcasts.py), and lists refresh. */
async function markPlayed(instanceId: string, track: Track, played: boolean) {
  const done = await setPlayed(instanceId, track, played);
  if (!done.ok) return notify(track.title, done.error, { instanceId, track });
  invalidateBrowserCache();
  notify(played ? "Marked as played" : "Marked as unplayed", track.title, { instanceId, track });
}

/** Context about where the menu was opened, for page-specific items. */
export interface MenuContext {
  /** the MusicDeck playlist being viewed, if any */
  playlistId?: string;
  /** a track in the Queue: where it plays, and how to pick it up and move it with the d-pad */
  queue?: { position: number; startMoving: () => void };
}

/** Moving a queued track (positions in play order, as the Queue lists them). */
function queueItems(position: number, startMoving: () => void) {
  const order = player.playOrder;
  const current = order.indexOf(player.state.get().index);
  const last = order.length - 1;
  // right after the playing track (removing it from above that track shifts the target up by one)
  const next = position > current ? current + 1 : current;
  return [
    <MenuItem key="move" onSelected={startMoving}>
      Move…
    </MenuItem>,
    position !== current && position !== next && (
      <MenuItem key="next" onSelected={() => player.move(position, next)}>
        Play next
      </MenuItem>
    ),
    position > 0 && (
      <MenuItem key="up" onSelected={() => player.move(position, position - 1)}>
        Move up
      </MenuItem>
    ),
    position < last && (
      <MenuItem key="down" onSelected={() => player.move(position, position + 1)}>
        Move down
      </MenuItem>
    ),
    position < last && (
      <MenuItem key="end" onSelected={() => player.move(position, last)}>
        Move to the end
      </MenuItem>
    ),
  ];
}

/**
 * Everything you can do with a track, as a native context menu. Items only appear when the track's
 * source supports them, so the menu stays short.
 */
const SLEEP_MINUTES = [15, 30, 45, 60, 90];

function showSleepMenu(parent: EventTarget) {
  const choose = (choice: SleepChoice, message: string) => {
    setSleepTimer(choice);
    notify("Sleep timer", message);
  };
  showContextMenu(
    <Menu label="Sleep timer">
      {SLEEP_MINUTES.map((m) => (
        <MenuItem key={m} onSelected={() => choose(m, `Pausing in ${m} minutes`)}>
          {m} minutes
        </MenuItem>
      ))}
      <MenuItem onSelected={() => choose("track", "Pausing when this track ends")}>End of this track</MenuItem>
      {sleepTimerLabel() && (
        <>
          <MenuSeparator />
          <MenuItem tone="destructive" onSelected={() => choose(null, "Off")}>
            Turn off
          </MenuItem>
        </>
      )}
    </Menu>,
    parent,
  );
}

export function showTrackMenu(instanceId: string, track: Track, parent?: EventTarget | null, ctx: MenuContext = {}) {
  const target = parent ?? window;
  const item = { instanceId, track };
  const isCurrent = player.current?.instanceId === instanceId && player.current.track.id === track.id;
  // the Library tab browses the active source, so only offer "Go to" for its tracks
  const browsable = pluginState.get()?.active_service === instanceId;
  showContextMenu(
    <Menu label={track.title}>
      {/* in the Queue these move the track; elsewhere they add it */}
      {ctx.queue && queueItems(ctx.queue.position, ctx.queue.startMoving)}
      {!isCurrent && !ctx.queue && (
        <MenuItem
          onSelected={() => {
            player.enqueue(instanceId, [track], true);
            notify("Playing next", track.title, item);
          }}
        >
          Play next
        </MenuItem>
      )}
      {!isCurrent && !ctx.queue && (
        <MenuItem
          onSelected={() => {
            player.enqueue(instanceId, [track]);
            notify("Added to queue", track.title, item);
          }}
        >
          Add to queue
        </MenuItem>
      )}
      {hasFeature(instanceId, "radio") && (
        <MenuItem onSelected={() => player.startRadio(instanceId, track)}>Start radio</MenuItem>
      )}
      {hasFeature(instanceId, "progress") && (
        <MenuItem onSelected={() => markPlayed(instanceId, track, !track.artist.endsWith("Played"))}>
          {track.artist.endsWith("Played") ? "Mark as unplayed" : "Mark as played"}
        </MenuItem>
      )}
      {hasFeature(instanceId, "star") && (
        <MenuItem onSelected={() => toggleStar(instanceId, track)}>
          {track.starred ? "Remove from favourites" : "Add to favourites"}
        </MenuItem>
      )}
      <MenuItem onSelected={() => showPlaylistPicker(item, target)}>Add to playlist…</MenuItem>
      {ctx.playlistId && (
        <MenuItem
          tone="destructive"
          onSelected={async () => {
            await playlistRemove(ctx.playlistId!, track.id);
            invalidateBrowserCache();
          }}
        >
          Remove from this playlist
        </MenuItem>
      )}
      {isCurrent && <MenuSeparator />}
      {isCurrent && (
        <MenuItem onSelected={() => showSleepMenu(target)}>
          {sleepTimerLabel() ? `Sleep timer · ${sleepTimerLabel()}` : "Sleep timer…"}
        </MenuItem>
      )}
      {browsable && (track.album_id || track.artist_id) && <MenuSeparator />}
      {browsable && track.album_id && (
        <MenuItem
          onSelected={() =>
            openInLibrary({
              id: track.album_id!,
              kind: "album",
              title: track.album,
              subtitle: track.artist,
              art: track.art,
            })
          }
        >
          {/* an episode's "album" is its show, a book file's the book: named rather than called an album */}
          {hasFeature(instanceId, "progress") ? `Go to ${track.album}` : "Go to album"}
        </MenuItem>
      )}
      {browsable && track.artist_id && (
        <MenuItem
          onSelected={() =>
            openInLibrary({ id: track.artist_id!, kind: "artist", title: track.artist, subtitle: "", art: null })
          }
        >
          Go to artist
        </MenuItem>
      )}
    </Menu>,
    target,
  );
}

// FileSelectionType.FILE from @decky/api (a const enum, which isolated builds can't import)
const SELECT_FILE = 0;
const IMAGE_EXTENSIONS = ["jpg", "jpeg", "png", "webp", "gif"];

/** Pick a picture for a MusicDeck playlist; it's copied into MusicDeck, so the original can move. */
async function pickArtwork(c: Collection) {
  const home = pluginState.get()?.user_home || "/home";
  let path: string;
  try {
    path = (await openFilePicker(SELECT_FILE, home, true, true, undefined, IMAGE_EXTENSIONS)).realpath;
  } catch {
    return; // picker cancelled
  }
  const r = await playlistSetArt(c.id, path);
  if (!r.ok) {
    notify("Couldn't set artwork", r.error);
    return;
  }
  invalidateBrowserCache();
  notify("Artwork updated", c.title);
}

/** Saves a source's playlist or album as a MusicDeck playlist (kept on the device, editable). */
export async function saveCollection(instanceId: string, c: Collection) {
  const r = await saveAsPlaylist(instanceId, c.kind, c.id);
  if (!r.ok) {
    notify("Couldn't save playlist", r.error);
    return;
  }
  invalidateBrowserCache();
  notify("Saved to your playlists", c.title);
}

/** "Play when <game> starts" while a game runs (album and playlist rows). */
function gameMusicItem(instanceId: string, c: Collection) {
  const game = runningGame.get();
  if (!game || !canPlayWithGame(c)) return null;
  const on = musicFor(game.appid, instanceId)?.collection.id === c.id;
  return (
    <MenuItem onSelected={() => toggleGameMusic(instanceId, c)}>
      {on ? `Don't play with ${game.name}` : `Play when ${game.name} starts`}
    </MenuItem>
  );
}

/** Options for a collection row: save remote playlists, manage MusicDeck playlists, play with a game. */
export function showCollectionMenu(instanceId: string, c: Collection, parent?: EventTarget | null) {
  const target = parent ?? window;
  if (isLocalPlaylist(c.id)) {
    showContextMenu(
      <Menu label={c.title}>
        {gameMusicItem(instanceId, c)}
        <MenuItem
          onSelected={() =>
            showModal(
              <NameModal
                title="Rename playlist"
                initial={c.title}
                action="Rename"
                onSubmit={async (name) => {
                  await playlistRename(c.id, name);
                  invalidateBrowserCache();
                }}
              />,
            )
          }
        >
          Rename…
        </MenuItem>
        <MenuItem onSelected={() => pickArtwork(c)}>Set artwork…</MenuItem>
        {c.art?.startsWith("mdart:") && (
          <MenuItem
            onSelected={async () => {
              await playlistClearArt(c.id);
              invalidateBrowserCache();
            }}
          >
            Use default artwork
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem
          tone="destructive"
          onSelected={() =>
            showModal(
              <ConfirmModal
                strTitle={`Delete ${c.title}?`}
                strDescription="The playlist is removed from MusicDeck. The music itself isn't affected."
                strOKButtonText="Delete"
                bDestructiveWarning
                onOK={async () => {
                  await playlistDelete(c.id);
                  invalidateBrowserCache();
                  // if we're looking at it, step back out
                  const views = navigation.get().stacks.library;
                  const top = views[views.length - 1];
                  if (top?.type === "collection" && top.collection.id === c.id) popView("library");
                }}
              />,
            )
          }
        >
          Delete…
        </MenuItem>
      </Menu>,
      target,
    );
    return;
  }
  if (c.kind === "playlist" || c.kind === "album") {
    showContextMenu(
      <Menu label={c.title}>
        {gameMusicItem(instanceId, c)}
        {!hasFeature(instanceId, "playlists") && (
          <MenuItem onSelected={() => saveCollection(instanceId, c)}>Save to your playlists</MenuItem>
        )}
      </Menu>,
      target,
    );
  }
}
