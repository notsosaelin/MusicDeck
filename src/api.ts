import { callable } from "@decky/api";

// Mirrors py_modules/musicdeck/models.py
export interface Track {
  id: string;
  title: string;
  artist: string;
  album: string;
  duration: number;
  art: string | null;
  album_id?: string | null;
  artist_id?: string | null;
  starred?: boolean;
  /** ReplayGain: dB adjustments and linear peaks, or null when the source has none */
  gain?: {
    track: number;
    album: number;
    track_peak: number;
    album_peak: number;
  } | null;
  /** an endless stream (radio station): no length, no seeking */
  live?: boolean;
}

export type Feature = "star" | "scrobble" | "lyrics" | "radio" | "queue_sync" | "playlists";

export interface LyricLine {
  /** seconds; null for unsynced lyrics */
  t: number | null;
  text: string;
}

export interface Lyrics {
  synced: boolean;
  lines: LyricLine[];
  source: string;
}

export interface PlaybackSettings {
  levelling: "off" | "track" | "album";
  scrobble: boolean;
  queue_sync: boolean;
  resume: boolean;
  autoplay?: boolean;
}

export interface Session {
  queue: { instanceId: string; track: Track }[];
  index: number;
  position: number;
  from_device?: string | null;
}

export type CollectionKind = "album" | "artist" | "playlist" | "folder" | "list";

export interface Collection {
  id: string;
  kind: CollectionKind;
  title: string;
  subtitle: string;
  art: string | null;
}

export interface Page {
  title: string;
  subtitle: string;
  art: string | null;
  collections: Collection[];
  tracks: Track[];
  /** more titled track lists, e.g. "Videos" in search results */
  extra?: { title: string; tracks: Track[] }[];
  /** show songs/videos above albums/artists/playlists (search results) */
  tracks_first?: boolean;
}

/** A home row: collections and/or tracks (e.g. YouTube Music's "Quick picks") */
export interface Section {
  title: string;
  items: Collection[];
  tracks?: Track[];
}

export interface EqSettings {
  enabled: boolean;
  preset: string;
  gains: number[];
  preamp: number;
}

export type QamMode = "replace" | "separate";

export interface ServiceInfo {
  id: string;
  type: string;
  name: string;
  config: Record<string, any>;
  has_credentials: boolean;
  status: Record<string, any>;
  features: Feature[];
}

export interface ServiceType {
  type: string;
  display_name: string;
}

export interface PluginState {
  services: ServiceInfo[];
  active_service: string | null;
  qam_mode: QamMode;
  volume: number;
  eq: EqSettings;
  playback: PlaybackSettings;
  debug_log: boolean;
  debug_log_path: string;
  user_home: string;
  server: { base: string; key: string };
  service_types: ServiceType[];
  planned_types: ServiceType[];
  /** linked ListenBrainz account ("" when not linked) and listens waiting to be sent */
  listenbrainz: { user: string; pending: number };
}

export type Result<T> = { ok: true; data: T } | { ok: false; error: string };

export interface ServiceDraft {
  id?: string;
  type: string;
  name: string;
  config: Record<string, any>;
}

export const getState = callable<[], PluginState>("get_state");
export const setSetting = callable<[key: string, value: any], void>("set_setting");
export const saveService = callable<[service: ServiceDraft], Result<ServiceInfo>>("save_service");
export const removeService = callable<[id: string], void>("remove_service");
export const setActiveService = callable<[id: string], void>("set_active_service");
export const testService = callable<[id: string], Result<string>>("test_service");
export const rescan = callable<[id: string], void>("rescan");
export const getHome = callable<[id: string], Result<Section[]>>("home");
export const openCollection = callable<[id: string, kind: CollectionKind, collectionId: string], Result<Page>>(
  "open_collection",
);
export const search = callable<[id: string, query: string], Result<Page>>("search");

