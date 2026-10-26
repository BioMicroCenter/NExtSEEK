import type { ReactNode } from "react";

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` with every search term marked where a word starts, the way the server matched it
 *  (nextseek_api/assistant/session_search.py). Plain text in, React nodes out: no HTML is parsed. */
export function highlightTerms(text: string, terms: string[]): ReactNode[] {
  const words = terms.filter(Boolean);
  if (!words.length) return [text];
  const re = new RegExp(`(?<![a-z0-9])(?:${words.map(escape).join("|")})`, "gi");
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    out.push(<mark key={m.index} className="rounded-sm bg-yellow-200/70 px-0.5 text-inherit dark:bg-yellow-500/30">{m[0]}</mark>);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
