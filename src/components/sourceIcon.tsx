import { ReactNode } from "react";
import { FaBroadcastTower, FaCompactDisc, FaFolder, FaServer } from "react-icons/fa";
import {
  SiApplepodcasts,
  SiAudiobookshelf,
  SiBandcamp,
  SiEmby,
  SiInternetarchive,
  SiJellyfin,
  SiPlex,
  SiSoundcloud,
  SiSpotify,
  SiSteam,
  SiTidal,
  SiYoutubemusic,
} from "react-icons/si";

/** Each kind of source's own mark, where there's one to have (the others show their initial) */
const MARKS: Record<string, ReactNode> = {
  soundcloud: <SiSoundcloud />,
  youtube: <SiYoutubemusic />,
  bandcamp: <SiBandcamp />,
  spotify: <SiSpotify />,
  tidal: <SiTidal />,
  plex: <SiPlex />,
  jellyfin: <SiJellyfin />,
  emby: <SiEmby />,
  steam: <SiSteam />,
  podcasts: <SiApplepodcasts />,
  livemusic: <SiInternetarchive />,
  audiobookshelf: <SiAudiobookshelf />,
  local: <FaFolder />,
  radio: <FaBroadcastTower />,
  subsonic: <FaServer />,
  cd: <FaCompactDisc />,
};

/** A source's mark: its logo, else the first letter of its name (Qobuz's "Q") */
export function sourceMark(type: string, name: string): ReactNode {
  return MARKS[type] ?? <span style={{ fontWeight: 800, fontSize: "0.95em", lineHeight: 1 }}>{name.trim().charAt(0).toUpperCase()}</span>;
}
