import Hls from "hls.js/dist/hls.light.min.js";
import {
  EqSettings,
  Feature,
  PlaybackSettings,
  Session,
  Track,
  getRadio,
  saveSession,
  scrobble,
  forgetStream,
  streamInfo,
} from "./api";
import { dlog } from "./log";
import { notify } from "./notify";
import { EQ_BANDS } from "./eq";
import { Store, mediaUrl, pluginState } from "./store";

/** A play counts once half the track (or 4 minutes) has been heard, the Last.fm/Subsonic convention. */
const SCROBBLE_FRACTION = 0.5;
const SCROBBLE_MAX_SECONDS = 240;
const SESSION_SAVE_INTERVAL_MS = 20_000;
/** Playing but the clock hasn't moved for this long: treat as a stall and recover. */
const STALL_TIMEOUT_MS = 12_000;

export const hasFeature = (instanceId: string, feature: Feature) =>
  !!pluginState
    .get()
    ?.services.find((s) => s.id === instanceId)
    ?.features?.includes(feature);

export interface QueueItem {
  /** Service instance the track belongs to; the queue can mix services. */
  instanceId: string;
  track: Track;
}

export type RepeatMode = "off" | "all" | "one";

export interface PlayerState {
  queue: QueueItem[];
  /** Index into `queue` of the current track, or -1. */
  index: number;
  playing: boolean;
  loading: boolean;
  shuffle: boolean;
  repeat: RepeatMode;
  volume: number;
}

export interface Position {
  current: number;
  duration: number;
}

/**
 * The audio engine. Lives in Steam's shared JS context for as long as the plugin is loaded,
 * so playback continues while the QAM is closed and while games run.
 *
 * Graph: <audio> -> levelling (ReplayGain) -> preamp -> 10 biquad filters -> destination. Built lazily
 * on first play because createMediaElementSource can only be called once per element.
 */
export class Player {
  readonly state = new Store<PlayerState>({
    queue: [],
    index: -1,
    playing: false,
    loading: false,
    shuffle: false,
    repeat: "off",
    volume: 0.8,
  });
  readonly position = new Store<Position>({ current: 0, duration: 0 });

  private audio = new Audio();
  private ctx?: AudioContext;
  private preamp?: GainNode;
  private leveller?: GainNode;
  private playback: PlaybackSettings = { levelling: "track", scrobble: true, queue_sync: true, resume: true };
  /**
   * Play-report bookkeeping for the current *listen*. A listen starts when a track is played (or
   * repeat-one loops back to its start); stream retries and session restores continue it, so they
   * never report the same play twice.
   */
  private nowPlayingSent = false;
  private submitted = false;
  private prefetched = false;
  private lastTime = 0;
  private sessionTimer?: number;
  private extendingRadio = false;
  /** Seek target applied once metadata loads (restoring a session) */
  private pendingSeek?: number;
  /** hls.js instance while an HLS source (SoundCloud) is attached */
  private hls?: Hls;
  /** Incremented per load so a slow stream resolution can't clobber a newer track */
  private loadToken = 0;
  /** True while the previous source is being torn down (its error events must be ignored) */
  private switching = false;
  private filters: BiquadFilterNode[] = [];
  private eq: EqSettings = { enabled: false, preset: "Flat", gains: EQ_BANDS.map(() => 0), preamp: 0 };
  /** Play order when shuffling: a permutation of queue indices. */
  private order: number[] = [];
  private consecutiveErrors = 0;
  /** Recovery attempts for the current track; one fresh re-resolve before giving up */
  private recoveries = 0;
  private stallWatch?: number;
  private lastProgress = { time: 0, at: 0 };

