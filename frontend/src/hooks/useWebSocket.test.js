import React, { act } from 'react';
import { createRoot } from 'react-dom/client';

import { useWebSocket, WebSocketProvider } from './useWebSocket';

jest.mock('../api', () => ({
  getWebSocketBaseUrl: () => 'wss://chat.example.com/ws'
}));

jest.mock('./useAuth', () => {
  const user = { user_id: 'user_1', tenant_id: 'tenant_1', roles: ['user'] };
  return {
    useAuth: () => ({ user, isAuthenticated: true })
  };
});

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances = [];

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.send = jest.fn();
    FakeWebSocket.instances.push(this);
  }

  open() {
    this.readyState = FakeWebSocket.OPEN;
    this.onopen?.();
  }

  receive(message) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

describe('WebSocketProvider', () => {
  let container;
  let root;
  let context;

  const Probe = () => {
    context = useWebSocket();
    return null;
  };

  beforeAll(() => {
    global.IS_REACT_ACT_ENVIRONMENT = true;
  });

  afterAll(() => {
    delete global.IS_REACT_ACT_ENVIRONMENT;
  });

  beforeEach(() => {
    jest.useFakeTimers();
    localStorage.setItem('access_token', 'signed-access-token');
    FakeWebSocket.instances = [];
    global.WebSocket = FakeWebSocket;
    container = document.createElement('div');
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    localStorage.clear();
    jest.runOnlyPendingTimers();
    jest.useRealTimers();
  });

  const renderProvider = () => {
    act(() => {
      root.render(
        <WebSocketProvider>
          <Probe />
        </WebSocketProvider>
      );
    });
    return FakeWebSocket.instances[0];
  };

  test('authenticates in the first frame and waits for acknowledgement', () => {
    const socket = renderProvider();
    expect(socket.url).toMatch(/^wss:\/\/chat\.example\.com\/ws\/conn_/);
    expect(socket.url).not.toContain('token');

    act(() => socket.open());
    expect(JSON.parse(socket.send.mock.calls[0][0])).toEqual({
      type: 'authenticate',
      token: 'signed-access-token'
    });
    expect(context.isConnected).toBe(false);

    act(() => socket.receive({ type: 'authenticated', connection_id: 'server_1' }));
    expect(context.isConnected).toBe(true);
    expect(context.activeConnections).toBe(1);

    act(() => jest.advanceTimersByTime(30000));
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({ type: 'heartbeat' });
  });

  test('delivers terminal events and manages conversation subscriptions', () => {
    const socket = renderProvider();
    const stopConversationSubscription = context.subscribeToConversation('conversation_1');
    act(() => socket.open());
    act(() => socket.receive({ type: 'authenticated' }));

    expect(JSON.parse(socket.send.mock.calls[1][0])).toEqual({
      type: 'subscribe',
      conversation_id: 'conversation_1'
    });

    const subscriber = jest.fn();
    const unsubscribe = context.subscribe(subscriber);
    const response = {
      type: 'agent_run.terminal.v1',
      event_id: 'event_1',
      conversation_id: 'conversation_1',
      agent_run_id: 'run_1',
      status: 'succeeded',
      output_message_id: 'message_1',
      conversation_version: 2,
      message_sequence: 2,
      occurred_at: '2026-01-01T00:00:00Z'
    };
    act(() => socket.receive(response));
    expect(subscriber).toHaveBeenCalledTimes(1);
    expect(subscriber).toHaveBeenCalledWith(response);

    act(() => {
      stopConversationSubscription();
    });
    expect(JSON.parse(socket.send.mock.calls.at(-1)[0])).toEqual({
      type: 'unsubscribe',
      conversation_id: 'conversation_1'
    });
    unsubscribe();
  });
});
