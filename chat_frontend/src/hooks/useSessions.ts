import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionListItem, Turn } from "@/lib/types/api";

export interface SessionServiceLike {
  listSessions: () => Promise<{ total: number; sessions: SessionListItem[] }>;
  renameSession: (id: string, title: string) => Promise<SessionListItem>;
  deleteSession: (id: string) => Promise<void>;
  fetchSessionTurns: (id: string) => Promise<{ turns?: Turn[] | null }>;
}

export interface UseSessionsArgs {
  service: SessionServiceLike;
  hydrate: (turns: Turn[]) => void;
  onRouteChange: (id: string | null) => void;
}

export interface UseSessionsReturn {
  sessions: SessionListItem[];
  activeSessionId: string | null;
  pendingNewChat: boolean;
  isLoading: boolean;
  isHydrating: boolean;
  refresh: () => Promise<void>;
  newChat: () => void;
  setActive: (id: string | null) => Promise<void>;
  setActiveImmediately: (id: string | null) => void;
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  promoteCreatedSession: (id: string) => void;
}

export function useSessions({ service, hydrate, onRouteChange }: UseSessionsArgs): UseSessionsReturn {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [pendingNewChat, setPendingNewChat] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isHydrating, setIsHydrating] = useState(false);
  const onRouteChangeRef = useRef(onRouteChange);
  onRouteChangeRef.current = onRouteChange;

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await service.listSessions();
      setSessions(res.sessions);
    } finally {
      setIsLoading(false);
    }
  }, [service]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [refresh]);

  const newChat = useCallback(() => {
    setActiveSessionId(null);
    setPendingNewChat(true);
    hydrate([]);
    onRouteChangeRef.current(null);
  }, [hydrate]);

  const setActiveImmediately = useCallback((id: string | null) => {
    setActiveSessionId(id);
    setPendingNewChat(id === null);
    onRouteChangeRef.current(id);
  }, []);

  const setActive = useCallback(async (id: string | null) => {
    if (id === null) {
      newChat();
      return;
    }
    setIsHydrating(true);
    try {
      const detail = await service.fetchSessionTurns(id);
      hydrate(detail.turns ?? []);
      setActiveSessionId(id);
      setPendingNewChat(false);
      onRouteChangeRef.current(id);
    } finally {
      setIsHydrating(false);
    }
  }, [service, hydrate, newChat]);

  const rename = useCallback(async (id: string, title: string) => {
    await service.renameSession(id, title);
    await refresh();
  }, [service, refresh]);

  const remove = useCallback(async (id: string) => {
    await service.deleteSession(id);
    if (activeSessionId === id) {
      setActiveSessionId(null);
      setPendingNewChat(true);
      hydrate([]);
      onRouteChangeRef.current(null);
    }
    await refresh();
  }, [service, refresh, activeSessionId, hydrate]);

  const promoteCreatedSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setPendingNewChat(false);
    onRouteChangeRef.current(id);
    refresh();
  }, [refresh]);

  return {
    sessions,
    activeSessionId,
    pendingNewChat,
    isLoading,
    isHydrating,
    refresh,
    newChat,
    setActive,
    setActiveImmediately,
    rename,
    remove,
    promoteCreatedSession,
  };
}
