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
/** A live stream paused longer than this rejoins the broadcast on play instead of resuming the buffer. */
const LIVE_REJOIN_MS = 30_000;
/** How long before a track ends the next one is loaded on the idle deck (more when crossfading). */
const PRELOAD_SECONDS = 15;
/** readyState from which a preloaded track can start (and fade in) without stuttering. */
const HAVE_FUTURE_DATA = 3;

export const hasFeature = (instanceId: string, feature: Feature) =>
  !!pluginState
    .get()
    ?.services.find((s) => s.id === instanceId)
    ?.features?.includes(feature);

/** Plays are reported when the source keeps its own play history, or ListenBrainz is linked. */
const reportsPlays = (instanceId: string) =>
  hasFeature(instanceId, "scrobble") || !!pluginState.get()?.listenbrainz?.user;

export interface QueueItem {
  /** Service instance the track belongs to: always the live source (switching sources clears the queue). */
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
 * One of the player's two audio elements. One plays; the other is idle, or holds the next track loaded
 * ahead of time, so it can start the moment the current one ends (gapless) or fade in over it
 * (crossfade).
 */
class Deck {
  readonly audio = new Audio();
  /** hls.js instance while an HLS source (SoundCloud, TIDAL DASH, some radio) is attached */
  hls?: Hls;
  /** Its track's loudness (ReplayGain) */
  leveller?: GainNode;
  /** Crossfades: 1 normally */
  fader?: GainNode;
  /** True while its previous source is being torn down (its error events must be ignored) */
  switching = false;
  /** The track loaded ahead of time, for which queue, so it's only played if it's still what's next */
  preloaded?: { index: number; item: QueueItem; queue: number; gain?: Track["gain"] };

  constructor() {
    // Required for the Web Audio graph to read samples from the loopback stream server
    this.audio.crossOrigin = "anonymous";
    this.audio.preload = "auto";
  }

  detachHls() {
    if (this.hls) {
      this.hls.detachMedia();
      this.hls.destroy();
      this.hls = undefined;
    }
  }

  /** Stops it, lets go of its source, and puts its fader back to full. */
  empty() {
    this.preloaded = undefined;
    this.detachHls();
    this.audio.pause();
    this.audio.removeAttribute("src");
    this.audio.load();
    if (this.fader) {
      this.fader.gain.cancelScheduledValues(0);
      this.fader.gain.value = 1;
    }
  }
}

/**
 * The audio engine. Lives in Steam's shared JS context for as long as the plugin is loaded,
 * so playback continues while the QAM is closed and while games run.
 *
 * Graph: each deck's <audio> -> levelling (ReplayGain) -> fader -> preamp -> 10 biquad filters ->
 * destination; the EQ is shared. Built lazily on first play because createMediaElementSource can only
 * be called once per element. Everything but gapless/crossfade only deals with the playing deck.
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

  private readonly decks = [new Deck(), new Deck()];
  /** The deck that's playing */
  private deck = this.decks[0];
  private ctx?: AudioContext;
  private preamp?: GainNode;
  /** The deck fading out during a crossfade, until it's done */
  private fading?: { deck: Deck; timer: number };
  private preloading = false;
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
  /** Incremented per load so a slow stream resolution can't clobber a newer track */
  private loadToken = 0;
  private filters: BiquadFilterNode[] = [];
  private eq: EqSettings = { enabled: false, preset: "Flat", gains: EQ_BANDS.map(() => 0), preamp: 0 };
  /** Play order when shuffling: a permutation of queue indices. */
  private order: number[] = [];
  private consecutiveErrors = 0;
  /** Bumped whenever the queue is replaced or cleared, so slow work for the old one is dropped. */
  private queueGeneration = 0;
  /** When a live stream was paused; resuming much later rejoins the broadcast (see toggle) */
  private livePausedAt = 0;
  /** Recovery attempts for the current track; one fresh re-resolve before giving up */
  private recoveries = 0;
  private stallWatch?: number;
  /** Sleep timer "end of track": pause when the current track finishes instead of moving on */
  stopAfterCurrent = false;
  private lastProgress = { time: 0, at: 0 };

  constructor() {
    this.decks.forEach((deck) => this.listen(deck));
    this.stallWatch = window.setInterval(() => this.checkStall(), 2000);
  }

  /** The playing deck's element; most of the player only ever deals with this one. */
  private get audio() {
    return this.deck.audio;
  }

