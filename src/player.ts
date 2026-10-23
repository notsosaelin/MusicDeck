import Hls from "hls.js/dist/hls.light.min.js";
import {
  CastDevice,
  CastStatus,
  EqSettings,
  Feature,
  PlaybackSettings,
  Session,
  Track,
  getRadio,
  saveProgress,
  saveSession,
  scrobble,
  forgetStream,
  streamInfo,
  castStart,
  castStop,
} from "./api";
import { RemoteAudio, onCastStatus } from "./cast";
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
/** How often a long track's position is saved while it plays (sources with "progress", e.g. podcasts). */
const PROGRESS_SAVE_MS = 15_000;
/** A live stream paused longer than this rejoins the broadcast on play instead of resuming the buffer. */
const LIVE_REJOIN_MS = 30_000;
/** Paused this long, the player lets go of its audio elements and graph; play builds them again */
const IDLE_RELEASE_MS = 10 * 60_000;
/** A stream link this close to running out (seconds) counts as gone when resuming */
const EXPIRY_MARGIN_S = 60;
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
  /** How fast talk plays (podcast episodes, books: sources with feature "progress"); music is always 1 */
  speed: number;
  /** The speaker or TV playing instead of the Deck, if any */
  castingTo: { name: string; kind: CastDevice["kind"] } | null;
}

export interface Position {
  current: number;
  duration: number;
}

/**
 * One of the player's two audio elements. One plays; the other is idle, or holds the next track loaded
 * ahead of time, so it can start the moment the current one ends (gapless) or fade in over it
 * (crossfade). The element is made when it's first needed and let go of entirely by release(): no
 * element outlives the music it was for (see Player.releaseAudio).
 */
class Deck {
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
  /** When its stream's link stops working (Unix seconds), for links signed for a while (SoundCloud's) */
  expires?: number;

  private element?: HTMLAudioElement;
  /** Ends the player's listeners on the element when it's let go of */
  private listening?: AbortController;

  /**
   * `make` builds its element (an <audio>, or while casting a RemoteAudio standing in for one) when
   * it's first needed; `wire` hooks the player's listeners to it, until the element is let go of.
   */
  constructor(
    private readonly make: () => HTMLAudioElement,
    private readonly wire: (deck: Deck, audio: HTMLAudioElement, signal: AbortSignal) => void,
  ) {}

  get audio(): HTMLAudioElement {
    if (!this.element) {
      const audio = this.make();
      // Required for the Web Audio graph to read samples from the loopback stream server
      audio.crossOrigin = "anonymous";
      audio.preload = "auto";
      this.element = audio;
      this.listening = new AbortController();
      this.wire(this, audio, this.listening.signal);
    }
    return this.element;
  }

  /** Makes its element now if it hasn't one (a method, not a bare read of `audio`: bundlers drop those). */
  build(): HTMLAudioElement {
    return this.audio;
  }

  /** It has an element now (without making one). */
  get built(): boolean {
    return !!this.element;
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
    this.expires = undefined;
    this.detachHls();
    if (this.element) {
      this.element.pause();
      this.element.removeAttribute("src");
      this.element.load();
    }
    if (this.fader) {
      this.fader.gain.cancelScheduledValues(0);
      this.fader.gain.value = 1;
    }
  }

  /** Empties it and lets go of its element and its part of the graph, listeners and all. */
  release() {
    this.empty();
    this.listening?.abort();
    this.listening = undefined;
    this.element = undefined;
    this.leveller = this.fader = undefined;
    this.switching = false;
  }
}

