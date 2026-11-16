import React, { useCallback, useEffect, useRef, useState } from 'react';
import { matchPath, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { toast } from 'react-toastify';

import { getApiErrorMessage } from '../api';
import {
  createClientUuid,
  createConversation,
  fetchConversationSnapshot,
  normalizeConversation
} from '../conversations';
import { useAuth } from '../hooks/useAuth';
import { useWebSocket } from '../hooks/useWebSocket';
import ChatInterface from './ChatInterface';
import ComplianceDashboard from './ComplianceDashboard';
import ConnectionStatus from './ConnectionStatus';
import { canReadGovernance } from '../governance';
import HumanReviewQueue from './HumanReviewQueue';
import Sidebar from './Sidebar';
import TenantInfo from './TenantInfo';

const Dashboard = () => {
  const { user, logout } = useAuth();
  const { connectionStatus, activeConnections } = useWebSocket();
  const location = useLocation();
  const navigate = useNavigate();
  const dashboardPath = location.pathname.replace(/\/+$/, '') || '/';
  const isChatRoute = dashboardPath === '/dashboard';
  const isComplianceRoute = dashboardPath === '/dashboard/compliance';
  const reviewDetailMatch = matchPath(
    '/dashboard/reviews/:agentRunId',
    dashboardPath
  );
  const isReviewsRoute =
    dashboardPath === '/dashboard/reviews' || reviewDetailMatch !== null;
  const selectedReviewId = reviewDetailMatch?.params.agentRunId || null;
  const canViewGovernance = canReadGovernance(user?.roles);
  const [selectedConversation, setSelectedConversation] = useState(null);
  const [conversations, setConversations] = useState([]);
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [creatingConversation, setCreatingConversation] = useState(false);
  const creatingRef = useRef(false);
  const initializedRef = useRef(false);

  const refreshConversation = useCallback(async (conversationId, notify = true) => {
    try {
      const snapshot = await fetchConversationSnapshot(conversationId);
      setConversations((current) =>
        current.map((conversation) =>
          conversation.id === conversationId
            ? normalizeConversation(
                { ...snapshot.conversation, messages: snapshot.messages },
                conversation
              )
            : conversation
        )
      );
      return snapshot;
    } catch (error) {
      console.error('Failed to refresh conversation:', error);
      if (notify) {
        toast.error(getApiErrorMessage(error, 'Could not refresh this conversation'));
      }
      return null;
    }
  }, []);

  const createNewConversation = useCallback(async () => {
    if (creatingRef.current) {
      return;
    }

    creatingRef.current = true;
    setCreatingConversation(true);
    const conversationId = createClientUuid();

    try {
      const response = await createConversation(conversationId);
      const fallback = {
        id: conversationId,
        title: 'New Conversation',
        messages: [],
        created_at: new Date().toISOString()
      };
      const conversation = normalizeConversation(
        response.context || response.conversation || response,
        fallback
      );

      setConversations((current) => [
        conversation,
        ...current.filter((item) => item.id !== conversation.id)
      ]);
      setSelectedConversation(conversation.id);
      await refreshConversation(conversation.id, false);
    } catch (error) {
      console.error('Failed to create conversation:', error);
      toast.error(getApiErrorMessage(error, 'Failed to create conversation'));
    } finally {
      creatingRef.current = false;
      setCreatingConversation(false);
      setLoadingConversations(false);
    }
  }, [refreshConversation]);

  useEffect(() => {
    if (!isChatRoute || initializedRef.current) {
      return;
    }
    initializedRef.current = true;
    createNewConversation();
  }, [createNewConversation, isChatRoute]);

  const selectConversation = useCallback(
    async (conversationId) => {
      navigate('/dashboard');
      setSelectedConversation(conversationId);
      await refreshConversation(conversationId);
    },
    [navigate, refreshConversation]
  );

  const createAndShowConversation = useCallback(async () => {
    navigate('/dashboard');
    await createNewConversation();
  }, [createNewConversation, navigate]);

  if (!isChatRoute && !isComplianceRoute && !isReviewsRoute) {
    return <Navigate to="/dashboard" replace />;
  }

  if ((isComplianceRoute || isReviewsRoute) && !canViewGovernance) {
    return <Navigate to="/dashboard" replace />;
  }

  const activeView = isComplianceRoute
    ? 'compliance'
    : isReviewsRoute
      ? 'reviews'
      : 'chat';
  const pageTitle = isComplianceRoute
    ? 'Governance Console'
    : isReviewsRoute
      ? 'Human Review'
      : 'Enterprise Chat Agent';

  return (
    <div className="flex h-screen min-w-[900px] bg-gray-100">
      <Sidebar
        conversations={conversations}
        selectedConversation={selectedConversation}
        onSelectConversation={selectConversation}
        onNewConversation={createAndShowConversation}
        creatingConversation={creatingConversation}
        activeView={activeView}
        canViewGovernance={canViewGovernance}
        onShowChat={() => navigate('/dashboard')}
        onShowCompliance={() => navigate('/dashboard/compliance')}
        onShowReviews={() => navigate('/dashboard/reviews')}
        user={user}
        onLogout={logout}
      />

      <div className="flex-1 flex flex-col min-w-0">
        <header className="bg-white shadow-sm border-b border-gray-200">
          <div className="flex items-center justify-between px-6 py-4">
            <div className="flex items-center space-x-4">
              <h1 className="text-xl font-semibold text-gray-900">
                {pageTitle}
              </h1>
              {isChatRoute && (
                <ConnectionStatus
                  status={connectionStatus}
                  activeConnections={activeConnections}
                />
              )}
            </div>
            <TenantInfo user={user} />
          </div>
        </header>

        <main className="flex-1 overflow-hidden">
          {isComplianceRoute ? (
            <ComplianceDashboard />
          ) : isReviewsRoute ? (
            <HumanReviewQueue
              selectedAgentRunId={selectedReviewId}
              onSelectReview={(agentRunId) =>
                navigate(
                  agentRunId
                    ? `/dashboard/reviews/${encodeURIComponent(agentRunId)}`
                    : '/dashboard/reviews'
                )
              }
            />
          ) : loadingConversations ? (
            <div className="h-full flex items-center justify-center bg-gray-50">
              <div
                className="animate-spin rounded-full h-8 w-8 border-b-2 border-indigo-600"
                aria-label="Creating conversation"
              />
            </div>
          ) : (
            <ChatInterface
              conversationId={selectedConversation}
              conversations={conversations}
              onUpdateConversations={setConversations}
              onRefreshConversation={refreshConversation}
            />
          )}
        </main>
      </div>
    </div>
  );
};

export default Dashboard;
