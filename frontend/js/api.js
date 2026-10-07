const API_BASE = '/api';
const ACCESS_TOKEN_KEY = 'adlv-media-access-token';
const LEGACY_REFRESH_TOKEN_KEY = 'adlv-media-refresh-token';
const USER_KEY = 'adlv-media-user';
const ACCESS_TOKEN_EXPIRES_AT_KEY = 'adlv-media-access-token-expires-at';
const TOKEN_REFRESH_LEEWAY_MS = 60 * 1000;

let refreshPromise = null;
let refreshTimer = null;

function getAccessToken() { return localStorage.getItem(ACCESS_TOKEN_KEY) || ''; }

function getAccessTokenExpiresAt() {
    const expiresAt = Number(localStorage.getItem(ACCESS_TOKEN_EXPIRES_AT_KEY));
    if (Number.isFinite(expiresAt) && expiresAt > 0) return expiresAt;
    try {
        const payload = JSON.parse(atob(getAccessToken().split('.')[1] || ''));
        return Number(payload.exp) * 1000 || 0;
    } catch (_) {
        return 0;
    }
}

function getStoredUser() {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || '{}'); }
    catch (_) { return {}; }
}

function scheduleTokenRefresh(expiresIn) {
    if (refreshTimer) clearTimeout(refreshTimer);
    const lifetimeMs = Number(expiresIn) * 1000;
    if (!Number.isFinite(lifetimeMs) || lifetimeMs <= 0) return;

    const expiresAt = Date.now() + lifetimeMs;
    localStorage.setItem(ACCESS_TOKEN_EXPIRES_AT_KEY, String(expiresAt));
    refreshTimer = setTimeout(() => {
        refreshApiSession().catch(() => { /* keep the session for transient failures */ });
    }, Math.max(1000, lifetimeMs - TOKEN_REFRESH_LEEWAY_MS));
}

function storeSession(session) {
    localStorage.setItem(ACCESS_TOKEN_KEY, session.access_token);
    localStorage.setItem(USER_KEY, JSON.stringify(session.user));
    scheduleTokenRefresh(session.expires_in);
    return session;
}

async function clearBrowserCaches() {
    if (!globalThis.caches) return;
    try {
        const names = await caches.keys();
        await Promise.all(names.map(name => caches.delete(name)));
    } catch (_) { /* cache cleanup is best effort */ }
}

function clearSession() {
    localStorage.removeItem(ACCESS_TOKEN_KEY);
    localStorage.removeItem(LEGACY_REFRESH_TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(ACCESS_TOKEN_EXPIRES_AT_KEY);
    if (refreshTimer) clearTimeout(refreshTimer);
    refreshTimer = null;
}

async function requestOnce(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const token = getAccessToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    if (options.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    const response = await fetch(`${API_BASE}${path}`, { ...options, headers, credentials: 'same-origin', cache: 'no-store' });
    if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try { detail = (await response.json()).detail || detail; } catch (_) { /* keep status */ }
        const error = new Error(detail);
        error.status = response.status;
        throw error;
    }
    return response.status === 204 ? null : response.json();
}

async function apiRequest(path, options = {}, allowRefresh = true) {
    try {
        return await requestOnce(path, options);
    } catch (error) {
        if (!allowRefresh || error.status !== 401 || path === '/auth/login' || path === '/auth/refresh') {
            throw error;
        }
        await refreshApiSession();
        return requestOnce(path, options);
    }
}

async function loginWithApi(email, password) {
    return storeSession(await apiRequest('/auth/login', {
        method: 'POST', body: JSON.stringify({ email, password })
    }));
}

async function refreshApiSession() {
    if (refreshPromise) return refreshPromise;

    refreshPromise = (async () => {
        try {
            return storeSession(await apiRequest('/auth/refresh', {
                method: 'POST'
            }, false));
        } catch (error) {
            // A transient/network failure must not sign the user out. Only a
            // server rejection of the refresh token proves the session ended.
            if (error.status === 401) clearSession();
            throw error;
        } finally {
            refreshPromise = null;
        }
    })();
    return refreshPromise;
}

async function getAuthenticatedSession() {
    if (!getAccessToken()) return null;
    const expiresAt = getAccessTokenExpiresAt();
    if (expiresAt > Date.now()) {
        scheduleTokenRefresh((expiresAt - Date.now()) / 1000);
    }
    if (expiresAt && expiresAt - Date.now() <= TOKEN_REFRESH_LEEWAY_MS) {
        try { await refreshApiSession(); } catch (error) {
            if (error.status !== 401) return { access_token: getAccessToken(), user: getStoredUser() };
            return null;
        }
    }
    try {
        const data = await apiRequest('/auth/session');
        return { access_token: getAccessToken(), user: data.user };
    } catch (error) {
        if (error.status === 401) {
            try {
                await refreshApiSession();
                const data = await requestOnce('/auth/session');
                return { access_token: getAccessToken(), user: data.user };
            } catch (refreshError) {
                if (refreshError.status === 401) clearSession();
                return null;
            }
        }
        return null;
    }
}

async function logoutFromApi() {
    try { await apiRequest('/auth/logout', { method: 'POST' }, false); } catch (_) { /* continue local logout */ }
    clearSession();
    localStorage.clear();
    sessionStorage.clear();
    await clearBrowserCaches();
}
