import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState
} from 'react';

import { getWebSocketBaseUrl } from '../api';
import { useAuth } from './useAuth';

const WebSocketContext = createContext(null);
const HEARTBEAT_INTERVAL_MS = 30000;
const MAX_RECONNECT_ATTEMPTS = 5;
const NON_RETRYABLE_CLOSE_CODES = new Set([1000, 1008, 4401, 4403]);

const createConnectionId = () => {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return `conn_${crypto.randomUUID()}`;
  }
  return `conn_${Date.now()}_${Math.random().toString(36).slice(2, 11)}`;
};

const readConnectionCount = (message) => {
  const candidates = [
    message.active_connections,
    message.current_connections,
    message.connection_count,
    message.tenant_connection_count
  ];
  return candidates.find((value) => Number.isInteger(value) && value >= 0);
};

export const useWebSocket = () => {
  const context = useContext(WebSocketContext);
  if (!context) {
    throw new Error('useWebSocket must be used within a WebSocketProvider');
  }
  return context;
};

export const WebSocketProvider = ({ children }) => {
  const { user, isAuthenticated } = useAuth();
  const [connectionStatus, setConnectionStatus] = useState('disconnected');
  const [activeConnections, setActiveConnections] = useState(0);
  const [isConnected, setIsConnected] = useState(false);
  const socketRef = useRef(null);
  const connectRef = useRef(null);
  const heartbeatTimerRef = useRef(null);
  const reconnectTimerRef = useRef(null);
  const reconnectAttemptsRef = useRef(0);
  const shouldReconnectRef = useRef(false);
  const subscribersRef = useRef(new Set());
  const conversationSubscriptionsRef = useRef(new Set());

  const clearHeartbeat = useCallback(() => {
    if (heartbeatTimerRef.current) {
      clearInterval(heartbeatTimerRef.current);
      heartbeatTimerRef.current = null;
    }
  }, []);

  const clearReconnectTimer = useCallback(() => {
    if (reconnectTimerRef.current) {
      clearTimeout(reconnectTimerRef.current);
      reconnectTimerRef.current = null;
    }
  }, []);

  const publish = useCallback((message) => {
    subscribersRef.current.forEach((subscriber) => {
      try {
        subscriber(message);
      } catch (error) {
        console.error('WebSocket subscriber failed:', error);
      }
    });
  }, []);

  const handleMessage = useCallback(
    (message) => {
      const connectionCount = readConnectionCount(message);
      if (connectionCount !== undefined) {
        setActiveConnections(connectionCount);
      }

      switch (message.type) {
        case 'heartbeat_ack':
          return false;
        case 'authenticated':
        case 'auth_success':
        case 'connection_ack':
          setConnectionStatus('connected');
          setIsConnected(true);
          setActiveConnections((count) => Math.max(count, 1));
          return true;
        default:
          publish(message);
          return false;
      }
    },
    [publish]
  );

  const startHeartbeat = useCallback(
    (socket) => {
      clearHeartbeat();
      heartbeatTimerRef.current = setInterval(() => {
        if (socket.readyState !== WebSocket.OPEN) {
          clearHeartbeat();
          return;
        }
        socket.send(JSON.stringify({ type: 'heartbeat' }));
      }, HEARTBEAT_INTERVAL_MS);
    },
    [clearHeartbeat]
  );

  const connect = useCallback(() => {
    shouldReconnectRef.current = true;
    if (!isAuthenticated || !user) {
      return;
    }

    const currentSocket = socketRef.current;
    if (
      currentSocket?.readyState === WebSocket.OPEN ||
      currentSocket?.readyState === WebSocket.CONNECTING
    ) {
      return;
    }

    const token = localStorage.getItem('access_token');
    if (!token) {
      setConnectionStatus('disconnected');
      setIsConnected(false);
      return;
    }

    clearReconnectTimer();
    setConnectionStatus('connecting');
    const socket = new WebSocket(
      `${getWebSocketBaseUrl()}/${encodeURIComponent(createConnectionId())}`
    );
    socketRef.current = socket;

    socket.onopen = () => {
      if (socketRef.current !== socket) {
        socket.close(1000);
        return;
      }
      socket.send(JSON.stringify({ type: 'authenticate', token }));
      setConnectionStatus('connecting');
      setIsConnected(false);
      setActiveConnections(0);
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        if (handleMessage(message)) {
          socket.authenticated = true;
          reconnectAttemptsRef.current = 0;
          startHeartbeat(socket);
          conversationSubscriptionsRef.current.forEach((conversationId) => {
            socket.send(
              JSON.stringify({ type: 'subscribe', conversation_id: conversationId })
            );
          });
        }
      } catch (error) {
        console.error('Failed to parse WebSocket message:', error);
      }
    };

    socket.onclose = (event) => {
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
      clearHeartbeat();
      setConnectionStatus('disconnected');
      setIsConnected(false);
      setActiveConnections(0);

      if (
        !shouldReconnectRef.current ||
        NON_RETRYABLE_CLOSE_CODES.has(event.code) ||
        reconnectAttemptsRef.current >= MAX_RECONNECT_ATTEMPTS
      ) {
        return;
      }

      const delay = Math.min(1000 * 2 ** reconnectAttemptsRef.current, 16000);
      reconnectAttemptsRef.current += 1;
      reconnectTimerRef.current = setTimeout(() => {
        reconnectTimerRef.current = null;
        connectRef.current?.();
      }, delay);
    };

    socket.onerror = () => {
      setConnectionStatus('disconnected');
      setIsConnected(false);
    };
  }, [
    clearHeartbeat,
    clearReconnectTimer,
    handleMessage,
    isAuthenticated,
    startHeartbeat,
    user
  ]);

  useEffect(() => {
    connectRef.current = connect;
  }, [connect]);

  const disconnect = useCallback(() => {
    shouldReconnectRef.current = false;
    clearHeartbeat();
    clearReconnectTimer();
    reconnectAttemptsRef.current = 0;

    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close(1000, 'Client disconnected');
    }

    setConnectionStatus('disconnected');
    setIsConnected(false);
    setActiveConnections(0);
  }, [clearHeartbeat, clearReconnectTimer]);

  useEffect(() => {
    if (isAuthenticated && user) {
      connect();
    } else {
      disconnect();
    }
    return disconnect;
  }, [connect, disconnect, isAuthenticated, user]);

  const subscribe = useCallback((subscriber) => {
    subscribersRef.current.add(subscriber);
    return () => subscribersRef.current.delete(subscriber);
  }, []);

  const subscribeToConversation = useCallback((conversationId) => {
    conversationSubscriptionsRef.current.add(conversationId);
    const socket = socketRef.current;
    if (socket?.readyState === WebSocket.OPEN && socket.authenticated) {
      socket.send(JSON.stringify({ type: 'subscribe', conversation_id: conversationId }));
    }

    return () => {
      conversationSubscriptionsRef.current.delete(conversationId);
      const currentSocket = socketRef.current;
      if (currentSocket?.readyState === WebSocket.OPEN && currentSocket.authenticated) {
        currentSocket.send(
          JSON.stringify({ type: 'unsubscribe', conversation_id: conversationId })
        );
      }
    };
  }, []);

  const value = useMemo(
    () => ({
      connectionStatus,
      activeConnections,
      isConnected,
      subscribe,
      subscribeToConversation,
      connect,
      disconnect
    }),
    [
      activeConnections,
      connect,
      connectionStatus,
      disconnect,
      isConnected,
      subscribe,
      subscribeToConversation
    ]
  );

  return (
    <WebSocketContext.Provider value={value}>{children}</WebSocketContext.Provider>
  );
};
