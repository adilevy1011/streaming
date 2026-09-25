let allMedia = [];
let activeView = 'my-library';
let selectedPath = '';
let previewObserver;
let previewGeneration = 0;
let previewManifests = new Map();
let currentUserId = '';
let mediaLoadGeneration = 0;
let mediaScanComplete = false;
let activeProgressByPath = new Map();
let mediaRenderScheduled = false;
let mediaLoadAbortController = null;
let refreshMessageTimer = null;
let adminAccess = false;
let adminUsers = [];
let adminVideos = [];
let selectedAdminUser = null;
let adminUserSearch = '';
let adminVideoSearch = '';
let adminDraftAccess = new Map();
let adminOriginalAccess = new Map();
let adminChangesPending = false;
let adminDraftNewVideosAccess = true;
let adminOriginalNewVideosAccess = true;
const adminCollapsedFolders = new Set();
let adminFolderStateInitialized = false;
const PROGRESS_STORAGE_KEY = 'adlv-video-progress';
const MEDIA_CACHE_KEY = 'adlv-media-library-cache';

async function login() {
    const email = document.getElementById('email').value;
    const password = document.getElementById('password').value;
    try { await loginWithApi(email, password); checkAuth(); }
    catch (error) { document.getElementById('auth-error').innerText = error.message; }
}

async function logout() {
    logoutFromApi();
    checkAuth();
}

async function refreshLibrary() {
    if (!await confirmLibraryRefresh()) return;
    setRefreshState(true);
    try { await loadMedia({ clearCache: true }); }
    finally { setRefreshState(false); }
}

function confirmLibraryRefresh() {
    const modal = document.getElementById('refresh-confirm-modal');
    const cancel = document.getElementById('refresh-confirm-cancel');
    const submit = document.getElementById('refresh-confirm-submit');
    modal.classList.remove('hidden');
    submit.focus();
    return new Promise(resolve => {
        const finish = confirmed => {
            modal.classList.add('hidden');
            cancel.onclick = null;
            submit.onclick = null;
            modal.onclick = null;
            resolve(confirmed);
        };
        cancel.onclick = () => finish(false);
        submit.onclick = () => finish(true);
        modal.onclick = event => { if (event.target === modal) finish(false); };
    });
}

async function refreshCurrentFolder() {
    const folderPath = activeView !== 'my-library' && activeView !== 'all' ? selectedPath : '';
    setRefreshState(true);
    try { await loadMedia(folderPath ? { path: folderPath } : { clearCache: true }); }
    finally { setRefreshState(false); }
}

function setRefreshState(loading) {
    const libraryButton = document.getElementById('refresh-library-button');
    const folderButton = document.getElementById('refresh-folder-button');
    const buttons = [libraryButton, folderButton].filter(Boolean);
    clearInterval(refreshMessageTimer);
    refreshMessageTimer = null;
    if (!loading) {
        buttons.forEach(button => {
            button.disabled = false;
            button.innerText = button === libraryButton ? 'Refresh library' : 'Refresh';
        });
        return;
    }

    const messages = [
        'Working on it...',
        'Fetching videos...',
        'Checking folders...',
        'Looking for videos...',
        'Updating previews...',
        'Rebuilding library...',
        'Saving cache...',
        'Almost there...'
    ];
    let messageIndex = 0;
    const update = () => {
        buttons.forEach(button => {
            button.disabled = true;
            button.innerHTML = `<span class="loading-spinner" aria-hidden="true"></span> ${messages[messageIndex]}`;
        });
        messageIndex = (messageIndex + 1) % messages.length;
    };
    update();
    refreshMessageTimer = setInterval(update, 1200);
}

async function checkAuth() {
    const session = await getAuthenticatedSession();
    currentUserId = session?.user?.id || '';
    adminAccess = false;
    if (session) {
        try { adminAccess = !!(await apiRequest('/profile')).admin_access; }
        catch (error) { console.warn('Unable to load profile access', error); }
    }
    document.getElementById('auth-section').classList.toggle('hidden', !!session);
    document.getElementById('stream-section').classList.toggle('hidden', !session);
    document.getElementById('logout-button').classList.toggle('hidden', !session);
    document.getElementById('refresh-library-button').classList.toggle('hidden', !session);
    document.getElementById('admin-actions-button').classList.toggle('hidden', !session || !adminAccess);
    if (session) {
        showLibrary('my-library');
        loadMedia();
    }
}

async function showAdminActions() {
    if (!adminAccess) return;
    const modal = document.getElementById('admin-modal');
    const status = document.getElementById('admin-status');
    const users = document.getElementById('admin-users');
    const videos = document.getElementById('admin-videos');
    modal.classList.remove('hidden');
    users.innerHTML = '';
    videos.innerHTML = '';
    selectedAdminUser = null;
    adminDraftAccess = new Map();
    adminOriginalAccess = new Map();
    adminChangesPending = false;
    adminDraftNewVideosAccess = true;
    adminOriginalNewVideosAccess = true;
    adminCollapsedFolders.clear();
    adminFolderStateInitialized = false;
    status.innerHTML = '<span class="loading-spinner" role="status" aria-label="Loading admin data"></span> Loading users and videos...';
    try {
        [adminUsers, adminVideos] = await Promise.all([
            apiRequest('/admin/users'),
            apiRequest('/admin/videos')
        ]);
        status.innerText = '';
        adminUserSearch = '';
        adminVideoSearch = '';
        document.getElementById('admin-user-search').value = '';
        document.getElementById('admin-video-search').value = '';
        renderAdminUsers();
        if (adminUsers.length) selectAdminUser(adminUsers[0]);
    } catch (error) {
        status.innerText = `Unable to load admin data: ${error.message}`;
    }
}

function renderAdminUsers() {
    const users = document.getElementById('admin-users');
    users.innerHTML = '';
    const query = adminUserSearch.trim().toLowerCase();
    adminUsers.filter(user => !query || `${user.email || ''} ${user.user_id}`.toLowerCase().includes(query)).forEach(user => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'admin-user-button';
        button.classList.toggle('active', selectedAdminUser?.user_id === user.user_id);
        button.innerText = `${user.email || user.user_id}${user.admin_access ? ' (Admin)' : ''}`;
        button.onclick = () => selectAdminUser(user);
        users.appendChild(button);
    });
}

