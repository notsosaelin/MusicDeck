import { openFilePicker } from "@decky/api";
import { unencryptedWarning } from "../addresses";
import { notify } from "../notify";
import {
  DialogBody,
  DialogButton,
  DialogHeader,
  DropdownItem,
  Focusable,
  ModalRoot,
  TextField,
  ToggleField,
  showModal,
} from "@decky/ui";
import { browseThenReturn, readClipboard, signIn } from "../signIn";
import { FC, useEffect, useRef, useState } from "react";
import { FaExternalLinkAlt, FaFolderPlus, FaTimes } from "react-icons/fa";
import { DialogActions, ScrollingText, countryName } from "./common";
import { ServiceDraft, ServiceInfo, signInCancel, signInPoll, signInStart, saveService, testService } from "../api";
import { pluginState, refreshState } from "../store";
import { invalidateBrowserCache } from "../nav";
import { syncSteam } from "../steam";

// FileSelectionType.FOLDER from @decky/api (a const enum, which isolated builds can't import)
const SELECT_FOLDER = 1;

const Field: FC<{ label: string; hint?: string; children: any }> = ({ label, hint, children }) => (
  <div style={{ marginBottom: 12 }}>
    <div style={{ fontSize: 12, opacity: 0.7, marginBottom: 4 }}>{label}</div>
    {children}
    {hint && <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>{hint}</div>}
  </div>
);

const LocalFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
}> = ({ draft, setConfig }) => {
  const paths: string[] = draft.config.paths ?? [];
  const addFolder = async () => {
    try {
      // start in the user's home: not every distro's user is "deck"
      const home = pluginState.get()?.user_home || "/home";
      const res = await openFilePicker(SELECT_FOLDER, paths[paths.length - 1] ?? home, false, true);
      if (res?.realpath && !paths.includes(res.realpath))
        setConfig({ ...draft.config, paths: [...paths, res.realpath] });
    } catch {
      // picker cancelled
    }
  };
  return (
    <Field label="Music folders (scanned recursively)">
      {paths.map((p) => (
        <Focusable
          key={p}
          style={{
            display: "flex",
            gap: 8,
            alignItems: "center",
            marginBottom: 4,
          }}
          flow-children="row"
        >
          <ScrollingText play text={p} style={{ flex: 1, minWidth: 0 }} />
          <DialogButton
            style={{ minWidth: 0, width: 40, padding: "6px 0" }}
            onClick={() =>
              setConfig({
                ...draft.config,
                paths: paths.filter((x) => x !== p),
              })
            }
          >
            <FaTimes />
          </DialogButton>
        </Focusable>
      ))}
      <DialogButton onClick={addFolder}>
        <FaFolderPlus style={{ marginRight: 8 }} /> Add folder
      </DialogButton>
    </Field>
  );
};

// Countries offered for radio's "Popular near you" (ISO codes; names come from the browser's own list)
// prettier-ignore
const RADIO_COUNTRIES = [
  "US", "GB", "CA", "AU", "DE", "FR", "NL", "ES", "IT", "BR", "MX", "JP", "KR", "SE", "NO", "PL",
  "IE", "NZ", "AT", "CH", "BE", "DK", "FI", "PT", "AR", "IN", "RU", "TR", "ZA",
];

/** Internet radio: no account; a country only adds "Popular near you" to the home view. */
const RadioFields: FC<{ draft: ServiceDraft; setConfig: (c: Record<string, any>) => void }> = ({
  draft,
  setConfig,
}) => (
  <DropdownItem
    label="Country"
    description="Adds the most popular stations there to the home view."
    rgOptions={[
      { data: "", label: "None" },
      ...RADIO_COUNTRIES.map((c) => ({ data: c, label: countryName(c) })).sort((a, b) =>
        a.label.localeCompare(b.label),
      ),
    ]}
    selectedOption={draft.config.country ?? ""}
    onChange={(o) => setConfig({ ...draft.config, country: o.data })}
  />
);

