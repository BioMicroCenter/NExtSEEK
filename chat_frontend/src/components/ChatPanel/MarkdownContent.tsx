import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkUidLinks from "@/lib/remark-uid-links";
import rehypeHighlight from "rehype-highlight";

interface MarkdownContentProps {
  content: string;
}

export function MarkdownContent({ content }: MarkdownContentProps) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm, remarkUidLinks]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
        ul: ({ children }) => <ul className="mb-2 ml-4 list-disc last:mb-0">{children}</ul>,
        ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal last:mb-0">{children}</ol>,
        li: ({ children }) => <li className="mb-0.5">{children}</li>,
        code: ({ className, children, ...props }) => {
          const isInline = !className;
          return isInline ? (
            <code className="rounded bg-muted px-1.5 py-0.5 text-[0.9em] font-mono" {...props}>
              {children}
            </code>
          ) : (
            <code className={`${className ?? ""} text-sm`} {...props}>
              {children}
            </code>
          );
        },
        pre: ({ children }) => (
          <pre className="mb-2 overflow-x-auto rounded-lg bg-muted p-3 text-sm last:mb-0">
            {children}
          </pre>
        ),
        table: ({ children }) => (
          <div className="mb-2 overflow-x-auto last:mb-0">
            <table className="min-w-full border-collapse text-sm">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-border bg-muted px-3 py-1.5 text-left font-semibold">{children}</th>
        ),
        td: ({ children }) => (
          <td className="border border-border px-3 py-1.5">{children}</td>
        ),
        a: ({ href, children }) => (
          <a href={href} className="text-primary underline hover:no-underline" target="_blank" rel="noopener noreferrer">
            {children}
          </a>
        ),
        blockquote: ({ children }) => (
          <blockquote className="mb-2 border-l-4 border-primary/30 pl-3 italic last:mb-0">
            {children}
          </blockquote>
        ),
        h1: ({ children }) => <h1 className="mb-2 text-xl font-bold">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-2 text-lg font-bold">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1.5 text-base font-semibold">{children}</h3>,
        hr: () => <hr className="my-3 border-border" />,
        input: ({ checked, ...props }) => (
          <input type="checkbox" checked={checked} disabled className="mr-1.5" {...props} />
        ),
        strong: ({ children }) => <strong className="font-bold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
