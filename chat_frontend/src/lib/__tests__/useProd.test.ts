import { describe, it, expect, beforeEach } from "vitest";
import { getUseProd, setUseProd } from "@/lib/useProd";

describe("useProd lib", () => {
  beforeEach(() => localStorage.clear());

  it("defaults to false when unset", () => {
    expect(getUseProd()).toBe(false);
  });

  it("round-trips true", () => {
    setUseProd(true);
    expect(getUseProd()).toBe(true);
  });

  it("round-trips back to false", () => {
    setUseProd(true);
    setUseProd(false);
    expect(getUseProd()).toBe(false);
  });
});