  constructor() {
    // Required for the Web Audio graph to read samples from the loopback stream server
    this.audio.crossOrigin = "anonymous";
    this.audio.preload = "auto";
    this.audio.addEventListener("playing", () => {
      dlog("audio playing", { at: this.audio.currentTime });
      this.state.update({ playing: true, loading: false });
      this.onPlaying();
    });
    this.audio.addEventListener("pause", () => {
      dlog("audio pause", { at: this.audio.currentTime, ended: this.audio.ended });
      this.state.update({ playing: false });
      this.persistSession();
    });
    this.audio.addEventListener("loadedmetadata", () => {
      if (this.pendingSeek !== undefined) {
        this.audio.currentTime = this.pendingSeek;
        this.pendingSeek = undefined;
      }
    });
    this.audio.addEventListener("waiting", () => {
      dlog("audio waiting", { at: this.audio.currentTime });
      this.state.update({ loading: true });
    });
    for (const name of ["stalled", "suspend", "emptied", "abort"]) {
      this.audio.addEventListener(name, () => dlog(`audio ${name}`, { at: this.audio.currentTime }));
    }
    this.audio.addEventListener("canplay", () => {
      this.consecutiveErrors = 0;
      this.state.update({ loading: false });
    });
    this.audio.addEventListener("timeupdate", () => {
      this.updatePosition();
      this.maybeScrobble();
    });
    this.audio.addEventListener("durationchange", () => this.updatePosition());
    this.audio.addEventListener("ended", () => {
      dlog("audio ended", { at: this.audio.currentTime, duration: this.audio.duration });
      this.onEnded();
    });
    this.audio.addEventListener("error", () => {
      const err = this.audio.error;
      dlog("audio error", { code: err?.code, message: err?.message, hls: !!this.hls, switching: this.switching });
      // Tearing down the previous source (switching tracks, hls.js detaching) makes the element
      // report an error that isn't about the new track; acting on it skipped good songs. With HLS
      // attached, hls.js reports real failures itself, with proper fatal flags.
      if (this.switching || this.hls || err?.code === MediaError.MEDIA_ERR_ABORTED) return;
      this.recover(`media error ${err?.code ?? ""} ${err?.message ?? ""}`.trim());
    });
    this.stallWatch = window.setInterval(() => this.checkStall(), 2000);
  }

  destroy() {
    window.clearInterval(this.sessionTimer);
    window.clearInterval(this.stallWatch);
    this.persistSession();
    this.detachHls();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    this.ctx?.close();
  }

  get current(): QueueItem | undefined {
    const { queue, index } = this.state.get();
    return queue[index];
  }

  setPlayback(playback: PlaybackSettings) {
    this.playback = playback;
    this.applyLevelling();
  }

  /** Put a saved queue back without starting playback, ready to resume where it left off. */
  restore(session: Session) {
    if (!session.queue?.length || this.state.get().queue.length) return;
    const index = Math.min(Math.max(session.index, 0), session.queue.length - 1);
    this.state.update({ queue: session.queue, index });
    this.order = this.buildOrder(session.queue.length, index);
    const item = session.queue[index];
    // a session saved mid-song is a listen that was already reported when it started
    const at = session.position || 0;
    this.nowPlayingSent = at > 5;
    this.submitted = at >= Math.min((item.track.duration || 0) * SCROBBLE_FRACTION, SCROBBLE_MAX_SECONDS);
    this.prefetched = false;
    this.position.set({ current: session.position || 0, duration: item.track.duration || 0 });
    void this.prepare(index, false, session.position || 0);
  }

  /** Patch a track everywhere it appears in the queue (e.g. after starring it). */
  updateTrack(instanceId: string, trackId: string, patch: Partial<Track>) {
    const { queue } = this.state.get();
    if (!queue.some((q) => q.instanceId === instanceId && q.track.id === trackId)) return;
    this.state.update({
      queue: queue.map((q) =>
        q.instanceId === instanceId && q.track.id === trackId ? { ...q, track: { ...q.track, ...patch } } : q,
      ),
    });
  }

  // --- queue ---

  /** Replace the queue and start playing at `start`. */
  playList(instanceId: string, tracks: Track[], start = 0, shuffle?: boolean) {
    if (!tracks.length) return;
    const queue = tracks.map((track) => ({ instanceId, track }));
    if (shuffle !== undefined) this.state.update({ shuffle });
    this.state.update({ queue });
    // "Shuffle play" starts on a random track; picking a track while shuffle is on starts on that one
    this.order = this.buildOrder(queue.length, shuffle ? -1 : start);
    this.load(this.state.get().shuffle ? this.order[0] : start);
  }

  enqueue(instanceId: string, tracks: Track[], next = false) {
    const { queue, index } = this.state.get();
    const items = tracks.map((track) => ({ instanceId, track }));
    const at = next && index >= 0 ? index + 1 : queue.length;
    const updated = [...queue.slice(0, at), ...items, ...queue.slice(at)];
    this.state.update({ queue: updated });
    this.order = this.buildOrder(updated.length, index);
    if (index < 0) this.load(0);
  }

  removeAt(i: number) {
    const { queue, index } = this.state.get();
    if (i === index) {
      if (queue.length === 1) return this.clear();
      this.state.update({ queue: queue.filter((_, j) => j !== i), index: index - 1 });
      this.order = this.buildOrder(queue.length - 1, -1);
      this.load(Math.min(i, queue.length - 2));
      return;
    }
    this.state.update({ queue: queue.filter((_, j) => j !== i), index: i < index ? index - 1 : index });
    this.order = this.buildOrder(queue.length - 1, this.state.get().index);
  }

