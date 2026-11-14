import React, { useEffect, useMemo, useState } from 'react';
import { BuildingOfficeIcon, ChartBarIcon } from '@heroicons/react/24/outline';

import api from '../api';

const TenantInfo = ({ user }) => {
  const [tenantInfo, setTenantInfo] = useState(null);
  const [quotaInfo, setQuotaInfo] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    const fetchTenantInfo = async () => {
      setLoading(true);
      try {
        const response = await api.get('/v1/tenant/info');
        if (!cancelled) {
          setTenantInfo(response.data.tenant_info || response.data.tenant || null);
          setQuotaInfo(response.data.quota_info || response.data.quota || null);
        }
      } catch (error) {
        console.error('Failed to fetch tenant info:', error);
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    };

    fetchTenantInfo();
    return () => {
      cancelled = true;
    };
  }, [user?.tenant_id]);

  const quotaPercentage = useMemo(() => {
    if (!quotaInfo?.daily_message_limit) {
      return 0;
    }
    return Math.min(
      (quotaInfo.daily_messages_used / quotaInfo.daily_message_limit) * 100,
      100
    );
  }, [quotaInfo]);

  if (loading) {
    return (
      <div className="flex items-center space-x-2">
        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-indigo-600" />
        <span className="text-sm text-gray-500">Loading...</span>
      </div>
    );
  }

  return (
    <div className="flex items-center space-x-4">
      <div className="flex items-center space-x-2">
        <BuildingOfficeIcon className="h-5 w-5 text-gray-400" />
        <div>
          <p className="text-sm font-medium text-gray-900">
            {tenantInfo?.name || 'Unknown Tenant'}
          </p>
          <p className="text-xs text-gray-500">
            {tenantInfo?.domain || user?.tenant_id}
          </p>
        </div>
      </div>

      {quotaInfo && (
        <div className="flex items-center space-x-2">
          <ChartBarIcon className="h-5 w-5 text-gray-400" />
          <div className="text-right">
            <p className="text-sm font-medium text-gray-900">
              {quotaInfo.current_connections}/{quotaInfo.max_connections}
            </p>
            <div className="flex items-center space-x-1">
              <div className="w-16 bg-gray-200 rounded-full h-1.5">
                <div
                  className="bg-indigo-600 h-1.5 rounded-full quota-bar"
                  style={{ width: `${quotaPercentage}%` }}
                />
              </div>
              <span className="text-xs text-gray-500">
                {quotaInfo.daily_messages_used}/{quotaInfo.daily_message_limit}
              </span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default TenantInfo;
