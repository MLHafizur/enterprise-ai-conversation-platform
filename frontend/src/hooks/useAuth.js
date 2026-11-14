import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState
} from 'react';

import api, { getApiErrorMessage, setAccessToken } from '../api';

const AuthContext = createContext(null);
const TOKEN_STORAGE_KEY = 'access_token';
const USER_STORAGE_KEY = 'enterprise_chat_user';

const decodeTokenPayload = (token) => {
  const payload = token.split('.')[1];
  if (!payload) {
    throw new Error('Malformed access token');
  }

  const normalized = payload.replace(/-/g, '+').replace(/_/g, '/');
  const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, '=');
  const decoded = decodeURIComponent(
    atob(padded)
      .split('')
      .map((character) => `%${character.charCodeAt(0).toString(16).padStart(2, '0')}`)
      .join('')
  );

  return JSON.parse(decoded);
};

const clearStoredSession = () => {
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  localStorage.removeItem(USER_STORAGE_KEY);
  setAccessToken(null);
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

export const AuthProvider = ({ children }) => {
  const [user, setUser] = useState(null);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const token = localStorage.getItem(TOKEN_STORAGE_KEY);
    if (token) {
      try {
        const payload = decodeTokenPayload(token);
        if (!payload.exp || payload.exp <= Date.now() / 1000) {
          throw new Error('Access token expired');
        }

        const storedUser = JSON.parse(localStorage.getItem(USER_STORAGE_KEY) || '{}');
        const restoredUser = {
          ...storedUser,
          user_id: payload.user_id || payload.sub || storedUser.user_id,
          tenant_id: payload.tenant_id || storedUser.tenant_id,
          roles: payload.roles || storedUser.roles || []
        };
        setAccessToken(token);
        setUser(restoredUser);
        setIsAuthenticated(true);
      } catch (error) {
        console.error('Could not restore the saved session:', error);
        clearStoredSession();
      }
    }
    setLoading(false);
  }, []);

  const login = useCallback(async ({ tenantId, role }) => {
    try {
      const response = await api.post('/auth/login', {
        tenant_id: tenantId,
        role
      });
      const { access_token: token, user_info: userInfo } = response.data;

      if (!token || !userInfo) {
        throw new Error('Authentication response was incomplete');
      }

      localStorage.setItem(TOKEN_STORAGE_KEY, token);
      localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(userInfo));
      setAccessToken(token);
      setUser(userInfo);
      setIsAuthenticated(true);
      return userInfo;
    } catch (error) {
      throw new Error(getApiErrorMessage(error, 'Login failed'));
    }
  }, []);

  const logout = useCallback(() => {
    clearStoredSession();
    setUser(null);
    setIsAuthenticated(false);
  }, []);

  const value = useMemo(
    () => ({ user, isAuthenticated, loading, login, logout }),
    [isAuthenticated, loading, login, logout, user]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};
