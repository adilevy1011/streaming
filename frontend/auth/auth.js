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

function loginErrorMessage(error) {
    if (error?.status === 403) {
        return 'Your email is not in the allowed email list.';
    }
    if (error?.status === 429) {
        return 'Too many login attempts. Please wait a moment and try again.';
    }
    if (error?.status === 401) {
        return 'Invalid email or password.';
    }
    return 'Unable to log in right now. Please try again.';
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
        errorElement.textContent = loginErrorMessage(error);
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
