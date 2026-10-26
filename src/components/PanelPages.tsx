import { Focusable } from "@decky/ui";
import { FC, ReactNode, useEffect, useRef, useState } from "react";
import { watchSize } from "./common";

/**
 * Pages in the Quick Access panel, as Now Playing's player and its lyrics/queue are: each as tall as the panel's room,
 * one showing at a time; the controller moving onto another page slides the frame a whole page, so nothing is ever
 * half shown and the panel itself never scrolls (Steam's own focus scrolling and scroll restore can't leave a page
 * half shown). What doesn't fit on a page is for its "Show all".
 */

/** How long a page takes to slide into place */
const SLIDE = "transform 0.24s cubic-bezier(0.25, 0.8, 0.3, 1)";
/** A page's height until the panel's been measured */
const UNMEASURED = 200;
/** Rows a page is given until a drawn one's been measured */
const FIRST_GUESS = 3;

function scrollParent(el: HTMLElement | null): HTMLElement | null {
  let node = el?.parentElement ?? null;
  while (node && !/auto|scroll/.test(getComputedStyle(node).overflowY)) node = node.parentElement;
  return node;
}

/**
 * `pages`: what's on each, given how many list rows a page holds (worked out from what's drawn: the step from one row
 * to the next, and what else a page has round its rows, a title and a Show all, against the page's height; so it fits
 * any screen, scale or text size); `onButtonDown`: the controller's buttons anywhere on them (L2/R2...).
 */
export const PanelPages: FC<{ pages: (rows: number) => ReactNode[]; onButtonDown?: (e: any) => void; className?: string }> = ({
  pages: draw,
  onButtonDown,
  className,
}) => {
  const root = useRef<HTMLDivElement>(null);
  const frames = useRef<(HTMLDivElement | null)[]>([]);
  const [room, setRoom] = useState(0);
  const [at, setAt] = useState(0);
  useEffect(() => {
    const el = root.current;
    const scroller = scrollParent(el);
    if (!el || !scroller) return;
    const measure = () => {
      const top = el.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop;
      setRoom(Math.floor(scroller.clientHeight - top));
    };
    measure();
    return watchSize(scroller, measure);
  }, []);
  // which page the controller is on decides which one shows
  useEffect(() => {
    const el = root.current;
    if (!el) return;
    const onFocus = (e: FocusEvent) => {
      const i = frames.current.findIndex((f) => f?.contains(e.target as Node));
      if (i >= 0) setAt(i);
    };
    el.addEventListener("focusin", onFocus);
    return () => el.removeEventListener("focusin", onFocus);
  }, []);
  // exactly the panel's room: never taller (the panel would scroll)
  const page = room > 0 ? room : UNMEASURED;
  // a row's step and what's round the rows on a page, measured from a page that has rows
  const [fit, setFit] = useState<{ step: number; around: number } | null>(null);
  const rows = fit ? Math.max(1, Math.floor((page - fit.around) / fit.step)) : FIRST_GUESS;
  const pages = draw(rows);
  useEffect(() => {
    // the most any page has round its rows (one with a title and a Show all, not one with neither)
    let step = 0;
    let around = 0;
    for (const frame of frames.current) {
      const found = frame ? [...frame.querySelectorAll<HTMLElement>(".md-list-row")] : [];
      if (!frame || !found.length) continue;
      const boxes = found.map((r) => r.getBoundingClientRect());
      const own = boxes.length > 1 ? boxes[1].top - boxes[0].top : boxes[0].height;
      step = Math.max(step, own);
      // the page's content, not its frame (as tall as the page however little is on it)
      const content = (frame.firstElementChild as HTMLElement | null)?.getBoundingClientRect().height ?? 0;
      around = Math.max(around, content - found.length * own);
    }
    if (step > 0 && (!fit || Math.abs(fit.step - step) > 1 || Math.abs(fit.around - around) > 1)) setFit({ step, around });
  });
  // fewer pages than before (other results): back onto one that's there
  useEffect(() => {
    if (at >= pages.length) setAt(Math.max(0, pages.length - 1));
  }, [pages.length]);
  return (
    // clip, not hidden: Steam's focus scrolling can move an overflow:hidden box
    <Focusable ref={root} className={className} flow-children="column" onButtonDown={onButtonDown} style={{ height: page, overflow: "clip" }}>
      <div style={{ transform: `translateY(${-Math.min(at, pages.length - 1) * page}px)`, transition: SLIDE }}>
        {pages.map((p, i) => (
          <div key={i} ref={(el) => {
              frames.current[i] = el;
            }} style={{ height: page, overflowY: "auto", scrollbarWidth: "none" }}>
            {/* what still doesn't fit scrolls inside its own page, never the panel */}
            <div>{p}</div>
          </div>
        ))}
      </div>
    </Focusable>
  );
};
