import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import InputPolicyNotice from './InputPolicyNotice';

const notice = (action) => ({
  action,
  findings: [
    {
      severity: action === 'block' ? 'high' : 'medium',
      safeMessage: 'A safe explanation for the user.'
    }
  ],
  assessedAt: '2026-11-16T20:00:00Z'
});

describe('InputPolicyNotice', () => {
  test.each([
    ['block', 'Blocked by policy'],
    ['escalate', 'Human review required']
  ])('renders an accessible %s notice beside the input', (action, title) => {
    const markup = renderToStaticMarkup(
      <InputPolicyNotice notice={notice(action)} />
    );

    expect(markup).toContain(`aria-label="${title}"`);
    expect(markup).toContain(title);
    expect(markup).toContain('A safe explanation for the user.');
    expect(markup).toContain('aria-hidden="true"');
    expect(markup).not.toContain('role="alert"');
  });

  test('bounds a large persisted finding list in the chat surface', () => {
    const manyFindings = {
      ...notice('block'),
      findings: Array.from({ length: 5 }, (_, index) => ({
        severity: 'high',
        safeMessage: `Safe finding ${index + 1}`
      }))
    };

    const markup = renderToStaticMarkup(
      <InputPolicyNotice notice={manyFindings} />
    );

    expect(markup).toContain('Safe finding 1');
    expect(markup).toContain('Safe finding 3');
    expect(markup).not.toContain('Safe finding 4');
    expect(markup).toContain('2 more findings');
  });

  test('fails closed for an unsupported action', () => {
    expect(renderToStaticMarkup(<InputPolicyNotice notice={notice('allow')} />)).toBe(
      ''
    );
  });
});
