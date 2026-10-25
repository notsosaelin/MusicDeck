import { openFilePicker } from "@decky/api";
import {
  ButtonItem,
  ConfirmModal,
  DialogBody,
  DialogButton,
  DialogHeader,
  DropdownItem,
  Field,
  Focusable,
  Menu,
  MenuGroup,
  MenuItem,
  MenuSeparator,
  ModalRoot,
  PanelSection,
  PanelSectionRow,
  TextField,
  ToggleField,
  showContextMenu,
  showModal,
} from "@decky/ui";
import { FC, ReactNode, useEffect, useState } from "react";
import {
  FaArchive,
  FaCheckCircle,
  FaBandcamp,
  FaBroadcastTower,
  FaCompactDisc,
  FaEllipsisH,
  FaFolderOpen,
  FaGamepad,
  FaPlus,
  FaPodcast,
  FaServer,
  FaSoundcloud,
  FaShareAlt,
  FaMobileAlt,
  FaSpotify,
  FaSyncAlt,
  FaUsb,
  FaSteam,
  FaYoutube,
} from "react-icons/fa";
import { SiApple, SiAudiobookshelf, SiEmby, SiJellyfin, SiPlex, SiTidal } from "react-icons/si";
import {
  DownloadStatus,
  exportBackup,
  findMissingArt,
  findMissingLyrics,
  Feature,
  importBackup,
  GameMusic,
  PlaybackSettings,
  PluginState,
  ServiceInfo,
  ServiceType,
  removeDownloads,
  rescan,
  renameDevice,
  ejectDevice,
  DeviceInfo,
  retryDownloads,
  setGameMusic,
  lastfmUnlink,
  setListenBrainz,
  setSetting,
  testService,
  youtubeUpdate,
} from "../api";
import { notify } from "../notify";
import { deleteSource, switchSource } from "../sources";
import { player } from "../player";
import { syncLibraryEntry } from "../libraryEntry";
import { pluginState, refreshState, useStore } from "../store";
import { invalidateBrowserCache } from "../nav";
import { ACCENT, DialogActions } from "./common";
import { browseThenReturn, linkLastfm, readClipboard } from "../signIn";
import { ServiceModal } from "./ServiceModal";

/** How shuffle orders the queue (player.ts: buildOrder) */
export const SHUFFLE_MODES: [NonNullable<PlaybackSettings["shuffle_mode"]>, string][] = [
  ["songs", "Songs"],
  ["albums", "Albums"],
  ["smart", "Smart"],
];
/** Changes playback settings from anywhere (the desktop player's shuffle menu too): the player, the state, saved. */
export async function updatePlayback(patch: Partial<PlaybackSettings>) {
  const state = pluginState.get();
  if (!state) return;
  const playback = { ...state.playback, ...patch };
  pluginState.set({ ...state, playback });
  player.setPlayback(playback);
  await setSetting("playback", playback);
}

export const SHUFFLE_NOTES: Record<NonNullable<PlaybackSettings["shuffle_mode"]>, string> = {
  songs: "Song by song",
  albums: "Whole albums, in random order",
  smart: "Your better-rated songs sooner, never the same artist twice in a row",
};

const TYPE_ICONS: Record<string, ReactNode> = {
  local: <FaFolderOpen />,
  steam: <FaSteam />,
  subsonic: <FaServer />,
  jellyfin: <SiJellyfin />,
  emby: <SiEmby />,
  plex: <SiPlex />,
  audiobookshelf: <SiAudiobookshelf />,
  soulsync: <FaSyncAlt />,
  soundcloud: <FaSoundcloud />,
  youtube: <FaYoutube />,
  bandcamp: <FaBandcamp />,
  radio: <FaBroadcastTower />,
  podcasts: <FaPodcast />,
  livemusic: <FaArchive />,
  qobuz: <FaCompactDisc />,
  spotify: <FaSpotify />,
  soulseek: <FaShareAlt />,
  tidal: <SiTidal />,
};

/** What sets a source apart in its row: the account or server, a library's size, a scan; nothing for
 * sources without one (no "No account" on every row). */
