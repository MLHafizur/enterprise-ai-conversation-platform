import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { RepositorySecurityList } from './SecurityRepositories';

const repository = {
  id: '33333333-3333-4333-8333-333333333333',
  provider: 'github',
  displayName: 'Payments API',
  cloneUrl: 'https://github.com/acme/payments.git',
  defaultBranch: 'main',
  enabled: true,
  createdAt: '2026-11-16T18:00:00Z',
  updatedAt: '2026-11-16T18:00:00Z'
};

const scan = {
  id: '11111111-1111-4111-8111-111111111111',
  repositoryId: repository.id,
  createdAt: '2026-11-16T18:01:00Z',
  totalFindings: 2,
  status: 'succeeded',
  gateDecision: 'review_required'
};

describe('RepositorySecurityList', () => {
  test('lets an agent scan an existing repository without repository management', () => {
    const view = renderToStaticMarkup(
      <RepositorySecurityList
        repositories={[repository]}
        scans={[scan]}
        canScan
      />
    );

    expect(view).toContain('Payments API');
    expect(view).toContain('https://github.com/acme/payments.git');
    expect(view).toContain('2 findings');
    expect(view).toContain('review required');
    expect(view).toContain('View findings and review');
    expect(view).toContain('Scan branch');
    expect(view).toContain('Scan pull request');
  });

  test('shows branch and pull-request scan controls to an admin', () => {
    const view = renderToStaticMarkup(
      <RepositorySecurityList
        repositories={[repository]}
        scans={[scan]}
        canManage
      />
    );

    expect(view).toContain('Scan branch');
    expect(view).toContain('Scan pull request');
  });

  test('does not show scan controls without scan permission', () => {
    const view = renderToStaticMarkup(
      <RepositorySecurityList repositories={[repository]} scans={[scan]} />
    );

    expect(view).not.toContain('Scan branch');
    expect(view).not.toContain('Scan pull request');
  });

  test('renders an explicit empty repository state', () => {
    const view = renderToStaticMarkup(
      <RepositorySecurityList repositories={[]} scans={[]} canManage />
    );
    expect(view).toContain('No repositories registered');
  });
});