/** Podcasts: no account; the country picks whose top charts the home view shows. */
const PodcastFields: FC<{ draft: ServiceDraft; setConfig: (c: Record<string, any>) => void }> = ({
  draft,
  setConfig,
}) => (
  <>
    <div style={{ fontSize: 13, opacity: 0.7, lineHeight: 1.5, marginBottom: 8 }}>
      Find shows in Apple's podcast directory (no account) or paste a feed address into Search. Subscriptions and where
      you left each episode stay on this device.
    </div>
    <DropdownItem
      label="Top charts"
      description="Whose most popular podcasts the home view shows."
      rgOptions={["US", ...RADIO_COUNTRIES.filter((c) => c !== "US")]
        .map((c) => ({ data: c === "US" ? "" : c, label: countryName(c) }))
        .sort((a, b) => a.label.localeCompare(b.label))}
      selectedOption={draft.config.country ?? ""}
      onChange={(o) => setConfig({ ...draft.config, country: o.data })}
    />
  </>
);

const TIDAL_QUALITIES = [
  { data: "LOW", label: "AAC 96 kbps" },
  { data: "HIGH", label: "AAC 320 kbps" },
  { data: "LOSSLESS", label: "FLAC 16-bit / 44.1 kHz (CD)" },
];

/** TIDAL: device login, approved on link.tidal.com in Steam's browser. */
const TidalFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  signedIn: boolean;
  closeModal?: () => void;
}> = ({ draft, setConfig, signedIn, closeModal }) => (
  <>
    <Field label="Account">
      <DialogButton
        onClick={() => {
          closeModal?.();
          signIn("tidal", draft);
        }}
      >
        {signedIn
          ? `Signed in${draft.config.username ? ` as ${draft.config.username}` : ""} · sign in again`
          : "Sign in with TIDAL"}
      </DialogButton>
      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
        Opens TIDAL's page to approve this device and comes back here by itself. MusicDeck never sees your password.
      </div>
    </Field>
    <DropdownItem
      label="Streaming quality"
      rgOptions={TIDAL_QUALITIES}
      selectedOption={draft.config.quality ?? "LOSSLESS"}
      onChange={(o) => setConfig({ ...draft.config, quality: o.data })}
    />
  </>
);

const QOBUZ_QUALITIES = [
  { data: 5, label: "MP3 320 kbps" },
  { data: 6, label: "FLAC 16-bit / 44.1 kHz (CD)" },
  { data: 7, label: "FLAC 24-bit / 96 kHz" },
  { data: 27, label: "FLAC 24-bit / 192 kHz" },
];

/**
 * Qobuz signs in on qobuz.com itself (OAuth) in Steam's browser, so the password never passes
 * through MusicDeck. The dialog closes right away; signInToQobuz brings the user back when done.
 */
const QobuzFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  signedIn: boolean;
  closeModal?: () => void;
}> = ({ draft, setConfig, signedIn, closeModal }) => (
  <>
    <Field label="Account">
      <DialogButton
        onClick={() => {
          closeModal?.();
          signIn("qobuz", draft);
        }}
      >
        {signedIn
          ? `Signed in${draft.config.email ? ` as ${draft.config.email}` : ""} · sign in again`
          : "Sign in with Qobuz"}
      </DialogButton>
      <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
        Opens Qobuz's sign-in page and comes back here by itself. MusicDeck never sees your password.
      </div>
    </Field>
    <DropdownItem
      label="Streaming quality"
      rgOptions={QOBUZ_QUALITIES}
      selectedOption={draft.config.quality ?? 6}
      onChange={(o) => setConfig({ ...draft.config, quality: o.data })}
    />
  </>
);

const SPOTIFY_SHARED_APP = "d420a117a32841c2b3474932e49fb54b";
const SPOTIFY_DASHBOARD = "https://developer.spotify.com/dashboard";
/** A Spotify client ID: 32 hex digits, wherever it sits in what was copied. */
const findClientId = (text: string | null) => text?.match(/\b[0-9a-f]{32}\b/i)?.[0].toLowerCase() ?? null;

