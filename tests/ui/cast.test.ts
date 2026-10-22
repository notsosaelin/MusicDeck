/**
 * Casting from the player's side: moving playback to a device and back, and the device's reports
 * (playing, paused, ended, gone) driving the queue as the Deck's own audio would.
 */
import { afterEach, beforeEach, describe, test } from "node:test";
import assert from "node:assert/strict";
import { backend, calls, fire } from "@decky/api";
import { player } from "../../src/player";
import { pluginState } from "../../src/store";

const track = (id: string) => ({ id, title: id.toUpperCase(), artist: "", album: "", duration: 200, art: null });
const settle = (ms = 5) => new Promise((resolve) => setTimeout(resolve, ms));
const localAudio = () => (player as any).decks[0].audio as { src: string; currentTime: number; paused: boolean };
const sent = (name: string) => calls.filter((c) => c.name === name).map((c) => c.args);
const device = { id: "cast:x", name: "Den", model: "", kind: "chromecast" as const };
const status = (s: object) => fire("cast_status", s);

beforeEach(async () => {
  pluginState.set({
    server: { base: "http://127.0.0.1:1234", key: "k" },
    services: [{ id: "src", features: [], castable: true }],
    listenbrainz: { user: "" },
  } as any);
  backend.stream_info = async () => ({ ok: true, data: { hls: false, gain: null, start_at: 0 } });
  backend.cast_start = async () => ({ ok: true, data: { id: "cast:x", name: "Den", kind: "chromecast", volume: 0.3 } });
  backend.cast_load = async () => ({ ok: true, data: null });
  backend.cast_control = async () => ({ ok: true, data: null });
  backend.cast_stop = async () => ({ ok: true, data: null });
  player.clear();
  player.setVolume(0.8);
  player.playList("src", ["a", "b", "c"].map(track));
  await settle();
  calls.length = 0;
});

afterEach(async () => {
  await player.stopCasting();
  player.clear();
});

describe("casting", () => {
  test("moves playback to the device from the same second, and the Deck goes quiet", async () => {
    localAudio().currentTime = 42;
    assert.equal(await player.castTo(device), null);
    await settle();
    const [[instance, t, start, autoplay]] = sent("cast_load");
    assert.deepEqual([instance, t.id, start, autoplay], ["src", "a", 42, true]);
    assert.equal(localAudio().paused, true);
    assert.equal(localAudio().src, "");
    assert.deepEqual(player.state.get().castingTo, { name: "Den", kind: "chromecast" });
    assert.equal(player.state.get().volume, 0.3, "the slider shows the device's volume");
  });

  test("the device's reports drive the player's state and clock", async () => {
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 10, duration: 180 });
    assert.equal(player.state.get().playing, true);
    assert.equal(player.position.get().duration, 180);
    await settle(600);
    assert.ok(player.position.get().current > 10.3, "the position moves on between reports");
    status({ state: "paused", position: 11 });
    assert.equal(player.state.get().playing, false, "paused on the device's own controls");
  });

  test("transport goes to the device", async () => {
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 0 });
    player.toggle();
    player.seek(30);
    player.setVolume(0.5);
    await settle(300);
    const controls = sent("cast_control").map(([action, value]) => (value === undefined ? action : `${action} ${value}`));
    assert.deepEqual(controls, ["pause", "seek 30", "volume 0.5"]);
  });

  test("a track ending on the device plays the next one there", async () => {
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 199 });
    status({ state: "idle", position: 0, ended: true });
    await settle();
    assert.equal(player.current?.track.id, "b");
    assert.equal(sent("cast_load").at(-1)?.[1].id, "b");
    assert.equal(sent("cast_load").at(-1)?.[2], 0);
  });

  test("repeat one restarts the track on the device", async () => {
    player.cycleRepeat();
    player.cycleRepeat();
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 199 });
    status({ state: "idle", ended: true });
    await settle();
    assert.equal(player.current?.track.id, "a");
    assert.equal(sent("cast_load").length, 2);
    player.cycleRepeat();
  });

  test("a track the device can't play is retried once, then skipped", async () => {
    backend.cast_load = async (_i: string, t: { id: string }) =>
      t.id === "a" ? { ok: false, error: "nope" } : { ok: true, data: null };
    await player.castTo(device);
    await settle(20);
    assert.equal(player.current?.track.id, "b");
    assert.deepEqual(
      sent("cast_load").map((args) => args[1].id),
      ["a", "a", "b"],
    );
  });

  test("stopping casting brings playback back to the Deck at the same place", async () => {
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 50 });
    await player.stopCasting();
    await settle();
    assert.equal(sent("cast_stop").length, 1);
    assert.equal(player.state.get().castingTo, null);
    assert.equal(localAudio().paused, false, "it keeps playing, now on the Deck");
    assert.ok(Math.abs((player as any).pendingSeek - 50) < 1, "from the second it had reached");
    assert.equal(player.state.get().volume, 0.8, "the Deck's own volume comes back");
  });

  test("a device that goes away leaves the music paused on the Deck, not blaring from it", async () => {
    await player.castTo(device);
    await settle();
    status({ state: "playing", position: 20 });
    status({ gone: "The device disconnected" });
    await settle();
    assert.equal(player.state.get().castingTo, null);
    assert.equal(localAudio().paused, true);
    assert.equal(sent("cast_stop").length, 0, "nothing to stop: the backend already ended the session");
    assert.equal(player.current?.track.id, "a");
  });

  test("clearing the queue stops the device's player but keeps casting", async () => {
    await player.castTo(device);
    await settle();
    player.clear();
    await settle();
    assert.deepEqual(sent("cast_control").at(-1), ["stop", undefined]);
    assert.ok(player.state.get().castingTo);
  });

  test("a device that can't be reached leaves playback where it was", async () => {
    backend.cast_start = async () => ({ ok: false, error: "Couldn't reach the device" });
    assert.equal(await player.castTo(device), "Couldn't reach the device");
    assert.equal(player.state.get().castingTo, null);
    assert.equal(localAudio().paused, false);
  });
});

test.after(() => player.destroy());
