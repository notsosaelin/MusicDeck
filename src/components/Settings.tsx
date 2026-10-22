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
  MenuItem,
  MenuSeparator,
  ModalRoot,
  Navigation,
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
  FaSpotify,
  FaSteam,
  FaYoutube,
} from "react-icons/fa";
import { SiAudiobookshelf, SiEmby, SiJellyfin, SiPlex, SiTidal } from "react-icons/si";
import {
  DownloadStatus,
  exportBackup,
  Feature,
  importBackup,
  GameMusic,
  PlaybackSettings,
  PluginState,
  ServiceInfo,
  removeDownloads,
  rescan,
  retryDownloads,
  setGameMusic,
  setListenBrainz,
  setSetting,
  testService,
  youtubeUpdate,
} from "../api";
import { notify } from "../notify";
import { deleteSource, switchSource } from "../sources";
import { player } from "../player";
import { pluginState, refreshState, useStore } from "../store";
import { invalidateBrowserCache } from "../nav";
import { ACCENT, DialogActions } from "./common";
import { ServiceModal } from "./ServiceModal";

const TYPE_ICONS: Record<string, ReactNode> = {
  local: <FaFolderOpen />,
  steam: <FaSteam />,
  subsonic: <FaServer />,
  jellyfin: <SiJellyfin />,
  emby: <SiEmby />,
  plex: <SiPlex />,
  audiobookshelf: <SiAudiobookshelf />,
  soundcloud: <FaSoundcloud />,
  youtube: <FaYoutube />,
  bandcamp: <FaBandcamp />,
  radio: <FaBroadcastTower />,
  podcasts: <FaPodcast />,
  livemusic: <FaArchive />,
  qobuz: <FaCompactDisc />,
  spotify: <FaSpotify />,
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
    case "local": {
      if (s.status.scanning) return `Scanning… ${s.status.found} files`;
      if (s.status.error) return `Scan failed: ${s.status.error}`;
      const n = s.status.tracks ?? 0;
      return `${n} track${n === 1 ? "" : "s"}`;
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
            Restore…
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
        <MenuItem onSelected={edit}>Edit…</MenuItem>
        {service.type === "local" || service.type === "steam" ? (
          <MenuItem onSelected={doRescan} disabled={!!service.status.scanning}>
            Rescan library
          </MenuItem>
        ) : (
          <MenuItem onSelected={test}>Test connection</MenuItem>
        )}
        {service.type === "youtube" && <MenuItem onSelected={updateYoutube}>Update YouTube support</MenuItem>}
        <MenuSeparator />
        <MenuItem tone="destructive" onSelected={remove}>
          Remove…
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

/** Linking ListenBrainz: the user token from listenbrainz.org's settings page. */
const ListenBrainzModal: FC<{ closeModal?: () => void }> = ({ closeModal }) => {
  const [token, setToken] = useState("");
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
        <DialogButton
          style={{ marginTop: 8 }}
          onClick={() => Navigation.NavigateToExternalWeb(LISTENBRAINZ_TOKEN_PAGE)}
        >
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
          {user ? "Unlink" : "Link…"}
        </DialogButton>
      </Field>
    </PanelSectionRow>
  );
};

/** The plugin's page in the Decky menu: sources, the active source, and QAM placement. */
export const Settings: FC = () => {
  const state = useStore(pluginState);

  useEffect(() => {
    refreshState();
    // keep scan progress fresh while this page is open
    const timer = window.setInterval(() => {
      if (pluginState.get()?.services.some((s) => s.status.scanning)) refreshState();
    }, 1500);
    return () => window.clearInterval(timer);
  }, []);

  if (!state) return null;
  const setPlayback = async (patch: Partial<PlaybackSettings>) => {
    const playback = { ...state.playback, ...patch };
    pluginState.set({ ...state, playback });
    player.setPlayback(playback);
    await setSetting("playback", playback);
  };
  const typeName = (type: string) => state.service_types.find((t) => t.type === type)?.display_name ?? type;

  const has = (feature: Feature) => state.services.some((s) => s.features.includes(feature));
  const addSource = (e: any) =>
    showContextMenu(
      <Menu label="Add a source">
        {state.service_types.map((t) => (
          <MenuItem key={t.type} onSelected={() => showModal(<ServiceModal type={t.type} typeName={t.display_name} />)}>
            {t.display_name}
          </MenuItem>
        ))}
      </Menu>,
      e?.currentTarget ?? window,
    );
  const downloads = state.offline.tracks > 0 || state.offline.pending > 0 || state.offline.failed > 0;

  return (
    <>
      <PanelSection title="Sources">
        {state.services.map((s) => (
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

      <PanelSection title="Playback">
        {state.services.length > 1 && (
          <PanelSectionRow>
            <DropdownItem
              label="Playing from"
              rgOptions={state.services.map((s) => ({ data: s.id, label: s.name }))}
              selectedOption={state.active_service}
              onChange={(o) => switchSource(o.data)}
            />
          </PanelSectionRow>
        )}
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
        <ListenBrainzRow user={state.listenbrainz?.user ?? ""} pending={state.listenbrainz?.pending ?? 0} />
      </PanelSection>

      <PanelSection title="Games">
        <PanelSectionRow>
          <ToggleField
            label="Pause in games"
            description="Resumes when the game closes"
            checked={!!state.playback.game_pause}
            onChange={(game_pause) => setPlayback({ game_pause })}
          />
        </PanelSectionRow>
        <GameMusicRows state={state} />
      </PanelSection>

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
        <BackupRows />
        <PanelSectionRow>
          <ToggleField
            label="Debug log"
            description={state.debug_log ? state.debug_log_path : undefined}
            checked={state.debug_log}
            onChange={async (on) => {
              pluginState.set({ ...state, debug_log: on });
              await setSetting("debug_log", on);
              await refreshState();
            }}
          />
        </PanelSectionRow>
      </PanelSection>
    </>
  );
};
