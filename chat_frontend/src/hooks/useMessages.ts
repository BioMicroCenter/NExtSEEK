import { useState, useCallback, useRef } from "react";
import type { Message } from "@/lib/types/chat";
import type { Turn } from "@/lib/types/api";

interface UseMessagesReturn {
  messages: Message[];
  addUserMessage: (content: string) => void;
  addAssistantMessage: (content: string) => void;
  addSystemMessage: (content: string) => void;
  updateLastAssistantMessage: (patch: Partial<Message>) => void;
  clearMessages: () => void;
  hydrateFromTurns: (turns: Turn[]) => void;
}

function createMessage(
  id: string,
  content: string,
  isUser: boolean,
  messageType: Message["messageType"] = "text",
): Message {
  return {
    id,
    content,
    isUser,
    timestamp: new Date(),
    status: "sent",
    messageType,
  };
}

export function useMessages(): UseMessagesReturn {
  const [messages, setMessages] = useState<Message[]>([]);
  const counterRef = useRef(0);

  const nextId = () => `msg-${Date.now()}-${++counterRef.current}`;

  const addUserMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, true, "text")]);
  }, []);

  const addAssistantMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, false, "text")]);
  }, []);

  const addSystemMessage = useCallback((content: string) => {
    setMessages((prev) => [...prev, createMessage(nextId(), content, false, "system")]);
  }, []);

  const updateLastAssistantMessage = useCallback((patch: Partial<Message>) => {
    setMessages((prev) => {
      for (let i = prev.length - 1; i >= 0; i--) {
        if (!prev[i].isUser && prev[i].messageType === "text") {
          const updated = [...prev];
          updated[i] = { ...updated[i], ...patch };
          return updated;
        }
      }
      return prev;
    });
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
  }, []);

  const hydrateFromTurns = useCallback((turns: Turn[]) => {
    setMessages(() => {
      const next: Message[] = [];
      for (const turn of turns) {
        const ts = turn.ts ? new Date(turn.ts) : new Date();
        next.push({
          id: `msg-hydrate-u-${turn.bundle_id}`,
          content: turn.user_query,
          isUser: true,
          timestamp: ts,
          status: "sent",
          messageType: "text",
        });
        next.push({
          id: `msg-hydrate-a-${turn.bundle_id}`,
          content: turn.reply,
          isUser: false,
          timestamp: ts,
          status: "sent",
          messageType: "text",
          bundleId: turn.bundle_id,
          debugEntries: [],
        });
      }
      return next;
    });
  }, []);

  return { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, clearMessages, hydrateFromTurns };
}
