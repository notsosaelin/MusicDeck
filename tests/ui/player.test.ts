/**
 * The player's queue and transport logic: what plays next, in what order, after every kind of edit.
 * Audio is a stand-in (tests/ui/setup.mjs) that plays the moment it's asked; streams resolve at once.
 */
import { afterEach, beforeEach, describe, test } from "node:test";
import assert from "node:assert/strict";
import { backend, calls } from "@decky/api";
import { player } from "../../src/player";
import { pluginState } from "../../src/store";

const track = (id: string, extra = {}) => ({
  id,
  title: id.toUpperCase(),
  artist: "",
  album: "",
  duration: 200,
  art: null,
  album_id: null,
  artist_id: null,
  starred: false,
  gain: null,
  ...extra,
});
const tracks = (...ids: string[]) => ids.map((id) => track(id));
const settle = () => new Promise((resolve) => setTimeout(resolve, 5));
const audio = () => (player as any).audio as { src: string; currentTime: number; paused: boolean; loop: boolean };
const playing = () => player.current?.track.id;
const queueIds = () => player.state.get().queue.map((q) => q.track.id);
const orderIds = () => player.playOrder.map((i) => player.state.get().queue[i].track.id);

beforeEach(() => {
  pluginState.set({
    server: { base: "http://127.0.0.1:1234", key: "k" },
    services: [{ id: "src", features: [] }],
    listenbrainz: { user: "" },
  } as any);
  backend.stream_info = async () => ({ ok: true, data: { hls: false, gain: null, start_at: 0 } });
  player.clear();
  if (player.state.get().shuffle) player.setShuffle(false);
  while (player.state.get().repeat !== "off") player.cycleRepeat();
  calls.length = 0;
});

afterEach(settle);

describe("playing a list", () => {
  test("starts at the chosen track and streams it from the loopback server", async () => {
    player.playList("src", tracks("a", "b", "c"), 1);
    await settle();
    assert.equal(playing(), "b");
    assert.equal(player.state.get().playing || !audio().paused, true);
    assert.match(audio().src, /^http:\/\/127\.0\.0\.1:1234\/stream\/src\/b\?k=k/);
  });

  test("an empty list changes nothing", () => {
    player.playList("src", tracks("a"), 0);
    player.playList("src", [], 0);
    assert.deepEqual(queueIds(), ["a"]);
  });

  test("shuffle play starts somewhere and plays everything once", () => {
    player.playList("src", tracks("a", "b", "c", "d", "e"), 0, true);
    assert.deepEqual([...orderIds()].sort(), ["a", "b", "c", "d", "e"]);
    assert.equal(playing(), orderIds()[0]);
  });

  test("a slow stream for a track skipped past can't take over", async () => {
    let release!: () => void;
    backend.stream_info = (_id: string, trackId: string) =>
      trackId === "a"
        ? new Promise((resolve) => (release = () => resolve({ ok: true, data: { hls: false, gain: null, start_at: 0 } })))
        : { ok: true, data: { hls: false, gain: null, start_at: 0 } };
    player.playList("src", tracks("a", "b"), 0);
    player.next();
    await settle();
    release();
    await settle();
    assert.equal(playing(), "b");
    assert.match(audio().src, /\/stream\/src\/b\?/);
  });
});

describe("the queue", () => {
  test("Play next goes right after the current track; Add to queue goes last", () => {
    player.playList("src", tracks("a", "b", "c"), 0);
    player.enqueue("src", tracks("n"), true);
    player.enqueue("src", tracks("z"));
    assert.deepEqual(queueIds(), ["a", "n", "b", "c", "z"]);
    assert.deepEqual(orderIds(), ["a", "n", "b", "c", "z"]);
  });

  test("Play next while shuffling plays next, and nothing heard comes round again", () => {
    player.playList("src", tracks("a", "b", "c", "d"), 0, true);
    const before = orderIds();
    player.enqueue("src", tracks("n"), true);
    const after = orderIds();
    assert.equal(after[after.indexOf(playing()!) + 1], "n");
    assert.deepEqual(
      after.filter((id) => id !== "n"),
      before,
    );
  });

  test("adding to an empty queue starts playing", () => {
    player.enqueue("src", tracks("a", "b"));
    assert.equal(playing(), "a");
  });

  test("moving a track reorders the queue and keeps the current one playing", () => {
    player.playList("src", tracks("a", "b", "c", "d"), 1);
    assert.equal(player.move(3, 0), true);
    assert.deepEqual(queueIds(), ["d", "a", "b", "c"]);
    assert.equal(playing(), "b");
  });

  test("moving while shuffled changes only the play order; shuffle off gives the queue back", () => {
    player.playList("src", tracks("a", "b", "c", "d"), 0, true);
    const queue = queueIds();
    const order = orderIds();
    assert.equal(player.move(1, 3), true);
    assert.deepEqual(queueIds(), queue);
    assert.deepEqual(orderIds(), [order[0], order[2], order[3], order[1]]);
    player.setShuffle(false);
    assert.deepEqual(orderIds(), queue);
  });

  test("moves that aren't moves are refused", () => {
    player.playList("src", tracks("a", "b"), 0);
    for (const [from, to] of [
      [0, 0],
      [-1, 1],
      [0, 2],
      [0.5, 1],
      [NaN, 0],
    ])
      assert.equal(player.move(from, to), false, `${from} -> ${to}`);
    assert.deepEqual(queueIds(), ["a", "b"]);
  });

  test("removing a track before the current one keeps the current one", () => {
    player.playList("src", tracks("a", "b", "c"), 2);
    player.removeAt(0);
    assert.deepEqual(queueIds(), ["b", "c"]);
    assert.equal(playing(), "c");
  });

  test("removing the playing track moves on to what was next", () => {
    player.playList("src", tracks("a", "b", "c"), 1);
    player.removeAt(1);
    assert.equal(playing(), "c");
  });

  test("removing the only track empties the queue; odd indices do nothing", () => {
    player.playList("src", tracks("a"), 0);
    player.removeAt(5);
    player.removeAt(-1);
    assert.deepEqual(queueIds(), ["a"]);
    player.removeAt(0);
    assert.deepEqual(queueIds(), []);
    assert.equal(player.state.get().index, -1);
  });
});

