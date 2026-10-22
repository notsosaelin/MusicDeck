/** The small building blocks every view uses: observable values and bounded caches. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { RecentlyUsed, Store, mediaUrl, pluginState } from "../../src/store";
import { formatBand } from "../../src/eq";

test("a Store tells its listeners about every change, until they unsubscribe", () => {
  const store = new Store({ a: 1, b: 2 });
  let heard = 0;
  const stop = store.subscribe(() => heard++);
  store.update({ b: 3 });
  assert.deepEqual(store.get(), { a: 1, b: 3 });
  stop();
  store.set({ a: 0, b: 0 });
  assert.equal(heard, 1);
});

test("RecentlyUsed keeps the most recently used, and null is a value", () => {
  const cache = new RecentlyUsed<string | null>(2);
  cache.set("a", "A");
  cache.set("b", null);
  cache.get("a");
  cache.set("c", "C");
  assert.equal(cache.has("b"), false);
  assert.equal(cache.get("a"), "A");
  cache.set("n", null);
  assert.equal(cache.has("n"), true);
  assert.equal(cache.get("n"), null);
  cache.clear();
  assert.equal(cache.has("a"), false);
});

test("media URLs carry the server's key and escape ids", () => {
  pluginState.set({ server: { base: "http://127.0.0.1:9", key: "a+b" } } as any);
  const url = mediaUrl("stream", "src", "f:B1/../x?y");
  assert.ok(url);
  assert.ok(url!.startsWith("http://127.0.0.1:9/stream/src/"));
  assert.ok(!url!.includes("/../"), url);
  assert.match(url!, /k=a%2Bb/);
});

test("EQ bands read as people say them", () => {
  assert.deepEqual([32, 1000, 16000].map(formatBand), ["32", "1k", "16k"]);
});