function selectAdminUser(user) {
    if (selectedAdminUser?.user_id !== user.user_id && adminChangesPending) {
        document.getElementById('admin-status').innerText = 'Save the current changes before selecting another user.';
        return;
    }
    selectedAdminUser = user;
    adminDraftAccess = new Map();
    adminOriginalAccess = new Map();
    adminVideos.forEach(video => {
        const access = [...(video.user_access || [])];
        adminDraftAccess.set(video.path, access);
        adminOriginalAccess.set(video.path, [...access]);
    });
    adminDraftNewVideosAccess = user.new_videos_access !== false;
    adminOriginalNewVideosAccess = adminDraftNewVideosAccess;
    adminChangesPending = false;
    renderAdminUsers();
    const title = document.getElementById('admin-video-title');
    title.innerText = user.admin_access ? `${user.email} - all videos (admin)` : `Videos for ${user.email}`;
    renderAdminVideos();
}

function adminVideoIsAccessible(video) {
    if (!selectedAdminUser) return false;
    if (selectedAdminUser.admin_access) return true;
    const access = adminDraftAccess.get(video.path) || [];
    return !access.length
        ? adminDraftNewVideosAccess
        : access.includes(selectedAdminUser.email.toLowerCase());
}

function buildAdminVideoTree(videos) {
    const root = { folders: new Map(), videos: [] };
    videos.forEach(video => {
        const parts = video.path.split('/').filter(Boolean);
        const folderParts = parts.slice(1, -1); // Ignore the tab-level root.
        let node = root;
        folderParts.forEach((folderName, index) => {
            if (!node.folders.has(folderName)) {
                const parentPath = folderParts.slice(0, index).join('/');
                node.folders.set(folderName, {
                    name: folderName,
                    key: parentPath ? `${parentPath}/${folderName}` : folderName,
                    folders: new Map(),
                    videos: []
                });
            }
            node = node.folders.get(folderName);
        });
        node.videos.push(video);
    });
    return root;
}

function renderAdminFolder(folder, depth) {
    const container = document.createElement('div');
    container.className = 'admin-folder';
    const descendants = [];
    const collect = node => {
        descendants.push(...node.videos);
        node.folders.forEach(collect);
    };
    collect(folder);
    if (descendants.length === 1) return renderAdminVideoRow(descendants[0]);

    const row = document.createElement('div');
    row.className = 'admin-folder-row';
    row.style.marginLeft = `${depth * 18}px`;
    const toggle = document.createElement('button');
    toggle.className = 'admin-folder-toggle';
    toggle.type = 'button';
    const collapsed = adminCollapsedFolders.has(folder.key);
    toggle.innerText = collapsed ? '▸' : '▾';
    toggle.setAttribute('aria-label', `${collapsed ? 'Expand' : 'Collapse'} ${folder.name}`);
    toggle.setAttribute('aria-expanded', String(!collapsed));
    toggle.onclick = () => {
        if (adminCollapsedFolders.has(folder.key)) adminCollapsedFolders.delete(folder.key);
        else adminCollapsedFolders.add(folder.key);
        renderAdminVideos();
    };
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    const accessibleCount = descendants.filter(adminVideoIsAccessible).length;
    checkbox.checked = accessibleCount === descendants.length;
    checkbox.indeterminate = accessibleCount > 0 && accessibleCount < descendants.length;
    checkbox.disabled = selectedAdminUser?.admin_access;
    checkbox.onchange = () => {
        descendants.forEach(video => {
            const next = adminVideoAccessForUser(video, selectedAdminUser, checkbox.checked);
            adminDraftAccess.set(video.path, next);
        });
        adminChangesPending = true;
        document.getElementById('admin-status').innerText = 'Unsaved changes';
        renderAdminVideos();
    };
    const name = document.createElement('span');
    name.className = 'admin-folder-name';
    name.innerText = `📁 ${folder.name}`;
    const count = document.createElement('span');
    count.className = 'admin-folder-count';
    count.innerText = `(${descendants.length})`;
    row.append(toggle, checkbox, name, count);
    container.appendChild(row);

    const children = document.createElement('div');
    children.className = 'admin-folder-children';
    children.classList.toggle('collapsed', collapsed);
    folder.folders.forEach(child => children.appendChild(renderAdminFolder(child, depth + 1)));
    folder.videos
        .slice()
        .sort((left, right) => left.path.localeCompare(right.path, undefined, { numeric: true, sensitivity: 'base' }))
        .forEach(video => children.appendChild(renderAdminVideoRow(video)));
    container.appendChild(children);
    return container;
}

function renderAdminVideoRow(video) {
    const row = document.createElement('label');
    row.className = 'admin-video-row';
    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = adminVideoIsAccessible(video);
    checkbox.disabled = selectedAdminUser?.admin_access;
    checkbox.onchange = () => updateAdminVideoAccess(video, checkbox);
    const name = document.createElement('span');
    name.className = 'admin-video-name';
    const parts = video.path.split('/').filter(Boolean);
    name.innerText = (parts[parts.length - 1] || video.path).replace(/\.[^.]+$/, '');
    name.title = video.path;
    row.append(checkbox, name);
    return row;
}

function renderAdminVideoTree(tree, container) {
    tree.folders.forEach(folder => container.appendChild(renderAdminFolder(folder, 0)));
    tree.videos
        .slice()
        .sort((left, right) => left.path.localeCompare(right.path, undefined, { numeric: true, sensitivity: 'base' }))
        .forEach(video => container.appendChild(renderAdminVideoRow(video)));
}