export const star = callable<[id: string, trackId: string, starred: boolean], Result<null>>("star");
export const scrobble = callable<[id: string, trackId: string, submission: boolean, track: Track], Result<null>>(
  "scrobble",
);
export const getLyrics = callable<[id: string, track: Track], Result<Lyrics | null>>("get_lyrics");
export const getRadio = callable<[id: string, track: Track], Result<Track[]>>("radio");
export const getUserPlaylists = callable<[id: string], Result<Collection[]>>("user_playlists");
export const addToPlaylist = callable<[id: string, playlistId: string, trackIds: string[]], Result<null>>(
  "add_to_playlist",
);
export const saveSession = callable<[session: Session], void>("save_session");
export const loadSession = callable<[], Session | null>("load_session");
export const streamInfo = callable<[id: string, trackId: string], Result<{ hls: boolean; gain?: Track["gain"] }>>(
  "stream_info",
);
export const forgetStream = callable<[id: string, trackId: string], void>("forget_stream");
export const logEvents = callable<[entries: { t: number; event: string; data?: Record<string, unknown> }[]], void>(
  "log_events",
);

// MusicDeck playlists (stored on the device, per source)
export const localPlaylists = callable<[id: string], Collection[]>("local_playlists");
export const createPlaylist = callable<[id: string, name: string, tracks: Track[]], Collection>("create_playlist");
export const saveAsPlaylist = callable<[id: string, kind: CollectionKind, collectionId: string], Result<Collection>>(
  "save_as_playlist",
);
export const playlistAdd = callable<[playlistId: string, tracks: Track[]], number>("playlist_add");
export const playlistRemove = callable<[playlistId: string, trackId: string], void>("playlist_remove");
export const playlistRename = callable<[playlistId: string, name: string], void>("playlist_rename");
export const playlistDelete = callable<[playlistId: string], void>("playlist_delete");
export const playlistSetArt = callable<[playlistId: string, imagePath: string], Result<null>>("playlist_set_art");
export const playlistClearArt = callable<[playlistId: string], void>("playlist_clear_art");
export const isLocalPlaylist = (id: string) => id.startsWith("md:");
export const youtubeUpdate = callable<[id: string], Result<string>>("youtube_update");
export const qobuzLoginStart = callable<[], Result<{ login_id: string; url: string }>>("qobuz_login_start");
export const qobuzLoginPoll = callable<
  [loginId: string, service: ServiceDraft],
  {
    status: "starting" | "waiting" | "done" | "error";
    error?: string;
    service?: ServiceInfo;
  }
>("qobuz_login_poll");
export const qobuzLoginCancel = callable<[loginId: string], void>("qobuz_login_cancel");
export const spotifyLoginStart = callable<[clientId: string], Result<{ login_id: string; url: string }>>(
  "spotify_login_start",
);
export const spotifyLoginPoll = callable<
  [loginId: string, service: ServiceDraft],
  {
    status: "waiting" | "done" | "error";
    error?: string;
    url?: string;
    service?: ServiceInfo;
  }
>("spotify_login_poll");
export const spotifyLoginCancel = callable<[loginId: string], void>("spotify_login_cancel");
export const tidalLoginStart = callable<[], Result<{ login_id: string; url: string }>>("tidal_login_start");
export const tidalLoginPoll = callable<
  [loginId: string, service: ServiceDraft],
  { status: "waiting" | "done" | "error"; error?: string; service?: ServiceInfo }
>("tidal_login_poll");
export const tidalLoginCancel = callable<[loginId: string], void>("tidal_login_cancel");
export const jellyfinLoginStart = callable<[url: string], Result<{ login_id: string; code: string }>>(
  "jellyfin_login_start",
);
export const jellyfinLoginPoll = callable<
  [loginId: string, service: ServiceDraft],
  { status: "starting" | "waiting" | "done" | "error"; error?: string; service?: ServiceInfo }
>("jellyfin_login_poll");
export const jellyfinLoginCancel = callable<[loginId: string], void>("jellyfin_login_cancel");
export const onAir = callable<[id: string, trackId: string], Result<string | null>>("on_air");
export const setListenBrainz = callable<[token: string], Result<{ user: string }>>("set_listenbrainz");
