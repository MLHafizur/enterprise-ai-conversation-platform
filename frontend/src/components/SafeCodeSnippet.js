import React, { useMemo } from 'react';

const KEYWORDS = new Set([
  'async',
  'await',
  'break',
  'case',
  'catch',
  'class',
  'const',
  'continue',
  'def',
  'delete',
  'do',
  'else',
  'except',
  'export',
  'extends',
  'false',
  'finally',
  'for',
  'from',
  'function',
  'if',
  'import',
  'in',
  'instanceof',
  'let',
  'new',
  'none',
  'null',
  'pass',
  'raise',
  'return',
  'static',
  'super',
  'switch',
  'throw',
  'true',
  'try',
  'var',
  'while',
  'with',
  'yield'
]);

const TOKEN_CLASSES = {
  comment: 'text-slate-500',
  identifier: 'text-slate-100',
  keyword: 'font-semibold text-fuchsia-300',
  number: 'text-cyan-300',
  operator: 'text-slate-300',
  redacted: 'rounded bg-amber-300/20 px-0.5 font-semibold text-amber-200',
  string: 'text-emerald-300',
  whitespace: ''
};

const LANGUAGE_LABELS = new Map([
  ['c', 'C'],
  ['cpp', 'C++'],
  ['cs', 'C#'],
  ['go', 'Go'],
  ['h', 'C header'],
  ['java', 'Java'],
  ['js', 'JavaScript'],
  ['jsx', 'JavaScript JSX'],
  ['php', 'PHP'],
  ['py', 'Python'],
  ['rb', 'Ruby'],
  ['ts', 'TypeScript'],
  ['tsx', 'TypeScript JSX']
]);

const isDigit = (value) => value >= '0' && value <= '9';
const isIdentifierStart = (value) =>
  (value >= 'a' && value <= 'z') ||
  (value >= 'A' && value <= 'Z') ||
  value === '_';
const isIdentifierPart = (value) => isIdentifierStart(value) || isDigit(value);

const pushToken = (tokens, type, text) => {
  if (text) tokens.push({ type, text });
};

// This deliberately small lexer never emits HTML. React renders each token as
// escaped text, which keeps even hostile source snippets inert.
export const tokenizeSafeCode = (value) => {
  const source = String(value ?? '');
  const tokens = [];
  let cursor = 0;

  while (cursor < source.length) {
    const start = cursor;
    const character = source[cursor];

    if (source.startsWith('[REDACTED]', cursor)) {
      cursor += '[REDACTED]'.length;
      pushToken(tokens, 'redacted', source.slice(start, cursor));
      continue;
    }

    if (/\s/.test(character)) {
      cursor += 1;
      while (cursor < source.length && /\s/.test(source[cursor])) cursor += 1;
      pushToken(tokens, 'whitespace', source.slice(start, cursor));
      continue;
    }

    if (character === '#' || source.startsWith('//', cursor)) {
      cursor += character === '#' ? 1 : 2;
      while (cursor < source.length && source[cursor] !== '\n') cursor += 1;
      pushToken(tokens, 'comment', source.slice(start, cursor));
      continue;
    }

    if (source.startsWith('/*', cursor)) {
      const end = source.indexOf('*/', cursor + 2);
      cursor = end === -1 ? source.length : end + 2;
      pushToken(tokens, 'comment', source.slice(start, cursor));
      continue;
    }

    if (character === '"' || character === "'" || character === '`') {
      const quote = character;
      cursor += 1;
      while (cursor < source.length) {
        if (source[cursor] === '\\') {
          cursor = Math.min(cursor + 2, source.length);
        } else if (source[cursor] === quote) {
          cursor += 1;
          break;
        } else {
          cursor += 1;
        }
      }
      pushToken(tokens, 'string', source.slice(start, cursor));
      continue;
    }

    if (isDigit(character)) {
      cursor += 1;
      while (
        cursor < source.length &&
        (isDigit(source[cursor]) || source[cursor] === '.' || source[cursor] === '_')
      ) {
        cursor += 1;
      }
      pushToken(tokens, 'number', source.slice(start, cursor));
      continue;
    }

    if (isIdentifierStart(character)) {
      cursor += 1;
      while (cursor < source.length && isIdentifierPart(source[cursor])) cursor += 1;
      const text = source.slice(start, cursor);
      pushToken(tokens, KEYWORDS.has(text.toLowerCase()) ? 'keyword' : 'identifier', text);
      continue;
    }

    cursor += 1;
    pushToken(tokens, 'operator', source.slice(start, cursor));
  }

  return tokens;
};

const languageForPath = (filePath) => {
  const extension = String(filePath ?? '').split('.').pop()?.toLowerCase();
  return LANGUAGE_LABELS.get(extension) || 'source code';
};

const SafeCodeSnippet = ({ code, filePath, label = 'Redacted evidence' }) => {
  const tokens = useMemo(() => tokenizeSafeCode(code), [code]);
  const language = languageForPath(filePath);

  return (
    <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-sm leading-6">
      <code aria-label={`${label}, syntax-highlighted ${language}`}>
        {tokens.map((token, index) => (
          <span
            className={TOKEN_CLASSES[token.type]}
            data-token={token.type}
            key={`${index}-${token.type}`}
          >
            {token.text}
          </span>
        ))}
      </code>
    </pre>
  );
};

export default SafeCodeSnippet;
