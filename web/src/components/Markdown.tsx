import { Fragment, type ReactNode } from "react";

/** Minimal markdown: paragraphs, `- ` lists, **bold** and *italic*. Enough for chat replies. */
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  const blocks: ReactNode[] = [];
  let list: string[] = [];
  const flush = () => {
    if (list.length) {
      blocks.push(
        <ul key={blocks.length} className="my-2 space-y-1.5 pl-1">
          {list.map((li, i) => (
            <li key={i} className="flex gap-2">
              <span className="mt-[0.55em] size-1.5 shrink-0 rotate-45 bg-gold/70" />
              <span>{inline(li)}</span>
            </li>
          ))}
        </ul>,
      );
      list = [];
    }
  };
  for (const line of text.split("\n")) {
    if (line.startsWith("- ")) list.push(line.slice(2));
    else {
      flush();
      if (line.trim()) blocks.push(<p key={blocks.length} className="my-1.5">{inline(line)}</p>);
    }
  }
  flush();
  return <div className={className}>{blocks}</div>;
}

function inline(s: string): ReactNode {
  return s.split(/(\*\*[^*]+\*\*|\*[^*]+\*)/g).map((part, i) =>
    part.startsWith("**") ? (
      <strong key={i} className="font-semibold text-ink">{part.slice(2, -2)}</strong>
    ) : part.startsWith("*") && part.length > 2 ? (
      <em key={i}>{part.slice(1, -1)}</em>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}
