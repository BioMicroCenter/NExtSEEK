import { useEffect, useState } from "react";

/** The phone cut: Bootstrap's md, so the rail shows from 768px up. */
export const PHONE_QUERY = "(max-width: 767.98px)";

/** True while the query matches. False when the browser (or a test) has no matchMedia. */
export function matches(query: string): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function"
    ? Boolean(window.matchMedia(query)?.matches)
    : false;
}

export function useMediaQuery(query: string): boolean {
  const [value, setValue] = useState(() => matches(query));
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mq = window.matchMedia(query);
    const onChange = () => setValue(Boolean(mq?.matches));
    mq?.addEventListener?.("change", onChange);
    return () => mq?.removeEventListener?.("change", onChange);
  }, [query]);
  return value;
}

/** Auto-focus is only for fine pointers: on a phone it opens the keyboard over the answer. */
export const isFinePointer = () => matches("(pointer: fine)");
