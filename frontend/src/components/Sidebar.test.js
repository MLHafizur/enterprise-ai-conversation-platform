import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import Sidebar from './Sidebar';

const renderSidebar = (canViewGovernance, activeView = 'chat') =>
  renderToStaticMarkup(
    <Sidebar
      conversations={[]}
      selectedConversation={null}
      onSelectConversation={() => {}}
      onNewConversation={() => {}}
      creatingConversation={false}
      activeView={activeView}
      canViewGovernance={canViewGovernance}
      onShowChat={() => {}}
      onShowCompliance={() => {}}
      onShowReviews={() => {}}
      user={{ name: 'Reviewer', tenant_id: 'tenant-a' }}
      onLogout={() => {}}
    />
  );

describe('Sidebar governance navigation', () => {
  test('shows Compliance to an authorized reviewer', () => {
    const markup = renderSidebar(true);

    expect(markup).toContain('Compliance');
    expect(markup).toContain('Review queue');
  });

  test('does not render Compliance for an ordinary user', () => {
    const markup = renderSidebar(false);

    expect(markup).not.toContain('Compliance');
    expect(markup).not.toContain('Review queue');
  });

  test('marks the review queue as the active workspace page', () => {
    const markup = renderSidebar(true, 'reviews');

    expect(markup).toMatch(/aria-current="page"[^>]*>.*Review queue/s);
  });
});
