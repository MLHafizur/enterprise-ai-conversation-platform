import api from './api';

const NOTICE_ACTIONS = new Set(['block', 'escalate']);
const SEVERITIES = new Set(['info', 'low', 'medium', 'high', 'critical']);
const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_FINDINGS = 32;
const MAX_SAFE_MESSAGE_LENGTH = 256;

const invalidResponse = () => new Error('Input policy notice response was invalid');

const requireExactKeys = (value, expectedKeys) => {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw invalidResponse();
  }
  const actual = Object.keys(value).sort();
  const expected = [...expectedKeys].sort();
  if (
    actual.length !== expected.length ||
    actual.some((key, index) => key !== expected[index])
  ) {
    throw invalidResponse();
  }
  return value;
};

const normalizeFinding = (value) => {
  const finding = requireExactKeys(value, ['severity', 'safe_message']);
  if (
    !SEVERITIES.has(finding.severity) ||
    typeof finding.safe_message !== 'string' ||
    !finding.safe_message.trim() ||
    finding.safe_message !== finding.safe_message.trim() ||
    finding.safe_message.length > MAX_SAFE_MESSAGE_LENGTH
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    severity: finding.severity,
    safeMessage: finding.safe_message
  });
};

const normalizeNotice = (value) => {
  const notice = requireExactKeys(value, [
    'input_message_id',
    'action',
    'findings',
    'assessed_at'
  ]);
  const assessedAt = new Date(notice.assessed_at);
  if (
    typeof notice.input_message_id !== 'string' ||
    !UUID_PATTERN.test(notice.input_message_id) ||
    !NOTICE_ACTIONS.has(notice.action) ||
    !Array.isArray(notice.findings) ||
    notice.findings.length < 1 ||
    notice.findings.length > MAX_FINDINGS ||
    typeof notice.assessed_at !== 'string' ||
    Number.isNaN(assessedAt.getTime())
  ) {
    throw invalidResponse();
  }
  return Object.freeze({
    inputMessageId: notice.input_message_id,
    action: notice.action,
    findings: Object.freeze(notice.findings.map(normalizeFinding)),
    assessedAt: notice.assessed_at
  });
};

export const normalizeInputPolicyNotices = (value) => {
  const collection = requireExactKeys(value, ['items']);
  if (!Array.isArray(collection.items)) {
    throw invalidResponse();
  }
  return Object.freeze(collection.items.map(normalizeNotice));
};

export const fetchInputPolicyNotices = async (conversationId) => {
  const response = await api.get(
    `/v1/conversations/${encodeURIComponent(conversationId)}/input-policy-notices`
  );
  return normalizeInputPolicyNotices(response.data);
};

export const attachInputPolicyNotices = (messages, notices) => {
  const byMessage = new Map();
  notices.forEach((notice) => {
    const current = byMessage.get(notice.inputMessageId) || [];
    byMessage.set(notice.inputMessageId, [...current, notice]);
  });

  return messages.map((message) => {
    const { policyNotices: _staleNotices, ...withoutStaleNotices } = message;
    const matching =
      message.role === 'user' ? byMessage.get(message.id) || [] : [];
    return {
      ...withoutStaleNotices,
      policyNotices: Object.freeze(matching)
    };
  });
};
