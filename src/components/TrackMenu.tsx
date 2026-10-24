import { openFilePicker } from "@decky/api";
import { ReactNode } from "react";
import { ConfirmModal, Menu, MenuItem, MenuSeparator, showContextMenu, showModal } from "@decky/ui";
import {
  Collection,
  LikeWords,
  Track,
  addToPlaylist,
  createPlaylist,
  createServicePlaylist,
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
  getTags,
  setPlayed,
  star,
  copyTo,
  onDevice,
} from "../api";
import { invalidateBrowserCache, navigation, openInLibrary, popView } from "../nav";
import { notify } from "../notify";
import { SleepChoice, setSleepTimer, sleepTimerLabel } from "../sleep";
import { canPlayWithGame, musicFor, runningGame, toggleGameMusic } from "../games";
import { hasFeature, player } from "../player";
import { pluginState } from "../store";
import { NameModal } from "./NameModal";
import { albumManageItems, artistManageItems, editTags, saveTrackLyrics } from "./TagEditor";

type Item = { instanceId: string; track: Track };

/** A source's words for a favourite (Spotify's Liked Songs, SoundCloud's likes). */
export const likeWords = (instanceId: string): LikeWords =>
  pluginState.get()?.services.find((s) => s.id === instanceId)?.like_words ?? {
    add: "Add to favourites",
    remove: "Remove from favourites",
    added: "Added to favourites",
    removed: "Removed from favourites",
  };

export async function toggleStar(instanceId: string, track: Track) {
  const starred = !track.starred;
  const words = likeWords(instanceId);
  const r = await star(instanceId, track.id, starred, track);
  if (!r.ok) {
    notify(`Couldn't ${(starred ? words.add : words.remove).toLowerCase()}`, r.error, { instanceId, track });
    return;
  }
  player.updateTrack(instanceId, track.id, { starred });
  // lists showing this track (and the Favourites list) are now stale
  invalidateBrowserCache();
  notify(starred ? words.added : words.removed, track.title, { instanceId, track });
}

/** A new playlist with the track in it: on the source when it keeps playlists, else MusicDeck's own. */
function newPlaylist(item: Item, server: boolean) {
  showModal(
    <NameModal
      title="New playlist"
      action="Create"
      onSubmit={async (name) => {
        if (server) {
          const made = await createServicePlaylist(item.instanceId, name, [item.track.id]);
          if (!made.ok) return notify("Couldn't make the playlist", made.error, item);
        } else {
          await createPlaylist(item.instanceId, name, [item.track]);
        }
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
export async function showPlaylistPicker(item: Item, parent: EventTarget) {
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
      <MenuItem onSelected={() => newPlaylist(item, server)}>New playlist</MenuItem>
      {playlists.length > 0 && <MenuSeparator />}
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

/**
 * Copying between the Deck and what's plugged in (Local files only, services/shelf.py): the Deck's
 * music to each player, phone or stick; a device's to the Deck. A CD is ripped from its album page.
 */
function copyItems(instanceId: string, kind: string, id: string): ReactNode[] {
  const state = pluginState.get();
  if (state?.services.find((s) => s.id === instanceId)?.type !== "local") return [];
  const copy = async (target: string) => {
    const r = await copyTo(instanceId, kind, id, target);
    notify(r.ok ? "Local files" : "Couldn't copy", r.ok ? r.data : r.error);
  };
  if (onDevice(id)) {
    const device = state.devices.find((d) => id.startsWith(`dv~${d.id}~`));
    return device && device.kind !== "cd"
      ? [
          <MenuItem key="copy-deck" onSelected={() => copy("deck")}>
            Copy to this Deck
          </MenuItem>,
        ]
      : [];
  }
  return state.devices
    .filter((d) => d.kind !== "cd")
    .map((d) => (
      <MenuItem key={`copy-${d.id}`} onSelected={() => copy(d.id)}>
        Copy to {d.name}
      </MenuItem>
    ));
}

/** Whether an album, artist or playlist can be copied somewhere (to a device, or from one to the Deck). */
export const canCopy = (instanceId: string, c: Collection) =>
  (c.kind === "album" || c.kind === "artist" || c.kind === "playlist") &&
  copyItems(instanceId, c.kind, c.id).length > 0;

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
      Move
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
          {track.starred ? likeWords(instanceId).remove : likeWords(instanceId).add}
        </MenuItem>
      )}
      <MenuItem onSelected={() => showPlaylistPicker(item, target)}>Add to playlist</MenuItem>
      {copyItems(instanceId, "track", track.id)}
      {/* a device's files aren't the Deck's to change */}
      {hasFeature(instanceId, "tags") && !onDevice(track.id) && (
        <MenuItem onSelected={() => editTags(instanceId, "track", track.id, track.title)}>Edit tags</MenuItem>
      )}
      {hasFeature(instanceId, "tags") && !onDevice(track.id) && (
        <MenuItem onSelected={() => saveTrackLyrics(instanceId, track.id, track.title)}>Save lyrics</MenuItem>
      )}
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
          {sleepTimerLabel() ? `Sleep timer · ${sleepTimerLabel()}` : "Sleep timer"}
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
          Rename
        </MenuItem>
        <MenuItem onSelected={() => pickArtwork(c)}>Set artwork</MenuItem>
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
                strDescription="The music itself isn't affected."
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
          Delete
        </MenuItem>
      </Menu>,
      target,
    );
    return;
  }
  if (c.kind === "artist" && hasFeature(instanceId, "artwork")) {
    const copies = copyItems(instanceId, "artist", c.id);
    const manage = onDevice(c.id) ? [] : artistManageItems(instanceId, c);
    showContextMenu(
      <Menu label={c.title}>
        {copies}
        {copies.length > 0 && manage.length > 0 && <MenuSeparator />}
        {manage}
      </Menu>,
      target,
    );
    return;
  }
  if (c.kind === "playlist" || c.kind === "album") {
    const show = (manage: ReactNode[]) =>
      showContextMenu(
        <Menu label={c.title}>
          {gameMusicItem(instanceId, c)}
          {!hasFeature(instanceId, "playlists") && (
            <MenuItem onSelected={() => saveCollection(instanceId, c)}>Save to your playlists</MenuItem>
          )}
          {copyItems(instanceId, c.kind, c.id)}
          {manage.length > 0 && <MenuSeparator />}
          {manage}
        </Menu>,
        target,
      );
    // a local album: its tags and picture too (whether it has a picture of its own decides "Use the files'")
    if (
      c.kind === "album" &&
      !onDevice(c.id) &&
      (hasFeature(instanceId, "tags") || hasFeature(instanceId, "artwork"))
    ) {
      getTags(instanceId, "album", c.id).then((r) => show(albumManageItems(instanceId, c, r.ok ? r.data : null)));
    } else {
      show([]);
    }
  }
}
