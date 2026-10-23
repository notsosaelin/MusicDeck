// The built plugin (dist/index.js), as Decky loads it: the bundle evaluates, the plugin starts, wires
// its player and listeners, and unloads leaving nothing registered. The UI tests run the TypeScript
// sources; this catches what only the bundler does to them (a getter read it dropped as unused once
// left casting unwired). Run after a build: npm run build && npm run smoke.
import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const BUNDLE = new URL("../../dist/index.js", import.meta.url);

/** Anything at all: every property and call gives another, so Steam's UI modules can be stood in for. */
const anything = () =>
  new Proxy(function () {}, {
    get: (_, key) => (key === Symbol.toPrimitive ? () => "" : key === "then" ? undefined : anything()),
    apply: () => anything(),
    construct: () => anything(),
  });

const STATE = {
  version: "0.0.0",
  services: [],
  active_service: null,
  qam_mode: "replace",
  volume: 0.8,
  eq: { enabled: false, preset: "Flat", gains: Array(10).fill(0), preamp: 0 },
  eq_per_output: false,
  output: null,
  playback: { levelling: "track", scrobble: true, queue_sync: true, resume: false, autoplay: true,
              game_pause: false, game_duck: 1, crossfade: 0 },
  debug_log: false,
  debug_log_path: "",
  user_home: "/home/deck",
  server: { base: "http://127.0.0.1:1", key: "k" },
  service_types: [],
  planned_types: [],
  listenbrainz: { user: "", pending: 0 },
  lastfm: { user: "", has_key: false, pending: 0 },
  game_music: [],
  offline: { tracks: 0, bytes: 0, pending: 0, failed: 0, current: null, stopped: "" },
  center: { open_on: "last", background: "art", cards: "medium", lyrics: "medium", show_library: true,
            show_browse: true, idle_off: 0 },
};

// what the plugin registers with Decky, to see it's all let go at unload
const listeners = new Map();
const routes = new Set();
const api = {
  _version: 2,
  callable: (name) => async () => (name === "get_state" ? structuredClone(STATE) : null),
  addEventListener: (name, handler) => {
    if (!listeners.has(name)) listeners.set(name, new Set());
    listeners.get(name).add(handler);
    return handler;
  },
  removeEventListener: (name, handler) => listeners.get(name)?.delete(handler),
  routerHook: { addRoute: (path) => routes.add(path), removeRoute: (path) => routes.delete(path) },
  openFilePicker: async () => null,
  toaster: { toast: () => {} },
};
globalThis.__DECKY_SECRET_INTERNALS_DO_NOT_USE_OR_YOU_WILL_BE_FIRED_deckyLoaderAPIInit = { connect: () => api };
globalThis.DFL = anything();
globalThis.SP_REACT = anything();
globalThis.SP_REACTDOM = anything();
globalThis.SP_JSX = { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: "F" };
globalThis.document ??= { addEventListener() {}, removeEventListener() {}, visibilityState: "visible" };

test("the built plugin starts, wires its player, and unloads cleanly", { skip: !existsSync(BUNDLE) && "no build" }, async () => {
  const { default: plugin } = await import(pathToFileURL(BUNDLE.pathname.replace(/^\/(\w:)/, "$1")).href);
  const loaded = plugin();
  assert.equal(loaded.name, "MusicDeck");
  const handle = globalThis.__musicdeck;
  assert.ok(handle?.player, "the debug handle is set");
  // the casting element is made as the player starts: playback while casting runs through it
  assert.ok(handle.player.castDeck?.built, "the cast player's element is built");
  assert.ok(routes.has("/musicdeck/player"), "the media center's route is added");
  assert.ok(listeners.get("cast_status")?.size, "casting status is listened for");

  await new Promise((r) => setTimeout(r, 50)); // the state loads
  assert.equal(handle.pluginState.get()?.version, "0.0.0", "the state arrives");

  loaded.onDismount();
  assert.equal(globalThis.__musicdeck, undefined, "the debug handle goes");
  assert.equal(routes.size, 0, "the route goes");
  for (const [name, set] of listeners) assert.equal(set.size, 0, `no ${name} listener is left`);
});

test("no JSX text in the bundle starts with //", { skip: !existsSync(BUNDLE) && "no build" }, () => {
  // a `// note` among JSX children is text on screen, not a comment
  const bundle = readFileSync(BUNDLE, "utf8");
  // (a string starting "// " is nothing else in this bundle: URLs start "https://", "//host")
  const stray = bundle.match(/"\s*\/\/ [^"\n]*"/g) ?? [];
  assert.deepEqual(stray, []);
});
