const PROGRESS_STORAGE_KEY = 'adlv-video-progress';
const PROGRESS_SAVE_INTERVAL = 2000;
const path = new URLSearchParams(window.location.search).get('path');
const player = document.getElementById('video-player');
const timelineShell = document.getElementById('timeline-shell');
const previewTimeline = document.getElementById('preview-timeline');
const timelinePreview = document.getElementById('timeline-preview');
const videoFrame = document.getElementById('video-frame');
const playToggle = document.getElementById('play-toggle');
const playerTime = document.getElementById('player-time');
const muteToggle = document.getElementById('mute-toggle');
const volumeControl = document.getElementById('volume-control');
const settingsToggle = document.getElementById('settings-toggle');
const settingsMenu = document.getElementById('settings-menu');
const playbackRate = document.getElementById('playback-rate');
const captionsToggle = document.getElementById('captions-toggle');
const captionsOption = document.getElementById('captions-option');
const fullscreenToggle = document.getElementById('fullscreen-toggle');

let currentUserId = '';
let progressSaveTimer;
let progressHydrated = false;
let subtitleObjectUrl = '';
let previewManifest = null;
let previewSpriteUrls = [];

let controlsHideTimer = null;
let isScrubbing = false;

function showPlayerControls() {
    videoFrame.classList.remove('user-idle');
    timelineShell.classList.add('controls-visible');
    clearTimeout(controlsHideTimer);

    if (player.paused || isScrubbing || settingsMenu.classList.contains('open')) {
        return;
    }

    controlsHideTimer = setTimeout(() => {
        timelineShell.classList.remove('controls-visible');
        videoFrame.classList.add('user-idle');
        timelinePreview.style.display = 'none';
    }, 2500);
}

function isPlayerFullscreen() {
    return document.fullscreenElement === videoFrame || document.webkitFullscreenElement === videoFrame;
}

