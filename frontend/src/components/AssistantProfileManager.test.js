import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';

import {
  activateAssistantProfileVersion,
  createAssistantProfileVersion,
  fetchAssistantProfileCatalog,
  previewAssistantProfile
} from '../assistantProfiles';
import AssistantProfileManager, {
  ActivationDialog,
  ProfileDetail,
  ProfileVersionList,
  PromptPreview
} from './AssistantProfileManager';

jest.mock('../assistantProfiles', () => {
  const actual = jest.requireActual('../assistantProfiles');
  return {
    ...actual,
    activateAssistantProfileVersion: jest.fn(),
    createAssistantProfileVersion: jest.fn(),
    fetchAssistantProfileCatalog: jest.fn(),
    previewAssistantProfile: jest.fn()
  };
});

const profileId = '11111111-1111-4111-8111-111111111111';
const actorId = '22222222-2222-4222-8222-222222222222';

const version = (number, overrides = {}) => ({
  profileId,
  version: number,
  promptTemplateVersion: 'enterprise-chat.v1',
  assistantName: number === 1 ? 'Acme Assistant' : 'Acme Guide',
  organizationName: 'Acme Corp',
  responseTone: number === 1 ? 'professional' : 'friendly',
  expertise: 'senior_engineer',
  communicationStyle: 'direct_helpful',
  contextCharacterLimit: 4096,
  createdAt: `2026-11-${String(number).padStart(2, '0')}T12:00:00Z`,
  createdBy: actorId,
  ...overrides
});

const catalog = ({ activeVersion = 1, revision = 0, versions = [version(1)] } = {}) => ({
  active: {
    profileId,
    profileVersion: activeVersion,
    revision,
    activatedAt: '2026-11-16T12:00:00Z',
    activatedBy: actorId
  },
  latestVersion: Math.max(...versions.map((item) => item.version)),
  versions
});

const click = async (element) => {
  await act(async () => {
    element.dispatchEvent(new MouseEvent('click', { bubbles: true }));
  });
};

const changeInput = async (element, value) => {
  const valueSetter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    'value'
  ).set;
  await act(async () => {
    valueSetter.call(element, value);
    element.dispatchEvent(new Event('input', { bubbles: true }));
  });
};

const buttonByText = (container, text) =>
  [...container.querySelectorAll('button')].find((button) =>
    button.textContent.includes(text)
  );

const mountManager = async () => {
  const container = document.createElement('div');
  const root = createRoot(container);
  // React DOM's root API requires act even though this test does not use
  // Testing Library's render helper.
  // eslint-disable-next-line testing-library/no-unnecessary-act
  await act(async () => {
    root.render(<AssistantProfileManager />);
  });
  return { container, root };
};

describe('assistant profile presentation', () => {
  test('shows active and immutable version status with safe profile fields', () => {
    const first = version(1);
    const view = renderToStaticMarkup(
      <ProfileVersionList
        versions={[version(2), first]}
        active={catalog().active}
        selectedKey={`${profileId}:1`}
        onSelect={() => {}}
      />
    );
    const utils = renderToStaticMarkup(
      <ProfileDetail
        profile={first}
        isActive
        busy={false}
        onPreview={() => {}}
        onCreateVersion={() => {}}
        onActivate={() => {}}
      />
    );

    expect(view).toContain('Active');
    expect(view).toContain('Version 2');
    expect(utils).toContain('Immutable v1');
    expect(utils).toContain('Senior Engineer');
    expect(utils).toContain('4,096 characters');
    expect(utils).not.toContain('Activate version');
  });

  test('renders an explicit empty version state', () => {
    const view = renderToStaticMarkup(
      <ProfileVersionList
        versions={[]}
        active={catalog().active}
        selectedKey=""
        onSelect={() => {}}
      />
    );

    expect(view).toContain('No profile versions');
    expect(view).toContain('has not been initialized');
  });

  test('escapes the server-rendered prompt preview', () => {
    const view = renderToStaticMarkup(
      <PromptPreview
        preview={{
          promptTemplateVersion: 'enterprise-chat.v1',
          systemInstruction: '<script>alert("unsafe")</script>'
        }}
        loading={false}
        errorMessage=""
      />
    );

    expect(view).toContain('&lt;script&gt;alert(&quot;unsafe&quot;)&lt;/script&gt;');
    expect(view).not.toContain('<script>');
  });

  test('explains pinning before activation', () => {
    const view = renderToStaticMarkup(
      <ActivationDialog
        profile={version(2)}
        activating={false}
        errorMessage=""
        onCancel={() => {}}
        onConfirm={() => {}}
      />
    );

    expect(view).toContain('role="alertdialog"');
    expect(view).toContain('New user turns will use version 2');
    expect(view).toContain(
      'Jobs already queued, running, or retried keep the profile version they were originally given.'
    );
  });
});

