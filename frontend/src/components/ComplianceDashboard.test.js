import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { ComplianceContent } from './ComplianceDashboard';

const snapshot = {
  counts: { allow: 8, block: 2, escalate: 1, total: 11 },
  recent: [
    {
      agentRunId: '11111111-1111-4111-8111-111111111111',
      conversationId: '22222222-2222-4222-8222-222222222222',
      policyVersion: 'conversation-input.v1',
      action: 'escalate',
      riskScore: 0.72,
      findings: [
        {
          code: 'review.ambiguous_intent',
          severity: 'medium',
          safeMessage: 'This request requires human review.'
        }
      ],
      assessedAt: '2026-11-16T15:30:00Z'
    }
  ]
};

describe('ComplianceContent', () => {
  test('renders backend counts and safe recent-decision fields', () => {
    const markup = renderToStaticMarkup(<ComplianceContent snapshot={snapshot} />);

    expect(markup).toContain('Total assessments');
    expect(markup).toContain('Escalated');
    expect(markup).toContain('Risk 72%');
    expect(markup).toContain('review.ambiguous_intent');
    expect(markup).toContain('This request requires human review.');
    expect(markup).toContain('Safe policy metadata only');
  });

  test('renders an explicit empty state', () => {
    const markup = renderToStaticMarkup(
      <ComplianceContent
        snapshot={{
          counts: { allow: 0, block: 0, escalate: 0, total: 0 },
          recent: []
        }}
      />
    );

    expect(markup).toContain('No assessments yet');
    expect(markup).toContain('Policy decisions will appear');
  });
});
