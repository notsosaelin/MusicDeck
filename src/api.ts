import { callable } from "@decky/api";
import type { OwnedSoundtrack } from "./steam";

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

export type Feature =
  "star" | "scrobble" | "lyrics" | "radio" | "queue_sync" | "playlists" | "progress" | "browse" | "tags" | "artwork";

export interface LyricLine {
  /** seconds; null for unsynced lyrics */
  t: number | null;
  text: string;
}

export interface Lyrics {
  synced: boolean;
  lines: LyricLine[];
  /** the lyrics site or source they came from; "" when that's the playing source itself */
  source: string;
  /** what the lines are, when not lyrics: "Chapters" for an audiobook */
  title?: string;
}

export interface PlaybackSettings {
  levelling: "off" | "track" | "album";
  scrobble: boolean;
  queue_sync: boolean;
  resume: boolean;
  autoplay?: boolean;
  /** pause while a game runs, resume when it closes */
  game_pause?: boolean;
  /** the share of its volume music keeps while a game is in front; 1 = as is (src/games.ts) */
  game_duck?: number;
  /** seconds the end of a track and the start of the next overlap; 0 = off (tracks still follow gaplessly) */
  crossfade?: number;
}

export interface Session {
  queue: { instanceId: string; track: Track }[];
  index: number;
  position: number;
  from_device?: string | null;
}

/** device: a player, phone or stick plugged in, in Local files' Devices tab (services/shelf.py) */
export type CollectionKind = "album" | "artist" | "playlist" | "folder" | "list" | "device";

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
  /**
   * a button in the header. url: a steam:// command (run in Steam, e.g. installing a soundtrack) or a
   * web page (opened in Steam's browser); call: something the source does (collectionAction)
   */
  action?: { label: string; url?: string; call?: string } | null;
  /** shown above the content, e.g. why there's nothing to play yet */
  notice?: string | null;
  /** MusicDeck playlists of a source that can download: whether it's downloaded, and how far */
  offline?: OfflineState | null;
  /**
   * an album, artist or playlist the source can keep in the user's library (saveToLibrary): whether it's
   * there; style "library" (albums and playlists saved, artists followed) or "favourite" (a server's)
   */
  save?: SaveState | null;
}

export interface SaveState {
  on: boolean;
  style: "library" | "favourite";
}

export interface OfflineState {
  on: boolean;
  saved: number;
  total: number;
}

/** Every download together (offline.py), for Settings */
export interface DownloadStatus {
  tracks: number;
  bytes: number;
  pending: number;
  failed: number;
  /** the track downloading now */
  current: string | null;
  /** why downloading stopped early ("Not enough free space"), or "" */
  stopped: string;
}

/** A home row: collections and/or tracks (e.g. YouTube Music's "Quick picks") */
export interface Section {
  title: string;
  items: Collection[];
  tracks?: Track[];
  /**
   * How the media center shows it (models.py): a row of cards ("shelf", the default); a row of music
   * per item ("rows": genres, moods, charts); a grid of names ("tiles"); every card, wrapping ("grid");
   * tracks as covers too ("covers": stations)
   */
  display?: "shelf" | "rows" | "tiles" | "grid" | "covers";
  /** in place of a section: a note for a tab with nothing in it yet */
  notice?: string;
}

/**
 * What a source plays, as Now Playing shows it: songs (lyrics, the queue), radio (what's on air, the
 * station), podcast episodes and books (notes, episodes or chapters, skip and speed), concerts (setlist)
 */
/** How Now Playing shows a source's tracks; "library" is music from files with facts about each (Info). */
export type NowPlayingKind = "music" | "library" | "radio" | "podcast" | "book" | "concert";

/** More about the playing track (MusicService.details) */
export interface TrackDetails {
  /** show notes, a book's blurb, a concert's notes: a paragraph a line */
  notes?: string;
  facts?: [string, string][];
  /** similar stations, the show's other episodes */
  related?: Section | null;
}

/** One of a source's own tabs in the media center */
export interface CenterTabInfo {
  id: string;
  title: string;
  /** where it sits on Browse: the user's own music, what they've listened to, ways into the catalogue,
   * the biggest genres, folders on the Deck */
  group?: "yours" | "listening" | "explore" | "genres" | "files";
  /** what's in it ("128 albums") */
  subtitle?: string;
  /** art ids of a few of its covers or pictures, for its tile */
  art?: string[];
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
  /** its tracks can be saved for offline listening */
  downloadable: boolean;
  /** can play on speakers and TVs (not Spotify) */
  castable: boolean;
  /** its tabs in the media center, between Now Playing and Search */
  tabs: CenterTabInfo[];
  /** how the media center's Now Playing shows its tracks */
  now_playing: NowPlayingKind;
  /** the track menu's words for a favourite, in the source's own ("Save to Liked Songs", "Like") */
  like_words?: LikeWords;
}

