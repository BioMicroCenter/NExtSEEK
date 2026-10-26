import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, act } from "@testing-library/react";
import { useScrollToTurn, TURN_FLASH_MS } from "../useScrollToTurn";

let scrollTo: (turnId: number | null) => void;

function Chat({ turns }: { turns: number[] }) {
  scrollTo = useScrollToTurn();
  return <div>{turns.map((t) => <div key={t} data-turn-id={t}>turn {t}</div>)}</div>;
}

describe("useScrollToTurn", () => {
  const spy = vi.fn();
  beforeEach(() => {
    vi.useFakeTimers();
    spy.mockReset();
    Element.prototype.scrollIntoView = spy;
  });
  afterEach(() => vi.useRealTimers());

  it("scrolls the turn into view and flashes it", () => {
    const { container } = render(<Chat turns={[1, 2, 3]} />);
    act(() => scrollTo(2));
    const el = container.querySelector('[data-turn-id="2"]')!;
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.contexts[0]).toBe(el);
    expect(el.getAttribute("data-turn-flash")).toBe("");
    act(() => { vi.advanceTimersByTime(TURN_FLASH_MS); });
    expect(el.hasAttribute("data-turn-flash")).toBe(false);
  });

  it("does nothing for a turn that is not on screen, and does not scroll later", () => {
    const { rerender } = render(<Chat turns={[1]} />);
    act(() => scrollTo(5));
    rerender(<Chat turns={[1, 5]} />);
    expect(spy).not.toHaveBeenCalled();
  });

  it("null leaves the chat where it opened", () => {
    render(<Chat turns={[1]} />);
    act(() => scrollTo(null));
    expect(spy).not.toHaveBeenCalled();
  });
});