/**
 * The audio engine. Lives in Steam's shared JS context for as long as the plugin is loaded,
 * so playback continues while the QAM is closed and while games run.
 *
 * Graph: each deck's <audio> -> levelling (ReplayGain) -> fader -> preamp -> 10 biquad filters ->
 * ducking (lower under a game) -> destination; the EQ and ducking are shared. Built lazily on first play because createMediaElementSource can only
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
    speed: 1,
    castingTo: null,
  });
  readonly position = new Store<Position>({ current: 0, duration: 0 });

  /** Its elements match the player's volume and repeat as they're made */
  private readonly newElement = () => {
    const audio = new Audio();
    audio.volume = this.casting ? this.localVolume : this.state.get().volume;
    audio.loop = this.state.get().repeat === "one";
    return audio;
  };
  private readonly decks = [
    new Deck(this.newElement, (deck, audio, signal) => this.listen(deck, audio, signal)),
    new Deck(this.newElement, (deck, audio, signal) => this.listen(deck, audio, signal)),
  ];
  /** While casting, the "deck" that plays: the device, behind an element-like RemoteAudio */
  private readonly remote = new RemoteAudio();
  private readonly castDeck = new Deck(
    () => this.remote as unknown as HTMLAudioElement,
    (deck, audio, signal) => this.listen(deck, audio, signal),
  );
  /** When paused this long, the player lets go of its elements and graph (rebuilt on play) */
  private idleRelease?: number;
  /** The Deck's own volume, put back when casting ends */
  private localVolume = 0.8;
  /** The deck that's playing */
  private deck = this.decks[0];
  private ctx?: AudioContext;
  private preamp?: GainNode;
  private ducker?: GainNode;
  /** What's heard, by frequency, for Now Playing's visualizer: after the EQ, before ducking */
  private analyser?: AnalyserNode;
  /** How much of its volume the music keeps for now: 1, or less while a game is in front (games.ts) */
  private duckLevel = 1;
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
  /** Unloaded (see destroy): nothing plays any more */
  private destroyed = false;
  /** Bumped whenever the queue is replaced or cleared, so slow work for the old one is dropped. */
  private queueGeneration = 0;
  private lastProgressSave = 0;
  /** When a live stream was paused; resuming much later rejoins the broadcast (see toggle) */
  private livePausedAt = 0;
  /** Recovery attempts for the current track; one fresh re-resolve before giving up */
  private recoveries = 0;
  private stallWatch?: number;
  /** Sleep timer "end of track": pause when the current track finishes instead of moving on */
  stopAfterCurrent = false;
  private lastProgress = { time: 0, at: 0 };

  constructor() {
    // the device's stand-in isn't an element, and casting talks to it directly: listen from the start
    this.castDeck.build();
    this.remote.onVolume = (volume) => this.state.update({ volume });
    this.stallWatch = window.setInterval(() => this.checkStall(), 2000);
  }

  /** The playing deck's element; most of the player only ever deals with this one. */
  private get audio() {
    return this.deck.audio;
  }

  private get hls() {
    return this.deck.hls;
  }

  /** Playing on a speaker or TV: no Web Audio, so no EQ, levelling, crossfade or gapless. */
  get casting() {
    return this.deck === this.castDeck;
  }

  private get idleDeck() {
    return this.decks[0] === this.deck ? this.decks[1] : this.decks[0];
  }

  /** Events from the idle deck (preloading) or the one fading out don't describe what's playing. */
  private listen(deck: Deck, audio: HTMLAudioElement, signal: AbortSignal) {
    const on = (name: string, handler: () => void) =>
      // an unloaded player's elements say nothing: it would save its queue over the new copy's
      audio.addEventListener(name, () => deck === this.deck && !this.destroyed && handler(), { signal });
    on("playing", () => {
      dlog("audio playing", { at: audio.currentTime });
      window.clearTimeout(this.idleRelease);
      this.applySpeed();
      this.state.update({ playing: true, loading: false });
      this.onPlaying();
    });
    on("pause", () => {
      dlog("audio pause", { at: audio.currentTime, ended: audio.ended });
      this.releaseWhenIdle();
      this.livePausedAt = Date.now();
      this.state.update({ playing: false });
      this.persistSession();
      this.reportProgress(true);
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
      this.reportProgress(false);
      void this.maybePreload();
      this.maybeCrossfade();
    });
    on("durationchange", () => this.updatePosition());
    on("ended", () => {
      dlog("audio ended", { at: audio.currentTime, duration: audio.duration });
      this.onEnded();
    });
    audio.addEventListener(
      "error",
      () => {
        const err = audio.error;
        if (this.destroyed) return;
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
      },
      { signal },
    );
  }

  /**
   * The plugin is unloading (or reloading): stop for good. A player from an unloaded copy of the plugin
   * must stay silent: parts of its UI can outlive it (a media center page left open, the Quick Access
   * panel), and their buttons still reach it. Before this, pressing play there brought it back to life,
   * with an audio graph of its own, playing where the new copy's controls couldn't stop it.
   */
  destroy() {
    // its last word: where it was, for the new copy to pick up
    this.persistSession();
    this.destroyed = true;
    this.loadToken++; // streams still being resolved are dropped
    window.clearInterval(this.sessionTimer);
    window.clearInterval(this.stallWatch);
    if (this.casting) void this.stopCasting();
    this.releaseAudio();
    this.castDeck.release();
    this.state.update({ playing: false, loading: false });
  }

  /** Where the current track is: its element's time, or where it was when the element was let go of. */
  private get heldAt(): number {
    return this.deck.built ? this.audio.currentTime || 0 : this.position.get().current;
  }

  /**
   * Lets go of the audio elements and the graph they feed, entirely: their streams, listeners and
   * decoders, nothing kept. When the queue is cleared, when the plugin unloads, and after
   * IDLE_RELEASE_MS paused; play builds them again (Deck.audio, ensureGraph).
   */
  private releaseAudio() {
    window.clearTimeout(this.idleRelease);
    this.cancelFade();
    this.decks.forEach((d) => d.release());
    if (!this.casting) this.deck = this.decks[0];
    void this.ctx?.close().catch(() => {});
    this.ctx = this.preamp = this.ducker = this.analyser = undefined;
    this.filters = [];
  }

  /** Paused: after IDLE_RELEASE_MS still paused, let go of the elements (where it was is kept). */
  private releaseWhenIdle() {
    window.clearTimeout(this.idleRelease);
    if (this.casting || this.destroyed) return;
    this.idleRelease = window.setTimeout(() => {
      if (this.casting || (this.deck.built && !this.audio.paused)) return;
      this.position.update({ current: this.heldAt });
      this.persistSession();
      dlog("released idle audio", { at: this.position.get().current });
      this.releaseAudio();
    }, IDLE_RELEASE_MS);
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

  /** The queue in the order it plays (shuffled or not), as indices into `queue`. */
  get playOrder(): readonly number[] {
    return this.order;
  }

  /**
   * Moves a track from play position `from` to `to` (positions as the Queue view lists them); false
   * if either isn't a position. Unshuffled, the queue itself is reordered; shuffled, only the play
   * order is, so turning shuffle off still gives back the queue as it was.
   */
  move(from: number, to: number): boolean {
    const { queue, index, shuffle } = this.state.get();
    const n = this.order.length;
    const valid = (pos: number) => Number.isInteger(pos) && pos >= 0 && pos < n;
    if (from === to || !valid(from) || !valid(to)) return false;
    if (shuffle) {
      const order = [...this.order];
      order.splice(to, 0, ...order.splice(from, 1));
      this.order = order;
      this.state.update({}); // the order isn't part of the state; this redraws the queue
    } else {
      const reordered = [...queue];
      reordered.splice(to, 0, ...reordered.splice(from, 1));
      this.order = reordered.map((_, i) => i);
      this.state.update({ queue: reordered, index: reordered.indexOf(queue[index]) });
    }
    this.persistSession();
    return true;
  }

  /** Removes a queued track; removing the one playing moves on to what was due next. */
  removeAt(i: number) {
    const { queue, index } = this.state.get();
    if (!Number.isInteger(i) || i < 0 || i >= queue.length) return;
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
    this.castDeck.empty();
    this.releaseAudio();
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
    dlog("toggle", { paused: !this.deck.built || this.audio.paused, released: !this.deck.built });
    if (this.state.get().index < 0) return;
    this.cancelFade();
    // let go of while paused (releaseAudio): built again, from where it was
    if (!this.deck.built && this.current) {
      this.state.update({ loading: true });
      return void this.prepare(this.state.get().index, true, this.current.track.live ? 0 : this.position.get().current);
    }
    if (!this.audio.paused) return this.audio.pause();
    // a live stream resumed after a while would play stale buffered audio (or stall): rejoin it now
    if (this.current?.track.live && Date.now() - this.livePausedAt > LIVE_REJOIN_MS) {
      return void this.prepare(this.state.get().index, true, 0);
    }
    // paused past its stream link's life: carry on from the same second on a fresh link (hls.js
    // would otherwise retry the dead one's next piece for good)
    const item = this.current;
    if (item && !this.casting && this.deck.expires && Date.now() / 1000 > this.deck.expires - EXPIRY_MARGIN_S) {
      const at = this.audio.currentTime || 0;
      dlog("link expired while paused", { id: item.track.id, at });
      this.state.update({ loading: true });
      return void forgetStream(item.instanceId, item.track.id)
        .catch(() => {})
        .then(() => this.prepare(this.state.get().index, true, at));
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
    dlog("previous", { from: this.state.get().index, at: this.heldAt });
    // Restart the track if we're more than a few seconds in, like every hardware player
    // (a live stream can't restart: previous is the previous station)
    if (this.heldAt > 3 && !this.current?.track.live) {
      this.seek(0);
      return;
    }
    const pos = this.order.indexOf(this.state.get().index);
    if (pos > 0) this.load(this.order[pos - 1]);
    else this.seek(0);
  }

  seek(seconds: number) {
    if (!Number.isFinite(seconds)) return;
    this.cancelFade();
    // let go of while paused: play picks up from here
    if (!this.deck.built) return this.position.update({ current: Math.max(0, seconds) });
    this.audio.currentTime = seconds;
  }

  /**
   * Fills `into` with how loud each frequency band is now (0-255, low to high) for the visualizer.
   * False when there's nothing to show: nothing played yet, or playing on a speaker or TV.
   */
  levels(into: Uint8Array): boolean {
    if (!this.analyser || this.casting) return false;
    this.analyser.getByteFrequencyData(into as Uint8Array<ArrayBuffer>);
    return true;
  }

  /** Back or ahead by `seconds` (talk's skip buttons), within the track. */
  skipBy(seconds: number) {
    const { duration } = this.position.get();
    const to = Math.max(0, this.heldAt + seconds);
    this.seek(duration ? Math.min(to, duration - 1) : to);
  }

  /** How fast podcast episodes and books play (0.5–3); kept for the next one too. */
  setSpeed(speed: number) {
    if (!Number.isFinite(speed)) return;
    this.state.update({ speed: Math.min(3, Math.max(0.5, speed)) });
    this.applySpeed();
  }

  /** Talk at the chosen speed, at its own pitch; music, and anything on a speaker or TV, as it is. */
  private applySpeed() {
    const item = this.current;
    const talk = !!item && !this.casting && hasFeature(item.instanceId, "progress");
    const rate = talk ? this.state.get().speed : 1;
    for (const deck of this.decks) {
      if (!deck.built) continue;
      deck.audio.preservesPitch = true;
      if (deck.audio.playbackRate !== rate) deck.audio.playbackRate = rate;
    }
  }

  setVolume(volume: number) {
    if (!Number.isFinite(volume)) return;
    const v = Math.min(1, Math.max(0, volume));
    // elements made later take it from the state (newElement)
    for (const deck of this.casting ? [this.castDeck] : this.decks)
      if (deck.built || this.casting) deck.audio.volume = v;
    this.state.update({ volume: v });
  }

  // --- casting ---

  /** Moves playback to a speaker or TV, at the same place; an error message if it couldn't. */
  async castTo(device: CastDevice): Promise<string | null> {
    const started = await castStart(device.id).catch((e) => ({ ok: false as const, error: String(e) }));
    if (!started.ok) return started.error;
    const wasPlaying = this.deck.built && !this.audio.paused;
    const at = this.heldAt;
    dlog("cast start", { kind: started.data.kind, at, wasPlaying });
    if (!this.casting) this.localVolume = this.state.get().volume;
    this.cancelFade();
    this.loadToken++;
    this.decks.forEach((d) => d.empty());
    this.remote.reset(started.data.volume);
    this.remote.loop = this.state.get().repeat === "one";
    this.castDeck.build(); // listening to the device before it says anything
    this.deck = this.castDeck;
    onCastStatus((status) => this.onCastStatus(status));
    this.state.update({
      castingTo: { name: started.data.name, kind: started.data.kind },
      volume: started.data.volume,
      playing: false,
    });
    if (this.current) void this.prepare(this.state.get().index, wasPlaying, at);
    return null;
  }

  /** Brings playback back to the Deck, at the same place (and playing if it was). */
  async stopCasting() {
    if (!this.casting) return;
    const wasPlaying = !this.remote.paused;
    this.backToDeck();
    await castStop().catch(() => {});
    if (this.current) void this.prepare(this.state.get().index, wasPlaying, this.position.get().current);
  }

  private backToDeck() {
    this.position.update({ current: this.remote.currentTime });
    this.loadToken++;
    onCastStatus(undefined);
    this.remote.detach();
    this.deck = this.decks[0];
    for (const deck of this.decks) if (deck.built) deck.audio.volume = this.localVolume;
    this.state.update({ castingTo: null, playing: false, loading: false, volume: this.localVolume });
  }

  private onCastStatus(status: CastStatus) {
    if (!status.gone) return this.remote.onStatus(status);
    // the device went away, or something else plays on it: back on the Deck but paused, so the
    // music doesn't suddenly come out of the Deck's speakers
    const name = this.state.get().castingTo?.name ?? "the device";
    dlog("cast gone", { reason: status.gone });
    this.backToDeck();
    notify(`Stopped playing on ${name}`, status.gone, this.current);
    if (this.current) void this.prepare(this.state.get().index, false, this.position.get().current);
  }

  setShuffle(shuffle: boolean) {
    this.state.update({ shuffle });
    this.order = this.buildOrder(this.state.get().queue.length, this.state.get().index);
  }

  cycleRepeat() {
    const modes: RepeatMode[] = ["off", "all", "one"];
    const repeat = modes[(modes.indexOf(this.state.get().repeat) + 1) % modes.length];
    for (const deck of [...this.decks, this.castDeck]) if (deck.built) deck.audio.loop = repeat === "one";
    this.state.update({ repeat });
  }

  // --- equalizer ---

  setEq(eq: EqSettings) {
    this.eq = eq;
    this.applyEq();
  }

  /**
   * Lowers the music to `level` (a fraction of its volume) under a game, or brings it back with 1,
   * easing over about a second. The volume setting stays as it is. Casting isn't lowered: the
   * speaker isn't where the game's sound is.
   */
  setDuck(level: number) {
    this.duckLevel = Math.min(1, Math.max(0, level));
    if (this.ctx && this.ducker) this.ducker.gain.setTargetAtTime(this.duckLevel, this.ctx.currentTime, 0.3);
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
    if (this.ctx || this.destroyed) return;
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
    this.ducker = this.ctx.createGain();
    this.ducker.gain.value = this.duckLevel;
    this.analyser = this.ctx.createAnalyser();
    this.analyser.fftSize = 256;
    this.analyser.smoothingTimeConstant = 0.78;
    let node: AudioNode = this.preamp;
    for (const f of this.filters) node = node.connect(f);
    node.connect(this.analyser).connect(this.ducker).connect(this.ctx.destination);
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
    // a long track (a podcast episode) picks up where it was left, unless told where to start
    if (!startAt && info.data.start_at) startAt = info.data.start_at;
    this.lastProgress = { time: startAt, at: Date.now() };
    // loudness that only shows up when resolving the stream (Spotify keeps it in the file header)
    if (info.data.gain && !item.track.gain) this.updateTrack(item.instanceId, item.track.id, { gain: info.data.gain });
    if (this.casting) {
      // the device fetches the track itself, from where it should start
      this.remote.open(item, startAt, autoplay);
      return;
    }
    this.pendingSeek = startAt > 0 ? startAt : undefined;
    this.attach(this.deck, url, info.data.hls, startAt, token);
    this.deck.expires = info.data.expires ?? undefined;
    this.applyLevelling();
    if (autoplay) this.play();
  }

  /**
   * Puts a stream on a deck, plain or through hls.js, without the old source's teardown errors being
   * mistaken for the new track's. A fatal HLS error recovers the playing track (if `token` is still the
   * current load) or drops a preloaded one.
   */
  private attach(deck: Deck, url: string, hls: boolean, startAt: number, token: number) {
    if (this.destroyed) return;
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
        // a piece refused: the link's signature ran out (hls.js would keep asking for it); start over on
        // a fresh link from here, once
        const status = (data as any).response?.code;
        if (!data.fatal && data.details === Hls.ErrorDetails.FRAG_LOAD_ERROR && [403, 410].includes(status)) {
          h.stopLoad();
          if (deck !== this.deck) deck.empty();
          else if (token === this.loadToken) this.recover(`hls link expired (${status})`);
          return;
        }
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
      this.casting ||
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
      // an episode that resumes partway isn't loaded ahead: it starts (from there) when its turn comes
      if (info.data.start_at) return;
      idle.empty();
      this.attach(idle, url, info.data.hls, 0, this.loadToken);
      idle.preloaded = { index: next, item, queue, gain: info.data.gain };
      idle.expires = info.data.expires ?? undefined;
      dlog("preloaded", { index: next, id: item.track.id, hls: info.data.hls });
    } finally {
      this.preloading = false;
    }
  }

  /** With crossfade on, starts the next track that many seconds before this one ends. */
  private maybeCrossfade() {
    const seconds = this.playback.crossfade ?? 0;
    if (!seconds || this.fading || this.casting || this.audio.paused) return;
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
        () => this.deck.built && !this.audio.paused && this.persistSession(),
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

  /**
   * Tells sources that keep it (feature "progress": podcasts) where the playing track is: every
   * PROGRESS_SAVE_MS while it plays, and at once (`now`) when it pauses or ends.
   */
  private reportProgress(now: boolean) {
    const item = this.current;
    if (!item || item.track.live || !hasFeature(item.instanceId, "progress")) return;
    if (!now && Date.now() - this.lastProgressSave < PROGRESS_SAVE_MS) return;
    const position = this.audio.ended ? this.audio.duration : this.audio.currentTime;
    if (!Number.isFinite(position) || position <= 0) return;
    this.lastProgressSave = Date.now();
    const duration = Number.isFinite(this.audio.duration) ? this.audio.duration : item.track.duration;
    saveProgress(item.instanceId, item.track, position, duration || 0).catch(() => {});
  }

  private persistSession() {
    const { queue, index } = this.state.get();
    if (!queue.length || this.destroyed) return;
    saveSession({ queue, index, position: this.heldAt }).catch(() => {});
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
    if (this.destroyed) return;
    if (!this.casting) this.ensureGraph();
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
    if (!this.deck.built) return;
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
    if (!this.deck.built) return;
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