export interface ServiceType {
  type: string;
  display_name: string;
  /** where "Add a source" lists it: your files and servers, streaming services, radio and podcasts */
  group: "yours" | "streaming" | "radio" | "sharing" | "other";
}

/** The media center's choices (settings.DEFAULTS["center"]) */
export interface CenterSettings {
  open_on: "last" | "playing" | "home" | "library";
  background: "art" | "plain";
  cards: "small" | "medium" | "large";
  lyrics: "small" | "medium" | "large";
  show_library: boolean;
  /** the source's genres, moods and charts in rows on Home */
  show_browse: boolean;
  /** minutes idle on Now Playing before the screen goes off; 0 = never */
  idle_off: 0 | 1 | 2 | 5 | 10;
}

/** A music player, stick ("usb"), phone ("mtp") or audio CD plugged in: shown in Local files, under Devices. */
export interface DeviceInfo {
  id: string;
  name: string;
  /** usb: a player or stick; mtp: a phone; ios: an iPhone or iPod touch (read only); disc: a data CD/DVD */
  kind: "usb" | "mtp" | "cd" | "ipod" | "ios" | "disc";
  status: Record<string, any>;
}

export interface PluginState {
  /** MusicDeck's version (package.json) */
  version: string;
  services: ServiceInfo[];
  devices: DeviceInfo[];
  active_service: string | null;
  qam_mode: QamMode;
  volume: number;
  /** the EQ that applies now: the output's own when each output has one, else the one EQ */
  eq: EqSettings;
  eq_per_output: boolean;
  /** the output sound plays on (speakers, headphones, a Bluetooth device...), when PipeWire says */
  output: { id: string; name: string } | null;
  playback: PlaybackSettings;
  debug_log: boolean;
  debug_log_path: string;
  user_home: string;
  /** native.py's platform: "linux-x86_64", "linux-arm64", "windows-x86_64"... */
  platform?: string;
  /** MusicDeck in the Steam library (src/libraryEntry.ts): on or off, and its shortcut's app id */
  library_entry?: { enabled: boolean; appid: number };
  /** a CD drive is plugged in and there's a burner (burn.py): Local files offers Burn to CD */
  can_burn?: boolean;
  server: { base: string; key: string };
  service_types: ServiceType[];
  planned_types: ServiceType[];
  /** linked ListenBrainz account ("" when not linked) and listens waiting to be sent */
  listenbrainz: { user: string; pending: number };
  /** has_key: an API account was given before, so linking again needs only the approval */
  lastfm: { user: string; has_key: boolean; pending: number };
  game_music: GameMusic[];
  offline: DownloadStatus;
  center: CenterSettings;
}

