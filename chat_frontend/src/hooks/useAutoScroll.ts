import { useRef, useCallback, useEffect } from "react";

const SCROLL_THRESHOLD = 120;

interface UseAutoScrollReturn {
  scrollRef: React.RefObject<HTMLDivElement | null>;
  scrollToBottom: () => void;
}

export function useAutoScroll(deps: unknown[] = []): UseAutoScrollReturn {
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const shouldAutoScroll = useRef(true);

  const scrollToBottom = useCallback(() => {
    const el = scrollRef.current;
    if (el) {
      el.scrollTop = el.scrollHeight;
    }
  }, []);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = el;
      const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
      shouldAutoScroll.current = distanceFromBottom < SCROLL_THRESHOLD;
    };

    el.addEventListener("scroll", handleScroll);
    return () => el.removeEventListener("scroll", handleScroll);
  }, []);

  useEffect(() => {
    if (shouldAutoScroll.current) {
      scrollToBottom();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return { scrollRef, scrollToBottom };
}
