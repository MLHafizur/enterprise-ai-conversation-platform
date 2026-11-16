import api from './api';
import {
  fetchHumanReview,
  fetchHumanReviews,
  normalizeHumanReviewCollection,
  normalizeHumanReviewDetail,
  resolveHumanReview
} from './reviews';

jest.mock('./api', () => ({
  __esModule: true,
  default: { get: jest.fn(), post: jest.fn() }
}));

const runOne = '11111111-1111-4111-8111-111111111111';
const runTwo = '22222222-2222-4222-8222-222222222222';
const conversationId = '33333333-3333-4333-8333-333333333333';
const inputMessageId = '44444444-4444-4444-8444-444444444444';

const review = ({
  agentRunId = runOne,
  status = 'pending',
  createdAt = '2026-11-16T15:30:00Z',
  inputContent
} = {}) => ({
  agent_run_id: agentRunId,
  conversation_id: conversationId,
  input_message_id: inputMessageId,
  status,
  version: status === 'pending' ? 0 : 1,
  policy_version: 'conversation-input.v1',
  risk_score: 0.72,
  findings: [
    {
      code: 'conversation.legal_review',
      severity: 'medium',
      safe_message: 'A qualified reviewer must handle this legal request.'
    }
  ],
  created_at: createdAt,
  resolved_at: status === 'resolved' ? '2026-11-16T16:00:00Z' : null,
  resolution: status === 'resolved' ? 'confirmed' : null,
  ...(inputContent === undefined ? {} : { input_content: inputContent })
});

describe('human review API adapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('normalizes and orders list items oldest first', () => {
    const items = normalizeHumanReviewCollection({
      items: [
        review({ agentRunId: runTwo, createdAt: '2026-11-16T16:00:00Z' }),
        review({ agentRunId: runOne, createdAt: '2026-11-16T15:00:00Z' })
      ]
    });

    expect(items.map((item) => item.agentRunId)).toEqual([runOne, runTwo]);
    expect(items[0]).toMatchObject({
      status: 'pending',
      policyVersion: 'conversation-input.v1',
      inputMessageId,
      findings: [
        {
          code: 'conversation.legal_review',
          safeMessage: 'A qualified reviewer must handle this legal request.'
        }
      ]
    });
  });

  test('normalizes exact input content only on a detail response', () => {
    expect(
      normalizeHumanReviewDetail(review({ inputContent: 'Please review this contract.' }))
    ).toMatchObject({ inputContent: 'Please review this contract.' });

    expect(() =>
      normalizeHumanReviewCollection({
        items: [review({ inputContent: 'must not be in the queue response' })]
      })
    ).toThrow('Human review response was invalid');
  });

  test.each([
    ['pending with a resolution', { resolution: 'dismissed' }],
    ['resolved without a timestamp', { status: 'resolved', resolution: 'confirmed', resolved_at: null }],
    ['unknown status', { status: 'in_review' }],
    ['out-of-range risk', { risk_score: 2 }],
    ['private reviewer identity', { resolved_by: '55555555-5555-4555-8555-555555555555' }],
    ['unexpected raw field', { content: 'unapproved duplicate content' }]
  ])('rejects %s', (_label, replacement) => {
    expect(() =>
      normalizeHumanReviewCollection({ items: [{ ...review(), ...replacement }] })
    ).toThrow('Human review response was invalid');
  });

  test('loads a bounded status-filtered queue', async () => {
    api.get.mockResolvedValue({ data: { items: [review()] } });

    await expect(fetchHumanReviews({ status: 'pending', limit: 20 })).resolves.toHaveLength(1);
    expect(api.get).toHaveBeenCalledWith('/v1/governance/human-reviews', {
      params: { status: 'pending', limit: 20 }
    });
  });

  test('loads one detail using its stable agent-run identifier', async () => {
    api.get.mockResolvedValue({
      data: review({ inputContent: 'Please review this contract.' })
    });

    await expect(fetchHumanReview(runOne)).resolves.toMatchObject({
      agentRunId: runOne,
      inputContent: 'Please review this contract.'
    });
    expect(api.get).toHaveBeenCalledWith(`/v1/governance/human-reviews/${runOne}`);
  });

  test('resolves with optimistic version and an allow-listed disposition', async () => {
    api.post.mockResolvedValue({
      data: review({
        status: 'resolved',
        inputContent: 'Please review this contract.'
      })
    });

    await expect(
      resolveHumanReview(runOne, { expectedVersion: 0, resolution: 'confirmed' })
    ).resolves.toMatchObject({ status: 'resolved', resolution: 'confirmed' });
    expect(api.post).toHaveBeenCalledWith(
      `/v1/governance/human-reviews/${runOne}/resolve`,
      { expected_version: 0, resolution: 'confirmed' }
    );
  });

  test('rejects invalid filters and commands before any request', async () => {
    await expect(fetchHumanReviews({ status: 'all' })).rejects.toThrow(
      'Human review status is invalid'
    );
    await expect(
      resolveHumanReview(runOne, { expectedVersion: 0, resolution: 'approve' })
    ).rejects.toThrow('Human review resolution is invalid');
    expect(api.get).not.toHaveBeenCalled();
    expect(api.post).not.toHaveBeenCalled();
  });
});