const describeStatus = (s: ServiceInfo): string => {
  // restored from a backup, which holds no sign-ins
  if (s.config.sign_in_again) return "Sign in again";
  const host = (url?: string) => url?.replace(/^https?:\/\//, "").replace(/\/$/, "");
  const parts = (...items: (string | undefined | false)[]) => items.filter(Boolean).join(" · ");
  switch (s.type) {
    case "youtube":
      return s.status.update ?? "";
    case "podcasts": {
      const n = s.config.subscriptions?.length ?? 0;
      return n ? `${n} subscription${n === 1 ? "" : "s"}` : "";
    }
    case "spotify":
      return s.config.display_name ?? "";
    case "tidal":
      return s.config.username ?? "";
    case "qobuz":
      return s.config.email ?? "";
    case "steam":
      return s.status.scanning ? "Scanning…" : `${s.status.installed ?? 0} installed`;
    case "plex":
      return parts(s.config.username, s.config.server_name) || host(s.config.server_url) || "";
    case "jellyfin":
    case "emby":
    case "audiobookshelf":
    case "subsonic":
      return parts(s.config.username, host(s.config.url));
    case "soulsync":
      return host(s.config.url) ?? "";
    case "soulseek":
      return s.status.connected
        ? parts(
            s.status.user,
            s.status.downloading ? `downloading ${s.status.downloading}` : "connected",
            s.status.shared ? `sharing ${s.status.shared} files` : "",
            s.status.uploading ? `uploading ${s.status.uploading}` : "",
          )
        : s.status.error || "Connecting…";
    case "local": {
      const copy = s.status.copy;
      if (copy?.running) return `Copying to ${copy.to}… ${copy.done} of ${copy.total}`;
      const burn = s.status.burn;
      if (burn?.running)
        return burn.stage === "converting"
          ? `Getting ${burn.title} ready for the CD… ${burn.done} of ${burn.total}`
          : burn.stage === "erasing"
            ? "Erasing the CD-RW…"
            : `Burning ${burn.title}… ${burn.percent}%`;
      if (burn?.error) return `Burning stopped: ${burn.error}`;
      if (s.status.scanning) return `Scanning… ${s.status.found} files`;
      if (s.status.art?.running)
        return s.status.art.total
          ? `Finding artwork… ${s.status.art.done} of ${s.status.art.total}`
          : "Finding artwork…";
      if (s.status.lyrics?.running) return `Finding lyrics… ${s.status.lyrics.done} of ${s.status.lyrics.total}`;
      if (s.status.importing?.running)
        return `Getting new music ready… ${s.status.importing.done} of ${s.status.importing.total}`;
      if (s.status.error) return `Scan failed: ${s.status.error}`;
      const n = s.status.tracks ?? 0;
      const review = s.status.review ?? 0;
      return (
        `${n} track${n === 1 ? "" : "s"}` + (review ? ` · ${review} new album${review === 1 ? "" : "s"} to review` : "")
      );
    }
    default:
      return "";
  }
};

// FileSelectionType.FILE from @decky/api (a const enum, which isolated builds can't import)
const SELECT_FILE = 0;

/**
 * Backing up to a file in Documents, and restoring from one. Backups hold no sign-ins, so they're
 * safe to copy anywhere; sources with accounts need signing in again after a restore.
 */
const BackupRows: FC = () => {
  const backUp = async () => {
    const saved = await exportBackup();
    notify(
      saved.ok ? "Backed up to Documents" : "Couldn't back up",
      saved.ok ? "Sign-ins aren't included" : saved.error,
    );
  };
  const restore = async () => {
    let path: string;
    try {
      const home = pluginState.get()?.user_home || "/home";
      path = (await openFilePicker(SELECT_FILE, home, true, true, undefined, ["json"])).realpath;
    } catch {
      return; // picker cancelled
    }
    const restored = await importBackup(path);
    notify(restored.ok ? "Restored" : "Couldn't restore", restored.ok ? restored.data : restored.error);
    invalidateBrowserCache();
    await refreshState();
  };
  return (
    <PanelSectionRow>
      <Field label="Backup" childrenLayout="below">
        <Focusable style={{ display: "flex", gap: 8 }} flow-children="row">
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={backUp}>
            Back up
          </DialogButton>
          <DialogButton style={{ minWidth: 0, flex: 1 }} onClick={restore}>
            Restore
          </DialogButton>
        </Focusable>
      </Field>
    </PanelSectionRow>
  );
};

const megabytes = (bytes: number) =>
  bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.max(1, Math.round(bytes / 1024 ** 2))} MB`;

/** What the downloaded playlists take up, what's downloading, and a way to free the space. */
const DownloadRows: FC<{ status: DownloadStatus }> = ({ status }) => {
  const progress = status.current
    ? `Downloading · ${status.pending} to go`
    : status.failed > 0
      ? `${status.failed} failed${status.stopped ? `: ${status.stopped.toLowerCase()}` : ""}`
      : status.stopped;
  const removeAll = () =>
    showModal(
      <ConfirmModal
        strTitle="Remove all downloads?"
        strDescription="Downloaded songs are deleted from this Deck. Your playlists stay."
        strOKButtonText="Remove"
        bDestructiveWarning
        onOK={() => removeDownloads()}
      />,
    );
  return (
    <>
      <PanelSectionRow>
        <Field
          label={`${status.tracks} song${status.tracks === 1 ? "" : "s"} · ${megabytes(status.bytes)}`}
          description={progress || undefined}
        />
      </PanelSectionRow>
      {status.failed > 0 && !status.current && (
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={() => retryDownloads()}>
            Try again
          </ButtonItem>
        </PanelSectionRow>
      )}
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={removeAll}>
          Remove all downloads
        </ButtonItem>
      </PanelSectionRow>
    </>
  );
};

/** Naming a plugged-in device: phones and players can call themselves by a board's code name. */
const RenameDeviceModal: FC<{ closeModal?: () => void; device: DeviceInfo }> = ({ closeModal, device }) => {
  const [name, setName] = useState(device.name);
  const save = async () => {
    await renameDevice(device.id, name);
    await refreshState();
    closeModal?.();
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>Rename</DialogHeader>
      <DialogBody>
        <TextField value={name} placeholder={device.name} onChange={(e) => setName(e.target.value)} />
      </DialogBody>
      <DialogActions primary="Save" onPrimary={save} primaryDisabled={!name.trim()} onCancel={closeModal} />
    </ModalRoot>
  );
};

const DEVICE_KINDS: Record<string, string> = { mtp: "Phone", ios: "iPhone", disc: "Disc", usb: "USB" };

/** "3.2 GB": a device's free space. */
const gigabytes = (bytes: number) =>
  bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;

/** What's on a device, or what's happening to a CD: "Phone · 412 songs · 3.2 GB free", "Audio CD · ripping 3 of 11". */
const describeDevice = (d: DeviceInfo): string => {
  if (d.kind === "cd") {
    const n = d.status.tracks ?? 0;
    const rip = d.status.rip;
    return [
      `Audio CD · ${n} track${n === 1 ? "" : "s"}`,
      rip?.running ? `ripping ${rip.done} of ${rip.total}` : rip?.error ? `ripping stopped: ${rip.error}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  const n = d.status.tracks ?? 0;
  const kind = d.kind === "ipod" ? d.status.model || "iPod" : (DEVICE_KINDS[d.kind] ?? "USB");
  const songs = d.status.scanning ? "Reading…" : `${n} song${n === 1 ? "" : "s"}`;
  const free = d.status.space ? `${gigabytes(d.status.space.free)} free` : "";
  // an iPod MusicDeck can play from but not sync (a nano 5G or later, a Mac-formatted one)
  const playOnly = (d.kind === "ipod" && d.status.why_not) || d.kind === "ios" ? "play only" : "";
  return [kind, songs, free, playOnly].filter(Boolean).join(" · ");
};

