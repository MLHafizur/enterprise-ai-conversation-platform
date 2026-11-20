import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';

import Dashboard from './Dashboard';

jest.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    user: {
      name: 'Security Admin',
      tenant_id: '11111111-1111-4111-8111-111111111111',
      roles: ['admin']
    },
    logout: () => {}
  })
}));

jest.mock('../hooks/useWebSocket', () => ({
  useWebSocket: () => ({
    activeConnections: 1,
    connectionStatus: 'connected',
    subscribe: () => () => {}
  })
}));

jest.mock('./SecurityDashboard', () => () => <div>Security dashboard content</div>);
jest.mock('./TenantInfo', () => () => <div>Tenant information</div>);

describe('Dashboard responsive shell', () => {
  test('uses a width-safe shell with a mobile navigation trigger', () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    const root = createRoot(container);

    // React DOM's root API requires act; this test does not use Testing Library.
    // eslint-disable-next-line testing-library/no-unnecessary-act
    act(() => {
      root.render(
        <MemoryRouter
          future={{ v7_relativeSplatPath: true, v7_startTransition: true }}
          initialEntries={['/dashboard/security']}
        >
          <Dashboard />
        </MemoryRouter>
      );
    });
    const view = container.innerHTML;

    expect(view).toContain('min-w-0 overflow-hidden');
    expect(view).not.toContain('min-w-[900px]');
    expect(view).toContain('aria-label="Open workspace navigation"');
    expect(view).toContain('aria-controls="mobile-workspace-sidebar"');
    expect(view).toContain('aria-expanded="false"');
    expect(view).toContain('aria-label="Primary workspace navigation"');

    const trigger = container.querySelector(
      '[aria-label="Open workspace navigation"]'
    );
    act(() => trigger.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();

    const overlay = container.querySelector(
      '[aria-label="Close navigation overlay"]'
    );
    act(() => overlay.dispatchEvent(new MouseEvent('click', { bubbles: true })));
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    expect(container.querySelector('[role="dialog"]')).toBeNull();

    act(() => root.unmount());
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });
});
