import api from './api';

export const ASSISTANT_PROFILE_ADMIN_ROLES = Object.freeze(['admin']);
export const ASSISTANT_PROFILE_TONES = Object.freeze([
  'professional',
  'friendly'
]);
export const ASSISTANT_PROFILE_EXPERTISE_LEVELS = Object.freeze([
  'senior_engineer',
  'product_specialist'
]);
export const ASSISTANT_PROFILE_COMMUNICATION_STYLES = Object.freeze([
  'direct_helpful',
  'concise_supportive'
]);
export const ASSISTANT_PROFILE_TEMPLATE_VERSIONS = Object.freeze([
  'enterprise-chat.v1'
]);
export const MAX_ASSISTANT_PROFILE_CONTEXT_CHARACTERS = 64000;

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SAFE_NAME_PATTERN = /^[A-Za-z0-9][A-Za-z0-9 .&()'_-]{0,127}$/;
const TEMPLATE_VERSION_PATTERN = /^[a-z0-9][a-z0-9._-]{0,63}$/;
const MAX_PROFILE_VERSIONS = 100;
const profileKey = (profile) => `${profile.profileId}:${profile.version}`;

const invalidResponse = () => new Error('Assistant profile response was invalid');
const invalidDraft = () => new Error('Assistant profile draft was invalid');

const requireObject = (value, errorFactory = invalidResponse) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw errorFactory();
  }
  return value;
};

const requireExactKeys = (value, allowedKeys, errorFactory = invalidResponse) => {
  const object = requireObject(value, errorFactory);
  const keys = Object.keys(object).sort();
  const expected = [...allowedKeys].sort();
  if (
    keys.length !== expected.length ||
    keys.some((key, index) => key !== expected[index])
  ) {
    throw errorFactory();
  }
  return object;
};

const requireUuid = (value, errorFactory = invalidResponse) => {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    throw errorFactory();
  }
  return value;
};

const requirePositiveInteger = (value, errorFactory = invalidResponse) => {
  if (!Number.isSafeInteger(value) || value <= 0) {
    throw errorFactory();
  }
  return value;
};

const requireNonnegativeInteger = (value, errorFactory = invalidResponse) => {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw errorFactory();
  }
  return value;
};

const requireTimestamp = (value) => {
  if (typeof value !== 'string' || Number.isNaN(new Date(value).getTime())) {
    throw invalidResponse();
  }
  return value;
};

const requireSafeName = (value, errorFactory) => {
  if (
    typeof value !== 'string' ||
    value !== value.trim() ||
    !SAFE_NAME_PATTERN.test(value)
  ) {
    throw errorFactory();
  }
  return value;
};

const requireEnum = (value, allowed, errorFactory) => {
  if (!allowed.includes(value)) {
    throw errorFactory();
  }
  return value;
};

const normalizeProfile = (value) => {
  const profile = requireExactKeys(value, [
    'profile_id',
    'version',
    'prompt_template_version',
    'assistant_name',
    'organization_name',
    'response_tone',
    'expertise',
    'communication_style',
    'context_character_limit',
    'created_at',
    'created_by'
  ]);

  if (
    typeof profile.prompt_template_version !== 'string' ||
    !TEMPLATE_VERSION_PATTERN.test(profile.prompt_template_version) ||
    !ASSISTANT_PROFILE_TEMPLATE_VERSIONS.includes(
      profile.prompt_template_version
    ) ||
    !Number.isSafeInteger(profile.context_character_limit) ||
    profile.context_character_limit < 1 ||
    profile.context_character_limit > MAX_ASSISTANT_PROFILE_CONTEXT_CHARACTERS
  ) {
    throw invalidResponse();
  }

  return Object.freeze({
    profileId: requireUuid(profile.profile_id),
    version: requirePositiveInteger(profile.version),
    promptTemplateVersion: profile.prompt_template_version,
    assistantName: requireSafeName(profile.assistant_name, invalidResponse),
    organizationName: requireSafeName(
      profile.organization_name,
      invalidResponse
    ),
    responseTone: requireEnum(
      profile.response_tone,
      ASSISTANT_PROFILE_TONES,
      invalidResponse
    ),
    expertise: requireEnum(
      profile.expertise,
      ASSISTANT_PROFILE_EXPERTISE_LEVELS,
      invalidResponse
    ),
    communicationStyle: requireEnum(
      profile.communication_style,
      ASSISTANT_PROFILE_COMMUNICATION_STYLES,
      invalidResponse
    ),
    contextCharacterLimit: profile.context_character_limit,
    createdAt: requireTimestamp(profile.created_at),
    createdBy: requireUuid(profile.created_by)
  });
};

const normalizeActiveAssignment = (value) => {
  const active = requireExactKeys(value, [
    'profile_id',
    'profile_version',
    'revision',
    'activated_at',
    'activated_by'
  ]);
  return Object.freeze({
    profileId: requireUuid(active.profile_id),
    profileVersion: requirePositiveInteger(active.profile_version),
    revision: requireNonnegativeInteger(active.revision),
    activatedAt: requireTimestamp(active.activated_at),
    activatedBy: requireUuid(active.activated_by)
  });
};

