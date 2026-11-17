import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import Sidebar from './Sidebar';

const renderSidebar = ({
  canViewGovernance = false,
  canManageProfiles = false,
  canViewCodeSecurity = false,
  activeView = 'chat'
} = {}) =>
  renderToStaticMarkup(
    <Sidebar
      conversations={[]}
      selectedConversation={null}
      onSelectConversation={() => {}}
      onNewConversation={() => {}}
      creatingConversation={false}
      activeView={activeView}
      canViewGovernance={canViewGovernance}
      canManageProfiles={canManageProfiles}
      canViewCodeSecurity={canViewCodeSecurity}
      onShowChat={() => {}}
      onShowCompliance={() => {}}
      onShowReviews={() => {}}
      onShowProfiles={() => {}}
      onShowSecurityDashboard={() => {}}
      onShowCodeAnalysis={() => {}}
      onShowSecurityRepositories={() => {}}
      onShowSecuritySettings={() => {}}
      user={{ name: 'Reviewer', tenant_id: 'tenant-a' }}
      onLogout={() => {}}
    />
  );

describe('Sidebar governance navigation', () => {
  test('shows Compliance to an authorized reviewer', () => {
    const view = renderSidebar({ canViewGovernance: true });

    expect(view).toContain('Compliance');
    expect(view).toContain('Review queue');
  });

  test('does not render Compliance for an ordinary user', () => {
    const view = renderSidebar();

    expect(view).not.toContain('Compliance');
    expect(view).not.toContain('Review queue');
  });

  test('marks the review queue as the active workspace page', () => {
    const view = renderSidebar({
      canViewGovernance: true,
      activeView: 'reviews'
    });

    expect(view).toMatch(/aria-current="page"[^>]*>.*Review queue/s);
  });

  test('shows assistant profiles only to an administrator', () => {
    const view = renderSidebar({ canManageProfiles: true });
    const utils = renderSidebar({ canViewGovernance: true });

    expect(view).toContain('Assistant profiles');
    expect(utils).not.toContain('Assistant profiles');
  });

  test('marks assistant profiles as the active workspace page', () => {
    const view = renderSidebar({
      canManageProfiles: true,
      activeView: 'profiles'
    });

    expect(view).toMatch(/aria-current="page"[^>]*>.*Assistant profiles/s);
  });

  test('shows every secure-code screen to an authorized reader', () => {
    const view = renderSidebar({ canViewCodeSecurity: true });

    expect(view).toContain('Security dashboard');
    expect(view).toContain('Code analysis');
    expect(view).toContain('Repositories');
    expect(view).toContain('Security settings');
  });

  test('hides secure-code routes from an ordinary user', () => {
    const view = renderSidebar();

    expect(view).not.toContain('Security dashboard');
    expect(view).not.toContain('Code analysis');
    expect(view).not.toContain('Security settings');
  });

  test('marks the active secure-code route', () => {
    const view = renderSidebar({
      canViewCodeSecurity: true,
      activeView: 'security-analysis'
    });

    expect(view).toMatch(/aria-current="page"[^>]*>.*Code analysis/s);
  });
});
