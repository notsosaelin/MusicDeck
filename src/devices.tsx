import { addEventListener, removeEventListener } from "@decky/api";
import { ButtonItem, ConfirmModal, DialogButton, Field, PanelSection, PanelSectionRow, showModal } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { Devices, OtherSession, Track, reportRenderStatus, allowFirewall, answerPairing, devices, otherSessions, pairDevice, takeOver, unpairDevice } from "./api";
import type { SectionFrame } from "./components/Settings";
import { notify } from "./notify";
import { player } from "./player";

/**
 * Your other devices (py_modules/musicdeck/peers.py): MusicDeck on the PC and the Deck, paired once. Pairing shows a
 * code on both, accepted on the device asked; after that each one's listening counts in the other's stats, and
 * what one was playing can be continued on the other.
 */

/** Pairing prompts, and what a paired device says (it took over the music), wherever MusicDeck shows. */
export function watchDevices(): () => void {
  const asked = (p: { rid: string; name: string; code: string }) =>
    showModal(
      <ConfirmModal
        strTitle={`Pair with ${p.name}?`}
        strDescription={`${p.code.slice(0, 3)} ${p.code.slice(3)} should show on ${p.name} too.`}
        strOKButtonText="Pair"
        onOK={() => answerPairing(p.rid, true).catch(() => {})}
        onCancel={() => answerPairing(p.rid, false).catch(() => {})}
      />,
    );
  const paired = (p: { name: string }) => notify("MusicDeck", `Paired with ${p.name}`);
  const failed = (p: { name: string }) => notify("MusicDeck", `${p.name} didn't pair`);
  // another device picked up the music: it stops here
  const tookOver = (p: { id: string; name: string }) => {
    if (player.state.get().playing) player.toggle();
    else player.savedForHandoff();
    notify("MusicDeck", `Playing on ${p.name}`);
    // play here again: on from where it's got to there, not from where it stopped here
    player.resumeElsewhere = async () => {
      const r = await takeOver(p.id).catch(() => null);
      if (!r?.ok) return false;
      player.takeOver(r.data);
      return true;
    };
  };
  addEventListener("peer_pair_request", asked);
  addEventListener("peer_paired", paired);
  addEventListener("peer_pair_failed", failed);
  addEventListener("peer_took_over", tookOver);
  addEventListener("peer_cast", playedOn);
  return () => {
    removeEventListener("peer_pair_request", asked);
    removeEventListener("peer_paired", paired);
    removeEventListener("peer_pair_failed", failed);
    removeEventListener("peer_took_over", tookOver);
    removeEventListener("peer_cast", playedOn);
    stopReporting();
  };
}

// --- another device playing on this one, as on a speaker (its cast/peer.py; peers.py and main.py here) ---

let reporting = 0;
const stopReporting = () => {
  window.clearInterval(reporting);
  reporting = 0;
};

/** Where this device has got to with what the other gave it, once a second: the other shows it as it goes. */
function report() {
  const { playing, loading, volume } = player.state.get();
  const { current, duration } = player.position.get();
  const state = player.renderEnded ? "idle" : loading ? "buffering" : playing ? "playing" : "paused";
  reportRenderStatus({ state, position: current, duration, ended: player.renderEnded, volume }).catch(() => {});
}

type CastHere =
  | { action: "start"; name: string }
  | { action: "load"; name: string; item: { instanceId: string; track: Track }; start: number; autoplay: boolean }
  | { action: "control"; control: "play" | "pause" | "seek" | "stop" | "volume"; value: number | null }
  | { action: "end"; stop: boolean };

function playedOn(e: CastHere) {
  if (e.action === "start") {
    player.renderFor = e.name;
    stopReporting();
    reporting = window.setInterval(report, 1000);
  } else if (e.action === "load") {
    player.renderFor = e.name;
    player.renderEnded = false;
    player.clear();
    player.restore({ queue: [e.item], index: 0, position: e.start }, e.autoplay);
  } else if (e.action === "control") {
    const { playing } = player.state.get();
    if (e.control === "play" && !playing) player.toggle();
    else if ((e.control === "pause" || e.control === "stop") && playing) player.toggle();
    else if (e.control === "seek" && e.value !== null) player.seek(e.value);
    else if (e.control === "volume" && e.value !== null) player.setVolume(e.value);
    report();
  } else if (e.action === "end") {
    if (e.stop && player.state.get().playing) player.toggle();
    player.renderFor = null;
    stopReporting();
  }
}

/** The paired devices and the ones nearby, looked at again every few seconds while shown. */
/** This device, the paired ones and those nearby, looked at every few seconds while shown (and meanwhile another
 * device may ask to pair). */
