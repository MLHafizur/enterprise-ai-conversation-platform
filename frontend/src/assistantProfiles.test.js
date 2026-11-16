import api from './api';
import {
  activateAssistantProfileVersion,
  canManageAssistantProfiles,
  createAssistantProfileVersion,
  fetchAssistantProfileCatalog,
  normalizeAssistantProfileCatalog,
  normalizeAssistantProfilePreview,
  previewAssistantProfile
} from './assistantProfiles';

jest.mock('./api', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() }
}));

const profileId = '11111111-1111-4111-8111-111111111111';
const actorId = '22222222-2222-4222-8222-222222222222';

const profile = (version = 1, overrides = {}) => ({
  profile_id: profileId,
  version,
  prompt_template_version: 'enterprise-chat.v1',
  assistant_name: version === 1 ? 'Acme Assistant' : 'Acme Guide',
  organization_name: 'Acme Corp',
  response_tone: 'professional',
  expertise: 'senior_engineer',
  communication_style: 'direct_helpful',
  context_character_limit: 4096,
  created_at: `2026-11-${String(version).padStart(2, '0')}T12:00:00Z`,
  created_by: actorId,
  ...overrides
});

const catalog = ({ activeVersion = 1, versions = [profile(1)] } = {}) => ({
  active: {
    profile_id: profileId,
    profile_version: activeVersion,
    revision: activeVersion - 1,
    activated_at: '2026-11-16T12:00:00Z',
    activated_by: actorId
  },
  latest_version: Math.max(...versions.map((item) => item.version)),
  versions
});

const draft = {
  promptTemplateVersion: 'enterprise-chat.v1',
  assistantName: 'Acme Guide',
  organizationName: 'Acme Corp',
  responseTone: 'friendly',
  expertise: 'product_specialist',
  communicationStyle: 'concise_supportive',
  contextCharacterLimit: 2048
};

describe('assistant profile API adapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('recognizes only a well-formed admin role collection', () => {
    expect(canManageAssistantProfiles(['admin'])).toBe(true);
    expect(canManageAssistantProfiles(['user', 'admin'])).toBe(true);
    expect(canManageAssistantProfiles(['agent'])).toBe(false);
    expect(canManageAssistantProfiles([' admin'])).toBe(false);
    expect(canManageAssistantProfiles(['admin', 42])).toBe(false);
    expect(canManageAssistantProfiles([])).toBe(false);
  });

  test('normalizes, freezes, and orders an internally consistent catalog', () => {
    const normalized = normalizeAssistantProfileCatalog(
      catalog({ activeVersion: 1, versions: [profile(1), profile(2)] })
    );

    expect(normalized).toMatchObject({
      latestVersion: 2,
      active: { profileId, profileVersion: 1, revision: 0 },
      versions: [
        { version: 2, assistantName: 'Acme Guide' },
        { version: 1, assistantName: 'Acme Assistant' }
      ]
    });
    expect(Object.isFrozen(normalized)).toBe(true);
    expect(Object.isFrozen(normalized.versions)).toBe(true);
  });

  test.each([
    ['unknown field', (value) => { value.private_prompt = 'secret'; }],
    ['active version missing from history', (value) => { value.active.profile_version = 9; }],
    ['incorrect latest version', (value) => { value.latest_version = 9; }],
    ['duplicate reference', (value) => { value.versions.push(profile(1)); }],
    ['mixed profile series', (value) => {
      value.versions.push(
        profile(2, { profile_id: '33333333-3333-4333-8333-333333333333' })
      );
      value.latest_version = 2;
    }],
    ['more than 100 versions', (value) => {
      value.versions = Array.from({ length: 101 }, (_item, index) =>
        profile(index + 1, { created_at: '2026-11-01T12:00:00Z' })
      );
      value.latest_version = 101;
    }],
    ['unsupported tone', (value) => { value.versions[0].response_tone = 'sarcastic'; }],
    ['oversized context', (value) => { value.versions[0].context_character_limit = 64001; }],
    ['invalid audit timestamp', (value) => { value.active.activated_at = 'tomorrow-ish'; }]
  ])('rejects %s', (_label, mutate) => {
    const value = catalog();
    mutate(value);
    expect(() => normalizeAssistantProfileCatalog(value)).toThrow(
      'Assistant profile response was invalid'
    );
  });

  test('loads the tenant catalog from the admin endpoint', async () => {
    api.get.mockResolvedValue({ data: catalog() });

    await expect(fetchAssistantProfileCatalog()).resolves.toMatchObject({
      latestVersion: 1
    });
    expect(api.get).toHaveBeenCalledWith('/v1/admin/assistant-profiles');
  });

  test('previews an allowlisted typed draft', async () => {
    api.post.mockResolvedValue({
      data: {
        prompt_template_version: 'enterprise-chat.v1',
        system_instruction: 'You are Acme Guide.'
      }
    });

    await expect(previewAssistantProfile(draft)).resolves.toEqual({
      promptTemplateVersion: 'enterprise-chat.v1',
      systemInstruction: 'You are Acme Guide.'
    });
    expect(api.post).toHaveBeenCalledWith(
      '/v1/admin/assistant-profiles/preview',
      {
        prompt_template_version: 'enterprise-chat.v1',
        assistant_name: 'Acme Guide',
        organization_name: 'Acme Corp',
        response_tone: 'friendly',
        expertise: 'product_specialist',
        communication_style: 'concise_supportive',
        context_character_limit: 2048
      }
    );
  });

  test('rejects malformed or unexpectedly extended preview responses', () => {
    expect(() =>
      normalizeAssistantProfilePreview({
        prompt_template_version: 'enterprise-chat.v1',
        system_instruction: 'You are Acme Guide.',
        hidden: 'not allowed'
      })
    ).toThrow('Assistant profile response was invalid');
  });

  test('creates a non-active version and then refreshes the catalog', async () => {
    const nextCatalog = catalog({
      activeVersion: 1,
      versions: [profile(1), profile(2)]
    });
    api.post.mockResolvedValue({ data: profile(2) });
    api.get.mockResolvedValue({ data: nextCatalog });

    const result = await createAssistantProfileVersion(profileId, {
      ...draft,
      expectedLatestVersion: 1
    });

    expect(api.post).toHaveBeenCalledWith(
      `/v1/admin/assistant-profiles/${profileId}/versions`,
      expect.objectContaining({ expected_latest_version: 1 })
    );
    expect(api.get).toHaveBeenCalledTimes(1);
    expect(result).toMatchObject({
      profile: { version: 2 },
      catalog: {
        active: { profileVersion: 1 },
        latestVersion: 2
      }
    });
  });

  test('activates with the assignment revision and validates the fresh catalog', async () => {
    api.post.mockResolvedValue({
      data: catalog({ activeVersion: 2, versions: [profile(1), profile(2)] })
    });

    await expect(
      activateAssistantProfileVersion(profileId, 2, 0)
    ).resolves.toMatchObject({ active: { profileVersion: 2, revision: 1 } });
    expect(api.post).toHaveBeenCalledWith(
      `/v1/admin/assistant-profiles/${profileId}/versions/2/activate`,
      { expected_revision: 0 }
    );
  });

  test('rejects an invalid draft before making a request', async () => {
    await expect(
      previewAssistantProfile({ ...draft, assistantName: ' Bad name' })
    ).rejects.toThrow('Assistant profile draft was invalid');
    expect(api.post).not.toHaveBeenCalled();
  });
});
