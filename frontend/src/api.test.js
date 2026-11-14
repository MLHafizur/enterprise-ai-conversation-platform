jest.mock('axios', () => ({
  __esModule: true,
  default: {
    create: jest.fn(() => ({ defaults: { headers: { common: {} } } }))
  }
}));

describe('frontend transport configuration', () => {
  const originalApiBaseUrl = process.env.REACT_APP_API_BASE_URL;
  const originalWebSocketBaseUrl = process.env.REACT_APP_WS_BASE_URL;

  afterEach(() => {
    jest.resetModules();
    if (originalApiBaseUrl === undefined) {
      delete process.env.REACT_APP_API_BASE_URL;
    } else {
      process.env.REACT_APP_API_BASE_URL = originalApiBaseUrl;
    }
    if (originalWebSocketBaseUrl === undefined) {
      delete process.env.REACT_APP_WS_BASE_URL;
    } else {
      process.env.REACT_APP_WS_BASE_URL = originalWebSocketBaseUrl;
    }
  });

  test('uses the same-origin API gateway path by default', () => {
    delete process.env.REACT_APP_API_BASE_URL;
    const { API_BASE_URL } = require('./api');
    expect(API_BASE_URL).toBe('/api');
  });

  test('derives a same-origin WebSocket URL', () => {
    delete process.env.REACT_APP_WS_BASE_URL;
    const { getWebSocketBaseUrl } = require('./api');
    expect(getWebSocketBaseUrl()).toBe('ws://localhost/ws');
  });

  test('normalizes an explicit WebSocket URL', () => {
    process.env.REACT_APP_WS_BASE_URL = 'wss://chat.example.com/ws/';
    const { getWebSocketBaseUrl } = require('./api');
    expect(getWebSocketBaseUrl()).toBe('wss://chat.example.com/ws');
  });

  test('extracts the target structured error message', () => {
    const { getApiErrorMessage } = require('./api');
    expect(
      getApiErrorMessage({
        response: { data: { detail: { code: 'invalid_turn', message: 'Turn is invalid' } } }
      })
    ).toBe('Turn is invalid');
  });
});