/** A device plugged in: played and copied to in Local files (its Devices tab); here it can be named and ejected. */
const DeviceRow: FC<{ device: DeviceInfo }> = ({ device }) => {
  const rename = () => showModal(<RenameDeviceModal device={device} />);
  const eject = async () => {
    const r = await ejectDevice(device.id);
    notify(device.name, r.ok ? r.data : r.error);
    await refreshState();
  };
  const openMenu = (e: any) =>
    showContextMenu(
      <Menu label={device.name}>
        <MenuItem onSelected={eject}>{device.kind === "cd" ? "Eject disc" : "Eject"}</MenuItem>
        {device.kind !== "cd" && device.kind !== "disc" && <MenuItem onSelected={rename}>Rename</MenuItem>}
      </Menu>,
      e?.currentTarget ?? window,
    );
  const icon =
    device.kind === "mtp" ? (
      <FaMobileAlt />
    ) : device.kind === "cd" || device.kind === "disc" ? (
      <FaCompactDisc />
    ) : device.kind === "ipod" || device.kind === "ios" ? (
      <SiApple />
    ) : (
      <FaUsb />
    );
  return (
    <PanelSectionRow>
      <Field
        focusable
        highlightOnFocus
        icon={icon}
        label={device.name}
        description={describeDevice(device)}
        onActivate={openMenu}
        onClick={openMenu}
        onOKActionDescription="Options"
      >
        <FaEllipsisH style={{ opacity: 0.6 }} />
      </Field>
    </PanelSectionRow>
  );
};