function renderAdminVideos() {
    const videos = document.getElementById('admin-videos');
    videos.innerHTML = '';
    const selectAll = document.getElementById('admin-select-all');
    const deselectAll = document.getElementById('admin-deselect-all');
    const search = document.getElementById('admin-video-search');
    const bulkActions = document.querySelector('.admin-bulk-actions');
    const permissionMessage = document.getElementById('admin-video-permission-message');
    const newVideosRow = document.getElementById('admin-new-videos-access-row');
    const newVideosCheckbox = document.getElementById('admin-new-videos-access');
    const query = adminVideoSearch.trim().toLowerCase();
    const visibleVideos = adminVideos.filter(video => !query || video.path.toLowerCase().includes(query));
    selectAll.disabled = !selectedAdminUser || selectedAdminUser.admin_access;
    deselectAll.disabled = !selectedAdminUser || selectedAdminUser.admin_access;
    const isAdminUser = !!selectedAdminUser?.admin_access;
    search.classList.toggle('hidden', isAdminUser);
    bulkActions.classList.toggle('hidden', isAdminUser);
    permissionMessage.classList.toggle('hidden', !isAdminUser);
    newVideosRow.classList.toggle('hidden', isAdminUser);
    newVideosCheckbox.checked = adminDraftNewVideosAccess;
    newVideosCheckbox.disabled = isAdminUser;
    if (!selectedAdminUser) return;
    const tree = buildAdminVideoTree(visibleVideos);
    if (!adminFolderStateInitialized) {
        const collapseFolders = node => node.folders.forEach(folder => {
            adminCollapsedFolders.add(folder.key);
            collapseFolders(folder);
        });
        collapseFolders(tree);
        adminFolderStateInitialized = true;
    }
    renderAdminVideoTree(tree, videos);
}

function adminVideoAccessForUser(video, user, checked, currentAccess = adminDraftAccess.get(video.path) || []) {
    const email = user.email.toLowerCase();
    const allowsNewVideos = user === selectedAdminUser ? adminDraftNewVideosAccess : user.new_videos_access !== false;
    const previous = [...currentAccess];
    let next = [...previous];
    if (checked) {
        if (!next.length) return allowsNewVideos ? next : [email];
        if (!next.includes(email)) next.push(email);
    } else if (!next.length) {
        next = adminUsers.filter(item => !item.admin_access && item.email).map(item => item.email.toLowerCase());
        next = next.filter(item => item !== email);
    } else {
        next = next.filter(item => item !== email);
    }
    return next;
}

async function setAdminVideoAccess(video, next) {
    const updated = await apiRequest(`/admin/video-access?path=${encodeURIComponent(video.path)}`, {
        method: 'PATCH',
        body: JSON.stringify({ user_access: next })
    });
    video.user_access = updated.user_access || [];
}

async function updateAdminVideoAccess(video, checkbox) {
    if (!selectedAdminUser || selectedAdminUser.admin_access) return;
    const next = adminVideoAccessForUser(video, selectedAdminUser, checkbox.checked);
    adminDraftAccess.set(video.path, next);
    adminChangesPending = true;
    document.getElementById('admin-status').innerText = 'Unsaved changes';
    renderAdminVideos();
}

function updateAdminNewVideosAccess(checkbox) {
    if (!selectedAdminUser || selectedAdminUser.admin_access) return;
    adminDraftNewVideosAccess = checkbox.checked;
    adminChangesPending = true;
    document.getElementById('admin-status').innerText = 'Unsaved changes';
    renderAdminVideos();
}

async function setAllAdminVideoAccess(checked) {
    if (!selectedAdminUser || selectedAdminUser.admin_access) return;
    const videos = adminVideos.filter(video => {
        const query = adminVideoSearch.trim().toLowerCase();
        return !query || video.path.toLowerCase().includes(query);
    });
    videos.forEach(video => adminDraftAccess.set(
        video.path,
        adminVideoAccessForUser(video, selectedAdminUser, checked)
    ));
    adminChangesPending = true;
    document.getElementById('admin-status').innerText = 'Unsaved changes';
    renderAdminVideos();
}

function sameAccessList(left, right) {
    return left.length === right.length && left.every((email, index) => email === right[index]);
}

async function saveAdminChanges() {
    if (!selectedAdminUser || selectedAdminUser.admin_access || !adminChangesPending) return;
    const changes = adminVideos.filter(video => !sameAccessList(
        adminDraftAccess.get(video.path) || [],
        adminOriginalAccess.get(video.path) || []
    ));
    const newVideosAccessChanged = adminDraftNewVideosAccess !== adminOriginalNewVideosAccess;
    if (!changes.length && !newVideosAccessChanged) {
        adminChangesPending = false;
        renderAdminVideos();
        return;
    }
    const loadingModal = document.getElementById('admin-save-modal');
    loadingModal.classList.remove('hidden');
    try {
        const requests = changes.map(video => setAdminVideoAccess(
            video,
            adminDraftAccess.get(video.path) || []
        ));
        if (newVideosAccessChanged) {
            requests.push(apiRequest(`/admin/user-access?user_id=${encodeURIComponent(selectedAdminUser.user_id)}`, {
                method: 'PATCH',
                body: JSON.stringify({ new_videos_access: adminDraftNewVideosAccess })
            }));
        }
        const results = await Promise.all(requests);
        changes.forEach(video => {
            const saved = [...(video.user_access || [])];
            adminOriginalAccess.set(video.path, saved);
            adminDraftAccess.set(video.path, [...saved]);
        });
        if (newVideosAccessChanged) {
            const updatedUser = results[changes.length];
            selectedAdminUser.new_videos_access = updatedUser.new_videos_access;
            adminUsers = adminUsers.map(user => user.user_id === selectedAdminUser.user_id ? selectedAdminUser : user);
            adminOriginalNewVideosAccess = adminDraftNewVideosAccess;
        }
        adminChangesPending = false;
        document.getElementById('admin-status').innerText = 'Changes saved';
        renderAdminVideos();
    } catch (error) {
        document.getElementById('admin-status').innerText = `Unable to save changes: ${error.message}`;
    } finally {
        loadingModal.classList.add('hidden');
    }
}

async function* listVideos(path = '', signal) {
    const headers = new Headers();
    const token = getAccessToken();
    if (token) headers.set('Authorization', `Bearer ${token}`);
    const query = path ? `?path=${encodeURIComponent(path)}` : '';
    const response = await fetch(`${API_BASE}/media/stream${query}`, { headers, cache: 'no-store', signal });
    if (!response.ok) {
        let detail = `Request failed (${response.status})`;
        try { detail = (await response.json()).detail || detail; } catch (_) {}
        throw new Error(detail);
    }
    if (!response.body) throw new Error('The media stream is unavailable.');

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    try {
        while (true) {
            const { value, done } = await reader.read();
            buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';
            for (const line of lines) {
                if (line.trim()) yield JSON.parse(line);
            }
            if (done) break;
        }
        if (buffer.trim()) yield JSON.parse(buffer);
    } finally {
        reader.releaseLock();
    }
}