describe("transport", () => {
  test("next at the end wraps when asked, and stops when the queue just runs out", () => {
    player.playList("src", tracks("a", "b"), 1);
    player.next();
    assert.equal(playing(), "a");
    player.next();
    player.next(true); // the end of "b", with repeat off and nothing to play next
    assert.equal(playing(), "b");
    assert.equal(audio().paused, true);
  });

  test("repeat all goes round at the end", () => {
    player.playList("src", tracks("a", "b"), 1);
    player.cycleRepeat();
    assert.equal(player.state.get().repeat, "all");
    player.next(true);
    assert.equal(playing(), "a");
  });

  test("repeat cycles off, all, one; one loops the element", () => {
    player.playList("src", tracks("a"), 0);
    player.cycleRepeat();
    player.cycleRepeat();
    assert.equal(player.state.get().repeat, "one");
    assert.equal(audio().loop, true);
    player.cycleRepeat();
    assert.equal(player.state.get().repeat, "off");
    assert.equal(audio().loop, false);
  });

  test("previous restarts a track a few seconds in, else goes back", () => {
    player.playList("src", tracks("a", "b"), 1);
    audio().currentTime = 30;
    player.previous();
    assert.equal(playing(), "b");
    assert.equal(audio().currentTime, 0);
    player.previous();
    assert.equal(playing(), "a");
  });

  test("volume stays between 0 and 1, and nonsense is ignored", () => {
    player.setVolume(3);
    assert.equal(player.state.get().volume, 1);
    player.setVolume(-1);
    assert.equal(player.state.get().volume, 0);
    player.setVolume(NaN);
    assert.equal(player.state.get().volume, 0);
  });

  test("seeking to nowhere is ignored", () => {
    player.playList("src", tracks("a"), 0);
    audio().currentTime = 12;
    player.seek(NaN);
    player.seek(Infinity);
    assert.equal(audio().currentTime, 12);
  });
});

describe("talk", () => {
  test("podcast episodes play at the chosen speed, music always as it is, and skips stay in the track", async () => {
    pluginState.set({ ...pluginState.get()!, services: [{ id: "src", features: [] }, { id: "pod", features: ["progress"] }] } as any);
    player.setSpeed(1.5);
    player.playList("pod", tracks("ep"), 0);
    await settle();
    assert.equal((audio() as any).playbackRate, 1.5);
    audio().currentTime = 10;
    player.skipBy(-15);
    assert.equal(audio().currentTime, 0);
    player.skipBy(30);
    assert.equal(audio().currentTime, 30);
    player.playList("src", tracks("song"), 0);
    await settle();
    assert.equal((audio() as any).playbackRate, 1);
    player.setSpeed(9);
    assert.equal(player.state.get().speed, 3);
    player.setSpeed(1);
  });
});

describe("stream links that run out", () => {
  const expiring = (expires: number) => async () => ({ ok: true, data: { hls: false, gain: null, start_at: 0, expires } });

  test("resuming after the link ran out fetches a fresh one and carries on from the same second", async () => {
    backend.stream_info = expiring(Date.now() / 1000 - 5);
    player.playList("src", tracks("a"), 0);
    await settle();
    player.toggle();
    audio().currentTime = 42;
    calls.length = 0;
    player.toggle();
    await settle();
    assert.deepEqual(calls.filter((c) => c.name === "forget_stream").map((c) => c.args[1]), ["a"]);
    assert.equal(calls.filter((c) => c.name === "stream_info").length, 1);
    assert.equal(playing(), "a");
    assert.equal(audio().paused, false);
    assert.equal(audio().currentTime, 42);
  });

  test("a link that's still good is just resumed", async () => {
    backend.stream_info = expiring(Date.now() / 1000 + 3600);
    player.playList("src", tracks("a"), 0);
    await settle();
    player.toggle();
    calls.length = 0;
    player.toggle();
    await settle();
    assert.equal(calls.filter((c) => c.name === "stream_info" || c.name === "forget_stream").length, 0);
    assert.equal(audio().paused, false);
  });
});

test("clear stops everything and saves the empty queue", async () => {
  player.playList("src", tracks("a", "b"), 0);
  player.clear();
  await settle();
  assert.deepEqual(queueIds(), []);
  assert.equal(audio().src, "");
  assert.ok(calls.some((c) => c.name === "save_session" && c.args[0].queue.length === 0));
});

test.after(() => player.destroy());
