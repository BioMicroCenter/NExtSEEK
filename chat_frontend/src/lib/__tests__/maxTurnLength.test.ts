import { describe, it, expect, beforeEach } from "vitest";
import { getMaxTurnLength, setMaxTurnLength } from "@/lib/maxTurnLength";

describe("maxTurnLength lib", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to null when unset", () => {
    expect(getMaxTurnLength()).toBeNull();
  });

  it("round-trips a positive value", () => {
    setMaxTurnLength(300);
    expect(getMaxTurnLength()).toBe(300);
  });

  it("clears on null", () => {
    setMaxTurnLength(300);
    setMaxTurnLength(null);
    expect(getMaxTurnLength()).toBeNull();
  });

  it("clears on non-positive", () => {
    setMaxTurnLength(300);
    setMaxTurnLength(0);
    expect(getMaxTurnLength()).toBeNull();
  });

  it("ignores garbage in storage", () => {
    localStorage.setItem("nextseek.maxTurnLength", "abc");
    expect(getMaxTurnLength()).toBeNull();
  });
});
