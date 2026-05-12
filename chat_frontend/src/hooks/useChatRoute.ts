import { useCallback, useEffect, useState } from "react";

function readBasename(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="chat-basename"]');
  const raw = meta?.content ?? "/";
  // Ensure a single trailing slash.
  return raw.endsWith("/") ? raw : `${raw}/`;
}

function parseSessionIdFromPath(basename: string): string | null {
  const path = window.location.pathname;
  if (!path.startsWith(basename)) return null;
  const rest = path.slice(basename.length);
  // Expect "chat/<uuid>" possibly with a trailing slash or query.
  const match = rest.match(/^chat\/([^/?#]+)/);
  return match ? match[1] : null;
}

export interface UseChatRouteReturn {
  sessionIdFromUrl: string | null;
  push: (id: string | null) => void;
}

export function useChatRoute(): UseChatRouteReturn {
  const [basename] = useState(readBasename);
  const [sessionIdFromUrl, setSessionIdFromUrl] = useState<string | null>(() =>
    parseSessionIdFromPath(basename),
  );

  useEffect(() => {
    const onPop = () => setSessionIdFromUrl(parseSessionIdFromPath(basename));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [basename]);

  const push = useCallback(
    (id: string | null) => {
      const next = id ? `${basename}chat/${id}` : basename;
      if (window.location.pathname === next) return;
      window.history.pushState({}, "", next);
      setSessionIdFromUrl(id);
    },
    [basename],
  );

  return { sessionIdFromUrl, push };
}
