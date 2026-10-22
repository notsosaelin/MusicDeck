import { logEvents } from "./api";
import { pluginState } from "./store";

/**
 * Debug log for the player, written by the backend into the same file as stream events when
 * "Debug log" is on in the plugin settings. Events are batched so logging never blocks playback.
 */
let buffer: { t: number; event: string; data?: Record<string, unknown> }[] = [];
let timer: number | undefined;

export function dlog(event: string, data?: Record<string, unknown>) {
  if (!pluginState.get()?.debug_log) return;
  buffer.push({ t: Date.now(), event, data });
  if (timer === undefined) {
    timer = window.setTimeout(flush, 1500);
  }
}

function flush() {
  window.clearTimeout(timer);
  timer = undefined;
  if (!buffer.length) return;
  const entries = buffer;
  buffer = [];
  logEvents(entries).catch(() => {});
}
