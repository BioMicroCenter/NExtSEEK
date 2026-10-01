import { useEffect, useRef, useState } from "react";
import { SendHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAutoResize } from "@/hooks/useAutoResize";
import { isFinePointer } from "@/hooks/useMediaQuery";
import type { NextseekApiService } from "@/lib/services/chatApi";
import { UploadControl } from "./UploadControl";

export interface SendOptions {
  pipeline: "standard" | "plan";
}

interface MessageInputProps {
  onSend: (message: string, opts: SendOptions) => void;
  disabled?: boolean;
  apiService?: NextseekApiService;
}

function readInitialQuery(): string {
  if (typeof window === "undefined") return "";
  try {
    const params = new URLSearchParams(window.location.search);
    return params.get("q") ?? "";
  } catch {
    return "";
  }
}

export function MessageInput({ onSend, disabled, apiService }: MessageInputProps) {
  const [value, setValue] = useState<string>(() => readInitialQuery());
  // Pipeline mode is fixed to "standard" — the Standard/Planner selector was
  // removed from the UI, but the field is still sent so the backend contract
  // (SendOptions.pipeline) is unchanged.
  const [pipeline] = useState<"standard" | "plan">("standard");
  const { textareaRef, handleInput, resetHeight } = useAutoResize();

  // Focus follows the turn (send, chip click, answer) on fine pointers only.
  const mounted = useRef(false);
  useEffect(() => {
    if (!mounted.current) { mounted.current = true; return; }
    if (isFinePointer()) textareaRef.current?.focus();
  }, [disabled, textareaRef]);

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSend(trimmed, { pipeline });
    setValue("");
    resetHeight();
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div data-testid="message-input" className="border-t bg-background px-4 pt-3" style={{ paddingBottom: "max(0.75rem, env(safe-area-inset-bottom))" }}>
      <div className="flex items-end gap-2">
        {apiService && <UploadControl apiService={apiService} disabled={disabled} />}
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          className="flex-1 resize-none rounded-lg border bg-transparent px-3 py-2 text-base placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring read-only:cursor-not-allowed read-only:opacity-50 [@media(pointer:coarse)]:min-h-11"
          placeholder="Ask NExtSEEK a question..."
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          readOnly={disabled}
          aria-disabled={disabled || undefined}
          rows={1}
        />
        <Button
          data-testid="send-button"
          className="h-10 w-10 shrink-0 rounded-lg p-0 [@media(pointer:coarse)]:h-11 [@media(pointer:coarse)]:w-11"
          onClick={handleSend}
          disabled={disabled || !value.trim()}
          aria-label="Send message"
        >
          {/* Wrap in span to escape Button's [&_svg]:size-4 which forces rem-based sizing */}
          <span className="flex items-center justify-center" style={{ width: 20, height: 20 }}>
            <SendHorizontal style={{ width: 20, height: 20 }} />
          </span>
        </Button>
      </div>
    </div>
  );
}