  clear() {
    saveSession({ queue: [], index: -1, position: 0 }).catch(() => {});
    this.loadToken++;
    this.detachHls();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    this.order = [];
    this.state.update({ queue: [], index: -1, playing: false, loading: false });
    this.position.set({ current: 0, duration: 0 });
  }

  jumpTo(i: number) {
    dlog("jumpTo", { i });
    this.load(i);
  }

  // --- transport ---

  toggle() {
    dlog("toggle", { paused: this.audio.paused });
    if (this.state.get().index < 0) return;
    if (this.audio.paused) this.play();
    else this.audio.pause();
  }

  next(auto = false) {
    dlog("next", { auto, from: this.state.get().index });
    const { queue, index, repeat } = this.state.get();
    if (!queue.length) return;
    const pos = this.order.indexOf(index);
    if (pos + 1 < this.order.length) return this.load(this.order[pos + 1]);
    if (auto && repeat === "off" && this.playback.autoplay !== false && this.current) {
      // End of the queue: keep going with similar music, like a radio
      if (hasFeature(this.current.instanceId, "radio")) return void this.extendWithRadio(true);
    }
    if (repeat === "all" || !auto) {
      // wrap; a manual skip on the last track wraps too, which is what most players do
      if (this.state.get().shuffle) this.order = this.buildOrder(queue.length, -1);
      return this.load(this.order[0]);
    }
    this.audio.pause();
  }

  previous() {
    dlog("previous", { from: this.state.get().index, at: this.audio.currentTime });
    // Restart the track if we're more than a few seconds in, like every hardware player
    if (this.audio.currentTime > 3) {
      this.audio.currentTime = 0;
      return;
    }
    const pos = this.order.indexOf(this.state.get().index);
    if (pos > 0) this.load(this.order[pos - 1]);
    else this.audio.currentTime = 0;
  }

  seek(seconds: number) {
    if (Number.isFinite(seconds)) this.audio.currentTime = seconds;
  }

  setVolume(volume: number) {
    this.audio.volume = Math.min(1, Math.max(0, volume));
    this.state.update({ volume: this.audio.volume });
  }

  setShuffle(shuffle: boolean) {
    this.state.update({ shuffle });
    this.order = this.buildOrder(this.state.get().queue.length, this.state.get().index);
  }

  cycleRepeat() {
    const modes: RepeatMode[] = ["off", "all", "one"];
    const repeat = modes[(modes.indexOf(this.state.get().repeat) + 1) % modes.length];
    this.audio.loop = repeat === "one";
    this.state.update({ repeat });
  }

  // --- equalizer ---

  setEq(eq: EqSettings) {
    this.eq = eq;
    this.applyEq();
  }

  private applyLevelling() {
    if (!this.leveller) return;
    const gain = this.current?.track.gain;
    if (!gain || this.playback.levelling === "off") {
      this.leveller.gain.value = 1;
      return;
    }
    const album = this.playback.levelling === "album";
    const db = album ? gain.album : gain.track;
    const peak = (album ? gain.album_peak : gain.track_peak) || 1;
    // never boost a track past its own peak, so levelling can't introduce clipping
    this.leveller.gain.value = Math.min(Math.pow(10, db / 20), 1 / peak);
  }

  private applyEq() {
    if (!this.preamp) return;
    const on = this.eq.enabled;
    this.preamp.gain.value = on ? Math.pow(10, this.eq.preamp / 20) : 1;
    this.filters.forEach((f, i) => (f.gain.value = on ? (this.eq.gains[i] ?? 0) : 0));
  }

  private ensureGraph() {
    if (this.ctx) return;
    this.ctx = new AudioContext();
    const source = this.ctx.createMediaElementSource(this.audio);
    this.leveller = this.ctx.createGain();
    this.preamp = this.ctx.createGain();
    this.filters = EQ_BANDS.map((freq, i) => {
      const f = this.ctx!.createBiquadFilter();
      f.type = i === 0 ? "lowshelf" : i === EQ_BANDS.length - 1 ? "highshelf" : "peaking";
      f.frequency.value = freq;
      f.Q.value = 1.4;
      return f;
    });
    let node: AudioNode = source.connect(this.leveller).connect(this.preamp);
    for (const f of this.filters) node = node.connect(f);
    node.connect(this.ctx.destination);
    this.applyEq();
    this.applyLevelling();
  }

  // --- internals ---

