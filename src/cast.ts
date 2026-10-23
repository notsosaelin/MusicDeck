import { addEventListener, removeEventListener } from "@decky/api";
import { CastStatus, Track, castControl, castLoad } from "./api";
import { dlog } from "./log";

/** How often the position moves on between the device's own reports (it only reports changes). */
const TICK_MS = 500;
/** Volume changes are sent at most this often (the sleep timer fades in steps of 100 ms). */
const VOLUME_EVERY_MS = 250;
/**
 * How long reports that disagree with what we just asked for are put down to the device not having
 * caught up yet. UPnP renderers are polled and slow to act (a Samsung TV takes seconds to pause or
 * seek), so for a while they keep reporting the old state; taking that as the device's own controls
 * undid the pause, or jumped the seek bar back.
 */
const CATCH_UP_MS = 6000;

type DeviceState = NonNullable<CastStatus["state"]>;

/**
 * Stands in for the player's <audio> element while casting: the same properties, methods and events
 * the player uses, forwarded to the device (py_modules/musicdeck/cast) and fed by its "cast_status"
 * reports. So the player's queue, play reports, sleep timer and so on work unchanged; only its
 * Web Audio parts (EQ, levelling, crossfade, gapless) have nothing to act on.
 */
export class RemoteAudio extends EventTarget {
  paused = true;
  ended = false;
  loop = false;
  duration = NaN;
  readyState = 0;
  error: { code: number; message: string } | null = null;
  // set by the player on its decks; meaningless here
  crossOrigin: string | null = null;
  preload = "";
  /** The device's volume changed by itself (its remote, a phone): the player shows it. */
  onVolume?: (volume: number) => void;

  private item?: { instanceId: string; track: Track };
  private src = "";
  private state: DeviceState = "idle";
  /** Position at `at` (ms), moved on from there while playing */
  private base = 0;
  private at = 0;
  private level = 1;
  private volumeTimer?: number;
  private lastVolumeSent = 0;
  private ticker?: number;
  /** Bumped per load, so reports and failures for a track that's been replaced are ignored */
  private generation = 0;
  /** A load is on its way to the device; its reports so far are about the previous track */
  private loading = false;
  /** What we last asked the device for, until it reports it (or CATCH_UP_MS passes) */
  private expected?: { playing?: boolean; position?: number; until: number };

  get currentTime(): number {
    if (this.state !== "playing" || this.paused) return this.base;
    const t = this.base + (Date.now() - this.at) / 1000;
    return Number.isFinite(this.duration) ? Math.min(t, this.duration) : t;
  }

  set currentTime(seconds: number) {
    this.moveTo(seconds);
    if (this.item && !this.loading) {
      this.expect({ position: this.base });
      this.send("seek", seconds);
    }
    this.fire("timeupdate");
  }

  get volume(): number {
    return this.level;
  }

  set volume(level: number) {
    this.level = Math.min(1, Math.max(0, level));
    window.clearTimeout(this.volumeTimer);
    const wait = Math.max(0, VOLUME_EVERY_MS - (Date.now() - this.lastVolumeSent));
    this.volumeTimer = window.setTimeout(() => {
      this.lastVolumeSent = Date.now();
      this.send("volume", this.level);
    }, wait);
  }

  /** Starts a session: the device's volume, nothing loaded. */
  reset(volume: number) {
    this.detach();
    this.level = volume;
  }

  /** Plays (or, without `autoplay`, cues) a track on the device from `startAt` seconds. */
  open(item: { instanceId: string; track: Track }, startAt: number, autoplay: boolean) {
    const generation = ++this.generation;
    this.item = item;
    this.src = `cast:${item.instanceId}/${item.track.id}`;
    this.ended = false;
    this.error = null;
    this.readyState = 0;
    this.duration = item.track.duration || NaN;
    this.loading = true;
    this.expected = undefined;
    this.state = "idle";
    this.moveTo(startAt);
    this.paused = !autoplay;
    this.fire("durationchange");
    if (autoplay) this.fire("waiting");
    castLoad(item.instanceId, item.track, startAt, autoplay)
      .then((r) => {
        if (generation !== this.generation) return;
        this.loading = false;
        if (!r.ok) return this.fail(r.error);
        // played or paused while the track was on its way
        if (this.paused === autoplay) this.send(this.paused ? "pause" : "play");
      })
      .catch((e) => generation === this.generation && this.fail(String(e)));
  }

  play(): Promise<void> {
    if (!this.item) return Promise.resolve();
    if (this.paused) {
      this.paused = false;
      this.fire("play");
      if (!this.loading) {
        // a track that played to its end starts again, as an element does
        if (this.ended) this.open(this.item, 0, true);
        else {
          this.expect({ playing: true });
          this.send("play");
          // shown as playing at once: a slow device's Play button otherwise looks unpressed, and the
          // second press pauses (if it never starts, its reports say so once the wait is over)
          this.moveTo(this.base);
          this.state = "playing";
          this.fire("playing");
          this.startTicking();
        }
      }
    }
    return Promise.resolve();
  }

