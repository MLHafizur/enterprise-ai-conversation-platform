import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  SecurityAnalysisResults,
  SecurityRuleCatalog
} from './CodeSecurityAnalysis';

const finding = {
  id: '22222222-2222-4222-8222-222222222222',
  ruleId: 'python.sql_injection',
  severity: 'high',
  detector: 'ast',
  cweId: 'CWE-89',
  owaspCategory: 'A03:2021',
  safeMessage: 'Dynamic SQL construction was detected.',
  filePath: 'src/users.py',
  lineNumber: 12,
  columnNumber: 4,
  redactedCodeSnippet: 'cursor.execute(query + "[REDACTED]")',
  recommendation: 'Use a parameterized database query.'
};

const scan = {
  id: '11111111-1111-4111-8111-111111111111',
  sourceLabel: 'src/users.py',
  status: 'succeeded',
  gateDecision: 'review_required',
  totalFindings: 1,
  counts: { critical: 0, high: 1, medium: 0, low: 0, info: 0 },
  filesScanned: 1,
  bytesScanned: 1900,
  rulesetVersion: 'enterprise-security.v1',
  safeAiSummary: null,
  findings: [finding]
};

const catalog = {
  rulesetVersion: 'enterprise-security.v1',
  rules: [
    {
      ruleId: 'python.sql_injection',
      title: 'SQL injection risk',
      severity: 'high',
      detectors: ['ast', 'pattern'],
      safeMessage: 'Dynamic SQL construction was detected.',
      recommendation: 'Use a parameterized database query.',
      cweId: 'CWE-89',
      owaspCategory: 'A03:2021',
      enabled: true,
      privatePattern: 'never-render-private-detector-data'
    }
  ]
};

describe('SecurityAnalysisResults', () => {
  test('shows only redacted evidence, mappings, and remediation actions', () => {
    const view = renderToStaticMarkup(
      <SecurityAnalysisResults scan={scan} />
    );

    expect(view).toContain('Analysis results');
    expect(view).toContain('review required');
    expect(view).toContain('CWE-89');
    expect(view).toContain('OWASP A03:2021');
    expect(view).toContain('Redacted evidence');
    expect(view).toContain('[REDACTED]');
    expect(view).toContain('Use a parameterized database query');
    expect(view).toContain('Explain finding');
    expect(view).toContain('Suggest secure fix');
    expect(view).not.toContain('Human security review required');
  });

  test('shows the durable review workflow only to administrators', () => {
    const view = renderToStaticMarkup(
      <SecurityAnalysisResults scan={scan} canManage />
    );

    expect(view).toContain('Human security review required');
    expect(view).toContain('Record review');
  });

  test('shows an existing durable review instead of offering a second decision', () => {
    const view = renderToStaticMarkup(
      <SecurityAnalysisResults
        scan={{
          ...scan,
          review: {
            id: '55555555-5555-4555-8555-555555555555',
            scanId: scan.id,
            decision: 'approved',
            safeNote: 'Compensating controls verified.',
            reviewedBy: '66666666-6666-4666-8666-666666666666',
            reviewedAt: '2026-11-16T18:02:00Z'
          }
        }}
        canManage
      />
    );

    expect(view).toContain('Recorded human review');
    expect(view).toContain('Compensating controls verified.');
    expect(view).not.toContain('Record review');
  });

  test('shows a clear clean-scan state', () => {
    const view = renderToStaticMarkup(
      <SecurityAnalysisResults
        scan={{
          ...scan,
          gateDecision: 'approve',
          totalFindings: 0,
          counts: { critical: 0, high: 0, medium: 0, low: 0, info: 0 },
          findings: []
        }}
      />
    );

    expect(view).toContain('No security findings');
  });
});

describe('SecurityRuleCatalog', () => {
  test('shows safe read-only deterministic rule metadata', () => {
    const view = renderToStaticMarkup(
      <SecurityRuleCatalog catalog={catalog} />
    );

    expect(view).toContain('Deterministic rule catalog');
    expect(view).toContain('Read-only, safe rule metadata');
    expect(view).toContain('SQL injection risk');
    expect(view).toContain('python.sql_injection');
    expect(view).toContain('high');
    expect(view).toContain('AST');
    expect(view).toContain('PATTERN');
    expect(view).toContain('CWE-89');
    expect(view).toContain('OWASP A03:2021');
    expect(view).toContain('Dynamic SQL construction was detected');
    expect(view).toContain('Use a parameterized database query');
    expect(view).not.toContain('never-render-private-detector-data');
  });
});