export const normalizeAssistantProfileCatalog = (value) => {
  const catalog = requireExactKeys(value, [
    'active',
    'latest_version',
    'versions'
  ]);
  if (
    !Array.isArray(catalog.versions) ||
    catalog.versions.length < 1 ||
    catalog.versions.length > MAX_PROFILE_VERSIONS
  ) {
    throw invalidResponse();
  }

  const versions = catalog.versions.map(normalizeProfile);
  const references = new Set(
    versions.map((profile) => `${profile.profileId}:${profile.version}`)
  );
  if (references.size !== versions.length) {
    throw invalidResponse();
  }

  const active = normalizeActiveAssignment(catalog.active);
  if (
    !references.has(`${active.profileId}:${active.profileVersion}`) ||
    versions.some((profile) => profile.profileId !== active.profileId)
  ) {
    throw invalidResponse();
  }

  const latestVersion = requirePositiveInteger(catalog.latest_version);
  if (Math.max(...versions.map((profile) => profile.version)) !== latestVersion) {
    throw invalidResponse();
  }

  versions.sort((left, right) => {
    const versionDifference = right.version - left.version;
    return versionDifference || left.profileId.localeCompare(right.profileId);
  });

  return Object.freeze({
    active,
    latestVersion,
    versions: Object.freeze(versions)
  });
};

const DRAFT_KEYS = [
  'promptTemplateVersion',
  'assistantName',
  'organizationName',
  'responseTone',
  'expertise',
  'communicationStyle',
  'contextCharacterLimit'
];

export const normalizeAssistantProfileDraft = (value) => {
  const draft = requireExactKeys(value, DRAFT_KEYS, invalidDraft);
  if (
    typeof draft.promptTemplateVersion !== 'string' ||
    !ASSISTANT_PROFILE_TEMPLATE_VERSIONS.includes(
      draft.promptTemplateVersion
    ) ||
    !Number.isSafeInteger(draft.contextCharacterLimit) ||
    draft.contextCharacterLimit < 1 ||
    draft.contextCharacterLimit > MAX_ASSISTANT_PROFILE_CONTEXT_CHARACTERS
  ) {
    throw invalidDraft();
  }

  return Object.freeze({
    promptTemplateVersion: draft.promptTemplateVersion,
    assistantName: requireSafeName(draft.assistantName, invalidDraft),
    organizationName: requireSafeName(draft.organizationName, invalidDraft),
    responseTone: requireEnum(
      draft.responseTone,
      ASSISTANT_PROFILE_TONES,
      invalidDraft
    ),
    expertise: requireEnum(
      draft.expertise,
      ASSISTANT_PROFILE_EXPERTISE_LEVELS,
      invalidDraft
    ),
    communicationStyle: requireEnum(
      draft.communicationStyle,
      ASSISTANT_PROFILE_COMMUNICATION_STYLES,
      invalidDraft
    ),
    contextCharacterLimit: draft.contextCharacterLimit
  });
};

const draftRequestBody = (value) => {
  const draft = normalizeAssistantProfileDraft(value);
  return {
    prompt_template_version: draft.promptTemplateVersion,
    assistant_name: draft.assistantName,
    organization_name: draft.organizationName,
    response_tone: draft.responseTone,
    expertise: draft.expertise,
    communication_style: draft.communicationStyle,
    context_character_limit: draft.contextCharacterLimit
  };
};

export const normalizeAssistantProfilePreview = (value) => {
  const preview = requireExactKeys(value, [
    'prompt_template_version',
    'system_instruction'
  ]);
  if (
    typeof preview.prompt_template_version !== 'string' ||
    !ASSISTANT_PROFILE_TEMPLATE_VERSIONS.includes(
      preview.prompt_template_version
    ) ||
    typeof preview.system_instruction !== 'string' ||
    !preview.system_instruction.trim() ||
    preview.system_instruction.length > 10000
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    promptTemplateVersion: preview.prompt_template_version,
    systemInstruction: preview.system_instruction
  });
};

export const canManageAssistantProfiles = (roles) =>
  Array.isArray(roles) &&
  roles.length > 0 &&
  roles.every(
    (role) =>
      typeof role === 'string' && role.length > 0 && role === role.trim()
  ) &&
  roles.some((role) => ASSISTANT_PROFILE_ADMIN_ROLES.includes(role));

export const fetchAssistantProfileCatalog = async () => {
  const response = await api.get('/v1/admin/assistant-profiles');
  return normalizeAssistantProfileCatalog(response.data);
};

export const previewAssistantProfile = async (draft) => {
  const response = await api.post(
    '/v1/admin/assistant-profiles/preview',
    draftRequestBody(draft)
  );
  return normalizeAssistantProfilePreview(response.data);
};

export const createAssistantProfileVersion = async (
  profileId,
  { expectedLatestVersion, ...draft }
) => {
  requireUuid(profileId, invalidDraft);
  requirePositiveInteger(expectedLatestVersion, invalidDraft);
  const response = await api.post(
    `/v1/admin/assistant-profiles/${encodeURIComponent(profileId)}/versions`,
    {
      ...draftRequestBody(draft),
      expected_latest_version: expectedLatestVersion
    }
  );
  const createdProfile = normalizeProfile(response.data);
  if (
    createdProfile.profileId !== profileId ||
    createdProfile.version !== expectedLatestVersion + 1
  ) {
    throw invalidResponse();
  }

  const catalog = await fetchAssistantProfileCatalog();
  if (
    !catalog.versions.some(
      (profile) => profileKey(profile) === profileKey(createdProfile)
    )
  ) {
    throw invalidResponse();
  }
  return Object.freeze({ profile: createdProfile, catalog });
};

export const activateAssistantProfileVersion = async (
  profileId,
  version,
  expectedRevision
) => {
  requireUuid(profileId, invalidDraft);
  requirePositiveInteger(version, invalidDraft);
  requireNonnegativeInteger(expectedRevision, invalidDraft);
  const response = await api.post(
    `/v1/admin/assistant-profiles/${encodeURIComponent(
      profileId
    )}/versions/${version}/activate`,
    { expected_revision: expectedRevision }
  );
  return normalizeAssistantProfileCatalog(response.data);
};
