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
    document.getElementById('auth-section').classList.toggle('hidden', !!session);
    document.getElementById('stream-section').classList.toggle('hidden', !session);
    document.getElementById('logout-button').classList.toggle('hidden', !session);
    document.getElementById('refresh-library-button').classList.toggle('hidden', !session);
    if (session) {
        showLibrary('my-library');
        loadMedia();
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
        renderWatchAgain();
        return;
    }
    document.getElementById('library-title').innerText = view === 'all' ? 'All Videos' : mediaCategoryLabel(view);
    document.getElementById('search').placeholder = `Search ${view === 'all' ? 'videos' : mediaCategoryLabel(view)}...`;
    renderContinueWatching();
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

function getProgressKey(userId, path) { return `${userId}:${path}`; }

function progressTimestamp(progress) {
    const timestamp = Date.parse(progress?.updated_at || '');
    return Number.isFinite(timestamp) ? timestamp : 0;
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
        const file = mediaByPath.get(path) || { name: path.split('/').pop() || path };
        const position = Number(progress.position_seconds);
        const duration = Number(progress.duration_seconds);
        const item = document.createElement('li');
        item.className = 'continue-item';
        item.tabIndex = 0;
        const name = document.createElement('span');
        name.className = 'continue-name';
        name.innerText = file.name.replace(/\.[^.]+$/, '');
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
        item.append(name, detail, track);
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
        const file = mediaByPath.get(path) || { name: path.split('/').pop() || path };
        const item = document.createElement('li');
        item.className = 'continue-item';
        item.tabIndex = 0;
        const name = document.createElement('span');
        name.className = 'continue-name';
        name.innerText = file.name.replace(/\.[^.]+$/, '');
        const detail = document.createElement('small');
        detail.className = 'muted';
        detail.innerText = 'Completed';
        item.append(name, detail);
        item.onclick = () => playMedia(path);
        item.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') playMedia(path); };
        list.appendChild(item);
    });
    section.classList.toggle('hidden', activeView !== 'my-library' || items.length === 0);
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
            if (!remotePaths.has(mediaPath)) syncs.push(saveProgressToApi(local));
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

function folderArtwork(folderPath) {
    const file = allMedia.find(item => item.folderArtworks?.[folderPath]);
    const artwork = file?.folderArtworks?.[folderPath];
    return artwork ? { path: artwork.path, updatedAt: artwork.updatedAt } : null;
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
    if (!manifest || !spritePath) return;
    preview.classList.remove('preview-loading');
    setSpriteFrame(preview, manifest, mediaFileUrl(spritePath));
    preview.dataset.previewLoaded = 'true';
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
                image.onload = () => {
                    if (generation !== previewGeneration || !document.contains(preview)) return;
                    preview.classList.remove('preview-loading');
                    preview.style.backgroundImage = `url("${mediaFileUrl(imagePath, 'preview', imageVersion)}")`;
                    preview.style.backgroundSize = 'contain';
                    preview.style.backgroundRepeat = 'no-repeat';
                    preview.style.backgroundPosition = 'center';
                    preview.dataset.previewLoaded = 'true';
                };
                image.onerror = () => {
                    preview.classList.remove('preview-loading');
                    if (preview.dataset.previewFallback === 'folder') {
                        preview.className = 'folder-icon';
                        preview.innerText = '\u{1F4C1}';
                    }
                };
                image.src = mediaFileUrl(imagePath, 'preview', imageVersion);
                if (manifest?.sheets?.[0]) {
                    loadSpritePreview(preview, manifest);
                } else {
                    preview.classList.add('preview-loading');
                }
            } else {
                loadSpritePreview(preview, manifest);
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
            if (preview.dataset.previewQueued || preview.dataset.previewLoaded) return;
            preview.dataset.previewQueued = 'true';
            queue.push(preview);
            loadNextPreview();
        });
    }, { rootMargin: '120px' });
    document.querySelectorAll('[data-preview-kind="sprite"], [data-preview-kind="image"], [data-preview-kind="pending"]').forEach(preview => previewObserver.observe(preview));
}



function renderMedia() {
    const listElement = document.getElementById('media-list');
    const query = document.getElementById('search').value.trim().toLowerCase();
    listElement.innerHTML = '';

    const browsingCategory = activeView !== 'my-library' && activeView !== 'all';
    const browsePath = browsingCategory ? selectedPath : '';
    const folderPaths = new Set();
    if (browsingCategory) {
        const prefix = `${browsePath}/`;
        allMedia.forEach(file => {
            if (!file.path.startsWith(prefix)) return;
            const remainder = file.path.slice(prefix.length).split('/');
            if (remainder.length > 1) folderPaths.add(`${browsePath}/${remainder[0]}`);
        });
    }

    if (browsingCategory && browsePath !== activeView) {
        const parentPath = browsePath.split('/').slice(0, -1).join('/');
        listElement.appendChild(renderFolderButton('\u2190 Back', 'Back', () => openFolder(parentPath)));
    }

    const flattenedFolderPaths = new Set([...folderPaths].filter(folderPath =>
        allMedia.filter(file => file.path.startsWith(`${folderPath}/`)).length === 1
    ));
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
            const count = allMedia.filter(file => file.path.startsWith(`${folderPath}/`)).length;
            listElement.appendChild(renderFolderButton(
                mediaCategoryLabel(folderPath),
                `${count} video${count === 1 ? '' : 's'}`,
                () => openFolder(folderPath),
                folderArtwork(folderPath)
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

checkAuth();
