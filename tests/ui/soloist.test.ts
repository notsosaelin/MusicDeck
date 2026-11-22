/**
 * Spotify through Spotify Soloist from the player's side: tracks its source hands over (stream_info's plays_itself)
 * go to Soloist, its "soloist_status" reports drive the queue as casting's do, and the player comes back to its own
 * decks for other sources.
 */
import { afterEach, beforeEach, describe, test } from "node:test";
import assert from "node:assert/strict";
import { backend, calls, fire } from "@decky/api";
import { watchSoloistStatus } from "../../src/cast";
import { player } from "../../src/player";
import { pluginState } from "../../src/store";

const track = (id: string) => ({ id, title: id.toUpperCase(), artist: "", album: "", duration: 200, art: null });
const settle = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms));
const localAudio = () => (player as any).decks[0].audio as { src: string; paused: boolean };
const sent = (name: string) => calls.filter((c) => c.name === name).map((c) => c.args);
const controls = () =>
  sent("soloist_control").map(([id, action, value]) => (value === undefined ? `${id} ${action}` : `${id} ${action} ${value}`));
// as the plugin does when it loads
watchSoloistStatus();
const status = (s: object) => fire("soloist_status", { instance: "sp", ...s });

beforeEach(async () => {
  pluginState.set({
    server: { base: "http://127.0.0.1:1234", key: "k" },
    services: [
      { id: "sp", features: [], castable: false },
      { id: "loc", features: [], castable: true },
    ],
    listenbrainz: { user: "" },
  } as any);
  backend.stream_info = async (id: string) => ({
    ok: true,
    data: { hls: false, gain: null, start_at: 0, ...(id === "sp" ? { plays_itself: true } : {}) },
  });
  backend.soloist_load = async () => ({ ok: true, data: null });
  backend.soloist_control = async () => ({ ok: true, data: null });
  player.clear();
  player.setDuck(1);
  player.setVolume(0.8);
  await settle(300);
  calls.length = 0;
});

afterEach(async () => {
  player.clear();
  await settle(300);
});

describe("Spotify through Soloist", () => {
  test("a track its source hands over goes to Soloist, at the player's volume", async () => {
    player.playList("sp", ["a", "b"].map(track));
    await settle();
    assert.equal(player.onSoloist, true);
    const [[id, t, start, autoplay]] = sent("soloist_load");
    assert.deepEqual([id, t.id, start, autoplay], ["sp", "a", 0, true]);
    assert.ok(controls().includes("sp volume 0.8"), "its volume is set before the track starts");
    assert.equal(localAudio().src, "", "the browser's own deck plays nothing");
    assert.equal(player.levels(new Uint8Array(8)), false, "no visualizer: its sound isn't in the browser");
  });

  test("its reports drive the player; another source's are ignored", async () => {
    player.playList("sp", ["a", "b"].map(track));
    await settle();
    status({ state: "playing", position: 10 });
    assert.equal(player.state.get().playing, true);
    fire("soloist_status", { instance: "other", state: "paused", position: 3 });
    assert.equal(player.state.get().playing, true, "another Spotify source's report isn't about this track");
    await settle(600);
    assert.ok(player.position.get().current > 10.3, "the position moves on between reports");
  });

  test("the transport goes to Soloist, for this source", async () => {
    player.playList("sp", ["a", "b"].map(track));
    await settle();
    status({ state: "playing", position: 0 });
    calls.length = 0;
    player.toggle();
    player.seek(30);
    player.setVolume(0.5);
    await settle(300);
    assert.deepEqual(controls(), ["sp pause", "sp seek 30", "sp volume 0.5"]);
  });

  test("lowered under a game like the rest (it plays where the game does)", async () => {
    player.playList("sp", ["a"].map(track));
    await settle();
    calls.length = 0;
    player.setDuck(0.25);
    await settle(300);
    assert.deepEqual(controls(), ["sp volume 0.2"]);
    assert.equal(player.state.get().volume, 0.8, "the volume setting stays as it is");
    status({ state: "playing", position: 1, volume: 0.2 });
    assert.equal(player.state.get().volume, 0.8, "its report of the lowered volume isn't taken for a change");
  });

  test("the end of a track plays the next one through Soloist", async () => {
    player.playList("sp", ["a", "b"].map(track));
    await settle();
    status({ state: "playing", position: 199 });
    status({ state: "idle", position: 200, ended: true });
    await settle();
    assert.equal(player.current?.track.id, "b");
    assert.equal(sent("soloist_load").at(-1)?.[1].id, "b");
  });

  test("another source's track after it plays in the browser again, and Soloist stops", async () => {
    player.playList("sp", ["a"].map(track));
    player.enqueue("loc", ["x"].map(track));
    await settle();
    status({ state: "playing", position: 199 });
    status({ state: "idle", position: 200, ended: true });
    await settle();
    assert.equal(player.current?.track.id, "x");
    assert.equal(player.onSoloist, false);
    assert.ok(controls().includes("sp stop"), "Soloist is told to stop");
    assert.match(localAudio().src, /\/stream\/loc\/x/, "the browser's deck plays the local track");
  });

  test("clearing the queue stops Soloist", async () => {
    player.playList("sp", ["a"].map(track));
    await settle();
    status({ state: "playing", position: 5 });
    calls.length = 0;
    player.clear();
    await settle();
    assert.ok(controls().includes("sp stop"));
    assert.equal(player.onSoloist, false);
  });

  test("a track Soloist can't play is an error the player handles", async () => {
    backend.soloist_load = async () => ({ ok: false, error: "Spotify Soloist couldn't play this track" });
    player.playList("sp", ["a", "b"].map(track));
    await settle(50);
    assert.ok(sent("soloist_load").length >= 1);
    assert.equal(player.state.get().playing, false);
  });

  test("not paired yet: it stops on the track instead of failing through the queue, and plays it once paired", async () => {
    backend.soloist_load = async () => ({ ok: false, error: "Pair Spotify Soloist first", blocked: true });
    player.playList("sp", ["a", "b", "c"].map(track));
    await settle(300);
    assert.equal(sent("soloist_load").length, 1, "asked once: no retry, no skipping");
    assert.equal(player.current?.track.id, "a", "still on the first track");
    assert.equal(player.state.get().playing, false);
    backend.soloist_load = async () => ({ ok: true, data: null });
    status({ paired: true });
    await settle(50);
    assert.equal(sent("soloist_load").length, 2, "paired: the same track is asked for again");
    assert.equal(sent("soloist_load")[1][1].id, "a");
    assert.equal(player.current?.track.id, "a");
    status({ paired: true });
    await settle(50);
    assert.equal(sent("soloist_load").length, 2, "only the track it stopped on is played on");
  });

  test("after it stopped, Play asks for the track again", async () => {
    backend.soloist_load = async () => ({ ok: false, error: "Pair Spotify Soloist first", blocked: true });
    player.playList("sp", ["a", "b"].map(track));
    await settle(300);
    backend.soloist_load = async () => ({ ok: true, data: null });
    player.toggle();
    await settle(50);
    assert.equal(sent("soloist_load").length, 2);
    assert.equal(sent("soloist_load")[1][1].id, "a");
  });
});

// the player keeps a timer going (its stall watch): let the test process end
test.after(() => player.destroy());