const ServiceRow: FC<{ service: ServiceInfo; typeName: string; active: boolean }> = ({ service, typeName, active }) => {
  const edit = () => showModal(<ServiceModal type={service.type} typeName={typeName} existing={service} />);
  const test = async () => {
    const r = await testService(service.id);
    notify(service.name, r.ok ? r.data : r.error);
  };
  const updateYoutube = async () => {
    notify(service.name, "Checking for a newer yt-dlp…");
    const r = await youtubeUpdate(service.id);
    notify(service.name, r.ok ? r.data : r.error);
    await refreshState();
  };
  const doRescan = async () => {
    await rescan(service.id);
    invalidateBrowserCache();
    await refreshState();
  };
  const makeActive = () => switchSource(service.id);
  const findArt = async () => {
    const r = await findMissingArt(service.id);
    if (!r.ok) return notify(service.name, r.error);
    await refreshState();
  };
  const findLyrics = async () => {
    const r = await findMissingLyrics(service.id);
    if (!r.ok) return notify(service.name, r.error);
    await refreshState();
  };
  const remove = () =>
    showModal(
      <ConfirmModal
        strTitle={`Remove ${service.name}?`}
        strDescription="Its MusicDeck playlists go too. Your music files stay."
        strOKButtonText="Remove"
        bDestructiveWarning
        onOK={() => deleteSource(service.id)}
      />,
    );

  const openMenu = (e: any) =>
    showContextMenu(
      <Menu label={service.name}>
        {!active && <MenuItem onSelected={makeActive}>Play from this source</MenuItem>}
        <MenuItem onSelected={edit}>Edit</MenuItem>
        {service.type === "local" || service.type === "steam" ? (
          <MenuItem onSelected={doRescan} disabled={!!service.status.scanning}>
            Rescan library
          </MenuItem>
        ) : (
          <MenuItem onSelected={test}>Test connection</MenuItem>
        )}
        {service.type === "youtube" && <MenuItem onSelected={updateYoutube}>Update YouTube support</MenuItem>}
        {service.features.includes("artwork") && (
          <MenuItem onSelected={findArt} disabled={!!service.status.art?.running || !!service.status.scanning}>
            Find missing artwork
          </MenuItem>
        )}
        {service.features.includes("tags") && (
          <MenuItem onSelected={findLyrics} disabled={!!service.status.lyrics?.running || !!service.status.scanning}>
            Find missing lyrics
          </MenuItem>
        )}
        <MenuSeparator />
        <MenuItem tone="destructive" onSelected={remove}>
          Remove
        </MenuItem>
      </Menu>,
      e?.currentTarget ?? window,
    );

  return (
    <PanelSectionRow>
      <Field
        focusable
        highlightOnFocus
        icon={<span style={{ color: active ? ACCENT : undefined }}>{TYPE_ICONS[service.type]}</span>}
        label={
          <span style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {service.name}
            {active && <FaCheckCircle style={{ color: ACCENT, fontSize: 12 }} />}
          </span>
        }
        // the type only when the user gave it another name ("Home", say)
        description={
          [service.name !== typeName && typeName, describeStatus(service)].filter(Boolean).join(" · ") || undefined
        }
        onActivate={openMenu}
        onClick={openMenu}
        onOKActionDescription="Options"
      >
        <FaEllipsisH style={{ opacity: 0.6 }} />
      </Field>
    </PanelSectionRow>
  );
};

const LISTENBRAINZ_TOKEN_PAGE = "https://listenbrainz.org/settings/";
const LASTFM_API_ACCOUNT_PAGE = "https://www.last.fm/api/account/create";

