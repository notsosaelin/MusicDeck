import { Focusable } from "@decky/ui";
import { FC, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { isFirstRow, scrollParent } from "./parts";

/**
 * Looking at one source at a time (Browse, search results): dots along the top, half either side of the name of the
 * one showing, as Now Playing's are; the controller landing on a dot shows that source (no press needed), and L2/R2
 * step through them from anywhere under them (unhinted). Switching keeps the page still: the source before stays as
 * it was until the next one has loaded and settled where it'll be, then the next fades in over it; the page is held
 * at its top meanwhile, and the controller stays on the dots.
 */

// GamepadButton.TRIGGER_LEFT and TRIGGER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L2 = 7;
const R2 = 8;
/** How long a source's page takes to fade in over the one before */
const CROSSFADE_MS = 220;
/** No change of its size for this long: what's in it has settled */
const SETTLED_MS = 160;
/** Shown by now however it's doing (a slow source's rows come in where they are) */
const SETTLED_MAX_MS = 1500;

export interface Place {
  id: string;
  name: string;
}

/** What's in it, hidden until it's settled (loaded and laid out, no longer changing size), then faded in: it arrives
 *  where it'll stay, never seen moving into place. `leaving`: the one before, shown as it was until this one's ready,
 *  and out of the controller's way. */
const Settled: FC<{ children: ReactNode; onReady?: (el: HTMLElement) => void; leaving?: boolean }> = ({
  children,
  onReady,
  leaving,
}) => {
  const box = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);
  useEffect(() => {
    if (ready && box.current) onReady?.(box.current);
  }, [ready]);
  // set on the element itself (Steam's React doesn't pass `inert` on)
  useEffect(() => {
    box.current?.toggleAttribute("inert", !!leaving);
  }, [leaving]);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const win = (el.ownerDocument.defaultView ?? window) as Window & typeof globalThis;
    let quiet = 0;
    const show = () => setReady(true);
    const changed = () => {
      win.clearTimeout(quiet);
      quiet = win.setTimeout(show, SETTLED_MS);
    };
    const observer = new win.ResizeObserver(changed);
    observer.observe(el);
    changed();
    const latest = win.setTimeout(show, SETTLED_MAX_MS);
    return () => {
      observer.disconnect();
      win.clearTimeout(quiet);
      win.clearTimeout(latest);
    };
  }, []);
  return (
    <div ref={box} className="mc-browse-body" data-ready={ready ? "" : undefined}>
      {children}
    </div>
  );
};

/**
 * The dots and the page of the one showing. `at`: the one showing; `onChoose` is told when the controller has rested
 * on another (after `settleMs`: passing along the dots doesn't load every source on the way); `page` draws one.
 */
export const SourcePager: FC<{
  places: Place[];
  at: string;
  onChoose: (id: string) => void;
  page: (id: string) => ReactNode;
  settleMs?: number;
}> = ({ places, at, onChoose, page, settleMs = 0 }) => {
  // the dot the controller's on lights and names itself at once
  const [shown, setShown] = useState(at);
  const settle = useRef(0);
  useEffect(() => setShown(at), [at]);
  useEffect(() => () => window.clearTimeout(settle.current), []);
  const choose = (id: string) => {
    setShown(id);
    window.clearTimeout(settle.current);
    if (settleMs) settle.current = window.setTimeout(() => onChoose(id), settleMs);
    else onChoose(id);
  };
  const step = (by: number) => {
    const from = Math.max(0, places.findIndex((p) => p.id === shown));
    choose(places[(from + by + places.length) % places.length].id);
  };
  const dot = (p: Place) => (
    <Focusable
      key={p.id}
      className={p.id === shown ? "mc-dot mc-on" : "mc-dot"}
      noFocusRing
      onGamepadFocus={() => choose(p.id)}
      onClick={() => choose(p.id)}
    >
      <span />
    </Focusable>
  );
  const half = Math.ceil(places.length / 2);
  // the controller was in the one going (L2/R2 from its results): onto the first thing of the one come, so it's never
  // left on nothing; on the dots, it stays there
  const landIn = (el: HTMLElement) => {
    const active = el.ownerDocument.activeElement as HTMLElement | null;
    if (active?.closest(".mc-sources") || (active && el.contains(active))) return;
    if (active && top.current?.contains(active) && !active.closest("[inert]")) return;
    el.querySelector<HTMLElement>(".mc-row, .mc-card, .mc-tile")?.focus({ preventScroll: true });
  };
  // what's shown: the one now, and while it comes in, the one before
  const [layers, setLayers] = useState<string[]>([at]);
  useEffect(() => setLayers((now) => (now[now.length - 1] === at ? now : [now[now.length - 1], at])), [at]);
  // the page at its very top the moment it switches, and held there (a scroll undone before it's drawn) until the
  // user moves it: the controller going on past the first row, the wheel, a finger
  const top = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const scroller = top.current && scrollParent(top.current);
    if (!scroller) return;
    scroller.scrollTop = 0;
    const hold = () => {
      if (scroller.scrollTop !== 0) scroller.scrollTop = 0;
    };
    const wrap = top.current;
    const release = () => {
      scroller.removeEventListener("scroll", hold);
      wrap?.removeEventListener("focusin", moved);
      scroller.removeEventListener("wheel", release);
      scroller.removeEventListener("touchstart", release);
    };
    const moved = (e: FocusEvent) => {
      const el = e.target as HTMLElement | null;
      const block = el?.closest(".mc-row-block") as HTMLElement | null;
      if (el && !el.closest(".mc-sources") && !(block && isFirstRow(scroller, block))) release();
    };
    scroller.addEventListener("scroll", hold);
    wrap?.addEventListener("focusin", moved);
    scroller.addEventListener("wheel", release, { passive: true });
    scroller.addEventListener("touchstart", release, { passive: true });
    return release;
  }, [at]);
  return (
    <Focusable
      ref={top}
      onButtonDown={(e: any) => {
        const button = e?.detail?.button;
        if (button === L2 || button === R2) step(button === R2 ? 1 : -1);
      }}
    >
      {places.length > 1 && (
        <Focusable flow-children="row" className="mc-sources">
          <div className="mc-sources-side" style={{ justifyContent: "flex-end" }}>
            {places.slice(0, half).map(dot)}
          </div>
          <div className="mc-sources-name">{places.find((p) => p.id === shown)?.name ?? ""}</div>
          <div className="mc-sources-side">{places.slice(half).map(dot)}</div>
        </Focusable>
      )}
      <div className="mc-browse-stack">
        {layers.map((id) => (
          <Settled
            key={id}
            leaving={id !== at}
            onReady={(el) => {
              if (id !== at) return;
              landIn(el);
              window.setTimeout(() => setLayers([id]), CROSSFADE_MS);
            }}
          >
            {page(id)}
          </Settled>
        ))}
      </div>
    </Focusable>
  );
};
