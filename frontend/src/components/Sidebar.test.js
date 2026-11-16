import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import Sidebar from './Sidebar';

const renderSidebar = ({
  canViewGovernance = false,
  canManageProfiles = false,
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
      onShowChat={() => {}}
      onShowCompliance={() => {}}
      onShowReviews={() => {}}
      onShowProfiles={() => {}}
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
});