function useDevices(): [Devices | null, () => void] {
  const [found, setFound] = useState<Devices | null>(null);
  const [again, setAgain] = useState(0);
  useEffect(() => {
    let live = true;
    // each look also asks the devices on this network to say where they are
    const look = () =>
      devices()
        .then((r) => live && r.ok && setFound(r.data))
        .catch(() => {});
    look();
    const timer = window.setInterval(look, 4000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, [again]);
  return [found, () => setAgain((n) => n + 1)];
}

/** A paired device's line: what it's playing (to continue here) and where it is, on with nothing to continue, or
 * offline. */
export function deviceState(d: Devices["paired"][number], sessions: OtherSession[]): string {
  if (!d.online) return "Offline";
  const away = d.via === "internet" ? " · via internet" : "";
  const s = sessions.find((x) => x.peer === d.id);
  if (!s) return `Online, nothing to continue${away}`;
  const what = [s.track.title, s.track.artist].filter(Boolean).join(" – ");
  return `${s.playing ? "Playing" : "Paused"}: ${what}${away}`;
}

const pair = async (id: string) => {
  const r = await pairDevice(id).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) {
    notify("MusicDeck", r.error);
    return;
  }
  showModal(
    <ConfirmModal
      bAlertDialog
      strTitle={`${r.data.code.slice(0, 3)} ${r.data.code.slice(3)}`}
      strDescription={`Accept on ${r.data.name} if it shows the same.`}
      strOKButtonText="OK"
    />,
  );
};

/** Settings' rows: each paired device (Unpair), then each one nearby (Pair). */
export const DeviceRows: FC<{ Section?: SectionFrame }> = ({ Section = PanelSection }) => {
  const [found, lookAgain] = useDevices();
  const [asking, setAsking] = useState(false);
  // what each one that's on was playing: looked at again when one comes or goes
  const sessions = useOtherSessions(
    (found?.paired ?? [])
      .filter((d) => d.online)
      .map((d) => d.id)
      .join(","),
  );
  if (!found) return null;
  return (
    <Section title="Your devices">
      <PanelSectionRow>
        <Field label="This device" description={found.me.name} />
      </PanelSectionRow>
      {found.firewall === "blocked" && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            disabled={asking}
            onClick={async () => {
              setAsking(true);
              const ok = await allowFirewall().catch(() => false);
              setAsking(false);
              if (!ok) notify("MusicDeck", "Windows didn't allow it");
            }}
          >
            {asking ? "Asking Windows…" : "Allow device connections"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {found.paired.map((d) => (
        <PanelSectionRow key={d.id}>
          <Field
            label={d.name}
            description={deviceState(d, sessions)}
            verticalAlignment="center"
          >
            <DialogButton
              style={{ minWidth: 80, width: 80, padding: "8px 12px" }}
              onClick={() =>
                showModal(
                  <ConfirmModal
                    strTitle={`Unpair ${d.name}?`}
                    strOKButtonText="Unpair"
                    bDestructiveWarning
                    onOK={() => unpairDevice(d.id).catch(() => {})}
                  />,
                )
              }
            >
              Unpair
            </DialogButton>
          </Field>
        </PanelSectionRow>
      ))}
      <PanelSectionRow>
        <Field
          label="Pair a new device"
          description={
            found.nearby.length
              ? "Pick it below: both show the same six digits, accepted on the other one."
              : "Looking on this network. Open Your devices in MusicDeck's settings on your other PC, Deck or Steam Machine too, and it shows here. Once paired, they reach each other anywhere."
          }
          verticalAlignment="center"
        >
          <DialogButton style={{ minWidth: 120, width: 120, padding: "8px 12px" }} onClick={lookAgain}>
            Look again
          </DialogButton>
        </Field>
      </PanelSectionRow>
      {found.nearby.map((d) => (
        <PanelSectionRow key={d.id}>
          <Field label={d.name} description="Nearby" verticalAlignment="center">
            <DialogButton style={{ minWidth: 80, width: 80, padding: "8px 12px" }} onClick={() => pair(d.id)}>
              Pair
            </DialogButton>
          </Field>
        </PanelSectionRow>
      ))}
    </Section>
  );
};

let lastSessions: OtherSession[] = [];

/** What the other devices were playing lately (looked at again every half minute while shown, and when `refresh`
 * changes), newest first. */
export function useOtherSessions(refresh = ""): OtherSession[] {
  // the last that came, at once (Home's first row is there as it opens, and takes the controller)
  const [found, setFound] = useState<OtherSession[]>(lastSessions);
  useEffect(() => {
    let live = true;
    const look = () =>
      otherSessions()
        .then((r) => {
          if (!r.ok) return;
          lastSessions = [...r.data].sort((a, b) => b.saved_at - a.saved_at);
          if (live) setFound(lastSessions);
        })
        .catch(() => {});
    look();
    const timer = window.setInterval(look, 30_000);
    // a device just came or went (over the internet), or what it plays changed: at once, not at the next half minute
    const moved = () => void look();
    addEventListener("peer_presence", moved);
    // one played, paused or changed song
    addEventListener("peer_session", moved);
    return () => {
      live = false;
      window.clearInterval(timer);
      removeEventListener("peer_presence", moved);
      removeEventListener("peer_session", moved);
    };
  }, [refresh]);
  // not what's playing here now (continued a moment ago); what stopped here when the other took it is offered, from
  // where it's got to there
  const here = player.current;
  const playing = player.state.get().playing;
  return found.filter((s) => !(playing && here && here.track.id === s.track.id));
}

/** Plays here what another device was playing, from where it was; that device stops. */
export async function continueFrom(s: OtherSession) {
  const r = await takeOver(s.peer).catch((e) => ({ ok: false as const, error: String(e) }));
  if (!r.ok) {
    notify("MusicDeck", r.error);
    return;
  }
  player.takeOver(r.data);
}
