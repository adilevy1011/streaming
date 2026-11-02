let activeHls = null;
let activeHlsRetryTimer = null;

const MAX_HLS_RETRIES = 3;
const HLS_RETRY_BASE_DELAY_MS = 250;

function hidePlaybackError(player) {
    const frame = player.closest('.video-frame') || player.parentElement;
    const message = frame?.querySelector('.playback-error');
    if (message) message.hidden = true;
}

function showPlaybackError(player) {
    const frame = player.closest('.video-frame') || player.parentElement;
    if (!frame) return;
    let message = frame.querySelector('.playback-error');
    if (!message) {
        message = document.createElement('div');
        message.className = 'playback-error';
        frame.appendChild(message);
    }
    message.textContent = 'This device does not support direct playing for this video type';
    message.hidden = false;
}

function directMediaUrl(encodedPath, token) {
    return `/api/media/file/${encodedPath}?token=${encodeURIComponent(token)}`;
}

function tryDirectFallback(player, encodedPath, token, reason) {
    if (player.dataset.directFallback === 'true') return false;
    player.dataset.directFallback = 'true';
    const directUrl = directMediaUrl(encodedPath, token);
    player.addEventListener('error', () => {
        showPlaybackError(player);
    }, { once: true });
    console.warn('HLS unavailable; trying direct file playback.', reason || 'unknown reason');
    player.src = directUrl;
    player.load();
    return true;
}

function setStreamingSource(player, mediaPath, token) {
    if (activeHlsRetryTimer) {
        clearTimeout(activeHlsRetryTimer);
        activeHlsRetryTimer = null;
    }
    if (activeHls) {
        activeHls.destroy();
        activeHls = null;
    }
    hidePlaybackError(player);
    delete player.dataset.directFallback;
    const encodedPath = mediaPath.split('/').map(encodeURIComponent).join('/');
    const manifest = `/api/media/hls/${encodedPath}/index.m3u8?token=${encodeURIComponent(token)}`;
    player.removeAttribute('src');
    player.load();
    if (player.canPlayType('application/vnd.apple.mpegurl')) {
        player.addEventListener('error', () => {
            console.error('Native HLS playback error', player.error);
            if (!tryDirectFallback(player, encodedPath, token, player.error?.message)) {
                showPlaybackError(player);
            }
        }, { once: true });
        player.src = manifest;
        player.load();
        return;
    }
    if (window.Hls?.isSupported()) {
        const hls = new Hls({ enableWorker: true });
        let hlsRetryCount = 0;
        activeHls = hls;
        const destroyHls = () => {
            if (activeHlsRetryTimer) {
                clearTimeout(activeHlsRetryTimer);
                activeHlsRetryTimer = null;
            }
            hls.destroy();
            if (activeHls === hls) activeHls = null;
        };
        const fallbackFromHls = reason => {
            destroyHls();
            if (!tryDirectFallback(player, encodedPath, token, reason)) showPlaybackError(player);
        };
        hls.on(Hls.Events.MEDIA_ATTACHED, () => {
            if (activeHls === hls) hls.loadSource(manifest);
        });
        hls.on(Hls.Events.MANIFEST_LOADED, () => { if (activeHls === hls) hlsRetryCount = 0; });
        hls.on(Hls.Events.FRAG_LOADED, () => { if (activeHls === hls) hlsRetryCount = 0; });
        hls.on(Hls.Events.ERROR, (_event, data) => {
            if (activeHls !== hls) return;
            if (!data.fatal) return;
            console.error('HLS playback error', data);
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                const status = Number(data.response?.code || 0);
                const manifestFailure = data.details === Hls.ErrorDetails.MANIFEST_LOAD_ERROR
                    || data.details === Hls.ErrorDetails.MANIFEST_PARSING_ERROR;
                const unrecoverable = data.details === Hls.ErrorDetails.MANIFEST_PARSING_ERROR || status === 404;
                if (unrecoverable) {
                    fallbackFromHls(data.details);
                    return;
                }
                if (hlsRetryCount < MAX_HLS_RETRIES) {
                    const delay = HLS_RETRY_BASE_DELAY_MS * (2 ** hlsRetryCount);
                    hlsRetryCount += 1;
                    if (activeHlsRetryTimer) clearTimeout(activeHlsRetryTimer);
                    activeHlsRetryTimer = setTimeout(() => {
                        activeHlsRetryTimer = null;
                        if (activeHls === hls) hls.startLoad();
                    }, delay);
                    return;
                }
                fallbackFromHls(manifestFailure ? data.details : `HLS network error (${status || 'unavailable'})`);
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                fallbackFromHls(data.details);
            } else {
                fallbackFromHls(data.details);
            }
        });
        hls.attachMedia(player);
        return;
    }
    // Some desktop browsers can still decode the original file even when they
    // do not support HLS. Keep that useful fallback, while HLS remains the
    // normal path for incompatible containers such as MKV on iOS.
    player.addEventListener('error', () => showPlaybackError(player), { once: true });
    player.src = directMediaUrl(encodedPath, token);
    player.load();
}