const SPOTIFY_BITRATES = [
  { data: 96, label: "Low (96 kbps)" },
  { data: 160, label: "Normal (160 kbps)" },
  { data: 320, label: "Very high (320 kbps)" },
];

/** Spotify: two consents on Spotify's own pages (playback, then library), handled as one sign-in. */
const SpotifyFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  signedIn: boolean;
  closeModal?: () => void;
  openDashboard: () => void;
}> = ({ draft, setConfig, signedIn, closeModal, openDashboard }) => {
  const clientId = (draft.config.client_id ?? "").trim();
  // a refresh token only works with the app that issued it
  const appChanged = signedIn && (clientId || SPOTIFY_SHARED_APP) !== (draft.config.signed_with ?? SPOTIFY_SHARED_APP);
  return (
    <>
      <Field label="Client ID (optional)">
        <TextField
          value={draft.config.client_id ?? ""}
          placeholder="Shared app"
          // a paste can bring text along with the ID; keep just the ID
          onChange={(e) => setConfig({ ...draft.config, client_id: findClientId(e.target.value) ?? e.target.value })}
        />
        <div style={{ fontSize: 12, opacity: 0.6, margin: "6px 0 10px" }}>
          Your own app gets its own rate limit instead of sharing one. Create an app with the redirect URI{" "}
          <b>http://127.0.0.1:8989/login</b> and Web API access, copy its Client ID and paste it here, then sign in
          again.
        </div>
        <DialogButton onClick={openDashboard}>
          <FaExternalLinkAlt style={{ marginRight: 8 }} /> Open Spotify Developer Dashboard
        </DialogButton>
      </Field>
      <Field label="Account">
        <DialogButton
          onClick={() => {
            closeModal?.();
            signIn("spotify", draft);
          }}
        >
          {appChanged
            ? "Sign in again to use this app"
            : signedIn
              ? `Signed in${draft.config.display_name ? ` as ${draft.config.display_name}` : ""} · sign in again`
              : "Sign in with Spotify"}
        </DialogButton>
        <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>
          Opens Spotify's sign-in pages and comes back here by itself. Needs Spotify Premium. MusicDeck never sees your
          password.
        </div>
      </Field>
      <DropdownItem
        label="Streaming quality"
        rgOptions={SPOTIFY_BITRATES}
        selectedOption={draft.config.bitrate ?? 320}
        onChange={(o) => setConfig({ ...draft.config, bitrate: o.data })}
      />
    </>
  );
};

/**
 * A server address, username and password: Navidrome/Subsonic, and Emby and Audiobookshelf (whose
 * password becomes a token on save). `passwordHint` explains other ways in, e.g. an API key.
 */
const ServerFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  hasCredentials: boolean;
  placeholder: string;
  passwordHint?: string;
}> = ({ draft, setConfig, hasCredentials, placeholder, passwordHint }) => (
  <>
    <Field label="Server URL" hint={unencryptedWarning(draft.config.url)}>
      <TextField
        value={draft.config.url ?? ""}
        placeholder={placeholder}
        onChange={(e) => setConfig({ ...draft.config, url: e.target.value })}
      />
    </Field>
    <Field label="Username">
      <TextField
        value={draft.config.username ?? ""}
        onChange={(e) => setConfig({ ...draft.config, username: e.target.value })}
      />
    </Field>
    <Field label="Password" hint={passwordHint}>
      <TextField
        bIsPassword
        value={draft.config.password ?? ""}
        placeholder={hasCredentials ? "Unchanged" : ""}
        onChange={(e) => setConfig({ ...draft.config, password: e.target.value })}
      />
    </Field>
  </>
);