function scheduleMediaRender() {
    if (mediaRenderScheduled) return;
    mediaRenderScheduled = true;
    const render = () => {
        mediaRenderScheduled = false;
        renderMedia();
    };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(render);
    else setTimeout(render, 0);
}

function getMediaCacheKey() {
    return `${MEDIA_CACHE_KEY}:${currentUserId}`;
}

function loadCachedMedia() {
    if (!currentUserId) return [];
    try {
        const cached = JSON.parse(localStorage.getItem(getMediaCacheKey()) || '[]');
        return Array.isArray(cached) ? cached.filter(file => file?.path && file?.name) : [];
    } catch (error) {
        console.warn('Unable to read cached media library', error);
        return [];
    }
}

function saveCachedMedia(media) {
    if (!currentUserId) return;
    try { localStorage.setItem(getMediaCacheKey(), JSON.stringify(media)); }
    catch (error) { console.warn('Unable to cache media library', error); }
}

function isPathWithin(filePath, folderPath) {
    return filePath === folderPath || filePath.startsWith(`${folderPath}/`);
}

async function loadMedia(options = {}) {
    const refreshPath = options.path || '';
    const fullRefresh = options.clearCache || !refreshPath;
    const generation = ++mediaLoadGeneration;
    mediaLoadAbortController?.abort();
    mediaLoadAbortController = new AbortController();
    const { signal } = mediaLoadAbortController;
    const listElement = document.getElementById('media-list');
    const status = document.getElementById('media-status');
    if (options.clearCache) {
        try { localStorage.removeItem(getMediaCacheKey()); } catch (_) {}
    }
    allMedia = loadCachedMedia();
    renderRecentlyAdded();
    if (refreshPath) allMedia = allMedia.filter(file => !isPathWithin(file.path, refreshPath));
    const initialRoots = new Set(mediaRoots());
    if (refreshPath) initialRoots.add(refreshPath.split('/')[0]);
    mediaScanComplete = false;
    renderNavigation(initialRoots);
    activeProgressByPath = new Map();
    listElement.innerHTML = '';
    status.innerHTML = '<span class="loading-spinner" role="status" aria-label="Loading videos"></span>';
    if (allMedia.length) {
        renderMedia();
        status.innerHTML = '';
    }
    let receivedFreshMedia = false;
    const streamedRoots = new Set(mediaRoots());
    const displayedRoots = new Set(initialRoots);
    try {
        const previewPromise = loadPreviewManifests();
        void loadContinueWatching(generation).catch(error => {
            if (generation === mediaLoadGeneration) console.warn('Unable to load Continue watching items', error);
        });
        for await (const video of listVideos(refreshPath, signal)) {
            if (generation !== mediaLoadGeneration) return;
            if (!receivedFreshMedia) {
                receivedFreshMedia = true;
                if (fullRefresh) {
                    allMedia = [];
                    streamedRoots.clear();
                }
                listElement.innerHTML = '';
                status.innerHTML = '';
            }
            const root = mediaCategory(video);
            const isNewRoot = !streamedRoots.has(root);
            streamedRoots.add(root);
            allMedia.push(video);
            if (isNewRoot) {
                displayedRoots.add(root);
                renderNavigation(displayedRoots);
            }
            scheduleMediaRender();

            if (allMedia.length === 1 || allMedia.length % 50 === 0) {
                await new Promise(resolve => setTimeout(resolve, 0));
            }
        }
        if (!receivedFreshMedia) {
            if (fullRefresh) {
                allMedia = [];
                try { localStorage.removeItem(getMediaCacheKey()); } catch (_) {}
            }
        }
        saveCachedMedia(allMedia);
        mediaScanComplete = true;
        renderNavigation();
        renderContinueWatching();
        renderRecentlyAdded();
        renderWatchAgain();
        if (generation !== mediaLoadGeneration) return;
        await previewPromise;
        if (generation !== mediaLoadGeneration) return;
        addVideoPreviews();
        status.innerText = '';
    } catch (error) {
        if (error.name === 'AbortError') return;
        if (generation !== mediaLoadGeneration) return;
        console.error(error);
        status.innerText = `Failed to load videos: ${error.message || 'unknown storage error'}`;
    }
}

async function loadPreviewManifests() {
    try {
        const data = await apiRequest('/previews');
        previewManifests = new Map((data || []).map(manifest => [manifest.media_path, manifest]));
    } catch (error) {
        console.warn('Pre-generated previews are not available yet', error.message);
        previewManifests = new Map();
    }
}

function mediaCategory(file) {
    return file.path.split('/')[0] || '';
}

function mediaCategoryLabel(path) {
    return path.split('/').filter(Boolean).pop() || path;
}

function capitalizeFirst(value) {
    return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

function mediaRoots() {
    return [...new Set(allMedia.map(mediaCategory).filter(Boolean))].sort(naturalCompare);
}

function renderNavigation(rootOverride = null) {
    const tabs = document.getElementById('media-tabs');
    if (!tabs) return;
    tabs.innerHTML = '';
    const roots = rootOverride ? [...rootOverride].sort(naturalCompare) : mediaRoots();
    roots.forEach(root => {
        const button = document.createElement('button');
        button.className = 'nav-button';
        button.dataset.view = root;
        button.innerText = capitalizeFirst(mediaCategoryLabel(root));
        button.onclick = () => showLibrary(root);
        tabs.appendChild(button);
    });
    document.querySelectorAll('.nav-button').forEach(button => {
        button.classList.toggle('active', button.dataset.view === activeView || (activeView !== 'my-library' && activeView !== 'all' && button.dataset.view === selectedPath.split('/')[0]));
    });
}

function showLibrary(view) {
    activeView = view;
    const isMyLibrary = view === 'my-library';
    if (!isMyLibrary && view !== 'all') selectedPath = view;
    document.getElementById('library-view').classList.toggle('hidden', isMyLibrary);
    renderNavigation();
    if (isMyLibrary) {
        renderContinueWatching();
        renderRecentlyAdded();
        renderWatchAgain();
        return;
    }
    document.getElementById('library-title').innerText = view === 'all' ? 'All Videos' : mediaCategoryLabel(view);
    document.getElementById('search').placeholder = `Search ${view === 'all' ? 'videos' : mediaCategoryLabel(view)}...`;
    renderContinueWatching();
    renderRecentlyAdded();
    renderWatchAgain();
    renderMedia();
}

function getLocalProgress() {
    try { return JSON.parse(localStorage.getItem(PROGRESS_STORAGE_KEY) || '{}'); }
    catch (error) { console.warn('Unable to read local playback progress', error); return {}; }
}

function setLocalProgress(progress) {
    try {
        const allProgress = getLocalProgress();
        allProgress[progress.key] = progress;
        localStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(allProgress));
    } catch (error) { console.warn('Unable to cache local playback progress', error); }
}

