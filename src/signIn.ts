import { Navigation, Router } from "@decky/ui";
import {
  ServiceDraft,
  ServiceInfo,
  qobuzLoginCancel,
  qobuzLoginPoll,
  qobuzLoginStart,
  spotifyLoginCancel,
  spotifyLoginPoll,
  spotifyLoginStart,
  tidalLoginCancel,
  tidalLoginPoll,
  tidalLoginStart,
} from "./api";
import { invalidateBrowserCache } from "./nav";
import { notify } from "./notify";
import { refreshState } from "./store";

const BROWSER_ROUTE = "/externalweb";
const POLL_MS = 1000;
/** Left the sign-in page without finishing: give up after this long, so nothing lingers. */
const ABANDON_MS = 15_000;

type Poll = {
  status: string;
  error?: string;
  url?: string;
  service?: ServiceInfo;
};

interface Provider {
  name: string;
  start: (
    draft: ServiceDraft,
  ) => Promise<{ ok: true; data: { login_id: string; url: string } } | { ok: false; error: string }>;
  poll: (loginId: string, draft: ServiceDraft) => Promise<Poll>;
  cancel: (loginId: string) => Promise<void>;
  signedIn: (service: ServiceInfo) => string;
}

const PROVIDERS: Record<string, Provider> = {
  qobuz: {
    name: "Qobuz",
    start: () => qobuzLoginStart(),
    poll: qobuzLoginPoll,
    cancel: qobuzLoginCancel,
    signedIn: (s) => `Signed in${s.config.email ? ` as ${s.config.email}` : ""}`,
  },
  spotify: {
    name: "Spotify",
    start: (draft) => spotifyLoginStart((draft.config.client_id ?? "").trim()),
    poll: spotifyLoginPoll,
    cancel: spotifyLoginCancel,
    signedIn: (s) => `Signed in${s.config.display_name ? ` as ${s.config.display_name}` : ""}`,
  },
  tidal: {
    name: "TIDAL",
    start: () => tidalLoginStart(),
    poll: tidalLoginPoll,
    cancel: tidalLoginCancel,
    signedIn: (s) => `Signed in${s.config.username ? ` as ${s.config.username}` : ""}`,
  },
};

const currentRoute = (): string => {
  try {
    return (Router as any).WindowStore.GamepadUIMainWindowInstance.m_history.location.pathname ?? "";
  } catch {
    return "";
  }
};

/** Close the sign-in browser (each page visited is one history entry) and land back where we were. */
async function leaveBrowser() {
  for (let i = 0; i < 4 && currentRoute().startsWith(BROWSER_ROUTE); i++) {
    Navigation.NavigateBack();
    await new Promise((r) => setTimeout(r, 300));
  }
}

/**
 * Opens a page in Steam's browser and calls `onReturn` once the user backs out of it, so a dialog
 * closed to make room for the browser can come back as it was.
 */
export async function browseThenReturn(url: string, onReturn: () => void) {
  Navigation.NavigateToExternalWeb(url);
  const wait = () => new Promise((r) => setTimeout(r, 500));
  for (let i = 0; i < 20 && !currentRoute().startsWith(BROWSER_ROUTE); i++) await wait();
  const until = Date.now() + 30 * 60_000;
  while (currentRoute().startsWith(BROWSER_ROUTE) && Date.now() < until) await wait();
  onReturn();
}

/** Clipboard text, if Steam lets us read it (the Steam UI window has to be focused). */
export async function readClipboard(): Promise<string | null> {
  try {
    const win = (Router as any).WindowStore.GamepadUIMainWindowInstance.BrowserWindow as Window;
    return await win.navigator.clipboard.readText();
  } catch {
    return null;
  }
}

let running = false;

/**
 * Browser sign-in for services that use OAuth (Qobuz, Spotify), start to finish: opens the
 * service's own page in Steam's browser, follows on to the next page when a service needs two
 * consents (Spotify), then closes the browser and saves the source. The user never has to back
 * out of anything.
 */
export async function signIn(type: string, draft: ServiceDraft) {
  const provider = PROVIDERS[type];
  if (!provider || running) return;
  running = true;
  try {
    const start = await provider.start(draft);
    if (!start.ok) {
      notify(provider.name, `Couldn't start sign-in: ${start.error}`);
      return;
    }
    let url = start.data.url;
    Navigation.NavigateToExternalWeb(url);
    let awaySince = 0;
    for (;;) {
      await new Promise((r) => setTimeout(r, POLL_MS));
      const poll = await provider.poll(start.data.login_id, draft);
      if (poll.status === "done" || poll.status === "error") {
        await leaveBrowser();
        if (poll.status === "done" && poll.service) {
          await refreshState();
          invalidateBrowserCache();
          notify(provider.name, provider.signedIn(poll.service));
        } else {
          notify(provider.name, poll.error || "Sign-in failed");
        }
        return;
      }
      if (poll.url && poll.url !== url) {
        // next consent page (Spotify: playback first, then library access)
        url = poll.url;
        Navigation.NavigateToExternalWeb(url);
        awaySince = 0;
        continue;
      }
      if (!currentRoute().startsWith(BROWSER_ROUTE)) {
        awaySince ||= Date.now();
        if (Date.now() - awaySince > ABANDON_MS) {
          await provider.cancel(start.data.login_id);
          return;
        }
      } else {
        awaySince = 0;
      }
    }
  } finally {
    running = false;
  }
}
