import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import SafeCodeSnippet, { tokenizeSafeCode } from './SafeCodeSnippet';

describe('SafeCodeSnippet', () => {
  test('highlights useful source tokens while preserving the exact text', () => {
    const code = 'def find_user(user_id):\n    return cursor.execute(query + "[REDACTED]")';
    const tokens = tokenizeSafeCode(code);

    expect(tokens.map((token) => token.text).join('')).toBe(code);
    expect(tokens).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: 'keyword', text: 'def' }),
        expect.objectContaining({ type: 'keyword', text: 'return' }),
        expect.objectContaining({ type: 'string', text: '"[REDACTED]"' })
      ])
    );
  });

  test('renders hostile-looking snippets only as escaped React text', () => {
    const view = renderToStaticMarkup(
      <SafeCodeSnippet
        code={'<script>alert("unsafe")</script>\n# [REDACTED]'}
        filePath="unsafe.py"
      />
    );

    expect(view).toContain('syntax-highlighted Python');
    expect(view).toContain('&lt;');
    expect(view).toContain('&gt;');
    expect(view).not.toContain('<script>');
    expect(view).not.toContain('dangerouslySetInnerHTML');
  });

  test('marks an unquoted redaction placeholder as a dedicated safe token', () => {
    const view = renderToStaticMarkup(
      <SafeCodeSnippet code="token = [REDACTED]" filePath="secrets.js" />
    );

    expect(view).toContain('data-token="redacted"');
    expect(view).toContain('[REDACTED]');
  });
});
