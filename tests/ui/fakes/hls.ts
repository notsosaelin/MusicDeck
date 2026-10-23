/** hls.js stand-in: the tests only play plain files. */
export default class Hls {
  static isSupported = () => false;
  static Events = {};
  static ErrorDetails = { FRAG_LOAD_ERROR: "fragLoadError" };
  loadSource() {}
  attachMedia() {}
  detachMedia() {}
  destroy() {}
  on() {}
}
