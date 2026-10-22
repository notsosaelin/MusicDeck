import {
  ButtonItem,
  ConfirmModal,
  DropdownItem,
  Field,
  Menu,
  MenuItem,
  MenuSeparator,
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
import { SiJellyfin, SiTidal } from "react-icons/si";
import {
  GameMusic,
  PlaybackSettings,
  PluginState,
  ServiceInfo,
  rescan,
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
import { ACCENT, countryName } from "./common";
import { ServiceModal } from "./ServiceModal";

const TYPE_ICONS: Record<string, ReactNode> = {
  local: <FaFolderOpen />,
  steam: <FaSteam />,
  subsonic: <FaServer />,
  jellyfin: <SiJellyfin />,
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

const describeStatus = (s: ServiceInfo) => {
  if (s.type === "youtube") {
    if (s.status.update) return s.status.update;
    return s.status.ytdlp ? `yt-dlp ${s.status.ytdlp} · no account` : "No account";
  }
  if (s.type === "soundcloud" || s.type === "bandcamp" || s.type === "livemusic") return "No account";
  if (s.type === "radio")
    return ["No account", s.config.country && countryName(s.config.country)].filter(Boolean).join(" · ");
  if (s.type === "podcasts") {
    const n = s.config.subscriptions?.length ?? 0;
    return `No account · ${n} subscription${n === 1 ? "" : "s"}`;
  }
  if (s.type === "spotify") return `${s.config.display_name ?? "Spotify"} · ${s.config.bitrate ?? 320} kbps`;
  if (s.type === "tidal") {
    const q: Record<string, string> = { LOW: "AAC 96", HIGH: "AAC 320", LOSSLESS: "FLAC CD" };
    return `${s.config.username || "TIDAL"} · ${q[s.config.quality ?? "LOSSLESS"] ?? ""}`;
  }
  if (s.type === "qobuz") {
    const q: Record<number, string> = { 5: "MP3 320", 6: "FLAC CD", 7: "FLAC 24/96", 27: "FLAC 24/192" };
    return `${s.config.email ?? ""} · ${q[s.config.quality ?? 6] ?? ""}`;
  }
  if (s.type === "steam") {
    if (s.status.scanning) return `Scanning… ${s.status.found} files`;
    return `${s.status.installed ?? 0} installed · ${s.status.owned ?? 0} owned`;
  }
  if (s.type === "jellyfin")
    return [s.config.username, s.config.url?.replace(/^https?:\/\//, "")].filter(Boolean).join(" · ");
  if (s.type !== "local") return s.config.url ?? "";
  if (s.status.scanning) return `Scanning… ${s.status.found} files`;
  if (s.status.error) return `Scan failed: ${s.status.error}`;
  const n = s.status.tracks ?? 0;
  return `${n} track${n === 1 ? "" : "s"}`;
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
        strDescription="Its sign-in or folder list, and the MusicDeck playlists made from it, will be deleted. Your music files are not touched."
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
        description={`${typeName} · ${describeStatus(service)}`}
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
          While a game is running, open an album or playlist in MusicDeck and press <FaGamepad /> to play it whenever
          that game starts.
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
              description={`${m.collection.title} · ${source}${live ? "" : " (when it's the live source)"}`}
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

/** Link ListenBrainz with a user token, or show the linked account. Plays from every source go there. */
const ListenBrainzRows: FC<{ user: string; pending: number }> = ({ user, pending }) => {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const link = async (value: string) => {
    setBusy(true);
    const result = await setListenBrainz(value);
    setBusy(false);
    if (!result.ok) return notify("ListenBrainz", result.error);
    setToken("");
    await refreshState();
    notify("ListenBrainz", result.data.user ? `Linked as ${result.data.user}` : "Unlinked");
  };
  if (user) {
    return (
      <PanelSectionRow>
        <ButtonItem
          layout="below"
          label="ListenBrainz"
          description={`Linked as ${user}${pending ? ` · ${pending} listen${pending === 1 ? "" : "s"} waiting to send` : ""}`}
          disabled={busy}
          onClick={() => link("")}
        >
          Unlink
        </ButtonItem>
      </PanelSectionRow>
    );
  }
  return (
    <>
      <PanelSectionRow>
        <Field
          label="ListenBrainz"
          description="Records what you play from any source on listenbrainz.org. Paste your user token from its settings page."
          childrenLayout="below"
        >
          <TextField bIsPassword value={token} placeholder="User token" onChange={(e) => setToken(e.target.value)} />
        </Field>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" disabled={busy || !token.trim()} onClick={() => link(token)}>
          {busy ? "Checking…" : "Link"}
        </ButtonItem>
      </PanelSectionRow>
      <PanelSectionRow>
        <ButtonItem layout="below" onClick={() => Navigation.NavigateToExternalWeb(LISTENBRAINZ_TOKEN_PAGE)}>
          Get your token
        </ButtonItem>
      </PanelSectionRow>
    </>
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

  return (
    <>
      <PanelSection title="Playback">
        {state.services.length > 0 && (
          <PanelSectionRow>
            <DropdownItem
              label="Active source"
              rgOptions={state.services.map((s) => ({ data: s.id, label: s.name }))}
              selectedOption={state.active_service}
              onChange={(o) => switchSource(o.data)}
            />
          </PanelSectionRow>
        )}
        <PanelSectionRow>
          <DropdownItem
            label="Volume levelling"
            description="Evens out loudness using ReplayGain data from your files or server."
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
            description="Fades each track into the next. Off still starts the next track without a gap."
            rgOptions={[0, 2, 4, 6, 8, 12].map((s) => ({ data: s, label: s ? `${s} seconds` : "Off" }))}
            selectedOption={state.playback.crossfade ?? 0}
            onChange={(o) => setPlayback({ crossfade: o.data })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Keep the music going"
            description="When the queue ends, continue with similar tracks."
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
      </PanelSection>

      <PanelSection title="Sources">
        {state.services.length === 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 13, opacity: 0.7, padding: "8px 0" }}>Add a source below to get started.</div>
          </PanelSectionRow>
        )}
        {state.services.map((s) => (
          <ServiceRow key={s.id} service={s} typeName={typeName(s.type)} active={s.id === state.active_service} />
        ))}
      </PanelSection>

      <PanelSection title="Games">
        <PanelSectionRow>
          <ToggleField
            label="Pause while playing"
            description="Pauses the music when a game starts and picks it up again when the game closes. Music set for a game plays instead."
            checked={!!state.playback.game_pause}
            onChange={(game_pause) => setPlayback({ game_pause })}
          />
        </PanelSectionRow>
        <GameMusicRows state={state} />
      </PanelSection>

      <PanelSection title="Add a source">
        {state.service_types.map((t) => {
          const open = () => showModal(<ServiceModal type={t.type} typeName={t.display_name} />);
          return (
            <PanelSectionRow key={t.type}>
              <Field
                focusable
                highlightOnFocus
                icon={TYPE_ICONS[t.type]}
                label={t.display_name}
                onActivate={open}
                onClick={open}
              >
                <FaPlus style={{ opacity: 0.6 }} />
              </Field>
            </PanelSectionRow>
          );
        })}
        {state.planned_types.length > 0 && (
          <PanelSectionRow>
            <div style={{ fontSize: 12, opacity: 0.5, padding: "10px 0", lineHeight: 1.4 }}>
              Coming soon: {state.planned_types.map((t) => t.display_name).join(", ")}
            </div>
          </PanelSectionRow>
        )}
      </PanelSection>

      <PanelSection title="Plays and sync">
        <PanelSectionRow>
          <ToggleField
            label="Report plays to sources"
            description="Updates play counts and recently played on your server or service, and Last.fm if it's linked there."
            checked={state.playback.scrobble}
            onChange={(scrobble) => setPlayback({ scrobble })}
          />
        </PanelSectionRow>
        <PanelSectionRow>
          <ToggleField
            label="Sync queue"
            description="Saves your queue to the server so you can pick it up on another device."
            checked={state.playback.queue_sync}
            onChange={(queue_sync) => setPlayback({ queue_sync })}
          />
        </PanelSectionRow>
        <ListenBrainzRows user={state.listenbrainz?.user ?? ""} pending={state.listenbrainz?.pending ?? 0} />
      </PanelSection>

      <PanelSection title="Quick Access">
        <PanelSectionRow>
          <ToggleField
            label="Replace Soundtracks tab"
            description="When off, MusicDeck gets its own Quick Access tab. Applies the next time the menu opens."
            checked={state.qam_mode === "replace"}
            onChange={async (on) => {
              const mode = on ? "replace" : "separate";
              pluginState.set({ ...state, qam_mode: mode });
              await setSetting("qam_mode", mode);
            }}
          />
        </PanelSectionRow>
      </PanelSection>

      <PanelSection title="Troubleshooting">
        <PanelSectionRow>
          <ToggleField
            label="Debug log"
            description={
              state.debug_log
                ? `Recording playback details to ${state.debug_log_path}`
                : "Records detailed playback events to a file, for tracking down problems."
            }
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
