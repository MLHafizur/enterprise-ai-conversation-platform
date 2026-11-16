import api from './api';
import {
  canReadGovernance,
  fetchInputPolicyGovernance,
  normalizeInputPolicyGovernance
} from './governance';

jest.mock('./api', () => ({
  __esModule: true,
  default: { get: jest.fn() }
}));

const validSnapshot = () => ({
  counts: { allow: 4, block: 1, escalate: 1, total: 6 },
  recent: [
    {
      agent_run_id: '11111111-1111-4111-8111-111111111111',
      conversation_id: '22222222-2222-4222-8222-222222222222',
      policy_version: 'conversation-input.v1',
      action: 'block',
      risk_score: 0.9,
      findings: [
        {
          code: 'credentials.private_key',
          severity: 'critical',
          safe_message: 'The request appears to contain a private credential.'
        }
      ],
      assessed_at: '2026-11-16T15:30:00Z'
    }
  ]
});

describe('governance API adapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('recognizes only governance-reader roles', () => {
    expect(canReadGovernance(['admin'])).toBe(true);
    expect(canReadGovernance(['user', 'agent'])).toBe(true);
    expect(canReadGovernance(['user'])).toBe(false);
    expect(canReadGovernance(undefined)).toBe(false);
    expect(canReadGovernance(['admin', 42])).toBe(false);
    expect(canReadGovernance([' agent'])).toBe(false);
    expect(canReadGovernance([])).toBe(false);
  });

  test('accepts the complete safe severity vocabulary', () => {
    const snapshot = validSnapshot();
    snapshot.recent[0].findings[0].severity = 'info';

    expect(normalizeInputPolicyGovernance(snapshot).recent[0].findings[0]).toMatchObject({
      severity: 'info'
    });
  });

  test('loads and normalizes the bounded governance snapshot', async () => {
    api.get.mockResolvedValue({ data: validSnapshot() });

    await expect(fetchInputPolicyGovernance()).resolves.toMatchObject({
      counts: { total: 6 },
      recent: [
        {
          action: 'block',
          findings: [
            {
              code: 'credentials.private_key',
              safeMessage: 'The request appears to contain a private credential.'
            }
          ]
        }
      ]
    });
    expect(api.get).toHaveBeenCalledWith('/v1/governance/input-policy', {
      params: { limit: 25 }
    });
  });

  test('rejects impossible aggregate counts', () => {
    const snapshot = validSnapshot();
    snapshot.counts.total = 99;

    expect(() => normalizeInputPolicyGovernance(snapshot)).toThrow(
      'Governance response was invalid'
    );
  });

  test('fails closed when an assessment contains raw message content', () => {
    const snapshot = validSnapshot();
    snapshot.recent[0].content = 'raw user prompt must never reach this view';

    expect(() => normalizeInputPolicyGovernance(snapshot)).toThrow(
      'Governance response was invalid'
    );
  });

  test('fails closed when a finding contains an unapproved field', () => {
    const snapshot = validSnapshot();
    snapshot.recent[0].findings[0].matched_text = 'secret';

    expect(() => normalizeInputPolicyGovernance(snapshot)).toThrow(
      'Governance response was invalid'
    );
  });

  test.each([
    ['unknown action', { action: 'review' }],
    ['out-of-range risk score', { risk_score: 1.2 }],
    ['invalid timestamp', { assessed_at: 'not-a-date' }],
    ['invalid policy version', { policy_version: 'Unsafe Version' }]
  ])('rejects %s', (_label, replacement) => {
    const snapshot = validSnapshot();
    Object.assign(snapshot.recent[0], replacement);

    expect(() => normalizeInputPolicyGovernance(snapshot)).toThrow(
      'Governance response was invalid'
    );
  });

  test('rejects an unbounded request before calling the API', async () => {
    await expect(fetchInputPolicyGovernance(101)).rejects.toThrow(
      'Governance limit must be between 1 and 100'
    );
    expect(api.get).not.toHaveBeenCalled();
  });
});
