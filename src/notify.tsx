import { toaster } from "@decky/api";
import { FaMusic } from "react-icons/fa";
import { Track } from "./api";
import { mediaUrl } from "./store";

/**
 * Steam toast with the track's artwork as its logo, so notifications (queued, playing next,
 * couldn't play) show what they're about at a glance.
 *
 * Note: the logo only appears when Decky's own toast renderer is active. On Steam builds where
 * Decky's renderer hook doesn't apply, Steam draws its native chat template, which takes plain text
 * only and shows a "?" avatar; body must therefore stay a string.
 */
export function notify(title: string, body: string, item?: { instanceId: string; track: Track }, duration = 2500) {
  const art = item?.track.art ? mediaUrl("art", item.instanceId, item.track.art) : undefined;
  toaster.toast({
    title,
    body,
    duration,
    logo: art ? (
      <img src={art} style={{ width: "100%", height: "100%", objectFit: "cover", borderRadius: 4 }} />
    ) : (
      <div style={{ width: "100%", height: "100%", display: "flex", alignItems: "center", justifyContent: "center" }}>
        <FaMusic />
      </div>
    ),
  });
}
