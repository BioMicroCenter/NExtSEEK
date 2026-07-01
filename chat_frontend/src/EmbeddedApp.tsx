import { useCallback, useEffect, useRef, useState } from "react";
import { useMessages, useProcessingState } from "@/hooks";
import { useChatRoute } from "@/hooks/useChatRoute";
import { useSessions } from "@/hooks/useSessions";
import { NextseekApiService } from "@/lib/services/chatApi";
import { SessionAuthService } from "@/lib/services/sessionAuth";
import { ChatPanel } from "@/components/ChatPanel";
import { CompactToolbar, RightSidebar } from "@/components/Layout";
import { SessionSidebar } from "@/components/Sessions";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  SearchStartedData,
  SearchCompleteData,
  QueryCompleteData,
  QueryErrorData,
} from "@/lib/types/api";
import type { DebugData, DebugEntry } from "@/lib/types/chat";

export function EmbeddedApp() {
  const [rightOpen, setRightOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("chat.sidebar.collapsed") === "1";
  });
  const [debugData, setDebugData] = useState<DebugData>({ entries: [], bundleId: null, query: "" });

  const sessionAuthRef = useRef(new SessionAuthService());
  const serviceRef = useRef(new NextseekApiService(sessionAuthRef.current));
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [isAdmin, setIsAdmin] = useState(false);

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, hydrateFromTurns } = useMessages();
  const pendingDebugRef = useRef<DebugEntry[]>([]);
  const {
    processingState,
    handleAgentStarted,
    handleAgentComplete,
    handleSearchStarted,
    handleSearchComplete,
    resetProcessing,
  } = useProcessingState();

  // sessionsRef breaks the chicken-and-egg between chatRoute (whose popstate
  // callback needs setActive) and sessions (returned after chatRoute). The
  // callback reads sessionsRef.current at dispatch time, so it always sees
  // the latest sessions object instead of forming a circular dependency.
  const sessionsRef = useRef<ReturnType<typeof useSessions> | null>(null);

  const chatRoute = useChatRoute({
    onSessionIdChange: (id) => {
      const s = sessionsRef.current;
      if (!s) return;
      if (id === s.activeSessionId) return;
      s.setActive(id).catch(() => {
        addSystemMessage("Couldn't load this conversation.");
        // Don't push(null) here — popstate is already a user-initiated nav.
        // The URL is whatever the user navigated to; just surface the error.
      });
    },
  });
  const sessions = useSessions({
    service: serviceRef.current,
    hydrate: hydrateFromTurns,
    onRouteChange: chatRoute.push,
  });
  sessionsRef.current = sessions;

  useEffect(() => {
    if (chatRoute.sessionIdFromUrl && sessions.activeSessionId !== chatRoute.sessionIdFromUrl) {
      sessions.setActive(chatRoute.sessionIdFromUrl).catch(() => {
        addSystemMessage("Couldn't load this conversation.");
        chatRoute.push(null);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    fetch("/nextseek_api/assistant/me/", { credentials: "include", headers: sessionAuthRef.current.getAuthHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then((me) => { if (me && typeof me.is_admin === "boolean") setIsAdmin(me.is_admin); })
      .catch(() => { /* non-admin fallback is fine */ });
  }, []);

  const handleProgress = useCallback(
    (event: ProgressEvent) => {
      switch (event.event) {
        case "agent_started": {
          const d = event.data as AgentStartedData;
          handleAgentStarted(d.agent, d.mode);
          break;
        }
        case "agent_complete": {
          const d = event.data as AgentCompleteData;
          handleAgentComplete(d.agent);
          const entry: DebugEntry = {
            agent: d.agent,
            summary: typeof d.summary === "string" ? d.summary : JSON.stringify(d.summary ?? "", null, 2),
            timestamp: new Date(),
          };
          pendingDebugRef.current.push(entry);
          setDebugData((prev) => ({ ...prev, entries: [...prev.entries, entry] }));
          break;
        }
        case "search_started": {
          handleSearchStarted(event.data as SearchStartedData);
          break;
        }
        case "search_complete": {
          handleSearchComplete(event.data as SearchCompleteData);
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          const captured = pendingDebugRef.current.slice();
          const bid = d.bundle_id ?? null;
          const artifacts = d.artifacts ?? null;
          const ccTraces = d.cc_traces ?? undefined;
          const mode = d.mode ?? undefined;
          queueMicrotask(() => {
            updateLastAssistantMessage({
              debugEntries: captured,
              bundleId: bid,
              artifacts,
              ccTraces,
              mode,
            });
          });
          resetProcessing();
          setDebugData((prev) => ({ ...prev, bundleId: d.bundle_id }));
          const authSid = serviceRef.current.sessionId ?? d.session_id;
          if (authSid) {
            if (sessions.pendingNewChat) sessions.promoteCreatedSession(authSid);
            else sessions.refresh();
          }
          break;
        }
        case "query_error": {
          const d = event.data as QueryErrorData;
          addSystemMessage(`Error: ${d.error}`);
          resetProcessing();
          break;
        }
      }
    },
    [handleAgentStarted, handleAgentComplete, handleSearchStarted, handleSearchComplete, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, resetProcessing, sessions],
  );

  const handleQueryError = useCallback(
    (error: string) => { addSystemMessage(`Error: ${error}`); resetProcessing(); },
    [addSystemMessage, resetProcessing],
  );

  const handleSendMessage = useCallback(
    (text: string, mode: string | { pipeline: "standard" | "plan"; useProd?: boolean }) => {
      addUserMessage(text);
      pendingDebugRef.current = [];
      setDebugData({ entries: [], bundleId: null, query: text });
      setIsQuerying(true);
      const opts =
        sessions.activeSessionId ? { sessionId: sessions.activeSessionId } :
        sessions.pendingNewChat   ? { forceNew: true } :
        {};
      serviceRef.current
        .submitQuery(text, mode, opts, handleProgress, handleQueryError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [addUserMessage, handleProgress, handleQueryError, sessions.activeSessionId, sessions.pendingNewChat],
  );

  const handleArtifactDownload = useCallback(
    (bundleId: number, artifactKey: string) => {
      const sid = serviceRef.current.sessionId ?? sessions.activeSessionId;
      if (sid) {
        serviceRef.current
          .downloadArtifact(sid, bundleId, artifactKey)
          .catch((err: Error) => addSystemMessage(`Download failed: ${err.message}`));
      }
    },
    [addSystemMessage, sessions.activeSessionId],
  );

  const handleCcArtifactDownload = useCallback(
    (artifactKey: string) => {
      const sid = serviceRef.current.sessionId ?? sessions.activeSessionId;
      if (sid) {
        void serviceRef.current.downloadCcArtifact(sid, artifactKey);
      }
    },
    [sessions.activeSessionId],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) serviceRef.current.downloadBundle(sessionId, debugData.bundleId, format);
    },
    [sessionId, debugData.bundleId],
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("chat.sidebar.collapsed", next ? "1" : "0");
      return next;
    });
  }, []);

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <CompactToolbar
        onRightToggle={() => setRightOpen(!rightOpen)}
        onLeftToggle={toggleSidebar}
      />
      <div className="flex flex-1 overflow-hidden">
        <SessionSidebar
          sessions={sessions.sessions}
          activeSessionId={sessions.activeSessionId}
          collapsed={sidebarCollapsed}
          inFlight={isQuerying || sessions.isHydrating}
          onNewChat={sessions.newChat}
          onSelect={(id) => sessions.setActive(id).catch(() => addSystemMessage("Couldn't load this conversation."))}
          onRename={(id, t) => sessions.rename(id, t).catch(() => addSystemMessage("Rename failed."))}
          onDelete={(id) => sessions.remove(id).catch(() => addSystemMessage("Delete failed."))}
        />
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isQuerying}
          onSendMessage={handleSendMessage}
          onArtifactDownload={handleArtifactDownload}
          onCcArtifactDownload={handleCcArtifactDownload}
          apiService={serviceRef.current}
          isAdmin={isAdmin}
        />
      </div>
      <RightSidebar
        isOpen={rightOpen}
        onOpenChange={setRightOpen}
        debugData={debugData}
        onDownload={handleDownload}
      />
    </div>
  );
}
