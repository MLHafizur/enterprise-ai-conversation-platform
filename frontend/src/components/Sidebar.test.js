import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import Sidebar from './Sidebar';

const renderSidebar = (canViewGovernance) =>
  renderToStaticMarkup(
    <Sidebar
      conversations={[]}
      selectedConversation={null}
      onSelectConversation={() => {}}
      onNewConversation={() => {}}
      creatingConversation={false}
      activeView="chat"
      canViewGovernance={canViewGovernance}
      onShowChat={() => {}}
      onShowCompliance={() => {}}
      user={{ name: 'Reviewer', tenant_id: 'tenant-a' }}
      onLogout={() => {}}
    />
  );

describe('Sidebar governance navigation', () => {
  test('shows Compliance to an authorized reviewer', () => {
    expect(renderSidebar(true)).toContain('Compliance');
  });

  test('does not render Compliance for an ordinary user', () => {
    expect(renderSidebar(false)).not.toContain('Compliance');
  });
});
