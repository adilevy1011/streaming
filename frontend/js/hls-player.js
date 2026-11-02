let activeHls = null;

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
                hls.startLoad();
            } else if (data.type === Hls.ErrorTypes.MEDIA_ERROR) {
                hls.recoverMediaError();
            } else {
                hls.destroy();
                if (activeHls === hls) activeHls = null;
                player.dispatchEvent(new Event('error'));
            }
        });
        hls.attachMedia(player);
        return;
    }
    // Some desktop browsers can still decode the original file even when they
    // do not support HLS. Keep that useful fallback, while HLS remains the
    // normal path for incompatible containers such as MKV on iOS.
    player.src = `/api/media/file/${encodedPath}?token=${encodeURIComponent(token)}`;
    player.load();
}
