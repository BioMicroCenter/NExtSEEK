import { useState } from "react";
import { SendHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAutoResize } from "@/hooks/useAutoResize";

interface MessageInputProps {
  onSend: (message: string, mode: string) => void;
  disabled?: boolean;
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

export function MessageInput({ onSend, disabled }: MessageInputProps) {
  const [value, setValue] = useState<string>(() => readInitialQuery());
  const [mode, setMode] = useState("standard");
  const { textareaRef, handleInput, resetHeight } = useAutoResize();

  const handleModeChange = (event: React.ChangeEvent<HTMLSelectElement>) => {
    setMode(event.target.value)
  }

  const handleSend = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    onSend(trimmed, mode);
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
    <div className="border-t bg-background px-4 py-3">
      <div className="flex items-end gap-2">
        <textarea
          ref={textareaRef}
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
        <label>
          Mode:
          <select
            id="mode"
            name="mode"
            defaultValue={mode}
            onChange={handleModeChange}>
            <option value="standard">Standard</option>
            <option value="plan">Plan</option>
          </select>
        </label>
        <Button
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
