import React, { act, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';

import Sidebar from './Sidebar';

const renderSidebar = ({
  canViewGovernance = false,
  canManageProfiles = false,
  canViewCodeSecurity = false,
  activeView = 'chat',
  mobileOpen = false
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
      mobileOpen={mobileOpen}
      onMobileClose={() => {}}
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

  test('keeps the desktop sidebar and exposes a labelled mobile dialog on demand', () => {
    const view = renderSidebar();
    const utils = renderSidebar({ mobileOpen: true });

    expect(view).toContain('aria-label="Primary workspace navigation"');
    expect(view).toContain('class="hidden w-80 shrink-0');
    expect(view).not.toContain('role="dialog"');
    expect(utils).toContain('role="dialog"');
    expect(utils).toContain('aria-modal="true"');
    expect(utils).toContain('aria-label="Mobile workspace navigation"');
    expect(utils).toContain('aria-label="Close navigation overlay"');
    expect(utils).toContain('aria-label="Close navigation"');
  });

  test('closes the mobile drawer after navigation and when Escape is pressed', () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const container = document.createElement('div');
    const root = createRoot(container);
    const close = jest.fn();
    const showCodeAnalysis = jest.fn();

    // React DOM's root API requires act; this test does not use Testing Library.
    // eslint-disable-next-line testing-library/no-unnecessary-act
    act(() => {
      root.render(
        <Sidebar
          conversations={[]}
          selectedConversation={null}
          onSelectConversation={() => {}}
          onNewConversation={() => {}}
          creatingConversation={false}
          activeView="security"
          canViewGovernance={false}
          canManageProfiles={false}
          canViewCodeSecurity
          onShowChat={() => {}}
          onShowCompliance={() => {}}
          onShowReviews={() => {}}
          onShowProfiles={() => {}}
          onShowSecurityDashboard={() => {}}
          onShowCodeAnalysis={showCodeAnalysis}
          onShowSecurityRepositories={() => {}}
          onShowSecuritySettings={() => {}}
          user={{ name: 'Reviewer', tenant_id: 'tenant-a' }}
          onLogout={() => {}}
          mobileOpen
          onMobileClose={close}
        />
      );
    });

    const drawer = container.querySelector('[role="dialog"]');
    const codeAnalysisButton = Array.from(drawer.querySelectorAll('button')).find(
      (button) => button.textContent.includes('Code analysis')
    );
    act(() =>
      codeAnalysisButton.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    );
    expect(showCodeAnalysis).toHaveBeenCalledTimes(1);
    expect(close).toHaveBeenCalledTimes(1);

    act(() =>
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    );
    expect(close).toHaveBeenCalledTimes(2);

    act(() => root.unmount());
    expect(document.body.style.overflow).toBe('');
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });

  test('closes and cleans up the mobile drawer when the viewport becomes desktop', () => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    const originalMatchMedia = window.matchMedia;
    let breakpointListener;
    const mediaQuery = {
      matches: false,
      media: '(min-width: 1024px)',
      addEventListener: jest.fn((eventName, listener) => {
        if (eventName === 'change') breakpointListener = listener;
      }),
      removeEventListener: jest.fn()
    };
    window.matchMedia = jest.fn(() => mediaQuery);
    const close = jest.fn();

    const Harness = () => {
      const [open, setOpen] = useState(true);
      return (
        <Sidebar
          conversations={[]}
          selectedConversation={null}
          onSelectConversation={() => {}}
          onNewConversation={() => {}}
          creatingConversation={false}
          activeView="chat"
          canViewGovernance={false}
          canManageProfiles={false}
          canViewCodeSecurity={false}
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
          mobileOpen={open}
          onMobileClose={() => {
            close();
            setOpen(false);
          }}
        />
      );
    };

    const container = document.createElement('div');
    const root = createRoot(container);
    // React DOM's root API requires act; this test does not use Testing Library.
    // eslint-disable-next-line testing-library/no-unnecessary-act
    act(() => root.render(<Harness />));

    expect(window.matchMedia).toHaveBeenCalledWith('(min-width: 1024px)');
    expect(mediaQuery.addEventListener).toHaveBeenCalledWith(
      'change',
      expect.any(Function)
    );
    expect(container.querySelector('[role="dialog"]')).not.toBeNull();
    expect(document.body.style.overflow).toBe('hidden');

    act(() => breakpointListener({ matches: true }));

    expect(close).toHaveBeenCalledTimes(1);
    expect(container.querySelector('[role="dialog"]')).toBeNull();
    expect(document.body.style.overflow).toBe('');
    expect(mediaQuery.removeEventListener).toHaveBeenCalledWith(
      'change',
      breakpointListener
    );

    act(() => root.unmount());
    window.matchMedia = originalMatchMedia;
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });
});
