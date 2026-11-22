import { Focusable } from "@decky/ui";
import { CSSProperties, FC, ReactNode, useEffect, useLayoutEffect, useRef, useState } from "react";
import { isFirstRow, scrollParent } from "./parts";

/**
 * Looking at one source at a time (Browse, search results): dots along the top, half either side of the name of the
 * one showing, as Now Playing's are; the controller landing on a dot shows that source (no press needed), and L2/R2
 * step through them from anywhere under them (unhinted).
 *
 * Switching is one clean swap: the source before stays as it is until the next one has loaded and settled where it'll
 * be (drawn out of sight meanwhile), then it goes and the next fades in, in the same place; never both at once, never
 * an empty page. The last few sources looked at stay drawn (out of sight), so going back to one is instant, nothing
 * loading again. The page is at its top when it swaps, and held there until the user moves it; the controller stays
 * on the dots (or, with L2/R2 from the page, lands on the new one's first thing).
 */

// GamepadButton.TRIGGER_LEFT and TRIGGER_RIGHT from @decky/ui (an ambient enum, which isolated builds can't import)
const L2 = 7;
const R2 = 8;
// NavEntryPositionPreferences.PREFERRED_CHILD (ambient too): coming into the dots lands on the lit one
const PREFERRED_CHILD = 4;
/** No change of its size for this long: what's in it has settled */
const SETTLED_MS = 160;
/** Shown by now however it's doing (a slow source's rows come in where they are) */
const SETTLED_MAX_MS = 1500;
/** Sources kept drawn (out of sight) for going back to them */
const KEPT = 4;

export interface Place {
  id: string;
  name: string;
}

/**
 * Tells `onReady` once what's in it has loaded and settled (no longer changing size), with its element. `away`: out of
 * the controller's reach (one kept out of sight, or coming), else up or down from the one in sight would try them.
 */
const Settled: FC<{
  children: ReactNode;
  onReady: (el: HTMLElement) => void;
  style: CSSProperties;
  className: string;
  away: boolean;
}> = ({ children, onReady, style, className, away }) => {
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const win = (el.ownerDocument.defaultView ?? window) as Window & typeof globalThis;
    let quiet = 0;
    let done = false;
    const show = () => {
      if (done) return;
      done = true;
      onReady(el);
    };
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
    <Focusable ref={box} className={className} style={style} flow-children="column" {...({ childFocusDisabled: away } as any)}>
      {children}
    </Focusable>
  );
};

/**
 * The dots and the page of the one showing. `at`: the one wanted; `onChoose` is told when the controller has rested
 * on another (after `settleMs`: passing along the dots doesn't load every source on the way); `page` draws one;
 * `leading`: what sits at the start of the dots' line (a search's words), balanced by room at its end.
 */