  private get hls() {
    return this.deck.hls;
  }

  private get idleDeck() {
    return this.decks[0] === this.deck ? this.decks[1] : this.decks[0];
  }

  /** Events from the idle deck (preloading) or the one fading out don't describe what's playing. */
  private listen(deck: Deck) {
    const audio = deck.audio;
    const on = (name: string, handler: () => void) =>
      audio.addEventListener(name, () => deck === this.deck && handler());
    on("playing", () => {
      dlog("audio playing", { at: audio.currentTime });
      this.state.update({ playing: true, loading: false });
      this.onPlaying();
    });
    on("pause", () => {
      dlog("audio pause", { at: audio.currentTime, ended: audio.ended });
      this.livePausedAt = Date.now();
      this.state.update({ playing: false });
      this.persistSession();
    });
    on("loadedmetadata", () => {
      if (this.pendingSeek !== undefined) {
        audio.currentTime = this.pendingSeek;
        this.pendingSeek = undefined;
      }
    });
    on("waiting", () => {
      dlog("audio waiting", { at: audio.currentTime });
      this.state.update({ loading: true });
    });
    for (const name of ["stalled", "suspend", "emptied", "abort"]) {
      on(name, () => dlog(`audio ${name}`, { at: audio.currentTime }));
    }
    on("canplay", () => {
      this.consecutiveErrors = 0;
      this.state.update({ loading: false });
    });
    on("timeupdate", () => {
      this.updatePosition();
      this.maybeScrobble();
      void this.maybePreload();
      this.maybeCrossfade();
    });
    on("durationchange", () => this.updatePosition());
    on("ended", () => {
      dlog("audio ended", { at: audio.currentTime, duration: audio.duration });
      this.onEnded();
    });
    audio.addEventListener("error", () => {
      const err = audio.error;
      if (deck !== this.deck) {
        // a track loaded ahead of time that won't play: drop it, it's loaded normally when its turn comes
        if (deck.preloaded && !deck.switching) deck.empty();
        return;
      }
      dlog("audio error", { code: err?.code, message: err?.message, hls: !!deck.hls, switching: deck.switching });
      // Tearing down the previous source (switching tracks, hls.js detaching) makes the element
      // report an error that isn't about the new track; acting on it skipped good songs. With HLS
      // attached, hls.js reports real failures itself, with proper fatal flags.
      if (deck.switching || deck.hls || err?.code === MediaError.MEDIA_ERR_ABORTED) return;
      this.recover(`media error ${err?.code ?? ""} ${err?.message ?? ""}`.trim());
    });
  }

  destroy() {
    window.clearInterval(this.sessionTimer);
    window.clearInterval(this.stallWatch);
    this.persistSession();
    this.cancelFade();
    this.decks.forEach((d) => d.empty());
    this.ctx?.close();
  }

  /** Changes whenever the queue is replaced or cleared (see queueGeneration). */
  get queueId(): number {
    return this.queueGeneration;
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
    const position = item.track.live ? 0 : session.position || 0;
    this.position.set({ current: position, duration: item.track.duration || 0 });
    void this.prepare(index, false, position);
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
    this.queueGeneration++;
    this.state.update({ queue });
    // "Shuffle play" starts on a random track; picking a track while shuffle is on starts on that one
    this.order = this.buildOrder(queue.length, shuffle ? -1 : start);
    this.load(this.state.get().shuffle ? this.order[0] : start);
  }

  /**
   * Adds tracks after the current one (`next`, "Play next") or at the end. The play order is kept,
   * shuffled or not: the new tracks come right after the current one or last, and nothing already
   * heard comes round again.
   */
  enqueue(instanceId: string, tracks: Track[], next = false) {
    if (!tracks.length) return;
    const { queue, index } = this.state.get();
    const items = tracks.map((track) => ({ instanceId, track }));
    const at = next && index >= 0 ? index + 1 : queue.length;
    const updated = [...queue.slice(0, at), ...items, ...queue.slice(at)];
    this.state.update({ queue: updated });
    if (index < 0) {
      this.queueGeneration++;
      this.order = this.buildOrder(updated.length, -1);
      this.load(this.order[0]);
      return;
    }
    const added = items.map((_, k) => at + k);
    const order = this.order.map((j) => (j >= at ? j + items.length : j));
    if (next) order.splice(order.indexOf(index) + 1, 0, ...added);
    else order.push(...added);
    this.order = order;
  }

