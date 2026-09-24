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
const completionMessage = document.getElementById('completion-message');
const completionActions = document.getElementById('completion-actions');
const libraryButton = document.getElementById('library-button');
const creditsButton = document.getElementById('credits-button');

let currentUserId = '';
let progressSaveTimer;
let progressHydrated = false;
let subtitlesEnabled = false;
let subtitleObjectUrl = '';
let previewManifest = null;
let previewSpriteUrls = [];
let creditsStartSeconds = null;
let creditTimestampAvailable = false;
let nextEpisodeFilesPromise = null;
let nextEpisodeLookupPromise = null;
let nextEpisodePath = null;
let nextEpisodePrefetchStarted = false;
let nextEpisodePreloader = null;
let completionActionsDismissed = false;

let controlsHideTimer = null;
let isScrubbing = false;

completionMessage.classList.add('hidden');
completionActions.classList.add('hidden');

function completionThresholdReached() {
    return creditTimestampAvailable
        && Number.isFinite(player.currentTime)
        && player.currentTime >= creditsStartSeconds;
}

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
    return document.fullscreenElement === videoFrame
        || document.webkitFullscreenElement === videoFrame
        || videoFrame.matches(':fullscreen')
        || videoFrame.matches(':-webkit-full-screen');
}

function handleFullscreenChange() {
    videoFrame.classList.toggle('fullscreen-active', isPlayerFullscreen());
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
    const completed = creditsStartSeconds !== null
        ? player.currentTime >= creditsStartSeconds
        : Number.isFinite(duration) && duration > 0 && player.currentTime >= duration - 10;
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

async function loadCredits(videoPath) {
    try {
        const data = await apiRequest(`/media/credits?video_path=${encodeURIComponent(videoPath)}`);
        const rawTimestamp = data?.credits_start_seconds;
        const timestamp = rawTimestamp === null || rawTimestamp === undefined || rawTimestamp === ''
            ? NaN
            : Number(rawTimestamp);
        creditsStartSeconds = Number.isFinite(timestamp) && timestamp >= 0 ? timestamp : null;
        creditTimestampAvailable = creditsStartSeconds !== null;
        creditsButton.disabled = creditsStartSeconds === null;
        creditsButton.title = creditsStartSeconds === null ? 'Credit timestamp is not available' : `Start at ${formatTime(creditsStartSeconds)}`;
    } catch (error) {
        creditsStartSeconds = null;
        creditTimestampAvailable = false;
        creditsButton.disabled = true;
        console.warn('Unable to load credit timestamp', error);
    }
}

function episodeNumber(fileName) {
    const seasonEpisode = fileName.match(/(s\d{1,3}e)(\d{1,3})/i);
    if (seasonEpisode) return { value: Number(seasonEpisode[2]), width: seasonEpisode[2].length, pattern: seasonEpisode };
    const namedEpisode = fileName.match(/((?:episode|ep)[ ._-]*)(\d{1,3})/i);
    if (namedEpisode) return { value: Number(namedEpisode[2]), width: namedEpisode[2].length, pattern: namedEpisode };
    const trailingNumber = fileName.match(/(\d{1,3})(?=\.[^.]+$)/);
    if (trailingNumber) return { value: Number(trailingNumber[1]), width: trailingNumber[1].length, pattern: trailingNumber };
    return null;
}

function replaceEpisodeNumber(fileName, episode) {
    const info = episodeNumber(fileName);
    if (!info) return null;
    const replacement = String(episode).padStart(info.width, '0');
    return fileName.slice(0, info.pattern.index + info.pattern[0].length - info.pattern[info.pattern.length - 1].length)
        + replacement
        + fileName.slice(info.pattern.index + info.pattern[0].length);
}

function naturalPathCompare(left, right) {
    return left.localeCompare(right, undefined, { numeric: true, sensitivity: 'base' });
}

function isTvShow(videoPath) {
    const root = (videoPath.split('/')[0] || '').toLowerCase();
    return ['shows', 'show', 'tv', 'tv-shows', 'tv_shows', 'tv shows', 'tvshows', 'television'].includes(root);
}

async function findNextEpisode(videoPath, filesPromise = null) {
    const parts = videoPath.split('/');
    if (!isTvShow(videoPath)) return null;

    let files;
    try { files = await (filesPromise || apiRequest('/media/files')); }
    catch (error) { console.warn('Unable to find the next episode', error); return null; }
    const available = new Set((files || []).map(file => file.path));
    const fileName = parts[parts.length - 1] || '';
    const currentEpisode = episodeNumber(fileName);
    if (!currentEpisode) return null;
    const directory = parts.slice(0, -1).join('/');
    const nextName = replaceEpisodeNumber(fileName, currentEpisode.value + 1);
    const sameSeasonPath = nextName ? (directory ? `${directory}/${nextName}` : nextName) : null;
    if (sameSeasonPath && available.has(sameSeasonPath)) return sameSeasonPath;

    const sameDirectoryPrefix = `${directory}/`;
    const sameSeasonEpisode = [...available]
        .filter(candidate => candidate.startsWith(sameDirectoryPrefix))
        .filter(candidate => candidate.split('/').length === parts.length)
        .filter(candidate => episodeNumber(candidate.split('/').pop() || '')?.value === currentEpisode.value + 1)
        .sort(naturalPathCompare)[0];
    if (sameSeasonEpisode) return sameSeasonEpisode;

    const seasonIndex = parts.findIndex(part => /^season\s+\d+$/i.test(part));
    if (seasonIndex < 0) return null;
    const seasonNumber = Number(parts[seasonIndex].match(/\d+/)[0]);
    const nextSeasonFiles = [...available]
        .filter(candidate => {
            const candidateParts = candidate.split('/');
            const candidateSeason = candidateParts[seasonIndex] || '';
            return candidateParts.length > seasonIndex + 1
                && candidateParts.slice(0, seasonIndex).every((part, index) => part.toLowerCase() === (candidateParts[index] || '').toLowerCase())
                && new RegExp(`^season\\s+${seasonNumber + 1}$`, 'i').test(candidateSeason);
        })
        .sort((left, right) => {
            const leftEpisode = episodeNumber(left.split('/').pop() || '');
            const rightEpisode = episodeNumber(right.split('/').pop() || '');
            if (leftEpisode && rightEpisode && leftEpisode.value !== rightEpisode.value) return leftEpisode.value - rightEpisode.value;
            if (leftEpisode) return -1;
            if (rightEpisode) return 1;
            return naturalPathCompare(left, right);
        });
    return nextSeasonFiles[0] || null;
}

function prefetchNextEpisode() {
    if (nextEpisodePrefetchStarted || !isTvShow(path)) return;
    nextEpisodePrefetchStarted = true;
    nextEpisodeLookupPromise = findNextEpisode(path, nextEpisodeFilesPromise).then(nextPath => {
        nextEpisodePath = nextPath;
        if (!nextPath) return null;

        nextEpisodePreloader = document.createElement('video');
        nextEpisodePreloader.preload = 'auto';
        nextEpisodePreloader.muted = true;
        nextEpisodePreloader.playsInline = true;
        nextEpisodePreloader.src = `/api/media/file/${nextPath.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(getAccessToken())}`;
        nextEpisodePreloader.style.position = 'fixed';
        nextEpisodePreloader.style.width = '1px';
        nextEpisodePreloader.style.height = '1px';
        nextEpisodePreloader.style.opacity = '0';
        nextEpisodePreloader.style.pointerEvents = 'none';
        document.body.appendChild(nextEpisodePreloader);
        nextEpisodePreloader.load();
        return nextPath;
    });
}

function showCompletionActions() {
    if (!completionThresholdReached()) return;
    completionMessage.classList.remove('hidden');
    completionActions.classList.remove('hidden');
}

function updateCompletionActions() {
    if (completionThresholdReached() && !completionActionsDismissed) {
        showCompletionActions();
        return;
    }
    if (!completionThresholdReached()) completionActionsDismissed = false;
    completionMessage.classList.add('hidden');
    completionActions.classList.add('hidden');
}

async function playNextEpisode(automatic = false) {
    if (libraryButton.disabled) return;
    libraryButton.disabled = true;
    libraryButton.innerText = 'Finding next episode…';
    libraryButton.setAttribute('aria-busy', 'true');
    try {
        const nextEpisode = nextEpisodePath || await (nextEpisodeLookupPromise || findNextEpisode(path, nextEpisodeFilesPromise));
        if (!nextEpisode) {
            if (automatic) {
                returnToLibrary();
                return;
            }
            libraryButton.disabled = false;
            libraryButton.innerText = 'Next episode';
            libraryButton.removeAttribute('aria-busy');
            completionMessage.innerText = 'There is no next episode.';
            return;
        }
        const watchPage = window.location.protocol === 'file:' ? 'watch.html' : 'watch';
        window.location.href = `${watchPage}?path=${encodeURIComponent(nextEpisode)}`;
    } catch (error) {
        console.warn('Unable to start the next episode', error);
        libraryButton.disabled = false;
        libraryButton.innerText = 'Next episode';
        libraryButton.removeAttribute('aria-busy');
        completionMessage.innerText = 'Unable to find the next episode.';
    }
}

function watchCredits() {
    completionActionsDismissed = true;
    completionMessage.classList.add('hidden');
    completionActions.classList.add('hidden');
}

async function loadProfile() {
    try {
        const profile = await apiRequest('/profile');
        subtitlesEnabled = profile?.subtitles_enabled === true;
    } catch (error) {
        console.warn('Unable to load profile preferences', error);
        subtitlesEnabled = false;
    }
}

async function saveSubtitlePreference(enabled) {
    subtitlesEnabled = enabled;
    try {
        await apiRequest('/profile', {
            method: 'PATCH',
            body: JSON.stringify({ subtitles_enabled: enabled })
        });
    } catch (error) {
        console.warn('Unable to save subtitle preference', error);
    }
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
    captionsToggle.checked = subtitlesEnabled;
    try {
        const subtitlePath = await findMatchingSubtitle(videoPath);
        if (!subtitlePath) return;
        const response = await fetch(`/api/media/file/${subtitlePath.split('/').map(encodeURIComponent).join('/')}`, {
            headers: { Authorization: `Bearer ${getAccessToken()}` },
            cache: 'no-store'
        });
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
        track.track.mode = subtitlesEnabled ? 'showing' : 'disabled';
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
    await loadProfile();
    const videoName = (path.split('/').pop() || path).replace(/\.[^.]+$/, '');
    document.getElementById('now-playing').innerText = videoName;
    document.title = `${videoName} | Adlv Media Stream`;
    libraryButton.onclick = returnToLibrary;
    if (isTvShow(path)) {
        libraryButton.innerText = 'Next episode';
        libraryButton.classList.remove('back-button');
        libraryButton.onclick = playNextEpisode;
        nextEpisodeFilesPromise = apiRequest('/media/files').catch(error => {
            console.warn('Unable to preload the media listing', error);
            return [];
        });
    }
    
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
        updateCompletionActions();
        if (Number.isFinite(player.duration) && player.duration > 0
            && player.currentTime / player.duration >= 0.75) {
            prefetchNextEpisode();
        }
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
    player.onended = async () => {
        await saveCurrentProgress(true);
        if (!creditTimestampAvailable) {
            await playNextEpisode(true);
            return;
        }
        showCompletionActions();
    };

    void attachMatchingSubtitle(path);
    await loadCredits(path);
    void restorePosition();
    player.play().catch(() => {});
}

function returnToLibrary() {
    const libraryPage = window.location.protocol === 'file:' ? 'index.html' : '/';
    saveCurrentProgress(true).then(() => { window.location.href = libraryPage; });
}

creditsButton.addEventListener('click', watchCredits);

player.addEventListener('seeking', () => {
    updateCompletionActions();
    saveCurrentProgress();
});
player.addEventListener('contextmenu', event => event.preventDefault());

function handlePointerMove() {
    showPlayerControls();
}

videoFrame.addEventListener('pointermove', handlePointerMove);
videoFrame.addEventListener('mousemove', handlePointerMove);
videoFrame.addEventListener('pointerleave', () => {
    if (isPlayerFullscreen() || videoFrame.classList.contains('fullscreen-active')) return;
    if (!player.paused && !isScrubbing && !settingsMenu.classList.contains('open')) {
        timelineShell.classList.remove('controls-visible');
        videoFrame.classList.add('user-idle');
    }
});
videoFrame.addEventListener('click', event => {
    if (event.target.closest('.timeline-shell, #completion-actions, #completion-message')) return;
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
    const enabled = captionsToggle.checked;
    [...player.textTracks].forEach(track => { track.mode = enabled ? 'showing' : 'disabled'; });
    void saveSubtitlePreference(enabled);
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