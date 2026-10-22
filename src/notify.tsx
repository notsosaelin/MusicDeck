import { toaster } from "@decky/api";
import { FaMusic } from "react-icons/fa";
import { Track } from "./api";
import { mediaUrl } from "./store";

/**
 * Steam toast with the track's artwork as its logo, so notifications (queued, playing next,
 * couldn't play) show what they're about at a glance. They're momentary news, so they leave Steam's
 * notification list once shown and are never marked unread there.
 *
 * Note: the logo only appears when Decky's own toast renderer is active. On Steam builds where
 * Decky's renderer hook doesn't apply, Steam draws its native chat template, which takes plain text
 * only and shows a "?" avatar; body must therefore stay a string.
 */
export function notify(title: string, body: string, item?: { instanceId: string; track: Track }, duration = 2500) {
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  const sentAt = Math.floor(Date.now() / 1000) - 1; // Steam's notification times are in seconds
  toaster.toast({
    title,
    body,
    duration,
    expiration: duration + 1000,
    showNewIndicator: false,
    logo: art ? (
      <img src={art} style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 4 }} />
    ) : (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <FaMusic />
      </div>
    ),
  });
  window.setTimeout(() => removeFromList(body, sentAt), duration + 1000);
}

/**
 * Decky's `expiration` removes its own toasts from the list, but when Steam draws its chat template
 * instead, the toast becomes one of Steam's chat notifications (often grouped with the previous ones),
 * which Decky can't find. Remove that one: the notification with our text, created when we sent it.
 */
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
