import { Fragment, type ReactNode } from "react";

interface SafeMarkdownProps {
  readonly content: string;
}

interface MarkdownBlock {
  readonly kind: "code" | "heading" | "list" | "paragraph" | "quote";
  readonly lines: readonly string[];
  readonly level?: number;
  readonly ordered?: boolean;
}

const inlinePattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*|\[[^\]\n]+\]\([^\s)]+\))/g;

function safeLinkTarget(value: string): string | null {
  try {
    const parsed = new URL(value, "https://safe.invalid");
    if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
      return null;
    }
    return value;
  } catch {
    return null;
  }
}

function inlineNodes(text: string, keyPrefix: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let cursor = 0;
  for (const match of text.matchAll(inlinePattern)) {
    const start = match.index;
    if (start > cursor) {
      nodes.push(text.slice(cursor, start));
    }
    const token = match[0];
    const key = `${keyPrefix}-${String(start)}`;
    if (token.startsWith("`")) {
      nodes.push(<code key={key}>{token.slice(1, -1)}</code>);
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={key}>{token.slice(2, -2)}</strong>);
    } else if (token.startsWith("*")) {
      nodes.push(<em key={key}>{token.slice(1, -1)}</em>);
    } else {
      const separator = token.lastIndexOf("](");
      const label = token.slice(1, separator);
      const rawTarget = token.slice(separator + 2, -1);
      const href = safeLinkTarget(rawTarget);
      nodes.push(
        href === null ? (
          <span className="markdown-invalid-link" key={key}>
            {label}
          </span>
        ) : (
          <a href={href} key={key} rel="noreferrer noopener" target="_blank">
            {label}
          </a>
        ),
      );
    }
    cursor = start + token.length;
  }
  if (cursor < text.length) {
    nodes.push(text.slice(cursor));
  }
  return nodes;
}

function parseBlocks(content: string): MarkdownBlock[] {
  const lines = content.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];

  function flushParagraph(): void {
    if (paragraph.length > 0) {
      blocks.push({ kind: "paragraph", lines: paragraph });
      paragraph = [];
    }
  }

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index] ?? "";
    if (line.startsWith("```")) {
      flushParagraph();
      const codeLines: string[] = [];
      index += 1;
      while (index < lines.length && !(lines[index] ?? "").startsWith("```")) {
        codeLines.push(lines[index] ?? "");
        index += 1;
      }
      blocks.push({ kind: "code", lines: codeLines });
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading !== null) {
      flushParagraph();
      blocks.push({ kind: "heading", level: heading[1]?.length ?? 1, lines: [heading[2] ?? ""] });
      continue;
    }
    const unordered = /^[-*]\s+(.+)$/.exec(line);
    const ordered = /^\d+[.)]\s+(.+)$/.exec(line);
    if (unordered !== null || ordered !== null) {
      flushParagraph();
      const isOrdered = ordered !== null;
      const items = [unordered?.[1] ?? ordered?.[1] ?? ""];
      while (index + 1 < lines.length) {
        const candidate = lines[index + 1] ?? "";
        const next = isOrdered
          ? /^\d+[.)]\s+(.+)$/.exec(candidate)
          : /^[-*]\s+(.+)$/.exec(candidate);
        if (next === null) {
          break;
        }
        items.push(next[1] ?? "");
        index += 1;
      }
      blocks.push({ kind: "list", lines: items, ordered: isOrdered });
      continue;
    }
    if (line.startsWith("> ")) {
      flushParagraph();
      blocks.push({ kind: "quote", lines: [line.slice(2)] });
      continue;
    }
    paragraph.push(line);
  }
  flushParagraph();
  return blocks;
}

export function SafeMarkdown({ content }: SafeMarkdownProps) {
  const blocks = parseBlocks(content);
  return (
    <div className="safe-markdown">
      {blocks.map((block, index) => {
        const key = `${block.kind}-${String(index)}`;
        if (block.kind === "code") {
          return (
            <pre key={key}>
              <code>{block.lines.join("\n")}</code>
            </pre>
          );
        }
        if (block.kind === "heading") {
          const children = inlineNodes(block.lines[0] ?? "", key);
          if (block.level === 1) return <h2 key={key}>{children}</h2>;
          if (block.level === 2) return <h3 key={key}>{children}</h3>;
          return <h4 key={key}>{children}</h4>;
        }
        if (block.kind === "list") {
          const List = block.ordered === true ? "ol" : "ul";
          return (
            <List key={key}>
              {block.lines.map((line, lineIndex) => (
                <li key={`${key}-${String(lineIndex)}`}>
                  {inlineNodes(line, `${key}-${String(lineIndex)}`)}
                </li>
              ))}
            </List>
          );
        }
        if (block.kind === "quote") {
          return <blockquote key={key}>{inlineNodes(block.lines[0] ?? "", key)}</blockquote>;
        }
        return (
          <p key={key}>
            {block.lines.map((line, lineIndex) => (
              <Fragment key={`${key}-${String(lineIndex)}`}>
                {lineIndex === 0 ? null : <br />}
                {inlineNodes(line, `${key}-${String(lineIndex)}`)}
              </Fragment>
            ))}
          </p>
        );
      })}
    </div>
  );
}