  private buildOrder(length: number, first: number): number[] {
    const indices = Array.from({ length }, (_, i) => i);
    if (!this.state.get().shuffle) return indices;
    // Fisher-Yates, then move `first` (the playing track) to the front so it isn't replayed
    for (let i = indices.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [indices[i], indices[j]] = [indices[j], indices[i]];
    }
    if (first >= 0) {
      indices.splice(indices.indexOf(first), 1);
      indices.unshift(first);
    }
    return indices;
  }

  private load(index: number) {
    const item = this.state.get().queue[index];
    if (!item) return;
    this.state.update({ index, loading: true });
    this.position.set({ current: 0, duration: item.track.duration || 0 });
    this.resetTrackState();
    this.recoveries = 0;
    dlog("load", { index, id: item.track.id, title: item.track.title, instance: item.instanceId });
    void this.prepare(index, true, 0);
    this.persistSession();
  }

  /**
   * Resolves the track's stream (remote sources may need extraction) and attaches it, plain or via
   * hls.js. Superseded loads are dropped, so skipping quickly through slow sources is safe.
   */
  private async prepare(index: number, autoplay: boolean, startAt: number) {
    const token = ++this.loadToken;
    const item = this.state.get().queue[index];
    const url = item && mediaUrl("stream", item.instanceId, item.track.id);
    if (!item || !url) return;
    this.state.update({ index });
    const info = await streamInfo(item.instanceId, item.track.id).catch((e) => ({
      ok: false as const,
      error: String(e),
    }));
    if (token !== this.loadToken) return;
    dlog("resolved", info.ok ? { hls: info.data.hls, startAt } : { error: info.error });
    if (!info.ok) {
      this.onError(info.error);
      return;
    }
    this.lastProgress = { time: startAt, at: Date.now() };
    // loudness that only shows up when resolving the stream (Spotify keeps it in the file header)
    if (info.data.gain && !item.track.gain) this.updateTrack(item.instanceId, item.track.id, { gain: info.data.gain });
    this.pendingSeek = startAt > 0 ? startAt : undefined;
    if (info.data.hls) {
      const hls = new Hls({ enableWorker: false, startPosition: startAt > 0 ? startAt : -1 });
      hls.on(Hls.Events.ERROR, (_e, data) => {
        dlog("hls error", {
          fatal: data.fatal,
          type: data.type,
          details: data.details,
          status: (data as any).response?.code,
          url: ((data as any).frag?.url ?? (data as any).url ?? "").slice(0, 120),
        });
        if (data.fatal && token === this.loadToken) this.recover(`hls ${data.details}`);
      });
      this.attachSource(() => {
        hls.loadSource(url);
        hls.attachMedia(this.audio);
        this.hls = hls;
      });
    } else {
      this.attachSource(() => (this.audio.src = url));
    }
    this.applyLevelling();
    if (autoplay) this.play();
  }

  private detachHls() {
    if (this.hls) {
      this.hls.detachMedia();
      this.hls.destroy();
      this.hls = undefined;
    }
  }

  /** Swap the element's source without its teardown errors being mistaken for the new track's. */
  private attachSource(attach: () => void) {
    this.switching = true;
    this.detachHls();
    this.audio.removeAttribute("src");
    this.audio.load();
    attach();
    // teardown events fire asynchronously; give them a moment before trusting errors again
    window.setTimeout(() => (this.switching = false), 500);
  }

  /** Warm the stream cache for the track after this one, so the next start is instant. */
  private prefetchNext() {
    const { queue, index } = this.state.get();
    const pos = this.order.indexOf(index);
    const next = queue[this.order[pos + 1]];
    if (next) streamInfo(next.instanceId, next.track.id).catch(() => {});
  }

  private resetTrackState() {
    this.nowPlayingSent = false;
    this.submitted = false;
    this.prefetched = false;
    this.lastTime = 0;
  }

  private onPlaying() {
    if (!this.prefetched) {
      this.prefetched = true;
      this.prefetchNext();
    }
    if (!this.sessionTimer) {
      this.sessionTimer = window.setInterval(
        () => !this.audio.paused && this.persistSession(),
        SESSION_SAVE_INTERVAL_MS,
      );
    }
    const item = this.current;
    if (item && !this.nowPlayingSent && hasFeature(item.instanceId, "scrobble")) {
      this.nowPlayingSent = true;
      scrobble(item.instanceId, item.track.id, false).catch(() => {});
    }
  }

