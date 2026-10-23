// Loaded before every UI test (node --import): installs the module hooks and the few browser
// pieces the frontend's logic touches (window timers, <audio>, Web Audio).
import { registerHooks } from "node:module";
import { load, resolve } from "./loader.mjs";

registerHooks({ resolve, load });

globalThis.window = globalThis;

/** An <audio> element that does what it's told at once: play() plays, setting src loads. */
class FakeAudio extends EventTarget {
  src = "";
  paused = true;
  currentTime = 0;
  duration = NaN;
  volume = 1;
  loop = false;
  readyState = 0;
  crossOrigin = null;
  preload = "";
  play() {
    this.paused = false;
    this.dispatchEvent(new Event("play"));
    this.dispatchEvent(new Event("playing"));
    return Promise.resolve();
  }
  pause() {
    if (this.paused) return;
    this.paused = true;
    this.dispatchEvent(new Event("pause"));
  }
  load() {}
  removeAttribute(name) {
    if (name === "src") this.src = "";
  }
}
globalThis.Audio = FakeAudio;

/** Web Audio nodes that connect and hold values, and make no sound. */
const param = () => ({
  value: 1,
  cancelScheduledValues() {},
  setValueAtTime() {},
  linearRampToValueAtTime() {},
  setTargetAtTime(v) {
    this.value = v;
  },
});
const node = (extra = {}) => ({ gain: param(), frequency: param(), Q: param(), connect: (next) => next, ...extra });
globalThis.AudioContext = class {
  state = "running";
  currentTime = 0;
  destination = node();
  createGain = () => node();
  createBiquadFilter = () => node();
  createAnalyser = () => node({ fftSize: 256, getByteFrequencyData() {} });
  createMediaElementSource = () => node();
  resume() {}
  close() {}
};
globalThis.MediaError = { MEDIA_ERR_ABORTED: 1 };
