import api from './api';

const fallbackUuid = () => {
  const bytes = new Uint8Array(16);
  if (typeof crypto !== 'undefined' && crypto.getRandomValues) {
    crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }

  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
  return [value.slice(0, 8), value.slice(8, 12), value.slice(12, 16), value.slice(16, 20), value.slice(20)].join('-');
};

export const createClientUuid = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return fallbackUuid();
};

const messageIdentity = (message, conversationId, index) =>
  message?.id ||
  message?.message_id ||
  `${conversationId}_${message?.role || 'message'}_${message?.sequence ?? message?.timestamp ?? message?.created_at ?? index}`;

export const normalizeMessage = (message, conversationId, index = 0) => ({
  ...message,
  id: messageIdentity(message, conversationId, index),
  conversation_id: message?.conversation_id || conversationId,
  role: message?.role || 'assistant',
  content: typeof message?.content === 'string' ? message.content : '',
  timestamp: message?.timestamp || message?.created_at || new Date().toISOString(),
  sequence: Number.isInteger(message?.sequence) ? message.sequence : null
});

const compareMessages = (left, right) => {
  if (left.sequence !== null && right.sequence !== null) {
    return left.sequence - right.sequence;
  }
  if (left.sequence !== null) {
    return -1;
  }
  if (right.sequence !== null) {
    return 1;
  }
  return new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
};

export const mergeMessages = (currentMessages, incomingMessages, conversationId) => {
  const merged = new Map();

  currentMessages.forEach((message, index) => {
    const normalized = normalizeMessage(message, conversationId, index);
    merged.set(normalized.id, normalized);
  });

  incomingMessages.forEach((message, index) => {
    const normalized = normalizeMessage(message, conversationId, index);
    const existing = merged.get(normalized.id);
    merged.set(normalized.id, {
      ...existing,
      ...normalized,
      optimistic: false
    });
  });

  return Array.from(merged.values()).sort(compareMessages);
};

export const normalizeConversation = (source = {}, fallback = {}) => {
  const conversationId = source.conversation_id || source.id || fallback.id;
  const sourceMessages = Array.isArray(source.messages) ? source.messages : [];
  const fallbackMessages = Array.isArray(fallback.messages) ? fallback.messages : [];

  return {
    ...fallback,
    ...source,
    id: conversationId,
    title: source.title || source.metadata?.title || fallback.title || 'New Conversation',
    created_at: source.created_at || fallback.created_at || new Date().toISOString(),
    messages: mergeMessages(fallbackMessages, sourceMessages, conversationId)
  };
};

export const createConversation = async (conversationId) => {
  const response = await api.put(
    `/v1/conversations/${encodeURIComponent(conversationId)}`
  );
  return response.data;
};

export const submitTurn = async (
  conversationId,
  content,
  idempotencyKey
) => {
  const response = await api.post(
    `/v1/conversations/${encodeURIComponent(conversationId)}/turns`,
    { content },
    { headers: { 'Idempotency-Key': idempotencyKey } }
  );
  return response.data;
};

export const fetchAgentRun = async (agentRunId) => {
  const response = await api.get(
    `/v1/agent-runs/${encodeURIComponent(agentRunId)}`
  );
  return response.data;
};

export const fetchAllMessages = async (conversationId) => {
  const messages = [];
  const seenCursors = new Set();
  let cursor = null;

  for (let page = 0; page < 100; page += 1) {
    const params = { limit: 100 };
    if (cursor !== null) {
      params.cursor = cursor;
    }

    const response = await api.get(
      `/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      { params }
    );
    const payload = response.data;
    const items = Array.isArray(payload)
      ? payload
      : payload?.items || payload?.messages || [];
    messages.push(...items);

    const nextCursor = Array.isArray(payload) ? null : payload?.next_cursor;
    if (nextCursor === null || nextCursor === undefined || seenCursors.has(nextCursor)) {
      break;
    }
    seenCursors.add(nextCursor);
    cursor = nextCursor;
  }

  return messages;
};

export const fetchConversationSnapshot = async (conversationId) => {
  const encodedId = encodeURIComponent(conversationId);
  const [conversationResponse, messages] = await Promise.all([
    api.get(`/v1/conversations/${encodedId}`),
    fetchAllMessages(conversationId)
  ]);

  return {
    conversation: conversationResponse.data,
    messages
  };
};
