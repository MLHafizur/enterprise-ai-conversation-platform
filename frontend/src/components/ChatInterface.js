import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { PaperAirplaneIcon } from '@heroicons/react/24/outline';
import { toast } from 'react-toastify';

import { getApiErrorMessage } from '../api';
import {
  createClientUuid,
  fetchAgentRun,
  mergeMessages,
  normalizeMessage,
  submitTurn
} from '../conversations';
import { useWebSocket } from '../hooks/useWebSocket';
import MessageBubble from './MessageBubble';

const TERMINAL_RUN_STATUSES = new Set(['succeeded', 'failed', 'cancelled']);
const MAX_POLL_ATTEMPTS = 120;

const wait = (milliseconds) =>
  new Promise((resolve) => {
    setTimeout(resolve, milliseconds);
  });

const ChatInterface = ({
  conversationId,
  conversations,
  onUpdateConversations,
  onRefreshConversation
}) => {
  const [message, setMessage] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [pendingRun, setPendingRun] = useState(null);
  const messagesEndRef = useRef(null);
  const pendingMessageRef = useRef(null);
  const retryTurnRef = useRef(null);
  const seenTerminalEventsRef = useRef(new Set());
  const settlingRunIdsRef = useRef(new Set());
  const settledRunIdsRef = useRef(new Set());
  const { subscribe, subscribeToConversation, isConnected } = useWebSocket();

  const currentConversation = conversations.find(
    (conversation) => conversation.id === conversationId
  );
  const messages = useMemo(
    () => currentConversation?.messages || [],
    [currentConversation]
  );
  const isWaitingForAssistant = submitting || pendingRun !== null;

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  useEffect(() => {
    if (!conversationId) {
      return undefined;
    }
    return subscribeToConversation(conversationId);
  }, [conversationId, subscribeToConversation]);

  const appendMessage = useCallback(
    (targetConversationId, nextMessage) => {
      onUpdateConversations((current) =>
        current.map((conversation) => {
          if (conversation.id !== targetConversationId) {
            return conversation;
          }

          const normalized = normalizeMessage(
            nextMessage,
            targetConversationId,
            conversation.messages.length
          );
          const shouldSetTitle =
            normalized.role === 'user' && conversation.messages.length === 0;
          return {
            ...conversation,
            title: shouldSetTitle
              ? normalized.content.slice(0, 40) || conversation.title
              : conversation.title,
            messages: mergeMessages(
              conversation.messages,
              [normalized],
              targetConversationId
            )
          };
        })
      );
    },
    [onUpdateConversations]
  );

  const rollbackPendingMessage = useCallback(() => {
    const pending = pendingMessageRef.current;
    if (!pending) {
      return;
    }

    onUpdateConversations((current) =>
      current.map((conversation) =>
        conversation.id === pending.conversationId
          ? {
              ...conversation,
              messages: conversation.messages.filter((item) => item.id !== pending.id)
            }
          : conversation
      )
    );
    pendingMessageRef.current = null;
  }, [onUpdateConversations]);

  const persistAcceptedMessage = useCallback(
    (content, response) => {
      const pending = pendingMessageRef.current;
      if (!pending) {
        return;
      }

      onUpdateConversations((current) =>
        current.map((conversation) => {
          if (conversation.id !== pending.conversationId) {
            return conversation;
          }
          const withoutOptimistic = conversation.messages.filter(
            (item) => item.id !== pending.id
          );
          return {
            ...conversation,
            messages: mergeMessages(
              withoutOptimistic,
              [
                {
                  id: response.message_id,
                  conversation_id: response.conversation_id || pending.conversationId,
                  role: 'user',
                  content,
                  sequence: response.message_sequence,
                  created_at: response.accepted_at
                }
              ],
              pending.conversationId
            )
          };
        })
      );
      pendingMessageRef.current = null;
    },
    [onUpdateConversations]
  );

  const reconcileTerminalRun = useCallback(
    async ({ agentRunId, targetConversationId, announcedStatus, knownRun = null }) => {
      if (settledRunIdsRef.current.has(agentRunId)) {
        setPendingRun((current) => (current?.id === agentRunId ? null : current));
        return true;
      }
      if (settlingRunIdsRef.current.has(agentRunId)) {
        return false;
      }

      settlingRunIdsRef.current.add(agentRunId);
      try {
        const run = knownRun || (await fetchAgentRun(agentRunId));
        const status = run.status || announcedStatus;
        if (!TERMINAL_RUN_STATUSES.has(status)) {
          return false;
        }

        const snapshot = await onRefreshConversation(targetConversationId, false);
        if (!snapshot) {
          throw new Error('Conversation refresh did not return a snapshot');
        }
        settledRunIdsRef.current.add(agentRunId);
        setPendingRun((current) =>
          current?.id === agentRunId ? null : current
        );

        if (status === 'failed') {
          toast.error(run.failure?.safe_message || 'The assistant could not complete this turn');
        } else if (status === 'cancelled') {
          toast.warning('The assistant turn was cancelled');
        }
        return true;
      } catch (error) {
        console.error('Failed to reconcile terminal agent run:', error);
        return false;
      } finally {
        settlingRunIdsRef.current.delete(agentRunId);
      }
    },
    [onRefreshConversation]
  );

  useEffect(
    () =>
      subscribe((event) => {
        if (event.type === 'error') {
          toast.error(event.message || event.detail?.message || 'Live update failed');
          return;
        }

        if (event.type !== 'agent_run.terminal.v1') {
          return;
        }
        if (
          !event.event_id ||
          !event.conversation_id ||
          !event.agent_run_id ||
          !TERMINAL_RUN_STATUSES.has(event.status) ||
          seenTerminalEventsRef.current.has(event.event_id)
        ) {
          return;
        }
        seenTerminalEventsRef.current.add(event.event_id);

        void reconcileTerminalRun({
          agentRunId: event.agent_run_id,
          targetConversationId: event.conversation_id,
          announcedStatus: event.status
        }).then((reconciled) => {
          if (!reconciled) {
            seenTerminalEventsRef.current.delete(event.event_id);
          }
        });
      }),
    [reconcileTerminalRun, subscribe]
  );

  useEffect(() => {
    if (!pendingRun) {
      return undefined;
    }

    let cancelled = false;
    let timer = null;
    let attempts = 0;

    const poll = async () => {
      try {
        const run = await fetchAgentRun(pendingRun.id);
        if (cancelled) {
          return;
        }
        if (TERMINAL_RUN_STATUSES.has(run.status)) {
          await reconcileTerminalRun({
            agentRunId: pendingRun.id,
            targetConversationId: pendingRun.conversationId,
            announcedStatus: run.status,
            knownRun: run
          });
          return;
        }
      } catch (error) {
        if (!cancelled) {
          console.error('Agent run poll failed:', error);
        }
      }

      attempts += 1;
      if (!cancelled && attempts < MAX_POLL_ATTEMPTS) {
        timer = setTimeout(poll, isConnected ? 5000 : 1000);
      } else if (!cancelled) {
        setPendingRun(null);
        toast.warning('The assistant is still working. Refresh the conversation to check again.');
      }
    };

    timer = setTimeout(poll, isConnected ? 5000 : 500);
    return () => {
      cancelled = true;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [isConnected, pendingRun, reconcileTerminalRun]);

  const handleSendMessage = async (event) => {
    event.preventDefault();
    const content = message.trim();
    if (!content || !conversationId || isWaitingForAssistant) {
      return;
    }

    const retry = retryTurnRef.current;
    const idempotencyKey =
      retry?.content === content ? retry.idempotencyKey : `turn-${createClientUuid()}`;
    const optimisticMessage = {
      id: `optimistic-${idempotencyKey}`,
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
      optimistic: true
    };
    pendingMessageRef.current = {
      id: optimisticMessage.id,
      conversationId
    };
    appendMessage(conversationId, optimisticMessage);
    setMessage('');
    setSubmitting(true);

    try {
      let response;
      try {
        response = await submitTurn(conversationId, content, idempotencyKey);
      } catch (error) {
        if (error.response) {
          throw error;
        }
        await wait(400);
        response = await submitTurn(conversationId, content, idempotencyKey);
      }

      retryTurnRef.current = null;
      persistAcceptedMessage(content, response);
      if (!settledRunIdsRef.current.has(response.agent_run_id)) {
        setPendingRun({
          id: response.agent_run_id,
          conversationId: response.conversation_id || conversationId
        });
      }
      void onRefreshConversation(response.conversation_id || conversationId, false);
    } catch (error) {
      console.error('Failed to submit turn:', error);
      rollbackPendingMessage();
      if (!error.response) {
        retryTurnRef.current = { content, idempotencyKey };
      } else {
        retryTurnRef.current = null;
      }
      setMessage(content);
      toast.error(getApiErrorMessage(error, 'Failed to send message'));
    } finally {
      setSubmitting(false);
    }
  };

  if (!conversationId) {
    return (
      <div className="h-full flex items-center justify-center bg-gray-50">
        <p className="text-gray-500">Create a conversation to start chatting</p>
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col bg-gray-50">
      <div className="flex-1 overflow-y-auto p-4 space-y-4" aria-live="polite">
        {messages.length === 0 && (
          <div className="text-center py-8">
            <h2 className="text-lg font-medium text-gray-900 mb-2">
              Start a conversation
            </h2>
            <p className="text-gray-500">
              Ask me anything! I&apos;m your enterprise AI assistant.
            </p>
          </div>
        )}

        {messages.map((item) => (
          <MessageBubble key={item.id} message={item} />
        ))}

        {isWaitingForAssistant && (
          <div className="flex items-center space-x-2" aria-label="Assistant is typing">
            <div className="bg-white rounded-lg px-4 py-2 shadow">
              <div className="flex space-x-1">
                <div className="w-2 h-2 bg-gray-400 rounded-full animate-bounce" />
                <div
                  className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: '0.1s' }}
                />
                <div
                  className="w-2 h-2 bg-gray-400 rounded-full animate-bounce"
                  style={{ animationDelay: '0.2s' }}
                />
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="bg-white border-t border-gray-200 p-4">
        <form onSubmit={handleSendMessage} className="flex space-x-4">
          <input
            type="text"
            value={message}
            onChange={(event) => setMessage(event.target.value)}
            placeholder="Type your message..."
            maxLength={16000}
            aria-label="Chat message"
            className="flex-1 border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500"
          />
          <button
            type="submit"
            disabled={!message.trim() || isWaitingForAssistant}
            aria-label="Send message"
            className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-lg shadow-sm text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <PaperAirplaneIcon className="h-5 w-5" />
          </button>
        </form>
        {!isConnected && (
          <p className="text-amber-600 text-sm mt-2">
            Live updates are unavailable. Responses will continue through API polling.
          </p>
        )}
      </div>
    </div>
  );
};

export default ChatInterface;