  /** Removes a queued track; removing the one playing moves on to what was due next. */
  removeAt(i: number) {
    const { queue, index } = this.state.get();
    if (i < 0 || i >= queue.length) return;
    if (queue.length === 1) return this.clear();
    const pos = this.order.indexOf(i);
    this.order = this.order.filter((j) => j !== i).map((j) => (j > i ? j - 1 : j));
    const remaining = queue.filter((_, j) => j !== i);
    if (i === index) {
      this.state.update({ queue: remaining });
      this.load(this.order[Math.min(pos, this.order.length - 1)]);
      return;
    }
    this.state.update({ queue: remaining, index: i < index ? index - 1 : index });
  }

  clear() {
    saveSession({ queue: [], index: -1, position: 0 }).catch(() => {});
    this.loadToken++;
    this.queueGeneration++;
    this.cancelFade();
    this.decks.forEach((d) => d.empty());
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
    this.cancelFade();
    if (!this.audio.paused) return this.audio.pause();
    // a live stream resumed after a while would play stale buffered audio (or stall): rejoin it now
    if (this.current?.track.live && Date.now() - this.livePausedAt > LIVE_REJOIN_MS) {
      return void this.prepare(this.state.get().index, true, 0);
    }
    this.play();
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
    // (a live stream can't restart: previous is the previous station)
    if (this.audio.currentTime > 3 && !this.current?.track.live) {
      this.seek(0);
      return;
    }
    const pos = this.order.indexOf(this.state.get().index);
    if (pos > 0) this.load(this.order[pos - 1]);
    else this.audio.currentTime = 0;
  }

  seek(seconds: number) {
    if (!Number.isFinite(seconds)) return;
    this.cancelFade();
    this.audio.currentTime = seconds;
  }

  setVolume(volume: number) {
    if (!Number.isFinite(volume)) return;
    for (const deck of this.decks) deck.audio.volume = Math.min(1, Math.max(0, volume));
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
    const leveller = this.deck.leveller;
    if (!leveller) return;
    const gain = this.current?.track.gain;
    if (!gain || this.playback.levelling === "off") {
      leveller.gain.value = 1;
      return;
    }
    const album = this.playback.levelling === "album";
    const db = album ? gain.album : gain.track;
    const peak = (album ? gain.album_peak : gain.track_peak) || 1;
    // never boost a track past its own peak, so levelling can't introduce clipping
    leveller.gain.value = Math.min(Math.pow(10, db / 20), 1 / peak);
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
    this.preamp = this.ctx.createGain();
    for (const deck of this.decks) {
      deck.leveller = this.ctx.createGain();
      deck.fader = this.ctx.createGain();
      this.ctx.createMediaElementSource(deck.audio).connect(deck.leveller).connect(deck.fader).connect(this.preamp);
    }
    this.filters = EQ_BANDS.map((freq, i) => {
      const f = this.ctx!.createBiquadFilter();
      f.type = i === 0 ? "lowshelf" : i === EQ_BANDS.length - 1 ? "highshelf" : "peaking";
      f.frequency.value = freq;
      f.Q.value = 1.4;
      return f;
    });
    let node: AudioNode = this.preamp;
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
    this.cancelFade();
    if (this.startPreloaded(index)) return;
    this.becomeCurrent(index);
    this.state.update({ loading: true });
    void this.prepare(index, true, 0);
  }

  /** Makes `index` the current track: position, play reports and the saved session start over. */
  private becomeCurrent(index: number) {
    const item = this.state.get().queue[index];
    this.state.update({ index });
    this.position.set({ current: 0, duration: item.track.duration || 0 });
    this.resetTrackState();
    this.recoveries = 0;
    dlog("load", { index, id: item.track.id, title: item.track.title, instance: item.instanceId });
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
    this.attach(this.deck, url, info.data.hls, startAt, token);
    this.applyLevelling();
    if (autoplay) this.play();
  }

