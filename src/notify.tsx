import { Navigation, findModuleExport } from "@decky/ui";
import { Track } from "./api";
import { ACCENT } from "./components/common";
import { openMusicDeck } from "./qam";
import { mediaUrl } from "./store";

/*
 * Steam toasts for MusicDeck, with the track's artwork (or a MusicDeck note) as their picture.
 *
 * Decky's toaster can't show one on current SteamOS: its own renderer doesn't apply there (Decky
 * toasts reach Steam components that fail on them), so Steam, or a plugin like Steamcord that
 * reroutes every Decky toast, draws them in Steam's chat-message style, with a "?" for a picture.
 * MusicDeck uses that style itself, the way Steamcord shows Discord avatars: each toast comes "from"
 * a stand-in persona, an account id in a range no Steam account uses, whose name is the toast's
 * title and whose avatar is the artwork. Steam's chat sound becomes its ordinary toast sound,
 * tapping the toast opens MusicDeck, and the toast leaves Steam's notification list once shown.
 */

const STEAMID64_BASE = BigInt("76561197960265728");
/** Stand-in account ids: 0xDD000000-0xDD00FFFF (Steamcord uses 0xDE…, real accounts are far below). */
const PERSONA_BASE = 0xdd000000;
const PERSONA_MASK = 0xffff;
/** Steam keeps a toast up about this long; it leaves the notification list after. */
const TOAST_MS = 6000;
/** Steam refreshes an unknown persona shortly after it's created, wiping its name; set it again until then. */
const NAME_RETRIES_MS = [100, 300, 800, 1500, 2500, 4000];
const FRIEND_CHAT_MESSAGE = 2; // EClientNotificationType: a private message, the style with a sender's avatar
const TOAST_SOUND = "/sounds/deck_ui_toast.wav";

/** A note on MusicDeck's blue, for toasts without artwork (an inline image: no request, nothing to fail). */
const MUSICDECK_ICON =
  "data:image/svg+xml;base64," +
  btoa(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" fill="${ACCENT}"/>` +
      `<path d="M26 16v22.5a7 7 0 1 0 4 6.3V24h14v-8z" fill="#fff"/></svg>`,
  );

export function notify(title: string, body: string, item?: { instanceId: string; track: Track }) {
  const art = (item?.track.art && mediaUrl("art", item.instanceId, item.track.art)) || MUSICDECK_ICON;
  try {
    installHooks();
    const accountid = PERSONA_BASE + (hash(`${title}\n${art}`) & PERSONA_MASK);
    const steamid = (STEAMID64_BASE + BigInt(accountid)).toString();
    dressPersona(accountid, steamid, title, art);
    const sentAt = Math.floor(Date.now() / 1000) - 1; // Steam's notification times are in seconds
    (window as any).SteamClient.ClientNotifications.DisplayClientNotification(
      FRIEND_CHAT_MESSAGE,
      JSON.stringify({ title: "", body, steamid }),
      () => {},
    );
    window.setTimeout(() => removeFromList(body, sentAt), TOAST_MS);
  } catch (e) {
    console.error("[MusicDeck] notification failed", e);
  }
}

function hash(text: string): number {
  let h = 5381;
  for (let i = 0; i < text.length; i++) h = (Math.imul(h, 33) ^ text.charCodeAt(i)) >>> 0;
  return h;
}

/**
 * Names the stand-in persona and gives it the artwork. The avatar is a getter (Steam reads it when
 * drawing); the name is a MobX field Steam overwrites when it refreshes the unknown account, so it's
 * set again a few times while the toast is up.
 */
function dressPersona(accountid: number, steamid: string, name: string, avatar: string) {
  const persona = (window as any).friendStore?.GetFriendState?.({
    GetAccountID: () => accountid,
    ConvertTo64BitString: () => steamid,
    BIsValid: () => true,
  })?.m_persona;
  if (!persona) return;
  for (const key of ["avatar_url_small", "avatar_url_medium", "avatar_url_full"])
    Object.defineProperty(persona, key, { get: () => avatar, configurable: true });
  const setName = () => {
    if (persona.m_strPlayerName !== name) persona.m_strPlayerName = name;
  };
  setName();
  for (const ms of NAME_RETRIES_MS) window.setTimeout(setName, ms);
}

/** Whether a Steam notification (or a click's steam id) is one of MusicDeck's stand-ins. */
function isOurs(steamid: unknown): boolean {
  try {
    const accountid = Number(BigInt(String(steamid)) - STEAMID64_BASE);
    return accountid >= PERSONA_BASE && accountid <= PERSONA_BASE + PERSONA_MASK;
  } catch {
    return false;
  }
}