describe('AssistantProfileManager behavior', () => {
  beforeEach(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
    jest.clearAllMocks();
  });

  afterEach(() => {
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });

  test('renders accessible loading and retryable error states', async () => {
    let finishLoad;
    fetchAssistantProfileCatalog.mockReturnValue(
      new Promise((resolve) => {
        finishLoad = resolve;
      })
    );
    const { container, root } = await mountManager();
    expect(container.querySelector('[role="status"]')).not.toBeNull();
    expect(container.textContent).toContain('Loading assistant profiles');

    await act(async () => {
      finishLoad(Promise.reject(new Error('Catalog offline')));
      await Promise.resolve();
    });
    expect(container.querySelector('[role="alert"]')).not.toBeNull();
    expect(container.textContent).toContain('Catalog offline');
    expect(buttonByText(container, 'Try again')).toBeDefined();
    act(() => root.unmount());
  });

  test('creates a new immutable version without activating it', async () => {
    const initial = catalog();
    const afterCreate = catalog({
      activeVersion: 1,
      versions: [version(2), version(1)]
    });
    fetchAssistantProfileCatalog.mockResolvedValue(initial);
    createAssistantProfileVersion.mockResolvedValue({
      profile: version(2),
      catalog: afterCreate
    });
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Create new version'));
    expect(container.textContent).toContain('Saving does not activate it');

    const form = container.querySelector('form');
    await act(async () => {
      form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(createAssistantProfileVersion).toHaveBeenCalledWith(
      profileId,
      expect.objectContaining({
        expectedLatestVersion: 1,
        assistantName: 'Acme Assistant'
      })
    );
    expect(container.textContent).toContain('Version 2 created. It is not active yet.');
    expect(container.textContent).toContain('Immutable v2');
    expect(container.textContent).toContain('Activate version');
    act(() => root.unmount());
  });

  test('labels a draft with its selected source and the next global version', async () => {
    fetchAssistantProfileCatalog.mockResolvedValue(
      catalog({ versions: [version(3), version(2), version(1)] })
    );
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Acme Assistant'));
    await click(buttonByText(container, 'Create new version'));

    expect(container.textContent).toContain('Create profile version 4');
    expect(container.textContent).toContain('Starts as a copy of version 1');
    expect(container.textContent).not.toContain('Starts as a copy of version 3');
    act(() => root.unmount());
  });

  test('requires confirmation and activates with the current assignment revision', async () => {
    const initial = catalog({ versions: [version(2), version(1)] });
    const activated = catalog({
      activeVersion: 2,
      revision: 1,
      versions: [version(2), version(1)]
    });
    fetchAssistantProfileCatalog.mockResolvedValue(initial);
    activateAssistantProfileVersion.mockResolvedValue(activated);
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Acme Guide'));
    await click(buttonByText(container, 'Activate version'));
    expect(activateAssistantProfileVersion).not.toHaveBeenCalled();

    const dialog = container.querySelector('[role="alertdialog"]');
    await click(buttonByText(dialog, 'Activate version'));

    expect(activateAssistantProfileVersion).toHaveBeenCalledWith(profileId, 2, 0);
    expect(container.textContent).toContain('Version 2 is now active for new user turns.');
    act(() => root.unmount());
  });

  test('refreshes the catalog after an activation conflict', async () => {
    const initial = catalog({ versions: [version(2), version(1)] });
    const refreshed = catalog({
      activeVersion: 1,
      revision: 1,
      versions: [version(2), version(1)]
    });
    fetchAssistantProfileCatalog
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(refreshed);
    activateAssistantProfileVersion.mockRejectedValue({
      response: { status: 409, data: { detail: 'Stale assignment revision' } }
    });
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Acme Guide'));
    await click(buttonByText(container, 'Activate version'));
    await click(
      buttonByText(container.querySelector('[role="alertdialog"]'), 'Activate version')
    );

    expect(fetchAssistantProfileCatalog).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain(
      'Another administrator changed the active profile. The catalog was refreshed'
    );
    expect(container.querySelector('[role="alertdialog"]')).toBeNull();
    act(() => root.unmount());
  });

  test('ignores an old preview response after selecting another version', async () => {
    const initial = catalog({ versions: [version(2), version(1)] });
    let finishPreview;
    const previewPromise = new Promise((resolve) => {
      finishPreview = resolve;
    });
    fetchAssistantProfileCatalog.mockResolvedValue(initial);
    previewAssistantProfile.mockReturnValue(previewPromise);
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Preview prompt'));
    expect(container.textContent).toContain('Rendering prompt preview');
    await click(buttonByText(container, 'Acme Guide'));

    await act(async () => {
      finishPreview({
        promptTemplateVersion: 'enterprise-chat.v1',
        systemInstruction: 'Old selected profile prompt'
      });
      await previewPromise;
    });

    expect(container.textContent).not.toContain('Old selected profile prompt');
    expect(container.textContent).toContain('Choose “Preview prompt”');
    act(() => root.unmount());
  });

  test('invalidates an in-flight preview when the draft changes', async () => {
    let finishPreview;
    const previewPromise = new Promise((resolve) => {
      finishPreview = resolve;
    });
    fetchAssistantProfileCatalog.mockResolvedValue(catalog());
    previewAssistantProfile.mockReturnValue(previewPromise);
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Create new version'));
    await click(buttonByText(container, 'Preview draft'));
    expect(container.textContent).toContain('Rendering prompt preview');
    await changeInput(
      container.querySelector('#assistant-profile-name'),
      'Changed Assistant'
    );

    await act(async () => {
      finishPreview({
        promptTemplateVersion: 'enterprise-chat.v1',
        systemInstruction: 'Prompt for the old draft'
      });
      await previewPromise;
    });

    expect(container.textContent).not.toContain('Prompt for the old draft');
    expect(container.textContent).toContain('Choose “Preview prompt”');
    expect(container.textContent).not.toContain('Rendering prompt preview');
    act(() => root.unmount());
  });

  test('invalidates an in-flight draft preview when editing is cancelled', async () => {
    let finishPreview;
    const previewPromise = new Promise((resolve) => {
      finishPreview = resolve;
    });
    fetchAssistantProfileCatalog.mockResolvedValue(catalog());
    previewAssistantProfile.mockReturnValue(previewPromise);
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Create new version'));
    await click(buttonByText(container, 'Preview draft'));
    await click(buttonByText(container, 'Cancel'));

    await act(async () => {
      finishPreview({
        promptTemplateVersion: 'enterprise-chat.v1',
        systemInstruction: 'Cancelled draft prompt'
      });
      await previewPromise;
    });

    expect(container.textContent).not.toContain('Cancelled draft prompt');
    expect(container.querySelector('form')).toBeNull();
    expect(container.textContent).toContain('Choose “Preview prompt”');
    act(() => root.unmount());
  });

  test('cancels a draft if conflict refresh evicts its source version', async () => {
    const initial = catalog({
      activeVersion: 1,
      versions: [version(3), version(2), version(1)]
    });
    const refreshed = catalog({
      activeVersion: 2,
      revision: 1,
      versions: [version(4), version(3), version(2)]
    });
    fetchAssistantProfileCatalog
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(refreshed);
    createAssistantProfileVersion.mockRejectedValue({
      response: { status: 409, data: { detail: 'Stale latest version' } }
    });
    const { container, root } = await mountManager();

    await click(buttonByText(container, 'Acme Assistant'));
    await click(buttonByText(container, 'Create new version'));
    expect(container.textContent).toContain('Starts as a copy of version 1');
    await act(async () => {
      container
        .querySelector('form')
        .dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
    });

    expect(fetchAssistantProfileCatalog).toHaveBeenCalledTimes(2);
    expect(container.querySelector('form')).toBeNull();
    expect(container.textContent).not.toContain('Starts as a copy of version 1');
    expect(container.textContent).toContain(
      'Another administrator created a version first. The catalog was refreshed'
    );
    expect(container.textContent).toContain('Immutable v2');
    act(() => root.unmount());
  });
});
