import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { SecurityDashboardContent } from './SecurityDashboard';

const scan = {
  id: '11111111-1111-4111-8111-111111111111',
  sourceLabel: 'acme/payments@main',
  status: 'succeeded',
  gateDecision: 'block',
  totalFindings: 3,
  createdAt: '2026-11-16T18:00:00Z'
};

const snapshot = {
  counts: {
    totalScans: 12,
    vulnerabilitiesFound: 9,
    filesScanned: 76,
    repositories: 3,
    approved: 8,
    reviewRequired: 2,
    blocked: 1,
    failed: 1,
    critical: 1,
    high: 2,
    medium: 3,
    low: 2,
    info: 1
  },
  recentScans: [scan],
  topVulnerabilities: [
    {
      ruleId: 'python.sql_injection',
      title: 'SQL injection risk',
      count: 4,
      severity: 'high',
      cweId: 'CWE-89'
    }
  ],
  trend: [
    {
      date: '2026-11-16',
      total: 9,
      critical: 1,
      high: 2,
      medium: 3,
      low: 2,
      info: 1
    }
  ]
};

describe('SecurityDashboardContent', () => {
  test('shows posture totals, severity trends, scans, and top vulnerabilities', () => {
    const view = renderToStaticMarkup(
      <SecurityDashboardContent snapshot={snapshot} />
    );

    expect(view).toContain('Total scans');
    expect(view).toContain('Vulnerabilities');
    expect(view).toContain('Review required');
    expect(view).toContain('Severity distribution');
    expect(view).toContain('Finding trend');
    expect(view).toContain('Severity trend chart for 1 day');
    expect(view).toContain('Severity legend');
    expect(view).toContain('Info');
    expect(view).toContain('acme/payments@main');
    expect(view).toContain('SQL injection risk');
    expect(view).toContain('CWE-89');
  });

  test('renders explicit empty states without inventing data', () => {
    const view = renderToStaticMarkup(
      <SecurityDashboardContent
        snapshot={{
          ...snapshot,
          recentScans: [],
          topVulnerabilities: [],
          trend: []
        }}
      />
    );

    expect(view).toContain('No scans have been submitted');
    expect(view).toContain('No vulnerabilities found');
    expect(view).toContain('No trend data yet');
  });
});