  pause() {
    if (this.paused) return;
    this.base = this.currentTime;
    this.paused = true;
    if (this.item) {
      this.expect({ playing: false });
      this.send("pause");
    }
    this.fire("pause");
  }

  getAttribute(name: string) {
    return name === "src" && this.src ? this.src : null;
  }

  /** The player letting go of the track (clearing the queue, say): the device stops too. */
  removeAttribute(name: string) {
    if (name !== "src" || !this.src) return;
    const had = !!this.item;
    this.detach();
    if (had) this.send("stop");
  }

  load() {}

  /** Forgets the track without telling the device (the session is ending anyway). */
  detach() {
    this.generation++;
    window.clearTimeout(this.volumeTimer);
    this.stopTicking();
    this.item = undefined;
    this.src = "";
    this.paused = true;
    this.ended = false;
    this.loading = false;
    this.expected = undefined;
    this.state = "idle";
    this.base = 0;
    this.duration = NaN;
  }

  /** A "cast_status" report. */
  onStatus(status: CastStatus) {
    if (typeof status.volume === "number" && Math.abs(status.volume - this.level) > 0.01) {
      this.level = status.volume;
      this.onVolume?.(status.volume);
    }
    if (!this.item || !status.state) return;
    // the device is still reporting on the track before; a fresh LOAD answers with the new one
    if (this.loading && status.state === "idle" && !status.error) return;
    this.loading = false;
    status = this.caughtUp(status);
    if (!status.state) return;
    if (typeof status.duration === "number" && status.duration !== this.duration) {
      this.duration = status.duration;
      this.fire("durationchange");
    }
    if (typeof status.position === "number") this.moveTo(status.position);
    const was = this.state;
    this.state = status.state;
    if (status.state !== "idle" && this.readyState === 0) {
      this.readyState = 4;
      this.fire("canplay");
    }
    if (status.error) return this.fail(status.error);
    if (status.state === "playing") {
      if (this.paused) {
        this.paused = false; // played from the device's own controls
        this.fire("play");
      }
      if (was !== "playing") this.fire("playing");
      this.startTicking();
    } else {
      this.stopTicking();
      if (status.state === "buffering") this.fire("waiting");
      if (status.state === "paused" && !this.paused) {
        this.paused = true; // paused on the device
        this.fire("pause");
      }
    }
    this.fire("timeupdate");
    if (status.ended) this.finish();
  }

  private expect(what: { playing?: boolean; position?: number }) {
    this.expected = { ...this.expected, ...what, until: Date.now() + CATCH_UP_MS };
  }

  /**
   * A report with what the device hasn't caught up on yet left out: a state other than the one we
   * asked for (`state` dropped: nothing to act on), a position far from where we sought.
   */
  private caughtUp(status: CastStatus): CastStatus {
    const expected = this.expected;
    if (!expected || Date.now() > expected.until || status.ended || status.error) {
      this.expected = undefined;
      return status;
    }
    const result = { ...status };
    if (expected.playing !== undefined) {
      const playing = status.state === "playing" || status.state === "buffering";
      if (playing === expected.playing && status.state !== "idle") expected.playing = undefined;
      else result.state = undefined;
    }
    if (expected.position !== undefined && typeof status.position === "number") {
      if (Math.abs(status.position - expected.position) < 3) expected.position = undefined;
      else result.position = undefined;
    }
    if (expected.playing === undefined && expected.position === undefined) this.expected = undefined;
    return result;
  }

  private finish() {
    dlog("cast ended", { loop: this.loop });
    if (this.loop && this.item) return this.open(this.item, 0, true);
    this.ended = true;
    if (!this.paused) {
      this.paused = true;
      this.fire("pause");
    }
    this.fire("ended");
  }

  private fail(message: string) {
    dlog("cast error", { message });
    this.loading = false;
    this.stopTicking();
    this.error = { code: 4, message };
    this.fire("error");
  }

  private moveTo(seconds: number) {
    this.base = Math.max(0, seconds || 0);
    this.at = Date.now();
  }

  private startTicking() {
    if (this.ticker === undefined) this.ticker = window.setInterval(() => this.fire("timeupdate"), TICK_MS);
  }

  private stopTicking() {
    window.clearInterval(this.ticker);
    this.ticker = undefined;
  }

  private send(action: string, value?: number) {
    castControl(action, value).then(
      (r) => !r.ok && dlog("cast control failed", { action, error: r.error }),
      () => {},
    );
  }

  private fire(name: string) {
    this.dispatchEvent(new Event(name));
  }
}

/** Where "cast_status" reports go: set by the player while it casts. */
let listener: ((status: CastStatus) => void) | undefined;

export function onCastStatus(handler: ((status: CastStatus) => void) | undefined) {
  listener = handler;
}

/**
 * Listens for the backend's "cast_status" reports while the plugin is loaded; returns the unregister
 * (each load of the plugin listens anew, so an unloaded copy mustn't keep listening).
 */
export function watchCastStatus(): () => void {
  const handler = (status: CastStatus) => listener?.(status);
  addEventListener("cast_status", handler);
  return () => {
    removeEventListener("cast_status", handler);
    listener = undefined;
  };
}
