import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { SecuritySettingsForm } from './CodeSecuritySettings';

const settings = {
  revision: 7,
  blockSeverity: 'critical',
  reviewSeverity: 'high',
  maxFileBytes: 1048576,
  maxRepositoryFiles: 2000,
  maxRepositoryBytes: 52428800,
  autoScanPush: true,
  autoScanPullRequest: true,
  aiSummaryEnabled: false
};

describe('SecuritySettingsForm', () => {
  test('shows the full policy read-only to a security reader', () => {
    const view = renderToStaticMarkup(
      <SecuritySettingsForm settings={settings} />
    );

    expect(view).toContain('Security gate policy');
    expect(view).toContain('Resource limits');
    expect(view).toContain('Auto-scan pushes');
    expect(view).toContain('Advisory AI summaries');
    expect(view).toContain('Optimistic revision 7');
    expect(view).toContain('Read only');
    expect(view).not.toContain('Save policy');
  });

  test('enables policy management for an administrator', () => {
    const view = renderToStaticMarkup(
      <SecuritySettingsForm settings={settings} canManage />
    );

    expect(view).toContain('Save policy');
    expect(view).not.toContain('Read only');
  });
});