function removeLocalProgress(path) {
    try {
        const allProgress = getLocalProgress();
        delete allProgress[getProgressKey(currentUserId, path)];
        localStorage.setItem(PROGRESS_STORAGE_KEY, JSON.stringify(allProgress));
    } catch (error) { console.warn('Unable to remove cached playback progress', error); }
}

function getProgressKey(userId, path) { return `${userId}:${path}`; }

function progressTimestamp(progress) {
    const timestamp = Date.parse(progress?.updated_at || '');
    return Number.isFinite(timestamp) ? timestamp : 0;
}

function createVideoPreview(file) {
    const preview = document.createElement('div');
    preview.className = 'preview';
    const manifest = previewManifests.get(file.path);
    const artwork = file.previewImagePath;
    preview.dataset.previewKind = artwork ? 'image' : manifest ? 'sprite' : 'pending';
    preview.dataset.previewPath = file.path;
    if (artwork) {
        preview.dataset.previewImagePath = artwork;
        preview.dataset.previewImageUpdatedAt = file.previewImageUpdatedAt || '';
    }
    preview.setAttribute('aria-label', artwork || manifest
        ? `Preview for ${file.name || file.path}`
        : `Preview unavailable for ${file.name || file.path}`);
    return preview;
}

function renderContinueWatching(progressByPath = activeProgressByPath) {
    const section = document.getElementById('continue-section');
    const list = document.getElementById('continue-list');
    list.innerHTML = '';
    section.classList.add('hidden');
    const mediaByPath = new Map(allMedia.map(file => [file.path, file]));
    const items = [...progressByPath.values()]
        .filter(progress => !progress.completed && Number(progress.position_seconds) > 0)
        .filter(progress => !mediaScanComplete || mediaByPath.has(progress.path || progress.media_path))
        .filter(progress => {
            const position = Number(progress.position_seconds);
            const duration = Number(progress.duration_seconds);
            return !Number.isFinite(duration) || duration <= 0 || position < duration - 10;
        })
        .sort((left, right) => new Date(right.updated_at || 0) - new Date(left.updated_at || 0))
        .slice(0, 8);

    items.forEach(progress => {
        const path = progress.path || progress.media_path;
        const file = mediaByPath.get(path) || { path, name: path.split('/').pop() || path };
        const position = Number(progress.position_seconds);
        const duration = Number(progress.duration_seconds);
        const item = document.createElement('li');
        item.className = 'continue-item media-preview-card';
        item.tabIndex = 0;
        const itemHeader = document.createElement('div');
        itemHeader.className = 'continue-item-header';
        const name = document.createElement('span');
        name.className = 'continue-name';
        name.innerText = file.name.replace(/\.[^.]+$/, '');
        const menuButton = document.createElement('button');
        menuButton.className = 'continue-menu-button';
        menuButton.type = 'button';
        menuButton.innerText = '...';
        menuButton.setAttribute('aria-label', `Options for ${name.innerText}`);
        menuButton.title = 'Options';
        const menu = document.createElement('div');
        menu.className = 'continue-menu hidden';
        const addMenuAction = (label, action) => {
            const button = document.createElement('button');
            button.type = 'button';
            button.innerText = label;
            button.onclick = event => { event.stopPropagation(); menu.classList.add('hidden'); action(); };
            menu.appendChild(button);
        };
        addMenuAction('Restart', () => restartProgress(path, progress));
        addMenuAction('Continue', () => playMedia(path));
        addMenuAction('Remove from your list', () => removeProgress(path));
        menuButton.onclick = event => {
            event.stopPropagation();
            document.querySelectorAll('.continue-menu').forEach(other => { if (other !== menu) other.classList.add('hidden'); });
            menu.classList.toggle('hidden');
        };
        itemHeader.append(name, menuButton);
        const detail = document.createElement('small');
        detail.className = 'muted';
        detail.innerText = Number.isFinite(duration) && duration > 0
            ? `${formatTime(position)} of ${formatTime(duration)}`
            : `Resume at ${formatTime(position)}`;
        const track = document.createElement('div');
        track.className = 'progress-track';
        const fill = document.createElement('div');
        fill.className = 'progress-fill';
        fill.style.width = Number.isFinite(duration) && duration > 0
            ? `${Math.min(100, Math.max(0, position / duration * 100))}%` : '8%';
        track.appendChild(fill);
        const content = document.createElement('div');
        content.className = 'preview-card-content';
        content.append(itemHeader, menu, detail, track);
        item.append(createVideoPreview(file), content);
        item.onclick = () => playMedia(path, path);
        item.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') playMedia(path, path); };
        list.appendChild(item);
    });
    section.classList.toggle('hidden', activeView !== 'my-library' || items.length === 0);
}

