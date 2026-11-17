import React from 'react';
import {
  ArrowRightOnRectangleIcon,
  ChatBubbleLeftRightIcon,
  ChatBubbleLeftIcon,
  CodeBracketSquareIcon,
  Cog6ToothIcon,
  ClipboardDocumentCheckIcon,
  FolderOpenIcon,
  PlusIcon,
  ShieldCheckIcon,
  SparklesIcon,
  UserCircleIcon
} from '@heroicons/react/24/outline';
import { format } from 'date-fns';

const formatConversationDate = (value) => {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : format(date, 'MMM d, yyyy');
};

const Sidebar = ({
  conversations,
  selectedConversation,
  onSelectConversation,
  onNewConversation,
  creatingConversation,
  activeView,
  canViewGovernance,
  canManageProfiles,
  canViewCodeSecurity,
  onShowChat,
  onShowCompliance,
  onShowReviews,
  onShowProfiles,
  onShowSecurityDashboard,
  onShowCodeAnalysis,
  onShowSecurityRepositories,
  onShowSecuritySettings,
  user,
  onLogout
}) => (
  <aside className="w-80 bg-white border-r border-gray-200 flex flex-col">
    <nav className="p-4 border-b border-gray-200 space-y-1" aria-label="Workspace">
      <button
        type="button"
        onClick={onShowChat}
        aria-current={activeView === 'chat' ? 'page' : undefined}
        className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
          activeView === 'chat'
            ? 'bg-indigo-50 text-indigo-700'
            : 'text-gray-700 hover:bg-gray-50'
        }`}
      >
        <ChatBubbleLeftRightIcon className="h-5 w-5 mr-3" aria-hidden="true" />
        Chat
      </button>
      {canViewGovernance && (
        <>
          <button
            type="button"
            onClick={onShowCompliance}
            aria-current={activeView === 'compliance' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'compliance'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <ShieldCheckIcon className="h-5 w-5 mr-3" aria-hidden="true" />
            Compliance
          </button>
          <button
            type="button"
            onClick={onShowReviews}
            aria-current={activeView === 'reviews' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'reviews'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <ClipboardDocumentCheckIcon
              className="h-5 w-5 mr-3"
              aria-hidden="true"
            />
            Review queue
          </button>
        </>
      )}
      {canManageProfiles && (
        <button
          type="button"
          onClick={onShowProfiles}
          aria-current={activeView === 'profiles' ? 'page' : undefined}
          className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
            activeView === 'profiles'
              ? 'bg-indigo-50 text-indigo-700'
              : 'text-gray-700 hover:bg-gray-50'
          }`}
        >
          <SparklesIcon className="h-5 w-5 mr-3" aria-hidden="true" />
          Assistant profiles
        </button>
      )}
      {canViewCodeSecurity && (
        <>
          <div className="px-3 pb-1 pt-4 text-xs font-semibold uppercase tracking-wide text-gray-400">
            Secure code
          </div>
          <button
            type="button"
            onClick={onShowSecurityDashboard}
            aria-current={activeView === 'security' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'security'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <ShieldCheckIcon className="h-5 w-5 mr-3" aria-hidden="true" />
            Security dashboard
          </button>
          <button
            type="button"
            onClick={onShowCodeAnalysis}
            aria-current={activeView === 'security-analysis' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'security-analysis'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <CodeBracketSquareIcon className="h-5 w-5 mr-3" aria-hidden="true" />
            Code analysis
          </button>
          <button
            type="button"
            onClick={onShowSecurityRepositories}
            aria-current={activeView === 'security-repositories' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'security-repositories'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <FolderOpenIcon className="h-5 w-5 mr-3" aria-hidden="true" />
            Repositories
          </button>
          <button
            type="button"
            onClick={onShowSecuritySettings}
            aria-current={activeView === 'security-settings' ? 'page' : undefined}
            className={`w-full flex items-center rounded-md px-3 py-2 text-sm font-medium transition-colors ${
              activeView === 'security-settings'
                ? 'bg-indigo-50 text-indigo-700'
                : 'text-gray-700 hover:bg-gray-50'
            }`}
          >
            <Cog6ToothIcon className="h-5 w-5 mr-3" aria-hidden="true" />
            Security settings
          </button>
        </>
      )}
    </nav>

    <div className="p-4 border-b border-gray-200">
      <button
        type="button"
        onClick={onNewConversation}
        disabled={creatingConversation}
        className="w-full flex items-center justify-center px-4 py-2 border border-gray-300 rounded-md shadow-sm bg-white text-sm font-medium text-gray-700 hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <PlusIcon className="h-5 w-5 mr-2" />
        {creatingConversation ? 'Creating...' : 'New Conversation'}
      </button>
    </div>

    <nav className="flex-1 overflow-y-auto" aria-label="Conversations">
      <div className="p-4 space-y-2">
        {conversations.map((conversation) => {
          const lastMessage = conversation.messages[conversation.messages.length - 1];
          return (
            <button
              type="button"
              key={conversation.id}
              onClick={() => onSelectConversation(conversation.id)}
              className={`w-full text-left p-3 rounded-lg transition-colors ${
                selectedConversation === conversation.id
                  ? 'bg-indigo-50 border border-indigo-200'
                  : 'hover:bg-gray-50 border border-transparent'
              }`}
            >
              <div className="flex items-start space-x-3">
                <ChatBubbleLeftIcon className="h-5 w-5 text-gray-400 mt-1" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-gray-900 truncate">
                    {conversation.title}
                  </p>
                  <p className="text-xs text-gray-500">
                    {formatConversationDate(conversation.created_at)}
                  </p>
                  {lastMessage && (
                    <p className="text-xs text-gray-400 mt-1 truncate">
                      {lastMessage.content.slice(0, 50)}
                      {lastMessage.content.length > 50 ? '...' : ''}
                    </p>
                  )}
                </div>
              </div>
            </button>
          );
        })}
      </div>
    </nav>

    <div className="p-4 border-t border-gray-200">
      <div className="flex items-center space-x-3">
        <UserCircleIcon className="h-8 w-8 text-gray-400" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-gray-900 truncate">
            {user?.name || 'Demo User'}
          </p>
          <p className="text-xs text-gray-500 truncate">
            {user?.tenant_id || 'Unknown tenant'}
          </p>
        </div>
        <button
          type="button"
          onClick={onLogout}
          aria-label="Sign out"
          className="p-1 text-gray-400 hover:text-gray-500"
        >
          <ArrowRightOnRectangleIcon className="h-5 w-5" />
        </button>
      </div>
    </div>
  </aside>
);

export default Sidebar;
