import { addEventListener, removeEventListener } from "@decky/api";
import { ButtonItem, ConfirmModal, Field, PanelSectionRow, showModal } from "@decky/ui";
import { FC, useEffect, useState } from "react";
import { Devices, OtherSession, Session, allowFirewall, answerPairing, devices, otherSessions, pairDevice, takeOver, unpairDevice } from "./api";
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
  const tookOver = (p: { name: string }) => {
    if (player.state.get().playing) player.toggle();
    notify("MusicDeck", `Playing on ${p.name}`);
  };
  addEventListener("peer_pair_request", asked);
  addEventListener("peer_paired", paired);
  addEventListener("peer_pair_failed", failed);
  addEventListener("peer_took_over", tookOver);
  // another device's Play on chose this one: its music plays on here
  const handed = (p: { name: string; session: Session }) => player.takeOver(p.session);
  addEventListener("peer_play", handed);
  return () => {
    removeEventListener("peer_pair_request", asked);
    removeEventListener("peer_paired", paired);
    removeEventListener("peer_pair_failed", failed);
    removeEventListener("peer_took_over", tookOver);
    removeEventListener("peer_play", handed);
  };
}

/** The paired devices and the ones nearby, looked at again every few seconds while shown. */
function useDevices(): Devices | null {
  const [found, setFound] = useState<Devices | null>(null);
  useEffect(() => {
    let live = true;
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
  }, []);
  return found;
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
export const DeviceRows: FC = () => {
  const found = useDevices();
  const [asking, setAsking] = useState(false);
  if (!found || (!found.paired.length && !found.nearby.length && found.firewall !== "blocked")) return null;
  return (
    <>
      <PanelSectionRow>
        <Field label="Your devices" description={found.firewall === "blocked" ? undefined : found.me.name} />
      </PanelSectionRow>
      {found.firewall === "blocked" && (
        <PanelSectionRow>
          <ButtonItem
            layout="below"
            label={found.me.name}
            disabled={asking}
            onClick={async () => {
              setAsking(true);
              const ok = await allowFirewall().catch(() => false);
              setAsking(false);
              if (!ok) notify("MusicDeck", "Windows didn't allow it");
            }}
          >
            {asking ? "Asking Windows…" : "Let your devices reach this PC"}
          </ButtonItem>
        </PanelSectionRow>
      )}
      {found.paired.map((d) => (
        <PanelSectionRow key={d.id}>
          <ButtonItem
            layout="inline"
            label={d.name}
            description={d.online ? "Paired · on now" : "Paired"}
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
          </ButtonItem>
        </PanelSectionRow>
      ))}
      {found.nearby.map((d) => (
        <PanelSectionRow key={d.id}>
          <ButtonItem layout="inline" label={d.name} onClick={() => pair(d.id)}>
            Pair
          </ButtonItem>
        </PanelSectionRow>
      ))}
    </>
  );
};

/** What the other devices were playing lately (looked at again every half minute while shown), newest first. */
export function useOtherSessions(): OtherSession[] {
  const [found, setFound] = useState<OtherSession[]>([]);
  useEffect(() => {
    let live = true;
    const look = () =>
      otherSessions()
        .then((r) => live && r.ok && setFound([...r.data].sort((a, b) => b.saved_at - a.saved_at)))
        .catch(() => {});
    look();
    const timer = window.setInterval(look, 30_000);
    return () => {
      live = false;
      window.clearInterval(timer);
    };
  }, []);
  // not what's playing here already (continued a moment ago)
  const here = player.current;
  return found.filter((s) => !(here && here.track.id === s.track.id));
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