/**
 * A sign-in code shown in the dialog (Jellyfin's Quick Connect, Plex's link code): asks the backend
 * for a code, shows it with where to enter it, and saves the source once it's approved elsewhere.
 * Closing the dialog abandons the attempt.
 */
const CodeSignIn: FC<{
  type: string;
  draft: ServiceDraft;
  options: Record<string, any>;
  label: string;
  instructions: string;
  disabled?: boolean;
  signedInAs: (service: ServiceInfo) => string;
  closeModal?: () => void;
}> = ({ type, draft, options, label, instructions, disabled, signedInAs, closeModal }) => {
  const [code, setCode] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const login = useRef<string | null>(null);

  useEffect(
    () => () => {
      if (login.current) signInCancel(login.current);
    },
    [],
  );

  const start = async () => {
    setStatus("Asking for a code…");
    const begun = await signInStart(type, options);
    if (!begun.ok) return setStatus(begun.error);
    login.current = begun.data.login_id;
    setCode(begun.data.code);
    setStatus(null);
    for (;;) {
      await new Promise((r) => setTimeout(r, 1500));
      if (login.current !== begun.data.login_id) return; // cancelled or restarted
      const poll = await signInPoll(begun.data.login_id, draft);
      if (poll.status === "error") {
        login.current = null;
        setCode(null);
        return setStatus(poll.error ?? "Sign-in failed");
      }
      if (poll.status === "done" && poll.service) {
        login.current = null;
        await refreshState();
        invalidateBrowserCache();
        notify(poll.service.name, signedInAs(poll.service));
        return closeModal?.();
      }
    }
  };

  return (
    <>
      {code ? (
        <div style={{ textAlign: "center", padding: "8px 0" }}>
          <div style={{ fontSize: 32, fontWeight: "bold", letterSpacing: 6 }}>{code}</div>
          <div style={{ fontSize: 12, opacity: 0.6, marginTop: 6 }}>{instructions} MusicDeck continues by itself.</div>
        </div>
      ) : (
        <DialogButton disabled={disabled} onClick={start}>
          {label}
        </DialogButton>
      )}
      {status && <div style={{ fontSize: 12, opacity: 0.8, marginTop: 6 }}>{status}</div>}
    </>
  );
};

/**
 * Jellyfin: Quick Connect (approve a code in any signed-in Jellyfin app, nothing typed here) or a
 * username and password, which the backend swaps for an access token on save.
 */
const JellyfinFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  hasCredentials: boolean;
  closeModal?: () => void;
}> = ({ draft, setConfig, hasCredentials, closeModal }) => (
  <>
    <Field label="Server address" hint={unencryptedWarning(draft.config.url)}>
      <TextField
        value={draft.config.url ?? ""}
        placeholder="https://jellyfin.example.com"
        onChange={(e) => setConfig({ ...draft.config, url: e.target.value })}
      />
    </Field>
    <Field label="Quick Connect">
      <CodeSignIn
        type="jellyfin"
        draft={draft}
        options={{ url: draft.config.url ?? "" }}
        disabled={!draft.config.url}
        label={hasCredentials ? "Sign in again with Quick Connect" : "Sign in with Quick Connect"}
        instructions="In a Jellyfin app you're signed in to, open your profile → Quick Connect and enter this code."
        signedInAs={(s) => `Signed in as ${s.config.username || "Jellyfin user"}`}
        closeModal={closeModal}
      />
    </Field>
    <div style={{ fontSize: 12, opacity: 0.6, margin: "4px 0 10px" }}>Or sign in with a username and password:</div>
    <Field label="Username">
      <TextField
        value={draft.config.username ?? ""}
        onChange={(e) => setConfig({ ...draft.config, username: e.target.value })}
      />
    </Field>
    <Field label="Password">
      <TextField
        bIsPassword
        value={draft.config.password ?? ""}
        placeholder={hasCredentials ? "Unchanged" : ""}
        onChange={(e) => setConfig({ ...draft.config, password: e.target.value })}
      />
    </Field>
  </>
);

