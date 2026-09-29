const API_BASE = '/api';
const ACCESS_TOKEN_KEY = 'adlv-media-access-token';
const REFRESH_TOKEN_KEY = 'adlv-media-refresh-token';
const USER_KEY = 'adlv-media-user';

function getAccessToken() { return localStorage.getItem(ACCESS_TOKEN_KEY) || ''; }

function storeSession(session) {
    localStorage.setItem(ACCESS_TOKEN_KEY, session.access_token);
    localStorage.setItem(REFRESH_TOKEN_KEY, session.refresh_token);
    localStorage.setItem(USER_KEY, JSON.stringify(session.user));
    return session;
}

function clearSession() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(REFRESH_TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
}

async function apiRequest(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = getAccessToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, cache: 'no-store' });
    if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try { detail = (await response.json()).detail || detail; } catch (_) { /* keep status */ }
        const error = new Error(detail);
        error.status = response.status;
        throw error;
    }
    return response.status === 204 ? null : response.json();
}

async function loginWithApi(email, password) {
    return storeSession(await apiRequest('/auth/login', {
        method: 'POST', body: JSON.stringify({ email, password })
    }));
}

async function refreshApiSession() {
    const refreshToken = localStorage.getItem(REFRESH_TOKEN_KEY);
    if (!refreshToken) return null;
    try {
        return storeSession(await apiRequest('/auth/refresh', {
            method: 'POST', body: JSON.stringify({ refresh_token: refreshToken })
        }));
    } catch (_) {
        clearSession();
        return null;
    }
}

async function getAuthenticatedSession() {
    if (!getAccessToken()) return null;
    try {
        const data = await apiRequest('/auth/session');
        return { access_token: getAccessToken(), user: data.user };
    } catch (error) {
        if (error.status === 401) return refreshApiSession();
        clearSession();
        return null;
    }
}

function logoutFromApi() {
    clearSession();
}
