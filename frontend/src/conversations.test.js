import api from './api';
import {
  createConversation,
  fetchAllMessages,
  fetchConversationSnapshot,
  mergeMessages,
  normalizeMessage,
  submitTurn
} from './conversations';

jest.mock('./api', () => ({
  __esModule: true,
  default: {
    get: jest.fn(),
    post: jest.fn(),
    put: jest.fn()
  }
}));

describe('conversation API adapter', () => {
  beforeEach(() => {
    jest.clearAllMocks();
  });

  test('creates a client-addressed conversation with PUT', async () => {
    api.put.mockResolvedValue({
      data: { conversation_id: 'conversation-id', replayed: false }
    });

    await expect(createConversation('conversation-id')).resolves.toEqual({
      conversation_id: 'conversation-id',
      replayed: false
    });
    expect(api.put).toHaveBeenCalledWith('/v1/conversations/conversation-id');
  });

  test('reads every ascending message page', async () => {
    api.get
      .mockResolvedValueOnce({
        data: { items: [{ id: 'first', content: 'One' }], next_cursor: 1 }
      })
      .mockResolvedValueOnce({
        data: { items: [{ id: 'second', content: 'Two' }], next_cursor: null }
      });

    await expect(fetchAllMessages('conversation-id')).resolves.toHaveLength(2);
    expect(api.get).toHaveBeenNthCalledWith(
      2,
      '/v1/conversations/conversation-id/messages',
      { params: { limit: 100, cursor: 1 } }
    );
  });

  test('submits a turn with the stable idempotency key', async () => {
    api.post.mockResolvedValue({ data: { agent_run_id: 'run-id' } });

    await submitTurn('conversation-id', 'Hello', 'turn-stable-key');
    expect(api.post).toHaveBeenCalledWith(
      '/v1/conversations/conversation-id/turns',
      { content: 'Hello' },
      { headers: { 'Idempotency-Key': 'turn-stable-key' } }
    );
  });

  test('loads metadata and messages as one snapshot', async () => {
    api.get.mockImplementation((url) => {
      if (url.endsWith('/messages')) {
        return Promise.resolve({ data: { items: [], next_cursor: null } });
      }
      return Promise.resolve({ data: { id: 'conversation-id', version: 1 } });
    });

    await expect(fetchConversationSnapshot('conversation-id')).resolves.toEqual({
      conversation: { id: 'conversation-id', version: 1 },
      messages: []
    });
  });
});

describe('message reconciliation', () => {
  test('normalizes target created_at timestamps', () => {
    expect(
      normalizeMessage(
        {
          id: 'message-id',
          role: 'assistant',
          content: 'Done',
          created_at: '2026-01-01T00:00:00Z',
          sequence: 2
        },
        'conversation-id'
      )
    ).toMatchObject({
      id: 'message-id',
      conversation_id: 'conversation-id',
      timestamp: '2026-01-01T00:00:00Z',
      sequence: 2
    });
  });

  test('replaces an optimistic message with the persisted representation', () => {
    const result = mergeMessages(
      [
        {
          id: 'message-id',
          role: 'user',
          content: 'Hello',
          timestamp: '2026-01-01T00:00:00Z',
          optimistic: true
        }
      ],
      [
        {
          id: 'message-id',
          role: 'user',
          content: 'Hello',
          created_at: '2026-01-01T00:00:01Z',
          sequence: 1
        }
      ],
      'conversation-id'
    );

    expect(result).toHaveLength(1);
    expect(result[0]).toMatchObject({
      id: 'message-id',
      sequence: 1,
      optimistic: false
    });
  });
});