const plexSignedIn = (s: ServiceInfo) =>
  `Signed in${s.config.username ? ` as ${s.config.username}` : ""} · ${s.config.server_name ?? "Plex"}`;

/**
 * Plex: a link code entered at plex.tv/link on any device signed in to Plex (or approved on Plex's
 * page in Steam's browser). The account's servers come from plex.tv; with more than one, pick here.
 * A typed address overrides plex.tv's, and alone is enough for a server that doesn't ask for a sign-in.
 */
const PlexFields: FC<{
  draft: ServiceDraft;
  setConfig: (c: Record<string, any>) => void;
  signedIn: boolean;
  closeModal?: () => void;
}> = ({ draft, setConfig, signedIn, closeModal }) => {
  const servers: { id: string; name: string }[] = draft.config.servers ?? [];
  return (
    <>
      <Field label="Account">
        <CodeSignIn
          type="plex"
          draft={draft}
          options={{ client_id: draft.config.client_id ?? "" }}
          label={
            signedIn && draft.config.username
              ? `Signed in as ${draft.config.username} · sign in again`
              : "Sign in with Plex"
          }
          instructions="On a phone or computer signed in to Plex, go to plex.tv/link and enter this code."
          signedInAs={plexSignedIn}
          closeModal={closeModal}
        />
        <DialogButton
          style={{ marginTop: 8 }}
          onClick={() => {
            closeModal?.();
            signIn("plex", draft);
          }}
        >
          Sign in on this Deck instead
        </DialogButton>
      </Field>
      {servers.length > 1 && (
        <DropdownItem
          label="Server"
          rgOptions={servers.map((s) => ({ data: s.id, label: s.name }))}
          selectedOption={draft.config.server ?? servers[0].id}
          onChange={(o) => setConfig({ ...draft.config, server: o.data })}
        />
      )}
      <Field label="Server address (optional)">
        <TextField
          value={draft.config.url ?? ""}
          placeholder={draft.config.server_url ?? "Found through your Plex account"}
          onChange={(e) => setConfig({ ...draft.config, url: e.target.value })}
        />
      </Field>
    </>
  );
};