function handleFullscreenChange() {
    showPlayerControls();
    if (isPlayerFullscreen()) {
        if (captionsToggle.checked) {
            [...player.textTracks].forEach(track => { track.mode = 'showing'; });
        }
    }
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

function getProgressKey(mediaPath) { return `${currentUserId}:${mediaPath}`; }

function progressTimestamp(progress) {
    const timestamp = Date.parse(progress?.updated_at || '');
    return Number.isFinite(timestamp) ? timestamp : 0;
}

function cacheProgress(position, duration, completed = false) {
    if (!currentUserId || !Number.isFinite(position)) return;
    setLocalProgress({
        key: getProgressKey(path), path,
        position_seconds: position, duration_seconds: duration || null,
        completed, updated_at: new Date().toISOString()
    });
}

async function saveProgress(position, duration, completed = false) {
    if (!currentUserId || !path || !Number.isFinite(position)) return;
    const payload = {
        user_id: currentUserId, media_path: path,
        position_seconds: Math.max(0, position),
        duration_seconds: Number.isFinite(duration) ? duration : null,
        completed, updated_at: new Date().toISOString()
    };
    cacheProgress(payload.position_seconds, payload.duration_seconds, completed);
    try { await apiRequest('/progress', { method: 'POST', body: JSON.stringify(payload) }); }
    catch (error) { console.warn('Unable to save playback progress', error); }
}

async function saveCurrentProgress(immediate = false) {
    if (!path || !Number.isFinite(player.currentTime)) return;
    const duration = Number.isFinite(player.duration) ? player.duration : null;
    const completed = Number.isFinite(duration) && duration > 0 && player.currentTime >= duration - 10;
    cacheProgress(player.currentTime, duration, completed);
    if (progressSaveTimer) clearTimeout(progressSaveTimer);
    if (immediate) await saveProgress(player.currentTime, duration, completed);
    else progressSaveTimer = setTimeout(() => saveProgress(player.currentTime, duration, completed), PROGRESS_SAVE_INTERVAL);
}

async function loadProgress() {
    const key = getProgressKey(path);
    const local = getLocalProgress()[key] || null;
    let data;
    try {
        data = (await apiRequest('/progress')).find(item => item.media_path === path) || null;
    } catch (error) { console.warn('Unable to load playback progress', error); return local; }
    const remote = data ? {
        ...data,
        key,
        path,
        updated_at: data.updated_at || ''
    } : null;
    const localIsNewer = local && (!remote || progressTimestamp(local) > progressTimestamp(remote));
    if (localIsNewer) {
        await saveProgress(Number(local.position_seconds), Number(local.duration_seconds), !!local.completed);
        return local;
    }
    if (remote) setLocalProgress(remote);
    return remote || local;
}

function formatTime(seconds) {
    if (!Number.isFinite(seconds)) return '0:00';
    const value = Math.max(0, Math.floor(seconds));
    return `${Math.floor(value / 60)}:${String(value % 60).padStart(2, '0')}`;
}

function updatePlayerControls() {
    const duration = Number.isFinite(player.duration) ? player.duration : 0;
    playerTime.innerText = `${formatTime(player.currentTime)} / ${formatTime(duration)}`;
    if (!isScrubbing) previewTimeline.value = String(player.currentTime || 0);
    playToggle.innerText = player.paused ? '▶' : '❚❚';
    playToggle.setAttribute('aria-label', player.paused ? 'Play' : 'Pause');
    muteToggle.innerText = player.muted || player.volume === 0 ? '🔇' : '🔊';
    muteToggle.setAttribute('aria-label', player.muted ? 'Unmute' : 'Mute');
}

function applySpriteFrame(timeSeconds) {
    if (!previewManifest || !previewSpriteUrls.length) return;
    const frame = Math.max(0, Math.floor(timeSeconds / previewManifest.interval_seconds));
    const capacity = previewManifest.columns * previewManifest.rows;
    const sheetIndex = Math.min(Math.floor(frame / capacity), previewSpriteUrls.length - 1);
    const cell = frame % capacity;
    const column = cell % previewManifest.columns;
    const row = Math.floor(cell / previewManifest.columns);
    timelinePreview.style.backgroundImage = `url("${previewSpriteUrls[sheetIndex]}")`;
    timelinePreview.style.backgroundSize = `${previewManifest.columns * 100}% ${previewManifest.rows * 100}%`;
    timelinePreview.style.backgroundPosition = `${previewManifest.columns === 1 ? 0 : (column / (previewManifest.columns - 1)) * 100}% ${previewManifest.rows === 1 ? 0 : (row / (previewManifest.rows - 1)) * 100}%`;
}

async function loadTimelinePreview(videoPath) {
    let data;
    try { data = (await apiRequest('/previews')).find(item => item.media_path === videoPath); }
    catch (_) { return; }
    if (!data?.sheets?.length) return;
    previewSpriteUrls = data.sheets.map(spritePath => `/api/media/file/${spritePath.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(getAccessToken())}`);
    if (!previewSpriteUrls.length) return;
    previewManifest = data;
    previewTimeline.max = String(data.duration_seconds);
    previewTimeline.step = '0.1';
    timelineShell.classList.remove('hidden');
    applySpriteFrame(0);
}

function updateTimelinePreview(event) {
    if (!previewManifest) return;
    const rect = previewTimeline.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    const time = fraction * Number(previewTimeline.max);
    timelinePreview.style.left = `${fraction * 100}%`;
    applySpriteFrame(time);
}

async function findMatchingSubtitle(videoPath) {
    const data = await apiRequest(`/media/subtitle?video_path=${encodeURIComponent(videoPath)}`);
    return data?.path || '';
}

function srtToWebVtt(srtText) {
    const normalized = srtText.replace(/^\uFEFF/, '').replace(/\r\n?/g, '\n').trim();
    const cues = normalized.split(/\n{2,}/).map(block => {
        const lines = block.split('\n');
        const timingIndex = lines.findIndex(line => line.includes('-->'));
        if (timingIndex < 0) return '';
        const timing = lines[timingIndex].replace(/,(\d{3})/g, '.$1');
        const text = lines.slice(timingIndex + 1).join('\n').trim();
        return text ? `${timing}\n${text}` : '';
    }).filter(Boolean);
    return `WEBVTT\n\n${cues.join('\n\n')}\n`;
}

async function attachMatchingSubtitle(videoPath) {
    captionsOption.classList.add('hidden');
    captionsToggle.checked = false;
    try {
        const subtitlePath = await findMatchingSubtitle(videoPath);
        if (!subtitlePath) return;
        const response = await fetch(`/api/media/file/${subtitlePath.split('/').map(encodeURIComponent).join('/')}`, { headers: { Authorization: `Bearer ${getAccessToken()}` } });
        if (!response.ok) throw new Error(`Subtitle request failed (${response.status})`);
        const webVtt = srtToWebVtt(await response.text());
        if (subtitleObjectUrl) URL.revokeObjectURL(subtitleObjectUrl);
        subtitleObjectUrl = URL.createObjectURL(new Blob([webVtt], { type: 'text/vtt' }));
        const track = document.createElement('track');
        track.kind = 'subtitles';
        track.label = 'Subtitles';
        track.srclang = 'en';
        track.src = subtitleObjectUrl;
        player.appendChild(track);
        track.track.mode = 'disabled';
        captionsOption.classList.remove('hidden');
    } catch (error) {
        console.warn('Unable to load matching subtitles', error);
    }
}

async function restorePosition() {
    const progress = await loadProgress();
    if (!progress || progress.completed || !Number(progress.position_seconds)) {
        progressHydrated = true;
        return;
    }
    const position = Number(progress.position_seconds);
    const duration = Number(progress.duration_seconds);
    if (Number.isFinite(duration) && duration > 0 && position >= duration - 10) {
        progressHydrated = true;
        return;
    }
    const apply = () => {
        if (progressHydrated || player.readyState < 1) return;
        player.currentTime = Math.min(position, Math.max(0, (player.duration || position) - 0.5));
        progressHydrated = true;
    };
    if (player.readyState >= 1) apply();
    else player.addEventListener('loadedmetadata', apply, { once: true });
}

async function startWatching() {
    if (!path) return;
    const session = await getAuthenticatedSession();
    if (!session) {
        window.location.href = window.location.protocol === 'file:' ? 'index.html' : '/';
        return;
    }
    currentUserId = session.user.id;
    const videoName = (path.split('/').pop() || path).replace(/\.[^.]+$/, '');
    document.getElementById('now-playing').innerText = videoName;
    document.title = `${videoName} | Adlv Media Stream`;
    
    player.src = `/api/media/file/${path.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(getAccessToken())}`;
    player.preload = 'auto';
    player.load();
    player.onloadedmetadata = () => {
        if (!previewManifest && Number.isFinite(player.duration)) previewTimeline.max = String(player.duration);
        timelineShell.classList.remove('hidden');
        showPlayerControls();
        updatePlayerControls();
        loadTimelinePreview(path);
    };
    player.ontimeupdate = () => {
        if (document.activeElement !== previewTimeline && !isScrubbing) {
            previewTimeline.value = String(player.currentTime);
        }
        updatePlayerControls();
        saveCurrentProgress();
    };
    player.onplay = () => {
        videoFrame.classList.remove('paused');
        updatePlayerControls();
        showPlayerControls();
    };
    player.onpause = () => {
        videoFrame.classList.add('paused');
        updatePlayerControls();
        showPlayerControls();
        saveCurrentProgress(true);
    };
    player.onended = () => saveCurrentProgress(true);

    void attachMatchingSubtitle(path);
    void restorePosition();
    player.play().catch(() => {});
}

function returnToLibrary() {
    const libraryPage = window.location.protocol === 'file:' ? 'index.html' : '/';
    saveCurrentProgress(true).then(() => { window.location.href = libraryPage; });
}

player.addEventListener('seeking', () => saveCurrentProgress());
player.addEventListener('contextmenu', event => event.preventDefault());

function handlePointerMove() {
    showPlayerControls();
}

videoFrame.addEventListener('pointermove', handlePointerMove);
videoFrame.addEventListener('mousemove', handlePointerMove);
videoFrame.addEventListener('pointerleave', () => {
    if (isPlayerFullscreen()) return;
    if (!player.paused && !isScrubbing && !settingsMenu.classList.contains('open')) {
        timelineShell.classList.remove('controls-visible');
        videoFrame.classList.add('user-idle');
    }
});
videoFrame.addEventListener('click', event => {
    if (event.target.closest('.timeline-shell')) return;
    if (player.paused) player.play().catch(() => {});
    else player.pause();
});

playToggle.addEventListener('click', () => player.paused ? player.play() : player.pause());
muteToggle.addEventListener('click', () => {
    player.muted = !player.muted;
    updatePlayerControls();
});
volumeControl.addEventListener('input', () => {
    player.volume = Number(volumeControl.value);
    player.muted = player.volume === 0;
    updatePlayerControls();
});
settingsToggle.addEventListener('click', event => {
    event.stopPropagation();
    settingsMenu.classList.toggle('open');
    showPlayerControls();
});
playbackRate.addEventListener('change', () => { player.playbackRate = Number(playbackRate.value); });
captionsToggle.addEventListener('change', () => {
    [...player.textTracks].forEach(track => { track.mode = captionsToggle.checked ? 'showing' : 'disabled'; });
});
fullscreenToggle.addEventListener('click', () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else videoFrame.requestFullscreen?.();
});

document.addEventListener('fullscreenchange', handleFullscreenChange);
document.addEventListener('click', event => {
    if (!settingsMenu.contains(event.target) && event.target !== settingsToggle) {
        settingsMenu.classList.remove('open');
    }
});

previewTimeline.addEventListener('pointerdown', () => { isScrubbing = true; });
document.addEventListener('pointerup', () => {
    if (isScrubbing) {
        isScrubbing = false;
        showPlayerControls();
    }
});

previewTimeline.addEventListener('input', () => {
    player.currentTime = Number(previewTimeline.value);
    applySpriteFrame(player.currentTime);
    updatePlayerControls();
});
previewTimeline.addEventListener('pointermove', updateTimelinePreview);
previewTimeline.addEventListener('pointerenter', () => { timelinePreview.style.display = 'block'; });
previewTimeline.addEventListener('pointerleave', () => { timelinePreview.style.display = 'none'; });

document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') saveCurrentProgress(true);
});
window.addEventListener('pagehide', () => saveCurrentProgress(true));

startWatching();