/** An album or playlist that plays when a game starts (for one source, while it's live). */
export interface GameMusic {
  instance_id: string;
  appid: number;
  /** the game's name when it was set */
  game: string;
  collection: Collection;
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
/** Saves the EQ for the output it was set on (when each output has its own), or as the one EQ */
export const saveEq = callable<[eq: EqSettings, outputId: string | null], void>("set_eq");
export const saveService = callable<[service: ServiceDraft], Result<ServiceInfo>>("save_service");
export const removeService = callable<[id: string], void>("remove_service");
export const setActiveService = callable<[id: string], void>("set_active_service");
export const testService = callable<[id: string], Result<string>>("test_service");
export const rescan = callable<[id: string], void>("rescan");
/** A plugged-in device's name, kept for next time; "" goes back to the one it gives itself. */
export const renameDevice = callable<[id: string, name: string], void>("rename_device");
/** Copies a track, album, artist or playlist of Local files to a device (its id), or a device's to the Deck ("deck"). */
export const copyTo = callable<[instanceId: string, kind: string, itemId: string, target: string], Result<string>>(
  "copy_to",
);
/** Deletes a track, album, artist or playlist's songs from the device they're on (an iPod's library too). */
export const removeFromDevice = callable<[instanceId: string, kind: string, itemId: string], Result<string>>(
  "remove_from_device",
);
/** Moves a song, album, folder or playlist file of Local files to the trash (Recently deleted brings it back). */
export const deleteFromLibrary = callable<[instanceId: string, kind: string, itemId: string], Result<string>>(
  "delete_from_library",
);
/** What burning an album, artist or playlist to CD would be, for the user to say yes to (services/shelf.py). */
export interface BurnPlan {
  drive: string;
  songs: number;
  minutes: number;
  /** the disc's minutes */
  capacity: number;
  /** a CD-RW with something on it: erased first */
  erase: boolean;
  title: string;
  artist: string;
}
export const burnPlan = callable<[instanceId: string, kind: string, itemId: string], Result<BurnPlan>>("burn_plan");
/** Burns an album, artist or playlist of Local files to the CD in the drive, in the background. */
export const burnCd = callable<[instanceId: string, kind: string, itemId: string], Result<string>>("burn_cd");
/** Ejects a plugged-in device ("Safely remove"), or opens a CD drive's tray. */
export const ejectDevice = callable<[deviceId: string], Result<string>>("eject_device");
/** Whether an id (a track's, an album's...) is of something on a plugged-in device (services/shelf.py). */
export const onDevice = (id: string | null | undefined) => !!id && id.startsWith("dv~");
export const getHome = callable<[id: string], Result<Section[]>>("home");
/** Genres, moods and charts, for sources with feature "browse" */
export const getBrowse = callable<[id: string], Result<Section[]>>("browse");
export const getTrackDetails = callable<[id: string, track: Track], Result<TrackDetails | null>>("track_details");
/** One of a source's media center tabs: its sections, each saying how to show it */
export const getCenterTab = callable<[id: string, tab: string], Result<Section[]>>("center_tab");
/** Lines and covers for Browse's tiles that don't come with their own, by tab id */
export const getBrowseInfo = callable<[id: string], Result<Record<string, TabInfo>>>("browse_info");
/** A Browse tile's line and covers, and the first of what it holds for its row */
export interface TabInfo {
  subtitle: string;
  art: string[];
  items: Collection[];
  tracks: Track[];
  /** how many its first part holds (items or tracks above are the first of them) */
  count: number;
}
export const openCollection = callable<[id: string, kind: CollectionKind, collectionId: string], Result<Page>>(
  "open_collection",
);
export const search = callable<[id: string, query: string], Result<Page>>("search");

export interface LikeWords {
  add: string;
  remove: string;
  added: string;
  removed: string;
}

/** A favourite added or taken back; sources that keep likes on the Deck keep the track itself. */
export const star = callable<[id: string, trackId: string, starred: boolean, track: Track], Result<null>>("star");
export const scrobble = callable<[id: string, trackId: string, submission: boolean, track: Track], Result<null>>(
  "scrobble",
);
export const getLyrics = callable<[id: string, track: Track], Result<Lyrics | null>>("get_lyrics");
export const getRadio = callable<[id: string, track: Track], Result<Track[]>>("radio");
export const getUserPlaylists = callable<[id: string], Result<Collection[]>>("user_playlists");
export const addToPlaylist = callable<[id: string, playlistId: string, trackIds: string[]], Result<null>>(
  "add_to_playlist",
);
/** Managing a library (local files): its tags (feature "tags") and albums' own pictures ("artwork"). */
export type TagKind = "track" | "album";
export interface TagField {
  field: string;
  label: string;
  value: string;
  /** an album's tracks differ on it: left as each has it unless changed */
  mixed: boolean;
}
export interface TagInfo {
  fields: TagField[];
  files: number;
  undo: boolean;
  own_art: boolean;
  album_id: string;
  /** a new album on the list to review (the source's "Ask me" for MusicBrainz tags) */
  review: boolean;
}
export interface TagResult {
  changed: number;
  failed: number;
  error: string;
  /** the album's id now (it changes with its name or artist) */
  album_id: string;
  art?: boolean;
}
/** A record an album may be (identify.py): what every catalogue asked says of it, weighed against the files. */
export interface Release {
  id: string;
  /** the catalogues that know it, joined ("MusicBrainz + Deezer") */
  source: string;
  sources?: string[];
  group: string;
  title: string;
  artist: string;
  year: string;
  country: string;
  tracks: number;
  format: string;
  /** how sure it is that the files are this record, 0-100 (also as score) */
  score: number;
  confidence?: number;
  /** a link to its cover, at a catalogue (shown through the stream server's relay) */
  cover?: string;
  label?: string;
  genres?: string[];
}
/** [label, before, after] */
export type Change = [string, string, string];
export interface LookupChanges {
  release: { id: string; title: string; artist: string; year: string; label: string; genre: string };
  album: Change[];
  tracks: [string, Change[]][];
  matched: boolean;
  /** each file beside the release's track it was matched to: [file, its length, "3. Title" or "", that track's length] */
  pairs?: [string, number, string, number][];
}
export const getTags = callable<[id: string, kind: TagKind, itemId: string], Result<TagInfo>>("get_tags");
export const setTags = callable<
  [id: string, kind: TagKind, itemId: string, changes: Record<string, string>],
  Result<TagResult>
>("set_tags");
export const undoTags = callable<[id: string, kind: TagKind, itemId: string], Result<TagResult>>("undo_tags");
export const lookupAlbum = callable<[id: string, albumId: string, artist?: string, album?: string], Result<Release[]>>(
  "lookup_album",
);
export const lookupChanges = callable<[id: string, albumId: string, releaseId: string], Result<LookupChanges>>(
  "lookup_changes",
);
export const applyLookup = callable<[id: string, albumId: string, releaseId: string], Result<TagResult>>(
  "apply_lookup",
);
export const albumArt = callable<
  [id: string, albumId: string, action: "set" | "find" | "clear", imagePath?: string],
  Result<boolean | null>
>("album_art");
export const findMissingArt = callable<[id: string], Result<null>>("find_missing_art");
export const artistArt = callable<
  [id: string, artistId: string, action: "set" | "find" | "clear", imagePath?: string],
  Result<boolean | null>
>("artist_art");
/** A track's lyrics kept beside it as an .lrc: false when the lyrics sites have none. */
export const saveLyrics = callable<[id: string, trackId: string], Result<boolean>>("save_lyrics");
export const findLyrics = callable<[id: string, albumId: string], Result<{ found: number; total: number }>>(
  "find_lyrics",
);
export const findMissingLyrics = callable<[id: string], Result<null>>("find_missing_lyrics");
export const dismissReview = callable<[id: string, albumId: string], Result<null>>("dismiss_review");

/** A new playlist on the source itself (feature "playlists"), holding these tracks. */
export const createServicePlaylist = callable<[id: string, name: string, trackIds: string[]], Result<Collection>>(
  "create_service_playlist",
);
export const saveSession = callable<[session: Session], void>("save_session");
export const loadSession = callable<[], Session | null>("load_session");
export const streamInfo = callable<
  [id: string, trackId: string],
  /**
   * start_at: where a long track (a podcast episode) was left, in seconds; expires: when the stream's
   * link stops working (Unix seconds), if it does
   */
  Result<{ hls: boolean; gain?: Track["gain"]; start_at?: number; expires?: number | null }>
>("stream_info");
export const collectionAction = callable<
  [id: string, kind: CollectionKind, collectionId: string, call: string],
  Result<string>
>("collection_action");
/** Saves an album or playlist to the user's library on the service, or follows an artist (Page.save). */
export const saveToLibrary = callable<
  [id: string, kind: CollectionKind, collectionId: string, on: boolean],
  Result<null>
>("save_to_library");
export const saveProgress = callable<[id: string, track: Track, position: number, duration: number], void>(
  "save_progress",
);
export const setPlayed = callable<[id: string, track: Track, played: boolean], Result<null>>("set_played");
export const forgetStream = callable<[id: string, trackId: string], void>("forget_stream");
export const logEvents = callable<[entries: { t: number; event: string; data?: Record<string, unknown> }[]], void>(
  "log_events",
);

// MusicDeck playlists (stored on the device, per source)
export const localPlaylists = callable<[id: string], Collection[]>("local_playlists");

/** A smart playlist (desktop Steam's Music section): Local files' songs picked by rules (LocalService.smart). */
export interface SmartRule {
  field: "title" | "artist" | "album" | "albumartist" | "genre" | "year" | "plays" | "added_days" | "played_days" | "starred" | "duration";
  op: "contains" | "is" | "is_not" | "starts" | "gt" | "lt";
  value: string;
}
export interface SmartPlaylist {
  id: string;
  name: string;
  /** the source it picks from */
  source: string;
  rules: { rules: SmartRule[]; match: "all" | "any"; sort: string; limit: number };
}
export const smartPlaylists = callable<[], SmartPlaylist[]>("smart_playlists");
export const saveSmartPlaylist = callable<[playlist: Partial<SmartPlaylist>], SmartPlaylist[]>("save_smart_playlist");
export const deleteSmartPlaylist = callable<[playlistId: string], SmartPlaylist[]>("delete_smart_playlist");
export const smartTracks = callable<[id: string, rules: SmartPlaylist["rules"]], Result<Track[]>>("smart_tracks");
export const createPlaylist = callable<[id: string, name: string, tracks: Track[]], Collection>("create_playlist");
export const saveAsPlaylist = callable<[id: string, kind: CollectionKind, collectionId: string], Result<Collection>>(
  "save_as_playlist",
);
export const playlistAdd = callable<[playlistId: string, tracks: Track[]], number>("playlist_add");
export const playlistRemove = callable<[playlistId: string, trackId: string], void>("playlist_remove");
export const playlistRename = callable<[playlistId: string, name: string], void>("playlist_rename");
export const playlistDelete = callable<[playlistId: string], void>("playlist_delete");
export const setPlaylistOffline = callable<[playlistId: string, on: boolean], Result<null>>("set_playlist_offline");
export const playlistOffline = callable<[id: string, playlistId: string], OfflineState | null>("playlist_offline");
export const retryDownloads = callable<[], void>("retry_downloads");
export const removeDownloads = callable<[], void>("remove_downloads");
export const exportBackup = callable<[], Result<string>>("export_backup");
export const importBackup = callable<[path: string], Result<string>>("import_backup");
export const playlistSetArt = callable<[playlistId: string, imagePath: string], Result<null>>("playlist_set_art");
export const playlistClearArt = callable<[playlistId: string], void>("playlist_clear_art");
export const isLocalPlaylist = (id: string) => id.startsWith("md:");
export const youtubeUpdate = callable<[id: string], Result<string>>("youtube_update");
/** Sign-in outside the form (OAuth pages, device codes): see SignIn in services/base.py. */
export const signInStart = callable<
  [type: string, options: Record<string, any>],
  Result<{ login_id: string; url: string; code: string }>
>("sign_in_start");
export const signInPoll = callable<
  [loginId: string, service: ServiceDraft],
  { status: "waiting" | "done" | "error"; error?: string; url?: string; service?: ServiceInfo }
>("sign_in_poll");
export const signInCancel = callable<[loginId: string], void>("sign_in_cancel");
export const steamLibrary = callable<[id: string, owned: OwnedSoundtrack[], running: number | null], void>(
  "steam_library",
);
/** What a live station says it's playing; `art` is the song's cover when the station sends one. */
export interface OnAir {
  text: string;
  title: string;
  artist: string;
  art: string | null;
}
export const searchHistory = callable<[id: string], string[]>("search_history");
/** query "" forgets them all */
export const forgetSearch = callable<[id: string, query: string], void>("forget_search");
export const playedCollection = callable<[id: string, collection: Collection], void>("played_collection");
/** A live station listened to: a radio source's Jump back in. */
export const playedStation = callable<[id: string, track: Track], void>("played_station");
export const setGameMusic = callable<
  [id: string, appid: number, game: string, collection: Collection | null],
  Result<GameMusic[]>
>("set_game_music");
export const onAir = callable<[id: string, trackId: string], Result<OnAir | null>>("on_air");
export const setListenBrainz = callable<[token: string], Result<{ user: string }>>("set_listenbrainz");
/** Starts linking Last.fm with the user's API account (blanks: the one used before): the approval page */
export const lastfmStart = callable<[key: string, secret: string], Result<{ url: string }>>("lastfm_start");
export const lastfmPoll = callable<[], { status: "waiting" | "done" | "error"; user?: string; error?: string }>(
  "lastfm_poll",
);
export const lastfmCancel = callable<[], void>("lastfm_cancel");
export const lastfmUnlink = callable<[], void>("lastfm_unlink");

// --- casting (py_modules/musicdeck/cast) ---

export interface CastDevice {
  id: string;
  name: string;
  model: string;
  kind: "chromecast" | "upnp" | "roku";
}

export interface CastSession {
  id: string;
  name: string;
  kind: CastDevice["kind"];
  volume: number;
}

/** "cast_status" events: the device's state, or `gone` when the session ended without us ending it. */
export interface CastStatus {
  state?: "playing" | "paused" | "buffering" | "idle";
  position?: number;
  duration?: number;
  ended?: boolean;
  error?: string;
  volume?: number | null;
  gone?: string;
}

export const castDevices = callable<[], Result<CastDevice[]>>("cast_devices");
export const castStart = callable<[deviceId: string], Result<CastSession>>("cast_start");
export const castLoad = callable<[id: string, track: Track, start: number, autoplay: boolean], Result<null>>(
  "cast_load",
);
export const castControl = callable<[action: string, value?: number], Result<null>>("cast_control");
export const castStop = callable<[], Result<null>>("cast_stop");
