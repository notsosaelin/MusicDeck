/** Which server addresses get the "not encrypted" warning in the add-source dialog. */
import { test } from "node:test";
import assert from "node:assert/strict";
import { unencryptedWarning } from "../../src/addresses";

test("plain http to the internet is warned about", () => {
  for (const url of ["http://music.example.com", "HTTP://8.8.8.8:4533/", " http://my.server.org/jellyfin "])
    assert.ok(unencryptedWarning(url), url);
});

test("https, and plain http inside the home network, are fine", () => {
  for (const url of [
    "https://music.example.com",
    "http://192.168.1.20:4533",
    "http://10.0.0.5",
    "http://172.20.1.1",
    "http://nas.local:8096",
    "http://nas:8096",
    "http://localhost:13378",
    "http://127.0.0.1",
    "http://100.101.102.103", // Tailscale
    "http://[fd00::1]:4533",
    "",
    undefined,
    "music.example.com", // no scheme: MusicDeck adds https://
  ])
    assert.equal(unencryptedWarning(url as any), undefined, String(url));
});

test("look-alikes of private addresses aren't private", () => {
  for (const url of [
    "http://192.168.1.1.evil.com",
    "http://172.32.0.1",
    "http://[2001:db8::1]:4533",
    "http://100.128.0.1",
    "http://10.0.0.1.nip.io",
  ])
    assert.ok(unencryptedWarning(url), url);
});