  /**
   * Puts a stream on a deck, plain or through hls.js, without the old source's teardown errors being
   * mistaken for the new track's. A fatal HLS error recovers the playing track (if `token` is still the
   * current load) or drops a preloaded one.
   */
  private attach(deck: Deck, url: string, hls: boolean, startAt: number, token: number) {
    deck.switching = true;
    deck.detachHls();
    deck.audio.removeAttribute("src");
    deck.audio.load();
    if (hls) {
      const h = new Hls({ enableWorker: false, startPosition: startAt > 0 ? startAt : -1 });
      h.on(Hls.Events.ERROR, (_e, data) => {
        dlog("hls error", {
          fatal: data.fatal,
          type: data.type,
          details: data.details,
          status: (data as any).response?.code,
          url: ((data as any).frag?.url ?? (data as any).url ?? "").slice(0, 120),
          preloaded: deck !== this.deck,
        });
        if (!data.fatal) return;
        if (deck !== this.deck) deck.empty();
        else if (token === this.loadToken) this.recover(`hls ${data.details}`);
      });
      h.loadSource(url);
      h.attachMedia(deck.audio);
      deck.hls = h;
    } else {
      deck.audio.src = url;
    }
    // teardown events fire asynchronously; give them a moment before trusting errors again
    window.setTimeout(() => (deck.switching = false), 500);
  }

  // --- gapless and crossfade ---

  /** The track that plays after this one if nothing changes; undefined if that isn't known ahead. */
  private upcoming(): number | undefined {
    const { queue, index, repeat, shuffle } = this.state.get();
    const current = queue[index];
    if (!current || current.track.live || repeat === "one" || this.stopAfterCurrent) return undefined;
    const pos = this.order.indexOf(index);
    if (pos >= 0 && pos + 1 < this.order.length) return this.order[pos + 1];
    // wrapping around: shuffle deals a new order then, so only an unshuffled queue knows its next track
    return repeat === "all" && !shuffle ? this.order[0] : undefined;
  }

  private isStillNext(p: Deck["preloaded"]) {
    return !!p && p.queue === this.queueGeneration && this.state.get().queue[p.index] === p.item;
  }

  /** Near the end of a track, loads the next one on the idle deck. */
  private async maybePreload() {
    const idle = this.idleDeck;
    const next = this.upcoming();
    // while a crossfade runs, the idle deck is the one still fading out
    if (
      next === undefined ||
      this.preloading ||
      this.fading ||
      (idle.preloaded?.index === next && this.isStillNext(idle.preloaded))
    )
      return;
    const remaining = this.audio.duration - this.audio.currentTime;
    const crossfade = this.playback.crossfade ?? 0;
    if (!Number.isFinite(remaining) || remaining > Math.max(PRELOAD_SECONDS, crossfade + 5)) return;
    const item = this.state.get().queue[next];
    const url = mediaUrl("stream", item.instanceId, item.track.id);
    if (!url || item.track.live) return;
    this.preloading = true;
    try {
      const queue = this.queueGeneration;
      const info = await streamInfo(item.instanceId, item.track.id).catch(() => null);
      if (!info?.ok || queue !== this.queueGeneration || this.state.get().queue[next] !== item || idle === this.deck)
        return;
      idle.empty();
      this.attach(idle, url, info.data.hls, 0, this.loadToken);
      idle.preloaded = { index: next, item, queue, gain: info.data.gain };
      dlog("preloaded", { index: next, id: item.track.id, hls: info.data.hls });
    } finally {
      this.preloading = false;
    }
  }

  /** With crossfade on, starts the next track that many seconds before this one ends. */
  private maybeCrossfade() {
    const seconds = this.playback.crossfade ?? 0;
    if (!seconds || this.fading || this.audio.paused) return;
    const duration = this.audio.duration;
    const remaining = duration - this.audio.currentTime;
    // a track barely longer than two fades would be mostly fade: let it end normally
    if (!Number.isFinite(remaining) || remaining > seconds || duration < seconds * 2 + 5) return;
    const next = this.upcoming();
    const idle = this.idleDeck;
    if (next === undefined || idle.preloaded?.index !== next || idle.audio.readyState < HAVE_FUTURE_DATA) return;
    dlog("crossfade", { from: this.state.get().index, to: next, seconds: Math.round(remaining * 10) / 10 });
    this.startPreloaded(next, remaining);
  }

