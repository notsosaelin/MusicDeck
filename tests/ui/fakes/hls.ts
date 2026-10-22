/** hls.js stand-in: the tests only play plain files. */
export default class Hls {
  static isSupported = () => false;
  static Events = {};
  loadSource() {}
  attachMedia() {}
  detachMedia() {}
  destroy() {}
  on() {}
}
