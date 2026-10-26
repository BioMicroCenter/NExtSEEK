import { useCallback, useEffect, useRef, useState } from "react";

export const TURN_FLASH_MS = 2000;

/** Scroll the open chat to a turn (a search hit) and flash it.
 *
 *  Call it after the chat's turns are in state (once `setActive` resolves): the scroll runs in the effect of
 *  the render that follows, after the chat panel's own scroll-to-bottom, and looks once. A turn that is not
 *  on screen is dropped rather than kept pending, so it can never scroll a chat opened later. */
export function useScrollToTurn(): (turnId: number | null) => void {
  const wanted = useRef<number | null>(null);
  const [request, setRequest] = useState(0);

  useEffect(() => {
    const turnId = wanted.current;
    wanted.current = null;
    if (turnId === null) return;
    const el = document.querySelector<HTMLElement>(`[data-turn-id="${turnId}"]`);
    if (!el) return;
    el.scrollIntoView({ block: "start" });
    el.setAttribute("data-turn-flash", "");
    const t = setTimeout(() => el.removeAttribute("data-turn-flash"), TURN_FLASH_MS);
    return () => {
      clearTimeout(t);
      el.removeAttribute("data-turn-flash"); // the next hit, inside the flash, must not leave this ring on
    };
  }, [request]);

  return useCallback((turnId: number | null) => {
    wanted.current = turnId;
    setRequest((n) => n + 1);
  }, []);
}