export const ServiceModal: FC<{
  closeModal?: () => void;
  type: string;
  typeName: string;
  existing?: ServiceInfo;
  /** the form as it was, when coming back from the browser */
  initialDraft?: ServiceDraft;
}> = ({ closeModal, type, typeName, existing, initialDraft }) => {
  const [draft, setDraft] = useState<ServiceDraft>(
    initialDraft ??
      (existing
        ? {
            id: existing.id,
            type,
            name: existing.name,
            config: { ...existing.config },
          }
        : { type, name: "", config: type === "local" ? { paths: [] } : {} }),
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const setConfig = (config: Record<string, any>) => setDraft({ ...draft, config });

  // The dialog would sit over the browser, so it closes and comes back, as filled in, afterwards.
  // A client ID copied in the browser is picked up from the clipboard on the way back.
  const openDashboard = () => {
    closeModal?.();
    browseThenReturn(SPOTIFY_DASHBOARD, async () => {
      let back = draft;
      const id = !draft.config.client_id && findClientId(await readClipboard());
      if (id) {
        back = { ...draft, config: { ...draft.config, client_id: id } };
        notify("Spotify", "Client ID pasted from the clipboard");
      }
      showModal(<ServiceModal type={type} typeName={typeName} existing={existing} initialDraft={back} />);
    });
  };

  // a ref as well as state: a double tap arrives before the re-render that disables Save
  const savingNow = useRef(false);
  const save = async () => {
    if (savingNow.current) return;
    savingNow.current = true;
    setSaving(true);
    setError(null);
    try {
      const saved = await saveService(draft);
      if (!saved.ok) return setError(saved.error);
      if (type === "steam") {
        await refreshState();
        await syncSteam(); // what the account owns, so the first test can count it
      }
      const test = await testService(saved.data.id);
      await refreshState();
      invalidateBrowserCache();
      notify(saved.data.name, test.ok ? test.data : `Saved, but: ${test.error}`);
      closeModal?.();
    } catch (e) {
      setError(String(e));
    } finally {
      savingNow.current = false;
      setSaving(false);
    }
  };

  return (
    <ModalRoot closeModal={closeModal} onCancel={closeModal}>
      <DialogHeader>{existing ? `Edit ${existing.name}` : `Add ${typeName}`}</DialogHeader>
      <DialogBody>
        <Field label="Name">
          <TextField
            value={draft.name}
            placeholder={typeName}
            onChange={(e) => setDraft({ ...draft, name: e.target.value })}
          />
        </Field>
        {type === "local" && <LocalFields draft={draft} setConfig={setConfig} />}
        {type === "subsonic" && (
          <ServerFields
            draft={draft}
            setConfig={setConfig}
            hasCredentials={!!existing?.has_credentials}
            placeholder="https://music.example.com"
          />
        )}
        {type === "emby" && (
          <ServerFields
            draft={draft}
            setConfig={setConfig}
            hasCredentials={!!existing?.has_credentials}
            placeholder="https://emby.example.com"
          />
        )}
        {type === "audiobookshelf" && (
          <ServerFields
            draft={draft}
            setConfig={setConfig}
            hasCredentials={!!existing?.has_credentials}
            placeholder="https://abs.example.com"
            passwordHint="Or paste an API key here and leave the username empty"
          />
        )}
        {type === "jellyfin" && (
          <JellyfinFields
            draft={draft}
            setConfig={setConfig}
            hasCredentials={!!existing?.has_credentials}
            closeModal={closeModal}
          />
        )}
        {type === "plex" && (
          <PlexFields
            draft={draft}
            setConfig={setConfig}
            signedIn={!!existing?.has_credentials}
            closeModal={closeModal}
          />
        )}
        {type === "qobuz" && (
          <QobuzFields
            draft={draft}
            setConfig={setConfig}
            signedIn={!!existing?.has_credentials}
            closeModal={closeModal}
          />
        )}
        {type === "tidal" && (
          <TidalFields
            draft={draft}
            setConfig={setConfig}
            signedIn={!!existing?.has_credentials}
            closeModal={closeModal}
          />
        )}
        {type === "spotify" && (
          <SpotifyFields
            draft={draft}
            setConfig={setConfig}
            signedIn={!!existing?.has_credentials}
            closeModal={closeModal}
            openDashboard={openDashboard}
          />
        )}
        {type === "radio" && <RadioFields draft={draft} setConfig={setConfig} />}
        {type === "podcasts" && <PodcastFields draft={draft} setConfig={setConfig} />}
        {type === "livemusic" && (
          <div style={{ fontSize: 13, opacity: 0.7, lineHeight: 1.5 }}>
            The Internet Archive's Live Music Archive: over 250,000 concert recordings that bands allow to be shared.
            Free, no account, nothing to set up.
          </div>
        )}
        {type === "steam" && (
          <div style={{ fontSize: 13, opacity: 0.7, lineHeight: 1.5 }}>
            Plays the soundtracks you own on Steam. Installed ones play from this device, from every Steam library (SD
            cards too); the rest can be installed from MusicDeck. Nothing to set up.
          </div>
        )}
        {type === "youtube" && (
          <ToggleField
            label="Include all YouTube videos in search"
            description="Also finds uploads YouTube Music doesn't label as music. No account needed."
            checked={draft.config.include_videos !== false}
            onChange={(on) => setConfig({ ...draft.config, include_videos: on })}
          />
        )}
        {error && <div style={{ color: "#ff6b6b", marginTop: 12 }}>{error}</div>}
      </DialogBody>
      <DialogActions
        primary={saving ? "Saving…" : "Save"}
        onPrimary={save}
        primaryDisabled={saving}
        onCancel={closeModal}
      />
    </ModalRoot>
  );
};
