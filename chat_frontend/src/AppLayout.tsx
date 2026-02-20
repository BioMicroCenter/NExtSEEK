import { useState, useCallback, useEffect } from "react";
import { useMessages, useProcessingState, useChatApi } from "@/hooks";
import { ChatPanel } from "@/components/ChatPanel";
import { HeaderBar, LeftSidebar } from "@/components/Layout";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
  TestCase,
} from "@/lib/types/api";

interface AppLayoutProps {
  credentialError: string | null;
}

export function AppLayout({ credentialError }: AppLayoutProps) {
  const [leftOpen, setLeftOpen] = useState(false);
  const [testCases, setTestCases] = useState<TestCase[]>([]);

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage } =
    useMessages();
  const {
    processingState,
    handleAgentStarted,
    handleAgentComplete,
    resetProcessing,
  } = useProcessingState();

  const {
    isQuerying,
    submitQuery,
    fetchTestCases,
  } = useChatApi();

  // Show credential error as system message
  useEffect(() => {
    if (credentialError) {
      addSystemMessage(credentialError);
    }
  }, [credentialError, addSystemMessage]);

  // Fetch test cases on mount
  useEffect(() => {
    if (!credentialError) {
      fetchTestCases().then(setTestCases).catch(() => {});
    }
  }, [credentialError, fetchTestCases]);

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
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          resetProcessing();
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
    [handleAgentStarted, handleAgentComplete, addAssistantMessage, addSystemMessage, resetProcessing],
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
      submitQuery(text, handleProgress, handleQueryError);
    },
    [addUserMessage, submitQuery, handleProgress, handleQueryError],
  );

  const isDisabled = !!credentialError || isQuerying;

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <HeaderBar
        onLeftToggle={() => setLeftOpen(!leftOpen)}
      />
      <div className="flex flex-1 overflow-hidden">
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isDisabled}
          onSendMessage={handleSendMessage}
        />
      </div>
      <LeftSidebar
        isOpen={leftOpen}
        onOpenChange={setLeftOpen}
        testCases={testCases}
        onRunTest={(tc) => handleSendMessage(tc.prompt)}
        onRunAllTests={() => {
          testCases.forEach((tc, i) => {
            setTimeout(() => handleSendMessage(tc.prompt), i * 500);
          });
        }}
      />
    </div>
  );
}