export const SourcePager: FC<{
  places: Place[];
  at: string;
  onChoose: (id: string) => void;
  page: (id: string) => ReactNode;
  settleMs?: number;
  leading?: ReactNode;
}> = ({ places, at, onChoose, page, settleMs = 0, leading }) => {
  // the dot the controller's on lights and names itself at once
  const [lit, setLit] = useState(at);
  const settle = useRef(0);
  useEffect(() => setLit(at), [at]);
  useEffect(() => () => window.clearTimeout(settle.current), []);
  const choose = (id: string) => {
    setLit(id);
    window.clearTimeout(settle.current);
    if (settleMs) settle.current = window.setTimeout(() => onChoose(id), settleMs);
    else onChoose(id);
  };

  // drawn: the sources looked at lately (the newest last); ready: those settled; shown: the one in sight
  const [drawn, setDrawn] = useState<string[]>([at]);
  const [ready, setReady] = useState<Set<string>>(new Set());
  const [shown, setShown] = useState(at);
  useEffect(() => {
    setDrawn((now) => [...now.filter((id) => id !== at), at].slice(-KEPT));
    if (ready.has(at)) setShown(at); // one kept: at once
  }, [at]);
  const top = useRef<HTMLDivElement>(null);
  const dots = useRef(new Map<string, HTMLElement>());
  const firstThing = (box: ParentNode | null | undefined) =>
    box?.querySelector<HTMLElement>(".mc-row, .mc-card, .mc-tile") ?? null;
  // L2/R2 (from the page or the dots): the controller goes at once onto the dot of the source it switches to. The dots
  // never hide, so it's never left in a page that's about to (Steam then drops it up onto the L1/R1 tabs: the fault
  // this is here for), and left/right on the dots go on from where the switch went. `carry`: switched from the page,
  // so once that source shows, the controller goes on down onto its first thing, if it's still on that dot.
  const carry = useRef<string | null>(null);
  const shownOnce = useRef(false);
  useLayoutEffect(() => {
    const doc = top.current?.ownerDocument;
    const active = doc?.activeElement as HTMLElement | null | undefined;
    if (carry.current === shown && active && active === dots.current.get(shown)) {
      carry.current = null;
      firstThing(top.current?.querySelector(".mc-in-sight"))?.focus({ preventScroll: true });
    } else if (active && top.current?.contains(active) && active.closest(".mc-browse-body:not(.mc-in-sight)")) {
      // anything else that left it in a page now out of sight: onto the lit dot before Steam drops it
      dots.current.get(shown)?.focus({ preventScroll: true });
    }
  }, [shown]);
  const settled = (id: string, el: HTMLElement) => {
    setReady((r) => new Set(r).add(id));
    if (id !== at) return;
    setShown(id);
    if (shownOnce.current) return;
    shownOnce.current = true;
    // the tab just opened (Steam puts the controller in its content): onto the first thing once there's one
    const active = el.ownerDocument.activeElement as HTMLElement | null;
    if (!active?.closest(".mc-sources") && !(active && el.contains(active)))
      window.setTimeout(() => firstThing(el)?.focus({ preventScroll: true }), 0);
  };

  // the page at its very top when it swaps, and held there (a scroll undone before it's drawn) until the user moves
  // it: the controller going on past the first row, the wheel, a finger
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
  }, [shown]);

  const dot = (p: Place) => (
    <Focusable
      key={p.id}
      ref={(el: HTMLElement | null) => {
        if (el) dots.current.set(p.id, el);
        else dots.current.delete(p.id);
      }}
      className={p.id === lit ? "mc-dot mc-on" : "mc-dot"}
      noFocusRing
      // pressable, so Steam makes it a stop for the controller (it skips a Focusable that only has onClick); landing on
      // it is choosing it all the same
      onActivate={() => choose(p.id)}
      preferredFocus={p.id === lit}
      onGamepadFocus={() => {
        if (carry.current !== p.id) carry.current = null; // moved along the dots: staying on them
        choose(p.id);
      }}
      onClick={() => choose(p.id)}
    >
      <span />
    </Focusable>
  );
  const half = Math.ceil(places.length / 2);
  return (
    // a column: the dots, then the page under them (up from the page's top comes back to the dots)
    <Focusable
      ref={top}
      flow-children="column"
      onButtonDown={(e: any) => {
        const button = e?.detail?.button;
        if (button !== L2 && button !== R2) return;
        e?.stopPropagation?.(); // this switch only: nothing above acts on the same press
        if (places.length < 2) return;
        const active = top.current?.ownerDocument.activeElement as HTMLElement | null | undefined;
        const fromPage = !!active && !!top.current?.contains(active) && !active.closest(".mc-sources");
        const from = Math.max(0, places.findIndex((p) => p.id === lit));
        const to = places[(from + (button === R2 ? 1 : -1) + places.length) % places.length].id;
        // pressed again before the last one showed: still carried down once it does
        carry.current = fromPage || carry.current ? to : null;
        choose(to);
        dots.current.get(to)?.focus({ preventScroll: true });
      }}
    >
      {(places.length > 1 || leading !== undefined) && (
        <Focusable flow-children="row" className="mc-sources" navEntryPreferPosition={PREFERRED_CHILD}>
          {leading !== undefined && <div className="mc-sources-end">{leading}</div>}
          <div className="mc-sources-side" style={{ justifyContent: "flex-end" }}>
            {places.length > 1 && places.slice(0, half).map(dot)}
          </div>
          <div className="mc-sources-name">{places.length > 1 ? (places.find((p) => p.id === lit)?.name ?? "") : ""}</div>
          <div className="mc-sources-side">
            {places.length > 1 && places.slice(half).map(dot)}
            {places.length > 1 && places.length % 2 === 1 && <div className="mc-dot-gap" />}
          </div>
          {leading !== undefined && <div className="mc-sources-end" />}
        </Focusable>
      )}
      <div className="mc-browse-stack">
        {drawn.map((id) => {
          const inSight = id === shown;
          const coming = id === at && !inSight;
          return (
            <Settled
              key={id}
              onReady={(el) => settled(id, el)}
              away={!inSight}
              className={inSight ? "mc-browse-body mc-in-sight" : "mc-browse-body"}
              // out of sight: kept (no layout), or coming (laid out, invisible, out of the controller's way)
              style={inSight ? {} : coming ? { visibility: "hidden" } : { display: "none" }}
            >
              {page(id)}
            </Settled>
          );
        })}
      </div>
    </Focusable>
  );
};
