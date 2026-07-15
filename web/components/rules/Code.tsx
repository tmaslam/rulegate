import styles from "./code.module.css";

/**
 * A very small Python highlighter.
 *
 * No highlighting library: this console inlines its own everything, and a
 * general-purpose tokeniser would be several hundred kB to colour five
 * snippets. It handles what these snippets actually contain — comments,
 * strings, keywords, builtin-ish names, numbers — and degrades to plain text
 * for anything else, which on a code block is a perfectly good failure mode.
 */

const KEYWORDS = new Set([
  "def", "class", "return", "if", "not", "or", "and", "is", "None", "True",
  "False", "for", "in", "self", "import", "from", "raise", "with", "as", "elif",
  "else", "while", "try", "except", "finally", "lambda", "yield", "assert",
  "pass", "break", "continue", "global", "nonlocal", "del", "await", "async",
]);

const BUILTINS = new Set([
  "isinstance", "str", "int", "float", "tuple", "list", "dict", "set", "len",
  "max", "min", "sorted", "print", "type", "super", "property", "staticmethod",
]);

type Tok = { t: string; k: string };

function tokenize(line: string): Tok[] {
  const out: Tok[] = [];

  // Comment wins over everything to its right.
  const hash = findComment(line);
  const code = hash === -1 ? line : line.slice(0, hash);
  const comment = hash === -1 ? null : line.slice(hash);

  // Split on strings first so keywords inside them are not coloured.
  const parts = code.split(/("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')/g);
  for (const part of parts) {
    if (!part) continue;
    if (/^["']/.test(part)) {
      out.push({ t: part, k: "str" });
      continue;
    }
    for (const word of part.split(/(\b)/)) {
      if (!word) continue;
      if (KEYWORDS.has(word)) out.push({ t: word, k: "kw" });
      else if (BUILTINS.has(word)) out.push({ t: word, k: "builtin" });
      else if (/^\d+(\.\d+)?$/.test(word)) out.push({ t: word, k: "num" });
      else if (/^[A-Z][A-Za-z0-9_]*$/.test(word)) out.push({ t: word, k: "cls" });
      else out.push({ t: word, k: "plain" });
    }
  }

  if (comment) out.push({ t: comment, k: "comment" });
  return out;
}

/** Index of a `#` that is not inside a string literal. */
function findComment(line: string): number {
  let quote: string | null = null;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (quote) {
      if (c === "\\") i++;
      else if (c === quote) quote = null;
    } else if (c === '"' || c === "'") {
      quote = c;
    } else if (c === "#") {
      return i;
    }
  }
  return -1;
}

export function Code({ source, label }: { source: string; label?: string }) {
  const lines = source.split("\n");
  return (
    <figure className={styles.wrap}>
      {label && (
        <figcaption className={styles.label}>
          <span className={styles.dots} aria-hidden>
            <i />
            <i />
            <i />
          </span>
          <code>{label}</code>
        </figcaption>
      )}
      {/* Own overflow container — long lines scroll here, never the page. */}
      <pre className={styles.pre} tabIndex={0}>
        <code>
          {lines.map((line, i) => (
            <span className={styles.line} key={i}>
              <span className={styles.ln} aria-hidden>
                {i + 1}
              </span>
              <span className={styles.code}>
                {tokenize(line).map((tok, j) => (
                  <span key={j} className={styles[tok.k]}>
                    {tok.t}
                  </span>
                ))}
                {line === "" ? " " : ""}
              </span>
            </span>
          ))}
        </code>
      </pre>
    </figure>
  );
}
