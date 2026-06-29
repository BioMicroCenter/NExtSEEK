import { useState } from "react";
import { SendHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAutoResize } from "@/hooks/useAutoResize";

export interface SendOptions {
  pipeline: "standard" | "plan";
  useProd: boolean;
}

interface MessageInputProps {
  onSend: (message: string, opts: SendOptions) => void;
  disabled?: boolean;
  isAdmin?: boolean;
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

export function MessageInput({ onSend, disabled, isAdmin = false }: MessageInputProps) {
  const [value, setValue] = useState<string>(() => readInitialQuery());
  // Pipeline mode is fixed to "standard" — the Standard/Planner selector was
  // removed from the UI, but the field is still sent so the backend contract
  // (SendOptions.pipeline) is unchanged.
  const [pipeline] = useState<"standard" | "plan">("standard");
  const [useProd, setUseProd] = useState<boolean>(false);
  const { textareaRef, handleInput, resetHeight } = useAutoResize();

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed, { pipeline, useProd: isAdmin && useProd });
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
    <div data-testid="message-input" className="border-t bg-background px-4 py-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
          data-testid="chat-input"
          className="flex-1 resize-none rounded-lg border bg-transparent px-3 py-2 text-base placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          placeholder="Ask NExtSEEK a question..."
          value={value}
          onChange={(e) => {
            setValue(e.target.value);
            handleInput();
          }}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          rows={1}
        />
        <div className="flex flex-col items-end gap-1">
          {isAdmin && (
            <label className="flex items-center gap-1 text-xs cursor-pointer select-none">
              <input
                type="checkbox"
                checked={useProd}
                onChange={(e) => setUseProd(e.target.checked)}
                disabled={disabled}
                aria-label="Use prod database"
              />
              <span>PROD</span>
            </label>
          )}
        </div>
        <Button
          data-testid="send-button"
          className="shrink-0 rounded-lg p-0"
          style={{ width: 40, height: 40 }}
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
