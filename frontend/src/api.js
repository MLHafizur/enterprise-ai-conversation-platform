import axios from 'axios';

const trimTrailingSlashes = (value) => value.replace(/\/+$/, '');

export const API_BASE_URL = trimTrailingSlashes(
  process.env.REACT_APP_API_BASE_URL || '/api'
);

export const getWebSocketBaseUrl = () => {
  if (process.env.REACT_APP_WS_BASE_URL) {
    return trimTrailingSlashes(process.env.REACT_APP_WS_BASE_URL);
  }

  if (typeof window !== 'undefined') {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${window.location.host}/ws`;
  }

  return 'ws://localhost/ws';
};

const api = axios.create({
  baseURL: API_BASE_URL,
  timeout: 15000
});

export const setAccessToken = (token) => {
  if (token) {
    api.defaults.headers.common.Authorization = `Bearer ${token}`;
    return;
  }

  delete api.defaults.headers.common.Authorization;
};

export const getApiErrorMessage = (error, fallback = 'Request failed') => {
  const detail = error?.response?.data?.detail;

  if (typeof detail === 'string' && detail.trim()) {
    return detail;
  }

  if (detail && typeof detail.message === 'string' && detail.message.trim()) {
    return detail.message;
  }

  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => item?.msg)
      .filter((message) => typeof message === 'string' && message.trim());
    if (messages.length > 0) {
      return messages.join('; ');
    }
  }

  if (typeof error?.response?.data?.message === 'string') {
    return error.response.data.message;
  }

  if (!error?.response && typeof error?.message === 'string') {
    return error.message;
  }

  return fallback;
};

export default api;
