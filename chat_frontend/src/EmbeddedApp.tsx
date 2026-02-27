import { useState, useCallback, useRef } from "react";
import { useMessages, useProcessingState } from "@/hooks";
import { NextseekApiService } from "@/lib/services/chatApi";
import { SessionAuthService } from "@/lib/services/sessionAuth";
import { ChatPanel } from "@/components/ChatPanel";
import { CompactToolbar, RightSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
} from "@/lib/types/api";
import type { DebugData, DebugEntry } from "@/lib/types/chat";

export function EmbeddedApp() {
  const [rightOpen, setRightOpen] = useState(false);
  const [debugData, setDebugData] = useState<DebugData>({
    entries: [],
    bundleId: null,
    query: "",
  });

  const serviceRef = useRef(
    new NextseekApiService(new SessionAuthService()),
  );
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage } =
    useMessages();
  const pendingDebugRef = useRef<DebugEntry[]>([]);
  const {
    processingState,
    handleAgentStarted,
    handleAgentComplete,
    resetProcessing,
  } = useProcessingState();

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
          const entry = {
            agent: d.agent,
            summary:
              typeof d.summary === "string"
                ? d.summary
                : JSON.stringify(d.summary ?? "", null, 2),
            timestamp: new Date(),
          };
          pendingDebugRef.current.push(entry);
          setDebugData((prev) => ({
            ...prev,
            entries: [...prev.entries, entry],
          }));
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          // Attach accumulated debug entries + bundleId + artifacts to the assistant message
          const captured = pendingDebugRef.current.slice();
          const bid = d.bundle_id ?? null;
          const artifacts = d.artifacts ?? null;
          // Use queueMicrotask to ensure the message is in state before patching
          queueMicrotask(() => {
            updateLastAssistantMessage({ debugEntries: captured, bundleId: bid, artifacts });
          });
          resetProcessing();
          setDebugData((prev) => ({
            ...prev,
            bundleId: d.bundle_id,
          }));
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
    [handleAgentStarted, handleAgentComplete, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, resetProcessing],
  );

  const handleQueryError = useCallback(
    (error: string) => {
      addSystemMessage(`Error: ${error}`);
      resetProcessing();
    },
    [addSystemMessage, resetProcessing],
  );

  const handleSendMessage = useCallback(
    (text: string) => {
      addUserMessage(text);
      pendingDebugRef.current = [];
      setDebugData({ entries: [], bundleId: null, query: text });
      setIsQuerying(true);

      serviceRef.current
        .submitQuery(text, handleProgress, handleQueryError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [addUserMessage, handleProgress, handleQueryError],
  );

  const handleInlineDownload = useCallback(
    (bundleId: number) => {
      const sid = serviceRef.current.sessionId;
      if (!sid) return;

      // Check if message has artifacts (report mode) vs no artifacts (search mode)
      const msg = messages.find((m) => m.bundleId === bundleId);
      if (msg?.artifacts && msg.artifacts.length > 0) {
        // Report mode: download combined xlsx from backend
        serviceRef.current.downloadArtifact(sid, bundleId, "all_tables");
      } else {
        // Search mode: try client-side Excel, fall back to JSON
        serviceRef.current
          .fetchBundle(sid, bundleId)
          .then((bundle) => {
            const apiResult = bundle?.api_result_full as Record<string, unknown> | undefined;
            const data = apiResult?.data;
            if (Array.isArray(data) && data.length > 0) {
              const rows = data.map((item: Record<string, unknown>) => {
                const attrs = (item.attributes ?? {}) as Record<string, unknown>;
                return { id: item.id, type: item.type, ...attrs };
              });
              serviceRef.current.downloadSearchAsExcel(rows, `search_results_${bundleId}.xlsx`);
            } else {
              serviceRef.current.downloadBundle(sid, bundleId, "json");
            }
          })
          .catch(() => {
            serviceRef.current.downloadBundle(sid, bundleId, "json");
          });
      }
    },
    [messages],
  );

  const handleArtifactDownload = useCallback(
    (bundleId: number, artifactKey: string) => {
      const sid = serviceRef.current.sessionId;
      if (sid) {
        serviceRef.current.downloadArtifact(sid, bundleId, artifactKey);
      }
    },
    [],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) {
        serviceRef.current.downloadBundle(sessionId, debugData.bundleId, format);
      }
    },
    [sessionId, debugData.bundleId],
  );

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <CompactToolbar
        onRightToggle={() => setRightOpen(!rightOpen)}
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isQuerying}
          onSendMessage={handleSendMessage}
          onDownload={handleInlineDownload}
          onArtifactDownload={handleArtifactDownload}
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
