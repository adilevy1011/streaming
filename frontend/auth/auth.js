function loginPageUrl() {
    return window.location.protocol === 'file:' ? 'auth/login.html' : '/login';
}

function appHomeUrl() {
    return window.location.protocol === 'file:' ? '../index.html' : '/';
}

function redirectToLogin() {
    if (window.location.pathname.endsWith('/auth/login.html')) return;
    window.location.replace(loginPageUrl());
}

async function requireAuthenticatedSession() {
    const session = await getAuthenticatedSession();
    if (!session) {
        redirectToLogin();
        return null;
    }
    return session;
}

async function handleLogin(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const email = document.getElementById('email').value.trim();
    const password = document.getElementById('password').value;
    const button = form.querySelector('button[type="submit"]');
    const errorElement = document.getElementById('auth-error');
    errorElement.textContent = '';
    button.disabled = true;
    button.textContent = 'Logging in...';
    try {
        await loginWithApi(email, password);
        window.location.replace(appHomeUrl());
    } catch (error) {
        errorElement.textContent = error.message || 'Unable to log in.';
        button.disabled = false;
        button.textContent = 'Log In';
    }
}

async function redirectAuthenticatedLoginPage() {
    const isLoginPage = window.location.pathname === '/login'
        || window.location.pathname.endsWith('/auth/login.html');
    if (!isLoginPage) return;
    if (await getAuthenticatedSession()) window.location.replace(appHomeUrl());
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('login-form')?.addEventListener('submit', handleLogin);
    redirectAuthenticatedLoginPage();
});
