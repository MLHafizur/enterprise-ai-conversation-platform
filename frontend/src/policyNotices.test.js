import api from './api';
import {
  attachInputPolicyNotices,
  fetchInputPolicyNotices,
  normalizeInputPolicyNotices
} from './policyNotices';

jest.mock('./api', () => ({
  __esModule: true,
  default: { get: jest.fn() }
}));

const MESSAGE_ID = '44444444-4444-4444-8444-444444444444';

const payload = () => ({
  items: [
    {
      input_message_id: MESSAGE_ID,
      action: 'escalate',
      findings: [
        {
          severity: 'medium',
          safe_message: 'This request requires human review.'
        }
      ],
      assessed_at: '2026-11-16T20:00:00Z'
    }
  ]
});

describe('input policy notice adapter', () => {
  beforeEach(() => jest.clearAllMocks());

  test('normalizes the narrow safe contract and fetches the owned endpoint', async () => {
    api.get.mockResolvedValue({ data: payload() });

    await expect(fetchInputPolicyNotices('conversation/id')).resolves.toEqual([
      {
        inputMessageId: MESSAGE_ID,
        action: 'escalate',
        findings: [
          {
            severity: 'medium',
            safeMessage: 'This request requires human review.'
          }
        ],
        assessedAt: '2026-11-16T20:00:00Z'
      }
    ]);
    expect(api.get).toHaveBeenCalledWith(
      '/v1/conversations/conversation%2Fid/input-policy-notices'
    );
  });

  test.each([
    ['raw content', { content: 'secret prompt' }],
    ['finding code', { code: 'internal.rule' }],
    ['risk score', { risk_score: 0.9 }],
    ['run identifier', { agent_run_id: MESSAGE_ID }],
    ['policy version', { policy_version: 'internal.v1' }]
  ])('rejects unexpected %s fields', (_label, extra) => {
    const value = payload();
    Object.assign(value.items[0], extra);
    expect(() => normalizeInputPolicyNotices(value)).toThrow(
      'Input policy notice response was invalid'
    );
  });

  test('rejects allow decisions and unsafe finding shapes', () => {
    const allow = payload();
    allow.items[0].action = 'allow';
    expect(() => normalizeInputPolicyNotices(allow)).toThrow();

    const codedFinding = payload();
    codedFinding.items[0].findings[0].code = 'secret.rule';
    expect(() => normalizeInputPolicyNotices(codedFinding)).toThrow();
  });

  test('attaches only to the exact persisted user message id', () => {
    const [notice] = normalizeInputPolicyNotices(payload());
    const messages = [
      { id: 'other-message', role: 'user', content: 'Other' },
      { id: MESSAGE_ID, role: 'assistant', content: 'Assistant collision' },
      { id: MESSAGE_ID, role: 'user', content: 'Original input' }
    ];

    const attached = attachInputPolicyNotices(messages, [notice]);

    expect(attached[0].policyNotices).toEqual([]);
    expect(attached[1].policyNotices).toEqual([]);
    expect(attached[2].policyNotices).toEqual([notice]);
    expect(messages[2]).not.toHaveProperty('policyNotices');
  });

  test('removes stale local notices when the durable snapshot has none', () => {
    const result = attachInputPolicyNotices(
      [
        {
          id: MESSAGE_ID,
          role: 'user',
          policyNotices: [{ action: 'block' }]
        }
      ],
      []
    );

    expect(result[0].policyNotices).toEqual([]);
  });
});
