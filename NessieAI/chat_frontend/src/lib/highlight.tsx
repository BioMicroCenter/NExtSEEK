import type { ReactNode } from "react";

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** `text` with every search term marked where a word starts, the way the server matched it
 *  (nextseek_api/assistant/session_search.py): after the start or anything but a letter or digit, in any script.
 *  Plain text in, React nodes out: no HTML is parsed. No lookbehind: Safari before 16.4 throws on one. */
export function highlightTerms(text: string, terms: string[]): ReactNode[] {
  const words = terms.filter(Boolean).sort((a, b) => b.length - a.length);
  if (!words.length) return [text];
  const re = new RegExp(`(^|[^\\p{L}\\p{N}])(${words.map(escape).join("|")})`, "giu");
  const out: ReactNode[] = [];
  let last = 0;
  for (const m of text.matchAll(re)) {
    const start = m.index + m[1].length;
    if (start > last) out.push(text.slice(last, start));
    out.push(<mark key={start} className="rounded-sm bg-yellow-200/70 px-0.5 text-inherit dark:bg-yellow-500/30">{m[2]}</mark>);
    last = start + m[2].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
