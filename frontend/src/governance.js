import api from './api';

export const GOVERNANCE_READER_ROLES = Object.freeze(['admin', 'agent']);
export const DEFAULT_GOVERNANCE_LIMIT = 25;
export const MAX_GOVERNANCE_LIMIT = 100;

const ACTIONS = new Set(['allow', 'block', 'escalate']);
const SEVERITIES = new Set(['info', 'low', 'medium', 'high', 'critical']);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const POLICY_VERSION_PATTERN = /^[a-z0-9][a-z0-9._-]*$/;
const FINDING_CODE_PATTERN = /^[a-z][a-z0-9_.-]*$/;

const invalidSnapshot = () => new Error('Governance response was invalid');

const requireObject = (value) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidSnapshot();
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
    throw invalidSnapshot();
  }
  return object;
};

const requireNonnegativeInteger = (value) => {
  if (!Number.isSafeInteger(value) || value < 0) {
    throw invalidSnapshot();
  }
  return value;
};

const normalizeFinding = (value) => {
  const finding = requireExactKeys(value, ['code', 'severity', 'safe_message']);
  if (
    typeof finding.code !== 'string' ||
    !FINDING_CODE_PATTERN.test(finding.code) ||
    !SEVERITIES.has(finding.severity) ||
    typeof finding.safe_message !== 'string' ||
    !finding.safe_message.trim() ||
    finding.safe_message !== finding.safe_message.trim()
  ) {
    throw invalidSnapshot();
  }

  return Object.freeze({
    code: finding.code,
    severity: finding.severity,
    safeMessage: finding.safe_message
  });
};

const normalizeAssessment = (value) => {
  const assessment = requireExactKeys(value, [
    'agent_run_id',
    'conversation_id',
    'policy_version',
    'action',
    'risk_score',
    'findings',
    'assessed_at'
  ]);
  const assessedAt = new Date(assessment.assessed_at);

  if (
    typeof assessment.agent_run_id !== 'string' ||
    !UUID_PATTERN.test(assessment.agent_run_id) ||
    typeof assessment.conversation_id !== 'string' ||
    !UUID_PATTERN.test(assessment.conversation_id) ||
    typeof assessment.policy_version !== 'string' ||
    !POLICY_VERSION_PATTERN.test(assessment.policy_version) ||
    !ACTIONS.has(assessment.action) ||
    typeof assessment.risk_score !== 'number' ||
    !Number.isFinite(assessment.risk_score) ||
    assessment.risk_score < 0 ||
    assessment.risk_score > 1 ||
    !Array.isArray(assessment.findings) ||
    assessment.findings.length > 32 ||
    typeof assessment.assessed_at !== 'string' ||
    Number.isNaN(assessedAt.getTime())
  ) {
    throw invalidSnapshot();
  }

  return Object.freeze({
    agentRunId: assessment.agent_run_id,
    conversationId: assessment.conversation_id,
    policyVersion: assessment.policy_version,
    action: assessment.action,
    riskScore: assessment.risk_score,
    findings: Object.freeze(assessment.findings.map(normalizeFinding)),
    assessedAt: assessment.assessed_at
  });
};

export const normalizeInputPolicyGovernance = (value) => {
  const snapshot = requireExactKeys(value, ['counts', 'recent']);
  const counts = requireExactKeys(snapshot.counts, [
    'allow',
    'block',
    'escalate',
    'total'
  ]);
  const normalizedCounts = Object.freeze({
    allow: requireNonnegativeInteger(counts.allow),
    block: requireNonnegativeInteger(counts.block),
    escalate: requireNonnegativeInteger(counts.escalate),
    total: requireNonnegativeInteger(counts.total)
  });

  if (
    normalizedCounts.allow + normalizedCounts.block + normalizedCounts.escalate !==
      normalizedCounts.total ||
    !Array.isArray(snapshot.recent) ||
    snapshot.recent.length > MAX_GOVERNANCE_LIMIT
  ) {
    throw invalidSnapshot();
  }

  return Object.freeze({
    counts: normalizedCounts,
    recent: Object.freeze(snapshot.recent.map(normalizeAssessment))
  });
};

export const canReadGovernance = (roles) =>
  Array.isArray(roles) &&
  roles.length > 0 &&
  roles.every(
    (role) =>
      typeof role === 'string' && role.length > 0 && role === role.trim()
  ) &&
  roles.some((role) => GOVERNANCE_READER_ROLES.includes(role));

export const fetchInputPolicyGovernance = async (
  limit = DEFAULT_GOVERNANCE_LIMIT
) => {
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > MAX_GOVERNANCE_LIMIT) {
    throw new Error('Governance limit must be between 1 and 100');
  }

  const response = await api.get('/v1/governance/input-policy', {
    params: { limit }
  });
  return normalizeInputPolicyGovernance(response.data);
};