  private maybeScrobble() {
    // repeat-one loops inside the element (no "ended"): jumping from the end back to the start is a new listen
    const t = this.audio.currentTime;
    if (this.state.get().repeat === "one" && this.lastTime > 5 && t < 2 && this.lastTime > t + 5) {
      this.nowPlayingSent = false;
      this.submitted = false;
      if (!this.audio.paused) this.onPlaying();
    }
    this.lastTime = t;
    const item = this.current;
    if (!item || this.submitted || !hasFeature(item.instanceId, "scrobble")) return;
    const duration = this.position.get().duration;
    if (duration < 30) return; // too short to count, per the usual scrobbling rules
    if (this.audio.currentTime >= Math.min(duration * SCROBBLE_FRACTION, SCROBBLE_MAX_SECONDS)) {
      this.submitted = true;
      scrobble(item.instanceId, item.track.id, true).catch(() => {});
    }
  }

  private persistSession() {
    const { queue, index } = this.state.get();
    if (!queue.length) return;
    saveSession({ queue, index, position: this.audio.currentTime || 0 }).catch(() => {});
  }

  /** Append tracks similar to the current one. `advance` moves on to the first of them. */
  async extendWithRadio(advance = false) {
    const item = this.current;
    if (!item || this.extendingRadio) return;
    this.extendingRadio = true;
    try {
      const result = await getRadio(item.instanceId, item.track);
      const known = new Set(this.state.get().queue.map((q) => q.track.id));
      const fresh = result.ok ? result.data.filter((t) => !known.has(t.id)) : [];
      dlog("radio", { ok: result.ok, fresh: fresh.length, error: result.ok ? undefined : result.error });
      if (!fresh.length) {
        if (advance) {
          this.audio.pause();
          notify("End of queue", "Couldn't find more music like this", item);
        }
        return;
      }
      const before = this.state.get().queue.length;
      this.enqueue(item.instanceId, fresh);
      if (advance) this.load(before);
    } finally {
      this.extendingRadio = false;
    }
  }

  /** Start a radio station from a track: it plays now, similar music follows. */
  async startRadio(instanceId: string, track: Track) {
    this.playList(instanceId, [track], 0, false);
    await this.extendWithRadio(false);
  }

  private play() {
    this.ensureGraph();
    if (this.ctx?.state === "suspended") this.ctx.resume();
    this.audio.play().catch((e) => {
      // AbortError just means a newer load() interrupted this one
      if (e?.name !== "AbortError") {
        console.warn("[MusicDeck] play() failed", e);
        dlog("play() rejected", { name: e?.name, message: e?.message });
      }
    });
  }

  private updatePosition() {
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : (this.current?.track.duration ?? 0);
    this.position.set({ current: this.audio.currentTime, duration });
  }

  private onEnded() {
    if (this.state.get().repeat !== "one") this.next(true);
  }

  /**
   * Something broke mid-track (expired signed URL, dropped connection, stall). Re-resolve the stream
   * fresh and resume from the same second once; only then report it and move on.
   */
  private recover(reason: string) {
    const item = this.current;
    if (!item || (!this.hls && !this.audio.getAttribute("src"))) return;
    if (this.recoveries >= 1) {
      this.onError(reason);
      return;
    }
    this.recoveries++;
    const at = this.audio.currentTime || 0;
    dlog("recover", { reason, at, id: item.track.id });
    forgetStream(item.instanceId, item.track.id)
      .catch(() => {})
      .then(() => this.prepare(this.state.get().index, true, at));
  }

  private checkStall() {
    const t = this.audio.currentTime;
    if (this.audio.paused || this.audio.ended || !this.current) {
      this.lastProgress = { time: t, at: Date.now() };
      return;
    }
    if (t !== this.lastProgress.time) {
      this.lastProgress = { time: t, at: Date.now() };
      return;
    }
    if (Date.now() - this.lastProgress.at > STALL_TIMEOUT_MS) {
      dlog("stall", { at: t, readyState: this.audio.readyState, networkState: this.audio.networkState });
      this.lastProgress = { time: t, at: Date.now() };
      this.recover("stalled");
    }
  }

  private onError(reason?: string) {
    if (!reason && !this.audio.getAttribute("src")) return;
    const item = this.current;
    const title = item?.track.title ?? "track";
    if (reason) console.warn("[MusicDeck] playback error", reason);
    dlog("giving up", { reason, id: item?.track.id });
    this.consecutiveErrors++;
    this.state.update({ loading: false, playing: false });
    notify("Couldn't play", title, item);
    // Skip past unplayable tracks, but stop if everything is failing (e.g. server down)
    if (this.consecutiveErrors < 3) this.next(true);
  }
}

export const player = new Player();
