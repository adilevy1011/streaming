let activeHls = null;

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

function tryWindowsDirectFallback(player, encodedPath, token, reason) {
    if (!/Windows/i.test(navigator.userAgent) || player.dataset.directFallback === 'true') return false;
    player.dataset.directFallback = 'true';
    player.addEventListener('error', () => showPlaybackError(player), { once: true });
    console.warn('HLS unavailable; trying direct Windows playback.', reason || 'unknown reason');
    player.src = directMediaUrl(encodedPath, token);
    player.load();
    return true;
}

function setStreamingSource(player, mediaPath, token) {
    if (activeHls) {
        activeHls.destroy();
        activeHls = null;
    }
    const encodedPath = mediaPath.split('/').map(encodeURIComponent).join('/');
    const manifest = `/api/media/hls/${encodedPath}/index.m3u8?token=${encodeURIComponent(token)}`;
    player.removeAttribute('src');
    player.load();
    if (player.canPlayType('application/vnd.apple.mpegurl')) {
        player.addEventListener('error', () => {
            console.error('Native HLS playback error', player.error);
            showPlaybackError(player);
        }, { once: true });
        player.src = manifest;
        player.load();
        return;
    }
    if (window.Hls?.isSupported()) {
        const hls = new Hls({ enableWorker: true });
        activeHls = hls;
        hls.on(Hls.Events.MEDIA_ATTACHED, () => {
            if (activeHls === hls) hls.loadSource(manifest);
        });
        hls.on(Hls.Events.ERROR, (_event, data) => {
            if (!data.fatal) return;
            console.error('HLS playback error', data);
            if (data.type === Hls.ErrorTypes.NETWORK_ERROR) {
                const status = Number(data.response?.code || 0);
                const manifestFailure = data.details === Hls.ErrorDetails.MANIFEST_LOAD_ERROR
                    || data.details === Hls.ErrorDetails.MANIFEST_PARSING_ERROR;
                if (manifestFailure || status === 404 || status >= 500) {
                    if (tryWindowsDirectFallback(player, encodedPath, token, data.details)) {
                        hls.destroy();
                        if (activeHls === hls) activeHls = null;
                        return;
                    }
                    showPlaybackError(player);
                }
                hls.startLoad();
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                if (tryWindowsDirectFallback(player, encodedPath, token, data.details)) {
                    hls.destroy();
                    if (activeHls === hls) activeHls = null;
                    return;
                }
                showPlaybackError(player);
                hls.destroy();
                if (activeHls === hls) activeHls = null;
            } else {
                hls.destroy();
                if (activeHls === hls) activeHls = null;
                showPlaybackError(player);
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
