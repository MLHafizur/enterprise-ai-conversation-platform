import React from 'react';
import { ExclamationTriangleIcon, WifiIcon } from '@heroicons/react/24/outline';

const statusPresentation = {
  connected: { color: 'text-green-600', text: 'Connected' },
  connecting: { color: 'text-yellow-600', text: 'Connecting...' },
  disconnected: { color: 'text-red-600', text: 'Disconnected' }
};

const ConnectionStatus = ({ status, activeConnections = 0 }) => {
  const presentation = statusPresentation[status] || {
    color: 'text-gray-400',
    text: 'Unknown'
  };
  const Icon = status === 'connected' ? WifiIcon : ExclamationTriangleIcon;

  return (
    <div className="flex items-center space-x-2">
      <div className="flex items-center space-x-1">
        <Icon className={`h-5 w-5 ${presentation.color}`} />
        <span className={`text-sm font-medium ${presentation.color}`}>
          {presentation.text}
        </span>
      </div>
      {status === 'connected' && (
        <span className="text-sm text-gray-500">({activeConnections} active)</span>
      )}
    </div>
  );
};

export default ConnectionStatus;
