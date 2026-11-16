import api from './api';

export const HUMAN_REVIEW_STATUSES = Object.freeze(['pending', 'resolved']);
export const HUMAN_REVIEW_RESOLUTIONS = Object.freeze(['confirmed', 'dismissed']);
export const DEFAULT_HUMAN_REVIEW_LIMIT = 50;
export const MAX_HUMAN_REVIEW_LIMIT = 100;

const STATUS_VALUES = new Set(HUMAN_REVIEW_STATUSES);
const RESOLUTION_VALUES = new Set(HUMAN_REVIEW_RESOLUTIONS);
const SEVERITY_VALUES = new Set(['info', 'low', 'medium', 'high', 'critical']);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const POLICY_VERSION_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const FINDING_CODE_PATTERN = /^[a-z][a-z0-9_.-]*$/;
const MAX_FINDINGS = 32;
const MAX_INPUT_LENGTH = 16000;

const invalidResponse = () => new Error('Human review response was invalid');

const requireObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponse();
  }
  return value;
};

const requireExactKeys = (value, allowedKeys) => {
  const object = requireObject(value);
  const keys = Object.keys(object).sort();
  const expected = [...allowedKeys].sort();

  if (
    keys.length !== expected.length ||
    keys.some((key, index) => key !== expected[index])
  ) {
    throw invalidResponse();
  }
  return object;
};

const requireUuid = (value) => {
  if (typeof value !== 'string' || !UUID_PATTERN.test(value)) {
    throw invalidResponse();
  }
  return value;
};

const requireTimestamp = (value) => {
  if (typeof value !== 'string' || Number.isNaN(new Date(value).getTime())) {
    throw invalidResponse();
  }
  return value;
};

const normalizeFinding = (value) => {
  const finding = requireExactKeys(value, ['code', 'severity', 'safe_message']);
  if (
    typeof finding.code !== 'string' ||
    !FINDING_CODE_PATTERN.test(finding.code) ||
    !SEVERITY_VALUES.has(finding.severity) ||
    typeof finding.safe_message !== 'string' ||
    !finding.safe_message.trim() ||
    finding.safe_message !== finding.safe_message.trim() ||
    finding.safe_message.length > 256
  ) {
    throw invalidResponse();
  }

  return Object.freeze({
    code: finding.code,
    severity: finding.severity,
    safeMessage: finding.safe_message
  });
};

const normalizeReview = (value, includeInputContent) => {
  const keys = [
    'agent_run_id',
    'conversation_id',
    'input_message_id',
    'status',
    'version',
    'policy_version',
    'risk_score',
    'findings',
    'created_at',
    'resolved_at',
    'resolution'
  ];
  if (includeInputContent) {
    keys.push('input_content');
  }
  const review = requireExactKeys(value, keys);
  const createdAt = requireTimestamp(review.created_at);

  if (
    !STATUS_VALUES.has(review.status) ||
    !Number.isSafeInteger(review.version) ||
    review.version < 0 ||
    typeof review.policy_version !== 'string' ||
    !POLICY_VERSION_PATTERN.test(review.policy_version) ||
    typeof review.risk_score !== 'number' ||
    !Number.isFinite(review.risk_score) ||
    review.risk_score < 0 ||
    review.risk_score > 1 ||
    !Array.isArray(review.findings) ||
    review.findings.length < 1 ||
    review.findings.length > MAX_FINDINGS
  ) {
    throw invalidResponse();
  }

  const pending = review.status === 'pending';
  if (
    (pending && (review.resolved_at !== null || review.resolution !== null)) ||
    (!pending &&
      (review.resolved_at === null || !RESOLUTION_VALUES.has(review.resolution)))
  ) {
    throw invalidResponse();
  }

  const resolvedAt = pending ? null : requireTimestamp(review.resolved_at);
  if (resolvedAt && new Date(resolvedAt).getTime() < new Date(createdAt).getTime()) {
    throw invalidResponse();
  }

  if (
    includeInputContent &&
    (typeof review.input_content !== 'string' ||
      !review.input_content.trim() ||
      review.input_content.length > MAX_INPUT_LENGTH)
  ) {
    throw invalidResponse();
  }

  const normalized = {
    agentRunId: requireUuid(review.agent_run_id),
    conversationId: requireUuid(review.conversation_id),
    inputMessageId: requireUuid(review.input_message_id),
    status: review.status,
    version: review.version,
    policyVersion: review.policy_version,
    riskScore: review.risk_score,
    findings: Object.freeze(review.findings.map(normalizeFinding)),
    createdAt,
    resolvedAt,
    resolution: review.resolution
  };
  if (includeInputContent) {
    normalized.inputContent = review.input_content;
  }
  return Object.freeze(normalized);
};

export const normalizeHumanReviewCollection = (value) => {
  const collection = requireExactKeys(value, ['items']);
  if (!Array.isArray(collection.items) || collection.items.length > MAX_HUMAN_REVIEW_LIMIT) {
    throw invalidResponse();
  }

  const items = collection.items.map((item) => normalizeReview(item, false));
  const identifiers = new Set(items.map((item) => item.agentRunId));
  if (identifiers.size !== items.length) {
    throw invalidResponse();
  }

  items.sort((left, right) => {
    const timestampDifference =
      new Date(left.createdAt).getTime() - new Date(right.createdAt).getTime();
    return timestampDifference || left.agentRunId.localeCompare(right.agentRunId);
  });
  return Object.freeze(items);
};

export const normalizeHumanReviewDetail = (value) => normalizeReview(value, true);

const requireStatus = (status) => {
  if (!STATUS_VALUES.has(status)) {
    throw new Error('Human review status is invalid');
  }
  return status;
};

const requireAgentRunId = (agentRunId) => {
  if (typeof agentRunId !== 'string' || !UUID_PATTERN.test(agentRunId)) {
    throw new Error('Human review agent run ID is invalid');
  }
  return agentRunId;
};

export const fetchHumanReviews = async ({
  status = 'pending',
  limit = DEFAULT_HUMAN_REVIEW_LIMIT
} = {}) => {
  requireStatus(status);
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_HUMAN_REVIEW_LIMIT) {
    throw new Error('Human review limit must be between 1 and 100');
  }

  const response = await api.get('/v1/governance/human-reviews', {
    params: { status, limit }
  });
  return normalizeHumanReviewCollection(response.data);
};

export const fetchHumanReview = async (agentRunId) => {
  requireAgentRunId(agentRunId);
  const response = await api.get(
    `/v1/governance/human-reviews/${encodeURIComponent(agentRunId)}`
  );
  return normalizeHumanReviewDetail(response.data);
};

export const resolveHumanReview = async (
  agentRunId,
  { expectedVersion, resolution }
) => {
  requireAgentRunId(agentRunId);
  if (!Number.isSafeInteger(expectedVersion) || expectedVersion < 0) {
    throw new Error('Human review expected version is invalid');
  }
  if (!RESOLUTION_VALUES.has(resolution)) {
    throw new Error('Human review resolution is invalid');
  }

  const response = await api.post(
    `/v1/governance/human-reviews/${encodeURIComponent(agentRunId)}/resolve`,
    { expected_version: expectedVersion, resolution }
  );
  return normalizeHumanReviewDetail(response.data);
};