/** The music set to play with games, from every source; each can be removed here. */
const GameMusicRows: FC<{ state: PluginState }> = ({ state }) => {
  const entries = [...state.game_music].sort((a, b) => a.game.localeCompare(b.game));
  if (!entries.length) {
    return (
      <PanelSectionRow>
        <div style={{ fontSize: 12, opacity: 0.6, padding: "8px 0", lineHeight: 1.4 }}>
          In a game, press <FaGamepad /> on an album or playlist to play it with that game.
        </div>
      </PanelSectionRow>
    );
  }
  const remove = async (m: GameMusic) => {
    const result = await setGameMusic(m.instance_id, m.appid, m.game, null);
    if (!result.ok) return notify("Game music", result.error);
    await refreshState();
  };
  return (
    <>
      {entries.map((m) => {
        const source = state.services.find((s) => s.id === m.instance_id)?.name ?? "";
        const live = m.instance_id === state.active_service;
        const open = (e: any) =>
          showContextMenu(
            <Menu label={m.game}>
              <MenuItem tone="destructive" onSelected={() => remove(m)}>
                Don't play with {m.game}
              </MenuItem>
            </Menu>,
            e?.currentTarget ?? window,
          );
        return (
          <PanelSectionRow key={`${m.instance_id}:${m.appid}`}>
            <Field
              focusable
              highlightOnFocus
              label={m.game}
              description={`${m.collection.title} · ${source}${live ? "" : " (not active)"}`}
              onActivate={open}
              onClick={open}
            >
              <FaEllipsisH style={{ opacity: 0.6 }} />
            </Field>
          </PanelSectionRow>
        );
      })}
    </>
  );
};

/** A ListenBrainz user token, as its settings page shows it */
const LISTENBRAINZ_TOKEN = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/i;
/** A Last.fm API key or shared secret */
const LASTFM_KEY = /\b[0-9a-f]{32}\b/i;

/** Linking ListenBrainz: the user token from listenbrainz.org's settings page. */
const ListenBrainzModal: FC<{ closeModal?: () => void; initialToken?: string }> = ({
  closeModal,
  initialToken = "",
}) => {
  const [token, setToken] = useState(initialToken);
  // the dialog would sit over the browser: it closes, and comes back as it was (with a token copied
  // on the page, pasted) when the browser does
  const getToken = () => {
    closeModal?.();
    browseThenReturn(LISTENBRAINZ_TOKEN_PAGE, async () => {
      const copied = token ? null : (await readClipboard())?.match(LISTENBRAINZ_TOKEN)?.[0];
      if (copied) notify("ListenBrainz", "Token pasted from the clipboard");
      showModal(<ListenBrainzModal initialToken={copied ?? token} />);
    });
  };
  const [busy, setBusy] = useState(false);
  const link = async () => {
    setBusy(true);
    const result = await setListenBrainz(token);
    setBusy(false);
    if (!result.ok) return notify("ListenBrainz", result.error);
    await refreshState();
    notify("ListenBrainz", `Linked as ${result.data.user}`);
    closeModal?.();
  };
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>Link ListenBrainz</DialogHeader>
      <DialogBody>
        <TextField bIsPassword value={token} placeholder="User token" onChange={(e) => setToken(e.target.value)} />
        <DialogButton style={{ marginTop: 8 }} onClick={getToken}>
          Get your token
        </DialogButton>
      </DialogBody>
      <DialogActions
        primary={busy ? "Checking…" : "Link"}
        onPrimary={link}
        primaryDisabled={busy || !token.trim()}
        onCancel={closeModal}
      />
    </ModalRoot>
  );
};

/** ListenBrainz scrobbles plays from every source; linked here with a user token. */
const ListenBrainzRow: FC<{ user: string; pending: number }> = ({ user, pending }) => {
  const unlink = async () => {
    const result = await setListenBrainz("");
    if (!result.ok) return notify("ListenBrainz", result.error);
    await refreshState();
  };
  return (
    <PanelSectionRow>
      <Field
        label="ListenBrainz"
        description={user ? `${user}${pending ? ` · ${pending} waiting` : ""}` : "Scrobble every source"}
      >
        <DialogButton
          style={{ minWidth: 0, width: "auto", padding: "8px 18px" }}
          onClick={() => (user ? unlink() : showModal(<ListenBrainzModal />))}
        >
          {user ? "Unlink" : "Link"}
        </DialogButton>
      </Field>
    </PanelSectionRow>
  );
};