function renderWatchAgain(progressByPath = activeProgressByPath) {
    const section = document.getElementById('watch-again-section');
    const list = document.getElementById('watch-again-list');
    list.innerHTML = '';
    const mediaByPath = new Map(allMedia.map(file => [file.path, file]));
    const items = [...progressByPath.values()]
        .filter(progress => progress.completed)
        .filter(progress => !mediaScanComplete || mediaByPath.has(progress.path || progress.media_path))
        .sort((left, right) => progressTimestamp(right) - progressTimestamp(left));

    items.forEach(progress => {
        const path = progress.path || progress.media_path;
        const file = mediaByPath.get(path) || { path, name: path.split('/').pop() || path };
        const item = document.createElement('li');
        item.className = 'continue-item media-preview-card';
        item.tabIndex = 0;
        const itemHeader = document.createElement('div');
        itemHeader.className = 'continue-item-header';
        const name = document.createElement('span');
        name.className = 'continue-name';
        name.innerText = file.name.replace(/\.[^.]+$/, '');
        const removeButton = document.createElement('button');
        removeButton.className = 'remove-progress-button';
        removeButton.type = 'button';
        removeButton.innerText = ' - ';
        removeButton.setAttribute('aria-label', `Remove ${name.innerText} from watch again`);
        removeButton.title = 'Remove from watch again';
        removeButton.onclick = event => { event.stopPropagation(); removeProgress(path); };
        itemHeader.append(name, removeButton);
        const detail = document.createElement('small');
        detail.className = 'muted';
        detail.innerText = 'Completed';
        const content = document.createElement('div');
        content.className = 'preview-card-content';
        content.append(itemHeader, detail);
        item.append(createVideoPreview(file), content);
        item.onclick = () => playMedia(path);
        item.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') playMedia(path); };
        list.appendChild(item);
    });
    section.classList.toggle('hidden', activeView !== 'my-library' || items.length === 0);
    addVideoPreviews();
}

function renderRecentlyAdded() {
    const section = document.getElementById('recently-added-section');
    const list = document.getElementById('recently-added-list');
    list.innerHTML = '';
    const items = allMedia
        .filter(file => file?.path)
        .slice()
        .sort((left, right) => new Date(right.uploadedAt || 0) - new Date(left.uploadedAt || 0))
        .slice(0, 3);

    items.forEach(file => {
        const item = document.createElement('li');
        item.className = 'continue-item media-preview-card';
        item.tabIndex = 0;
        const name = document.createElement('span');
        name.className = 'continue-name';
        name.innerText = (file.name || file.path.split('/').pop() || file.path).replace(/\.[^.]+$/, '');
        const detail = document.createElement('small');
        detail.className = 'muted';
        const addedAt = new Date(file.uploadedAt || 0);
        detail.innerText = Number.isNaN(addedAt.getTime()) || !file.uploadedAt
            ? 'Recently added'
            : `Added ${addedAt.toLocaleDateString()}`;
        const content = document.createElement('div');
        content.className = 'preview-card-content';
        content.append(name, detail);
        item.append(createVideoPreview(file), content);
        item.onclick = () => playMedia(file.path);
        item.onkeydown = event => {
            if (event.key === 'Enter' || event.key === ' ') playMedia(file.path);
        };
        list.appendChild(item);
    });
    section.classList.toggle('hidden', activeView !== 'my-library' || items.length === 0);
}

async function removeProgress(path) {
    try {
        await apiRequest(`/progress?media_path=${encodeURIComponent(path)}`, { method: 'DELETE' });
        activeProgressByPath.delete(path);
        removeLocalProgress(path);
        renderContinueWatching();
        renderWatchAgain();
    } catch (error) {
        console.warn('Unable to remove playback progress', error);
    }
}

async function restartProgress(path, progress) {
    const reset = {
        ...progress,
        path,
        media_path: path,
        position_seconds: 0,
        completed: false,
        updated_at: new Date().toISOString()
    };
    try {
        await apiRequest('/progress', { method: 'POST', body: JSON.stringify({
            media_path: path,
            position_seconds: 0,
            duration_seconds: Number.isFinite(Number(progress.duration_seconds)) ? Number(progress.duration_seconds) : null,
            completed: false,
            updated_at: reset.updated_at
        }) });
        activeProgressByPath.set(path, reset);
        setLocalProgress({ ...reset, key: getProgressKey(currentUserId, path) });
        playMedia(path);
    } catch (error) {
        console.warn('Unable to restart playback', error);
    }
}

async function loadContinueWatching(generation) {
    if (!currentUserId) return;

    const localProgress = getLocalProgress();
    const progressByPath = new Map();
    Object.values(localProgress).forEach(progress => {
        if (progress.key?.startsWith(`${currentUserId}:`)) progressByPath.set(progress.path, progress);
    });
    activeProgressByPath = progressByPath;
    renderContinueWatching(progressByPath);
    renderWatchAgain(progressByPath);

    try {
        const data = await apiRequest('/progress');
        const syncs = [];
        const remotePaths = new Set();
        (data || []).forEach(progress => {
            remotePaths.add(progress.media_path);
            const local = progressByPath.get(progress.media_path);
        const remote = {
                ...progress,
                key: getProgressKey(currentUserId, progress.media_path),
                path: progress.media_path
            };
            if (!local || progressTimestamp(remote) >= progressTimestamp(local)) {
                progressByPath.set(progress.media_path, remote);
                setLocalProgress(remote);
            } else {
                syncs.push(saveProgressToApi(local));
            }
        });
        progressByPath.forEach((local, mediaPath) => {
            // A successful progress fetch is authoritative. Do not write a
            // stale cached row back after it was removed from the database.
            if (!remotePaths.has(mediaPath)) {
                progressByPath.delete(mediaPath);
                removeLocalProgress(mediaPath);
            }
        });
        await Promise.all(syncs);
    } catch (error) {
        console.warn('Unable to load Continue watching items', error);
    }
    if (generation === mediaLoadGeneration) renderContinueWatching(progressByPath);
    if (generation === mediaLoadGeneration) renderWatchAgain(progressByPath);
}

async function saveProgressToApi(progress) {
    const payload = {
        user_id: currentUserId,
        media_path: progress.path || progress.media_path,
        position_seconds: Math.max(0, Number(progress.position_seconds) || 0),
        duration_seconds: Number.isFinite(Number(progress.duration_seconds))
            ? Number(progress.duration_seconds) : null,
        completed: !!progress.completed,
        updated_at: new Date().toISOString()
    };
    try {
        await apiRequest('/progress', { method: 'POST', body: JSON.stringify(payload) });
    } catch (error) {
        console.warn('Unable to sync local playback progress', error);
        return;
    }
    setLocalProgress({ ...progress, ...payload, key: getProgressKey(currentUserId, payload.media_path), path: payload.media_path });
}

