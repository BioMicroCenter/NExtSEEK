import { useCallback, useEffect, useRef, useState } from "react";

export const TURN_FLASH_MS = 2000;

/** Scroll the open chat to a turn (a search hit) and flash it.
 *
 *  Call it after the chat's turns are in state (once `setActive` resolves): the scroll runs in the effect of
 *  the render that follows, after the chat panel's own scroll-to-bottom, and looks once. A turn that is not
 *  on screen is dropped rather than kept pending, so it can never scroll a chat opened later. */
export interface ScrollToTurnOptions {
  /** Also open the answer's Search Details (an agent-steps hit: the Container-CC activity is in there). */
  openDetails?: boolean;
}

export function useScrollToTurn(): (turnId: number | null, opts?: ScrollToTurnOptions) => void {
  const wanted = useRef<number | null>(null);
  const openDetails = useRef(false);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const turnId = wanted.current;
    wanted.current = null;
    if (turnId === null) return;
    const el = document.querySelector<HTMLElement>(`[data-turn-id="${turnId}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    if (openDetails.current) {
      // The answer is the next bubble after the question.
      const toggle = el.nextElementSibling?.querySelector<HTMLButtonElement>(
        '[data-testid="search-details-toggle"][aria-expanded="false"]');
      toggle?.click();
    }
    el.setAttribute("data-turn-flash", "");
    const t = setTimeout(() => el.removeAttribute("data-turn-flash"), TURN_FLASH_MS);
    return () => {
      clearTimeout(t);
      el.removeAttribute("data-turn-flash"); // the next hit, inside the flash, must not leave this ring on
    };
  }, [request]);

  return useCallback((turnId: number | null, opts: ScrollToTurnOptions = {}) => {
    wanted.current = turnId;
    openDetails.current = Boolean(opts.openDetails);
    setRequest((n) => n + 1);
  }, []);
}
