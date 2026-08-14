import { useState } from "react";
import { getMaxTurnLength, setMaxTurnLength } from "@/lib/maxTurnLength";

interface MaxTurnLengthInputProps {
  isAdmin?: boolean;
}

/**
 * Admin-only sticky control: per-turn wall-clock cap (seconds) for a
 * Container-CC turn. Persists to localStorage; the shell reads it at send time
 * and posts `max_turn_length_s`. The server admin-gates it and clamps to
 * [30, NEXTSEEK_CC_TIMEOUT_HARD_MAX] — so a value above the deployment's hard
 * ceiling is silently capped. Empty = server default. Renders nothing for
 * non-admins.
 */
export function MaxTurnLengthInput({ isAdmin = false }: MaxTurnLengthInputProps) {
  const [value, setValue] = useState<string>(() => {
    const v = getMaxTurnLength();
    return v ? String(v) : "";
  });
  if (!isAdmin) return null;

  const active = value.trim() !== "" && Number(value) > 0;
  return (
    <div className="flex items-center gap-2 px-4 py-2 text-sm">
      <label htmlFor="max-turn-length" className="text-muted-foreground">
        Max turn (s)
      </label>
      <input
        id="max-turn-length"
        type="number"
        min={30}
        step={30}
        value={value}
        placeholder="default"
        onChange={(e) => {
          const next = e.target.value;
          setValue(next);
          const n = parseInt(next, 10);
          setMaxTurnLength(Number.isFinite(n) && n > 0 ? n : null);
        }}
        aria-label="Max Container-CC turn length in seconds"
        className={`w-24 rounded border bg-background px-2 py-1 text-sm ${
          active
            ? "border-amber-500 text-amber-600 dark:text-amber-400"
            : "border-input"
        }`}
      />
    </div>
  );
}