/*
 * Two hooks into Steam: its notification sound, so ours play the toast sound instead of the chat
 * ping, and its open-a-chat action, so tapping ours opens MusicDeck. They wrap whatever is there
 * (Steamcord wraps the same two) and pass everything else through.
 *
 * Wrapped functions can't be cleanly unwrapped once something else has wrapped them too, so the
 * hooks stay for the Steam session, shared by every copy of the plugin loaded in it (like the Quick
 * Access panel registry). What they do comes from the registry, which the newest copy fills and
 * an unloading copy empties; a copy whose hooks themselves changed raises HOOKS_VERSION, and its
 * hooks are installed on top of the old ones.
 */
const HOOKS_KEY = "__musicdeckToastHooks";
const HOOKS_VERSION = 2;
interface ToastHooks {
  version: number;
  /** true when the notification is ours (and its sound has been dealt with) */
  sound: ((notification: any) => boolean) | null;
  /** true when the tapped toast is ours (and has been acted on) */
  click: ((steamid: unknown) => boolean) | null;
}

function installHooks() {
  const w = window as any;
  const hooks: ToastHooks = (w[HOOKS_KEY] ??= { version: 0, sound: null, click: null });
  if (typeof hooks.version !== "number") hooks.version = 0; // the first, unversioned hooks
  hooks.sound = ourSound;
  hooks.click = ourClick;
  if (hooks.version >= HOOKS_VERSION) return;
  hooks.version = HOOKS_VERSION;

  const store = w.NotificationStore;
  const proto = store && Object.getPrototypeOf(store);
  const wrapSound = (play: any) =>
    function (this: any, notification: any) {
      if (hooks.sound?.(notification)) return;
      return play.call(this, notification);
    };
  // on the prototype, for whatever wraps it later (Steamcord re-reads it when it re-patches), and on
  // the store itself if something already wraps it there
  if (typeof proto?.PlayNotificationSound === "function") proto.PlayNotificationSound = wrapSound(proto.PlayNotificationSound);
  if (Object.prototype.hasOwnProperty.call(store ?? {}, "PlayNotificationSound"))
    store.PlayNotificationSound = wrapSound(store.PlayNotificationSound);

  const chats = findModuleExport(
    (e: any) => typeof e?.ShowFriendChatDialog === "function" && typeof e?.ShowChatRoomGroupDialog === "function",
  );
  if (chats) {
    const open = chats.ShowFriendChatDialog;
    chats.ShowFriendChatDialog = function (this: any, context: any, steamID: any, ...rest: any[]) {
      let id: unknown;
      try {
        id = steamID?.ConvertTo64BitString?.();
      } catch {}
      if (hooks.click?.(id)) return;
      return open.call(this, context, steamID, ...rest);
    };
  }
}

function ourSound(notification: any): boolean {
  // the notification's data is a protobuf message: its fields are methods (steamid() on current
  // builds, steamid_sender() on some others)
  const data = notification?.data;
  let sender: unknown;
  try {
    const field = data?.steamid_sender ?? data?.steamid;
    sender = typeof field === "function" ? field.call(data) : field;
  } catch {}
  if (!isOurs(sender)) return false;
  playToastSound();
  return true;
}

function ourClick(steamid: unknown): boolean {
  if (!isOurs(steamid)) return false;
  Navigation.CloseSideMenus();
  openMusicDeck();
  return true;
}

/** Plugin unload: the hooks stay (see above) but stop treating anything as ours. */
export function releaseToastHooks() {
  const hooks: ToastHooks | undefined = (window as any)[HOOKS_KEY];
  if (hooks?.sound === ourSound) hooks.sound = null;
  if (hooks?.click === ourClick) hooks.click = null;
}

let audio: any;
function playToastSound() {
  try {
    audio ??= findModuleExport((e: any) => typeof e?.GamepadUIAudio?.m_AudioPlaybackManager?.PlayAudioURL === "function")
      ?.GamepadUIAudio?.m_AudioPlaybackManager;
    audio?.PlayAudioURL?.(TOAST_SOUND);
  } catch {}
}

/** Takes our toast out of Steam's notification list once it has been shown: it's momentary news. */
function removeFromList(body: string, sentAt: number) {
  const store = (window as any).NotificationStore;
  const list: any[] = store?.m_rgNotificationTray ?? [];
  const bodyOf = (data: any) => {
    try {
      return typeof data?.body === "function" ? data.body() : data?.body;
    } catch {
      return undefined;
    }
  };
  for (const group of list) {
    const notifications: any[] = group.notifications ?? [];
    const i = notifications.findIndex((n) => n.rtCreated >= sentAt && bodyOf(n.data) === body);
    if (i < 0) continue;
    if (notifications.length === 1) store.RemoveGroupFromTray(group);
    else notifications.splice(i, 1);
    return;
  }
}