function formatTime(seconds) {
    const value = Math.max(0, Math.floor(seconds));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

function naturalCompare(left, right) {
    return left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' });
}

function renderFolderButton(label, subtitle, onClick, artwork = null) {
    const li = document.createElement('li');
    li.className = 'media-item library-card';
    li.tabIndex = 0;
    let leading = document.createElement('span');
    if (artwork?.path) {
        leading = document.createElement('div');
        leading.className = 'preview';
        leading.dataset.previewKind = 'image';
        leading.dataset.previewImagePath = artwork.path;
        leading.dataset.previewImageUpdatedAt = artwork.updatedAt || '';
        leading.dataset.previewFallback = 'folder';
        leading.setAttribute('aria-label', `Preview for ${label}`);
    } else {
        leading.className = 'folder-icon';
        leading.innerText = '\u{1F4C1}';
    }
    const copy = document.createElement('span');
    copy.className = 'media-copy';
    const title = document.createElement('span');
    title.className = 'media-name';
    title.innerText = label;
    const detail = document.createElement('small');
    detail.className = 'muted';
    detail.innerText = subtitle;
    copy.append(title, document.createElement('br'), detail);
    li.append(leading, copy);
    li.onclick = onClick;
    li.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') onClick(); };
    return li;
}

function openFolder(folderPath) {
    selectedPath = folderPath;
    document.getElementById('search').value = '';
    document.getElementById('library-title').innerText = mediaCategoryLabel(folderPath);
    document.getElementById('search').placeholder = `Search ${mediaCategoryLabel(folderPath)}...`;
    renderNavigation();
    renderMedia();
}

function setSpriteFrame(element, manifest, spriteUrl, timeSeconds = 0) {
    const frame = Math.max(0, Math.floor(timeSeconds / manifest.interval_seconds));
    const capacity = manifest.columns * manifest.rows;
    const sheetIndex = Math.min(Math.floor(frame / capacity), manifest.sheets.length - 1);
    const cell = frame % capacity;
    const column = cell % manifest.columns;
    const row = Math.floor(cell / manifest.columns);
    element.style.backgroundImage = `url("${spriteUrl}")`;
    element.style.backgroundSize = `${manifest.columns * 100}% ${manifest.rows * 100}%`;
    element.style.backgroundPosition = `${manifest.columns === 1 ? 0 : (column / (manifest.columns - 1)) * 100}% ${manifest.rows === 1 ? 0 : (row / (manifest.rows - 1)) * 100}%`;
    element.dataset.spriteSheetIndex = String(sheetIndex);
}

function mediaFileUrl(path, cache = '', version = '') {
    const cacheQuery = cache ? `&cache=${encodeURIComponent(cache)}` : '';
    const versionQuery = version ? `&v=${encodeURIComponent(version)}` : '';
    return `/api/media/file/${path.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(getAccessToken())}${cacheQuery}${versionQuery}`;
}

function loadSpritePreview(preview, manifest) {
    const spritePath = manifest?.sheets?.[0];
    if (!manifest || !spritePath) return Promise.resolve();
    const spriteUrl = mediaFileUrl(spritePath, 'preview', manifest.updated_at || '');
    const image = new Image();
    preview.classList.add('preview-loading');
    return new Promise(resolve => {
        image.onload = () => {
            if (document.contains(preview)) {
                preview.classList.remove('preview-loading');
                setSpriteFrame(preview, manifest, spriteUrl);
                preview.dataset.previewLoaded = 'true';
            }
            resolve();
        };
        image.onerror = () => {
            preview.classList.remove('preview-loading');
            preview.dataset.previewError = 'true';
            resolve();
        };
        image.src = spriteUrl;
    });
}

async function addVideoPreviews() {
    const generation = ++previewGeneration;
    if (previewObserver) previewObserver.disconnect();
    const queue = [];
    let activeLoads = 0;

    const loadNextPreview = async () => {
        if (generation !== previewGeneration || activeLoads >= 2) return;
        const preview = queue.shift();
        if (!preview) return;
        activeLoads += 1;
        try {
            const manifest = previewManifests.get(preview.dataset.previewPath);
            if (generation !== previewGeneration || !document.contains(preview)) return;
            const imagePath = preview.dataset.previewImagePath;
            if (imagePath) {
                const image = new Image();
                const imageVersion = preview.dataset.previewImageUpdatedAt;
                preview.classList.add('preview-loading');
                await new Promise(resolve => {
                    image.onload = () => {
                        if (generation === previewGeneration && document.contains(preview)) {
                            preview.classList.remove('preview-loading');
                            preview.style.backgroundImage = `url("${mediaFileUrl(imagePath, 'preview', imageVersion)}")`;
                            preview.style.backgroundSize = 'contain';
                            preview.style.backgroundRepeat = 'no-repeat';
                            preview.style.backgroundPosition = 'center';
                            preview.dataset.previewLoaded = 'true';
                        }
                        resolve();
                    };
                    image.onerror = () => {
                        if (manifest?.sheets?.[0]) {
                            loadSpritePreview(preview, manifest).then(resolve);
                        } else {
                            preview.classList.remove('preview-loading');
                            if (preview.dataset.previewFallback === 'folder') {
                                preview.className = 'folder-icon';
                                preview.innerText = '\u{1F4C1}';
                            }
                            resolve();
                        }
                    };
                    image.src = mediaFileUrl(imagePath, 'preview', imageVersion);
                });
            } else {
                await loadSpritePreview(preview, manifest);
            }
        } finally {
            activeLoads -= 1;
            loadNextPreview();
            loadNextPreview();
        }
    };

    previewObserver = new IntersectionObserver(entries => {
        entries.forEach(entry => {
            if (!entry.isIntersecting) return;
            const preview = entry.target;
            previewObserver.unobserve(preview);
            if (preview.dataset.previewLoaded) return;
            preview.dataset.previewQueued = 'true';
            queue.push(preview);
            loadNextPreview();
        });
    }, { rootMargin: '120px' });
    document.querySelectorAll('[data-preview-kind="sprite"], [data-preview-kind="image"], [data-preview-kind="pending"]').forEach(preview => {
        if (preview.dataset.previewLoaded) return;
        delete preview.dataset.previewQueued;
        previewObserver.observe(preview);
    });
}



function renderMedia() {
    const listElement = document.getElementById('media-list');
    const query = document.getElementById('search').value.trim().toLowerCase();
    listElement.innerHTML = '';

    const existingBackButton = document.getElementById('folder-back-button');
    if (existingBackButton) existingBackButton.remove();

    const browsingCategory = activeView !== 'my-library' && activeView !== 'all';
    const browsePath = browsingCategory ? selectedPath : '';
    const folderPaths = new Set();
    const folderVideoCounts = new Map();
    const folderArtworkByPath = new Map();

    // Build these indexes once per render instead of repeatedly filtering allMedia
    // for every folder card and every flattening decision.
    allMedia.forEach(file => {
        Object.entries(file.folderArtworks || {}).forEach(([folderPath, artwork]) => {
            if (!folderArtworkByPath.has(folderPath)) folderArtworkByPath.set(folderPath, artwork);
        });
        const parts = file.path.split('/');
        for (let index = 1; index < parts.length; index += 1) {
            const folderPath = parts.slice(0, index).join('/');
            folderVideoCounts.set(folderPath, (folderVideoCounts.get(folderPath) || 0) + 1);
            if (browsingCategory && file.path.startsWith(`${browsePath}/`) && index >= browsePath.split('/').length + 1) {
                folderPaths.add(folderPath);
            }
        }
    });
    if (browsingCategory) {
        // The index above includes descendants; retain only immediate children.
        const depth = browsePath.split('/').filter(Boolean).length + 1;
        [...folderPaths].forEach(folderPath => {
            if (folderPath.split('/').filter(Boolean).length !== depth) folderPaths.delete(folderPath);
        });
    }

    if (browsingCategory && browsePath !== activeView) {
        const parentPath = browsePath.split('/').slice(0, -1).join('/');
        const backButton = document.createElement('button');
        backButton.id = 'folder-back-button';
        backButton.className = 'folder-back-button';
        backButton.type = 'button';
        backButton.innerText = '\u2190';
        backButton.setAttribute('aria-label', 'Back to parent folder');
        backButton.title = 'Back to parent folder';
        backButton.onclick = () => openFolder(parentPath);
        listElement.parentElement.insertBefore(backButton, listElement);
    }

    const flattenedFolderPaths = new Set([...folderPaths].filter(folderPath => folderVideoCounts.get(folderPath) === 1));
    const flattenedVideoPaths = new Set();
    flattenedFolderPaths.forEach(folderPath => {
        allMedia.forEach(file => {
            if (file.path.startsWith(`${folderPath}/`)) flattenedVideoPaths.add(file.path);
        });
    });

    [...folderPaths]
        .filter(folderPath => !flattenedFolderPaths.has(folderPath))
        .filter(folderPath => mediaCategoryLabel(folderPath).toLowerCase().includes(query))
        .sort(naturalCompare)
        .forEach(folderPath => {
            listElement.appendChild(renderFolderButton(
                mediaCategoryLabel(folderPath),
                `${folderVideoCounts.get(folderPath) || 0} video${folderVideoCounts.get(folderPath) === 1 ? '' : 's'}`,
                () => openFolder(folderPath),
                folderArtworkByPath.get(folderPath) || null
            ));
        });

    const visibleMedia = allMedia
        .filter(file => activeView === 'all'
            || (browsingCategory && file.path.split('/').slice(0, -1).join('/') === browsePath)
            || flattenedVideoPaths.has(file.path))
        .filter(file => file.path.toLowerCase().includes(query))
        .sort((a, b) => new Date(b.uploadedAt || 0) - new Date(a.uploadedAt || 0));

    visibleMedia.forEach(file => {
        const li = document.createElement('li');
        li.className = 'media-item library-card';
        li.tabIndex = 0;
        const manifest = previewManifests.get(file.path);
        const preview = document.createElement('div');
        preview.className = 'preview';
        const artwork = file.previewImagePath;
        preview.dataset.previewKind = artwork ? 'image' : manifest ? 'sprite' : 'pending';
        preview.dataset.previewPath = file.path;
        if (artwork) {
            preview.dataset.previewImagePath = artwork;
            preview.dataset.previewImageUpdatedAt = file.previewImageUpdatedAt || '';
        }
        preview.setAttribute('aria-label', artwork || manifest ? `Preview for ${file.name}` : `Preview unavailable for ${file.name}`);
        const copy = document.createElement('span');
        copy.className = 'media-copy';
        const name = document.createElement('span');
        name.className = 'media-name';
        name.title = file.path;
        name.innerText = activeView !== 'all'
            ? file.name.replace(/\.[^.]+$/, '')
            : file.path.replace(/\.[^.]+$/, '');
        copy.append(name);
        li.append(preview, copy);
        li.onclick = () => playMedia(file.path, file.path);
        li.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') playMedia(file.path, file.path); };
        listElement.appendChild(li);
    });
    addVideoPreviews();
}

function playMedia(path) {
    mediaLoadAbortController?.abort();
    mediaLoadAbortController = null;
    previewGeneration += 1;
    previewObserver?.disconnect();
    const watchPage = window.location.protocol === 'file:' ? 'watch.html' : 'watch';
    window.location.href = `${watchPage}?path=${encodeURIComponent(path)}`;
}

document.getElementById('admin-close').onclick = () => {
    document.getElementById('admin-modal').classList.add('hidden');
};
document.getElementById('admin-user-search').oninput = event => {
    adminUserSearch = event.target.value;
    renderAdminUsers();
};
document.getElementById('admin-video-search').oninput = event => {
    adminVideoSearch = event.target.value;
    renderAdminVideos();
};
document.getElementById('admin-select-all').onclick = () => setAllAdminVideoAccess(true);
document.getElementById('admin-deselect-all').onclick = () => setAllAdminVideoAccess(false);
document.getElementById('admin-save').onclick = saveAdminChanges;
document.getElementById('admin-new-videos-access').onchange = event => updateAdminNewVideosAccess(event.target);

checkAuth();
