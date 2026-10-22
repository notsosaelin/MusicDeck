declare module "*.svg" {
  const content: string;
  export default content;
}

declare module "*.png" {
  const content: string;
  export default content;
}

declare module "*.jpg" {
  const content: string;
  export default content;
}

// hls.js "light" build (no subtitles/alt-audio/DRM; enough for audio) — same API as the full build
declare module "hls.js/dist/hls.light.min.js" {
  import Hls from "hls.js";
  export default Hls;
}