  /**
   * Plays the track preloaded on the idle deck if it's `index` of the current queue (false if it
   * isn't), straight away or fading in over `fadeSeconds` while the old one fades out.
   */
  private startPreloaded(index: number, fadeSeconds = 0): boolean {
    const incoming = this.idleDeck;
    const preloaded = incoming.preloaded;
    if (!preloaded || preloaded.index !== index || !this.isStillNext(preloaded)) return false;
    incoming.preloaded = undefined;
    const outgoing = this.deck;
    this.deck = incoming;
    this.loadToken++; // anything still resolving for the old deck is dropped
    this.audio.loop = this.state.get().repeat === "one";
    this.becomeCurrent(index);
    if (preloaded.gain && !preloaded.item.track.gain) {
      this.updateTrack(preloaded.item.instanceId, preloaded.item.track.id, { gain: preloaded.gain });
    }
    this.applyLevelling();
    if (fadeSeconds > 0 && this.ctx && outgoing.fader && incoming.fader) {
      // equal-power curves keep the loudness steady through the fade
      const steps = 32;
      const fadeIn = new Float32Array(steps).map((_, i) => Math.sin(((i / (steps - 1)) * Math.PI) / 2));
      const fadeOut = fadeIn.map((_, i) => Math.cos(((i / (steps - 1)) * Math.PI) / 2));
      const now = this.ctx.currentTime;
      for (const [deck, curve] of [
        [incoming, fadeIn],
        [outgoing, fadeOut],
      ] as const) {
        deck.fader!.gain.cancelScheduledValues(now);
        deck.fader!.gain.setValueCurveAtTime(curve, now, fadeSeconds);
      }
      const timer = window.setTimeout(() => this.cancelFade(), fadeSeconds * 1000 + 100);
      this.fading = { deck: outgoing, timer };
    } else {
      outgoing.empty();
    }
    this.play();
    return true;
  }

  /** Ends a crossfade at once: the outgoing track stops, the incoming one plays at full level. */
  private cancelFade() {
    if (!this.fading) return;
    window.clearTimeout(this.fading.timer);
    this.fading.deck.empty();
    this.fading = undefined;
    const fader = this.deck.fader;
    if (fader) {
      fader.gain.cancelScheduledValues(0);
      fader.gain.value = 1;
    }
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
    // a live stream can drop any number of times over hours; each reconnect that plays starts afresh
    if (this.current?.track.live) this.recoveries = 0;
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
    if (item && !this.nowPlayingSent && reportsPlays(item.instanceId)) {
      this.nowPlayingSent = true;
      scrobble(item.instanceId, item.track.id, false, item.track).catch(() => {});
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
    if (!item || this.submitted || !reportsPlays(item.instanceId)) return;
    const duration = this.position.get().duration;
    if (duration < 30) return; // too short to count, per the usual scrobbling rules
    if (this.audio.currentTime >= Math.min(duration * SCROBBLE_FRACTION, SCROBBLE_MAX_SECONDS)) {
      this.submitted = true;
      scrobble(item.instanceId, item.track.id, true, item.track).catch(() => {});
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
    const generation = this.queueGeneration;
    try {
      const result = await getRadio(item.instanceId, item.track);
      // another album, a cleared queue or a source switch while waiting: these belong to the old one
      if (generation !== this.queueGeneration) return;
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

  /** Lowers the volume to silence over `seconds`, pauses, then restores the volume for next time. */
  async fadeOutAndPause(seconds: number) {
    this.cancelFade();
    const volume = this.audio.volume;
    const steps = Math.max(1, Math.round(seconds * 10));
    for (let i = 1; i <= steps && !this.audio.paused; i++) {
      this.audio.volume = volume * (1 - i / steps);
      await new Promise((r) => setTimeout(r, 100));
    }
    this.audio.pause();
    this.audio.volume = volume;
  }

  private onEnded() {
    if (this.stopAfterCurrent) {
      this.stopAfterCurrent = false;
      dlog("sleep timer: stopped after track");
      return;
    }
    // a live stream only "ends" when the station drops the connection: reconnect instead of skipping
    if (this.current?.track.live) return this.recover("live stream ended");
    if (this.state.get().repeat !== "one") this.next(true);
  }

  /**
   * Something broke mid-track (expired signed URL, dropped connection, stall). Re-resolve the stream
   * fresh and resume from the same second once; only then report it and move on.
   */
  private recover(reason: string) {
    const item = this.current;
    if (!item || (!this.hls && !this.audio.getAttribute("src"))) return;
    this.cancelFade();
    if (this.recoveries >= 1) {
      this.onError(reason);
      return;
    }
    this.recoveries++;
    const at = item.track.live ? 0 : this.audio.currentTime || 0; // live streams rejoin at "now"
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