/**
 * Linking Last.fm: the user's own API account (Last.fm wants one per app, and there's no MusicDeck one
 * to share), then their approval on Last.fm's page. With an account given before, only the approval.
 */
const LastfmModal: FC<{ hasKey: boolean; closeModal?: () => void; initialKey?: string; initialSecret?: string }> = ({
  hasKey,
  closeModal,
  initialKey = "",
  initialSecret = "",
}) => {
  const [key, setKey] = useState(initialKey);
  const [secret, setSecret] = useState(initialSecret);
  // as ListenBrainz's: out of the browser's way, back as it was; a key or secret copied on the page
  // fills the first empty field
  const createAccount = () => {
    closeModal?.();
    browseThenReturn(LASTFM_API_ACCOUNT_PAGE, async () => {
      const copied = (await readClipboard())?.match(LASTFM_KEY)?.[0];
      const fill = copied && copied !== key && copied !== secret ? (!key ? "key" : !secret ? "secret" : null) : null;
      if (fill) notify("Last.fm", `${fill === "key" ? "API key" : "Shared secret"} pasted from the clipboard`);
      showModal(
        <LastfmModal
          hasKey={hasKey}
          initialKey={fill === "key" ? copied! : key}
          initialSecret={fill === "secret" ? copied! : secret}
        />,
      );
    });
  };
  const ready = hasKey ? !key.trim() || !!secret.trim() : !!key.trim() && !!secret.trim();
  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>Link Last.fm</DialogHeader>
      <DialogBody>
        <div style={{ fontSize: 13, opacity: 0.8, marginBottom: 8 }}>
          {hasKey
            ? "Uses the API account you gave before. To use another, enter its key and secret."
            : "Last.fm needs an API account of your own: create one (any name will do), then enter its key and secret."}
        </div>
        <TextField
          value={key}
          placeholder={hasKey ? "API key (same as before)" : "API key"}
          onChange={(e) => setKey(e.target.value)}
        />
        <TextField
          bIsPassword
          value={secret}
          placeholder={hasKey ? "Shared secret (same as before)" : "Shared secret"}
          onChange={(e) => setSecret(e.target.value)}
        />
        <DialogButton style={{ marginTop: 8 }} onClick={createAccount}>
          Create an API account
        </DialogButton>
      </DialogBody>
      <DialogActions
        primary="Sign in"
        onPrimary={() => {
          closeModal?.();
          void linkLastfm(key, secret);
        }}
        primaryDisabled={!ready}
        onCancel={closeModal}
      />
    </ModalRoot>
  );
};

/** Last.fm scrobbles plays from every source, alongside ListenBrainz. */
const LastfmRow: FC<{ lastfm: PluginState["lastfm"] }> = ({ lastfm }) => {
  const { user, pending, has_key } = lastfm;
  return (
    <PanelSectionRow>
      <Field
        label="Last.fm"
        description={user ? `${user}${pending ? ` · ${pending} waiting` : ""}` : "Scrobble every source"}
      >
        <DialogButton
          style={{ minWidth: 0, width: "auto", padding: "8px 18px" }}
          onClick={async () => {
            if (!user) {
              showModal(<LastfmModal hasKey={has_key} />);
              return;
            }
            await lastfmUnlink();
            await refreshState();
          }}
        >
          {user ? "Unlink" : "Link"}
        </DialogButton>
      </Field>
    </PanelSectionRow>
  );
};

/** "Add a source"'s groups, in its order */
const SOURCE_GROUPS: { group: ServiceType["group"]; label: string }[] = [
  { group: "yours", label: "Your files and servers" },
  { group: "streaming", label: "Streaming services" },
  { group: "radio", label: "Radio and podcasts" },
  { group: "sharing", label: "File sharing" },
  { group: "other", label: "Other" },
];

/** The plugin's page in the Decky menu: sources, the active source, and QAM placement. */
/** A titled group of settings: Decky's PanelSection in the panel, the media center's own heading there. */
export type SectionFrame = FC<{ title: string; children?: ReactNode }>;

/**
 * Playback, Scrobbling and Games: the same settings in Decky → MusicDeck and the media center's
 * Settings tab (sources are chosen in Decky only).
 */
