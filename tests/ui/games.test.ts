/**
 * Music lowered under a game in front: by the share set, back up when Steam is in front, never for
 * apps that aren't games, and with the volume setting left alone.
 */
import { after, beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import { backend } from "@decky/api";
import { duckFor, watchGames } from "../../src/games";
import { player } from "../../src/player";
import { pluginState } from "../../src/store";

const GAME = 570;
const STEAM = 769;
const TOOL = 1070560; // a runtime: starts and stops like a game, but isn't one

(globalThis as any).window.appStore = {
  GetAppOverviewByAppID: (appid: number) =>
    appid === GAME ? { app_type: 1, display_name: "Game" } : appid === TOOL ? { app_type: 4 } : undefined,
};

const track = { id: "a", title: "A", artist: "", album: "", duration: 200, art: null, album_id: null, artist_id: null, starred: false, gain: null };
const level = () => (player as any).ducker.gain.value as number;
const setDuck = (game_duck: number) =>
  pluginState.set({ ...pluginState.get()!, playback: { ...pluginState.get()!.playback, game_duck } });

const unwatch = watchGames();

beforeEach(async () => {
  pluginState.set({
    server: { base: "http://127.0.0.1:1234", key: "k" },
    services: [{ id: "src", features: [] }],
    listenbrainz: { user: "" },
    game_music: [],
    playback: { levelling: "off", scrobble: false, queue_sync: false, resume: false, game_duck: 0.3 },
  } as any);
  backend.stream_info = async () => ({ ok: true, data: { hls: false, gain: null, start_at: 0 } });
  player.playList("src", [track], 0);
  await new Promise((resolve) => setTimeout(resolve, 5));
  duckFor(STEAM);
});

after(() => {
  unwatch();
  player.destroy();
});

test("a game in front lowers the music by the share set, and Steam in front brings it back", () => {
  const volume = player.state.get().volume;
  duckFor(GAME);
  assert.equal(level(), 0.3);
  assert.equal(player.state.get().volume, volume);
  duckFor(STEAM);
  assert.equal(level(), 1);
});

test("changing the setting applies at once, and Off leaves the music as is", () => {
  duckFor(GAME);
  setDuck(0.15);
  assert.equal(level(), 0.15);
  setDuck(1);
  assert.equal(level(), 1);
});

test("apps that aren't games don't lower it", () => {
  duckFor(TOOL);
  assert.equal(level(), 1);
  duckFor(0);
  assert.equal(level(), 1);
});
