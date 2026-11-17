import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
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

const earlierScan = {
  ...scan,
  id: '22222222-2222-4222-8222-222222222222',
  sourceLabel: 'acme/payments@release/2026.10',
  branch: 'release/2026.10',
  createdAt: '2026-11-15T18:01:00Z',
  totalFindings: 0,
  status: 'succeeded',
  gateDecision: 'approve'
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
    expect(view).toContain('Show recent scans (1)');
    expect(view).toContain('aria-expanded="false"');
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

  test('does not claim all-time scan history when the recent window is empty', () => {
    const view = renderToStaticMarkup(
      <RepositorySecurityList repositories={[repository]} scans={[]} />
    );

    expect(view).toContain('No scans in the recent activity window.');
    expect(view).not.toContain('has not been scanned');
  });

  test('expands newest-first scans from the recent activity window', () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    const root = createRoot(container);
    const openScan = jest.fn();

    // React DOM's root API requires act; this test does not use Testing Library.
    // eslint-disable-next-line testing-library/no-unnecessary-act
    act(() => {
      root.render(
        <RepositorySecurityList
          repositories={[repository]}
          scans={[earlierScan, scan]}
          onOpenScan={openScan}
        />
      );
    });

    const toggle = Array.from(container.querySelectorAll('button')).find((button) =>
      button.textContent.includes('Show recent scans')
    );
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(container.textContent).not.toContain('release/2026.10');

    act(() => toggle.dispatchEvent(new MouseEvent('click', { bubbles: true })));

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.textContent).toContain('Hide recent scans (2)');
    expect(container.textContent).toContain('release/2026.10');
    const detailButtons = Array.from(container.querySelectorAll('button')).filter(
      (button) => button.textContent === 'View scan details'
    );
    expect(detailButtons).toHaveLength(2);
    act(() => detailButtons[1].dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(openScan).toHaveBeenCalledWith(earlierScan.id);

    act(() => root.unmount());
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });
});