export const PlaybackSections: FC<{ state: PluginState; Section?: SectionFrame }> = ({
  state,
  Section = PanelSection,
}) => {
  const setPlayback = updatePlayback;
  const has = (feature: Feature) => state.services.some((s) => s.features.includes(feature));
  return (
    <>
      <Section title="Playback">
        <PanelSectionRow>
          <DropdownItem
            label="Volume levelling"
            rgOptions={[
              { data: "off", label: "Off" },
              { data: "track", label: "Per track" },
              { data: "album", label: "Per album" },
            ]}
            selectedOption={state.playback.levelling}
            onChange={(o) => setPlayback({ levelling: o.data })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Crossfade"
            rgOptions={[0, 2, 4, 6, 8, 12].map((s) => ({ data: s, label: s ? `${s} s` : "Off" }))}
            selectedOption={state.playback.crossfade ?? 0}
            onChange={(o) => setPlayback({ crossfade: o.data })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <DropdownItem
            label="Shuffle"
            description={SHUFFLE_NOTES[state.playback.shuffle_mode ?? "songs"]}
            rgOptions={SHUFFLE_MODES.map(([data, label]) => ({ data, label }))}
            selectedOption={state.playback.shuffle_mode ?? "songs"}
            onChange={(o) => setPlayback({ shuffle_mode: o.data })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Autoplay"
            description="Similar songs when the queue ends"
            checked={state.playback.autoplay !== false}
            onChange={(autoplay) => setPlayback({ autoplay })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Resume where you left off"
            checked={state.playback.resume}
            onChange={(resume) => setPlayback({ resume })}
          />
        </PanelSectionRow>
        {has("queue_sync") && (
          <PanelSectionRow>
            <ToggleField
              label="Sync queue"
              description="Pick up on another device"
              checked={state.playback.queue_sync}
              onChange={(queue_sync) => setPlayback({ queue_sync })}
            />
          </PanelSectionRow>
        )}
      </Section>

      <Section title="Scrobbling">
        {has("scrobble") && (
          <PanelSectionRow>
            <ToggleField
              label="Report plays"
              description="Play counts on the source"
              checked={state.playback.scrobble}
              onChange={(scrobble) => setPlayback({ scrobble })}
            />
          </PanelSectionRow>
        )}
        <ListenBrainzRow user={state.listenbrainz?.user ?? ""} pending={state.listenbrainz?.pending ?? 0} />
        {state.lastfm && <LastfmRow lastfm={state.lastfm} />}
      </Section>

      <Section title="Games">
        <PanelSectionRow>
          <DropdownItem
            // under its label, full width: its value (a source's name, a choice) is never cut short
            layout="below"
            label="In games"
            description={
              state.playback.game_pause
                ? "Resumes when the game closes"
                : (state.playback.game_duck ?? 1) < 1
                  ? "While the game is in front"
                  : undefined
            }
            // lowering keeps a share of the volume: -6, -10 and -16 dB, about a little, half and a lot as heard
            rgOptions={[
              { data: 1, label: "Keep playing" },
              { data: 0.5, label: "Lower a little" },
              { data: 0.3, label: "Lower by half" },
              { data: 0.15, label: "Lower a lot" },
              { data: "pause", label: "Pause" },
            ]}
            selectedOption={state.playback.game_pause ? "pause" : (state.playback.game_duck ?? 1)}
            onChange={(o) =>
              setPlayback(o.data === "pause" ? { game_pause: true } : { game_pause: false, game_duck: o.data })
            }
          />
        </PanelSectionRow>
        <GameMusicRows state={state} />
      </Section>
    </>
  );
};

export const Settings: FC = () => {
  const state = useStore(pluginState);

  useEffect(() => {
    refreshState();
    // keep scan, artwork and lyrics progress fresh while this page is open, and say when a job is done
    const jobs: Record<string, (found: number, total: number) => string> = {
      art: (found, total) =>
        total ? `Found ${found} of ${total} missing pictures` : "No albums or artists were missing pictures",
      lyrics: (found) => (found ? `Lyrics saved for ${found} tracks` : "No new lyrics found"),
    };
    const running = new Set<string>();
    const timer = window.setInterval(async () => {
      const busy = pluginState
        .get()
        ?.services.some((s) => s.status.scanning || Object.keys(jobs).some((j) => s.status[j]?.running));
      if (!busy && !running.size) return;
      const state = await refreshState();
      for (const s of state.services) {
        for (const [job, done] of Object.entries(jobs)) {
          const key = `${s.id}:${job}`;
          if (s.status[job]?.running) running.add(key);
          else if (running.delete(key)) {
            invalidateBrowserCache();
            notify(s.name, done(s.status[job]?.found ?? 0, s.status[job]?.total ?? 0));
          }
        }
      }
    }, 1500);
    return () => window.clearInterval(timer);
  }, []);

  if (!state) return null;
  const typeName = (type: string) => state.service_types.find((t) => t.type === type)?.display_name ?? type;

  // sources and the kinds of source to add, A to Z
  const byName = (a: string, b: string) => a.localeCompare(b, undefined, { sensitivity: "base" });
  const services = [...state.services].sort((a, b) => byName(a.name, b.name));
  const types = [...state.service_types].sort((a, b) => byName(a.display_name, b.display_name));
  // by kind, as submenus (Steam's own menus nest the same way), each A to Z
  const addSource = (e: any) =>
    showContextMenu(
      <Menu label="Add a source">
        {SOURCE_GROUPS.map(({ group, label }) => {
          const inGroup = types.filter((t) => (t.group ?? "other") === group);
          return (
            inGroup.length > 0 && (
              <MenuGroup key={group} label={label}>
                {inGroup.map((t) => (
                  <MenuItem
                    key={t.type}
                    onSelected={() => showModal(<ServiceModal type={t.type} typeName={t.display_name} />)}
                  >
                    {t.display_name}
                  </MenuItem>
                ))}
              </MenuGroup>
            )
          );
        })}
      </Menu>,
      e?.currentTarget ?? window,
    );
  const downloads = state.offline.tracks > 0 || state.offline.pending > 0 || state.offline.failed > 0;

  return (
    <>
      <PanelSection title="Sources">
        {services.length > 1 && (
          <PanelSectionRow>
            <DropdownItem
              // under its label, full width: its value (a source's name, a choice) is never cut short
              layout="below"
              label="Playing from"
              rgOptions={services.map((s) => ({ data: s.id, label: s.name }))}
              selectedOption={state.active_service}
              onChange={(o) => switchSource(o.data)}
            />
          </PanelSectionRow>
        )}
        {services.map((s) => (
          <ServiceRow key={s.id} service={s} typeName={typeName(s.type)} active={s.id === state.active_service} />
        ))}
        <PanelSectionRow>
          <ButtonItem layout="below" onClick={addSource}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
              <FaPlus /> Add a source
            </span>
          </ButtonItem>
        </PanelSectionRow>
      </PanelSection>
      {/* what's plugged in plays in Local files (its Devices tab): listed here to be named */}
      {(state.devices ?? []).length > 0 && (
        <PanelSection title="Plugged in">
          {state.devices.map((d) => (
            <DeviceRow key={d.id} device={d} />
          ))}
        </PanelSection>
      )}

      <PlaybackSections state={state} />

      {downloads && (
        <PanelSection title="Downloads">
          <DownloadRows status={state.offline} />
        </PanelSection>
      )}

      <PanelSection title="More">
        <PanelSectionRow>
          <ToggleField
            label="Replace Soundtracks tab"
            description={state.qam_mode === "replace" ? undefined : "MusicDeck has its own tab"}
            checked={state.qam_mode === "replace"}
            onChange={async (on) => {
              const mode = on ? "replace" : "separate";
              pluginState.set({ ...state, qam_mode: mode });
              await setSetting("qam_mode", mode);
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Show in your Library"
            description="Play opens the media center"
            checked={!!state.library_entry?.enabled}
            onChange={async (on) => {
              const entry = { enabled: on, appid: state.library_entry?.appid ?? 0 };
              pluginState.set({ ...state, library_entry: entry });
              await setSetting("library_entry", entry);
              await syncLibraryEntry();
              await refreshState();
            }}
          />
        </PanelSectionRow>
        <BackupRows />
        <PanelSectionRow>
          <ToggleField
            label="Debug log"
            // the file's name: its full path wraps over two lines, and Decky's logs are all in one place
            description={state.debug_log ? state.debug_log_path.split("/").pop() : undefined}
            checked={state.debug_log}
            onChange={async (on) => {
              pluginState.set({ ...state, debug_log: on });
              await setSetting("debug_log", on);
              await refreshState();
            }}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <Field label="Version">{state.version}</Field>
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
