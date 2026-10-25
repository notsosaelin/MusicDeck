/** The one library's search, filter and order (src/library.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { bySource, filterLibrary, librarySources, mergeSearch, sourcesIn, splitPage } from "../../src/library";
import type { Page, Section, ServiceInfo } from "../../src/api";

const service = (id: string, type: string, name: string) => ({ id, type, name }) as unknown as ServiceInfo;
const SERVICES = [service("qz", "qobuz", "Qobuz"), service("lf", "local", "Local files"), service("st", "steam", "Steam")];
const page = (tracks: string[], collections: string[]): Page => ({
  title: "", subtitle: "", art: null,
  tracks: tracks.map((t) => ({ id: t, title: t, artist: "", album: "", duration: 1, art: null })),
  collections: collections.map((c) => ({ id: c, kind: "album", title: c, subtitle: "", art: null })),
});

test("Local files comes first, the rest as they're listed", () => {
  assert.deepEqual(librarySources(SERVICES).map((s) => s.id), ["lf", "qz", "st"]);
});

test("search: each source's songs and albums, each saying its source; split back the same", () => {
  const merged = mergeSearch("q", [
    { source: SERVICES[1], page: page(["a", "b"], ["A"]) },
    { source: SERVICES[0], page: page(["c"], []) },
  ]);
  assert.deepEqual(merged.tracks.map((t) => [t.id, t.source]), [["a", "lf"], ["b", "lf"], ["c", "qz"]]);
  assert.equal(merged.collections[0].source, "lf");
  const parts = splitPage(merged, "lf", ["qz", "lf"]);
  assert.deepEqual(parts.map((p) => [p.source, p.page.tracks.length]), [["qz", 1], ["lf", 2]]);
  assert.deepEqual(bySource(merged.tracks, "x").map((p) => p.source), ["lf", "qz"]);
});

const albums: Section[] = [{
  title: "",
  items: [
    { id: "1", kind: "album", title: "The Zebra", subtitle: "Beta · 2001", art: null, source: "qz" },
    { id: "2", kind: "album", title: "Apple", subtitle: "26 tracks", art: null, source: "st" },
    { id: "3", kind: "album", title: "Mango", subtitle: "Alpha · 1999", art: null, source: "lf" },
  ],
}];

test("Your music: one source's only, or every one's", () => {
  assert.deepEqual(sourcesIn(albums, SERVICES).map((s) => s.id), ["lf", "qz", "st"]);
  assert.deepEqual(filterLibrary(albums, "qz", "title", SERVICES)[0].items.map((c) => c.id), ["1"]);
});

test("Your music's orders: by title (\"The\" aside), by artist (none last), by source", () => {
  const order = (sort: "title" | "artist" | "source") => filterLibrary(albums, "", sort, SERVICES)[0].items.map((c) => c.title);
  assert.deepEqual(order("title"), ["Apple", "Mango", "The Zebra"]);
  assert.deepEqual(order("artist"), ["Mango", "The Zebra", "Apple"]);
  assert.deepEqual(order("source"), ["Mango", "The Zebra", "Apple"]);
});
