const params = new URLSearchParams(location.search);
const roomId = params.get('room');
const videoPath = params.get('path');
const token = getAccessToken();
const player = document.getElementById('room-video');
const videoFrame = document.getElementById('video-frame');
const videoTitle = document.getElementById('video-title');
const timelineShell = document.getElementById('timeline-shell');
const timeline = document.getElementById('preview-timeline');
const timelinePreview = document.getElementById('timeline-preview');
const playerTime = document.getElementById('player-time');
const playToggle = document.getElementById('play-toggle');
const muteToggle = document.getElementById('mute-toggle');
const volumeControl = document.getElementById('volume-control');
const settingsToggle = document.getElementById('settings-toggle');
const settingsMenu = document.getElementById('settings-menu');
const playbackRate = document.getElementById('playback-rate');
const captionsOption = document.getElementById('captions-option');
const captionsToggle = document.getElementById('captions-toggle');
const fullscreenToggle = document.getElementById('fullscreen-toggle');
const statusElement = document.getElementById('status');
const waitingElement = document.getElementById('waiting');
const readyButton = document.getElementById('ready-button');
const participantsElement = document.getElementById('participants');
const linkElement = document.getElementById('room-link');
const roomGrid = document.querySelector('.room-grid');
const roomSidebar = document.getElementById('room-sidebar');
const sidebarToggle = document.getElementById('sidebar-toggle');
let socket;
let participantId = '';
let owner = false;
let ready = false;
let started = false;
let applyingRemote = false;
let revision = -1;
let loadedVideoPath = '';
let isScrubbing = false;
let subtitleUrl = '';
let previewManifest = null;
let previewSpriteUrls = [];
let serverClockOffsetMs = 0;
let syncTimer = null;

roomSidebar.appendChild(waitingElement);
sidebarToggle.onclick = () => {
    const collapsed = roomGrid.classList.toggle('sidebar-collapsed');
    sidebarToggle.innerText = collapsed ? '❮' : '❯';
    sidebarToggle.setAttribute('aria-label', collapsed ? 'Expand controls' : 'Collapse controls');
    sidebarToggle.title = collapsed ? 'Expand controls' : 'Collapse controls';
};

if (!roomId || !videoPath || !token) location.href = '/';
else {
    linkElement.value = location.href;
    connect();
}

function roomSocketUrl() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${protocol}//${location.host}/api/watch/rooms/${encodeURIComponent(roomId)}`;
}

function connect() {
    socket = new WebSocket(roomSocketUrl());
    socket.onopen = () => socket.send(JSON.stringify({ type: 'auth', token }));
    socket.onmessage = event => {
        let message;
        try { message = JSON.parse(event.data); } catch (_) { return; }
        if (message.type === 'participant') { participantId = message.participant_id; owner = message.owner === true; return; }
        if (message.type === 'participants') { renderParticipants(message.participants || []); return; }
        if (message.type === 'ping') { socket.send(JSON.stringify({ type: 'pong', client_received_ms: Date.now() })); return; }
        if (message.type === 'clock') {
            const clientReceived = Number(message.client_received_ms);
            if (Number.isFinite(clientReceived) && Number.isFinite(Number(message.server_time_ms))) {
                serverClockOffsetMs = Number(message.server_time_ms) - ((clientReceived + Date.now()) / 2);
            }
            return;
        }
        if (message.type === 'kicked') { showRemoved(); return; }
        if (message.type === 'error') { statusElement.innerText = message.detail || 'Room error.'; return; }
        if (message.type === 'state') applyRoomState(message);
    };
    socket.onclose = event => {
        if (syncTimer) { clearInterval(syncTimer); syncTimer = null; }
        if (event.code !== 4406) statusElement.innerText = 'The room connection was lost. Refresh to reconnect.';
        readyButton.disabled = true;
    };
    syncTimer = setInterval(() => {
        if (ready && started && socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'sync' }));
    }, 3000);
}

function renderParticipants(people) {
    participantsElement.innerHTML = '';
    people.forEach(person => {
        const item = document.createElement('li'); item.className = 'person';
        const label = document.createElement('span');
        label.innerText = person.email || 'Room member';
        const state = document.createElement('small'); state.innerText = person.ready ? 'Ready' : 'Not ready';
        item.append(label, state);
        if (owner && person.participant_id !== participantId) {
            const kick = document.createElement('button'); kick.type = 'button'; kick.innerText = 'Remove';
            kick.onclick = () => socket.send(JSON.stringify({ type: 'kick', participant_id: person.participant_id }));
            item.appendChild(kick);
        }
        participantsElement.appendChild(item);
    });
}

function applyRoomState(state) {
    const isPeriodicSync = state.sync === true;
    if (Number(state.revision) < revision || (Number(state.revision) === revision && !isPeriodicSync)) return;
    revision = Number(state.revision);
    if (state.media_path && state.media_path !== loadedVideoPath) {
        loadedVideoPath = state.media_path;
        const videoName = (loadedVideoPath.split('/').pop() || loadedVideoPath).replace(/\.[^.]+$/, '');
        videoTitle.innerText = videoName;
        document.title = `${videoName} | Watch Together | Adlv Media Stream`;
        player.src = `/api/media/file/${loadedVideoPath.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(token)}`;
        loadSubtitles(loadedVideoPath);
        loadTimelinePreview(loadedVideoPath);
    }
    started = state.started === true;
    waitingElement.innerText = started
        ? (ready ? 'Playback is synchronized for everyone.' : 'You joined an active room. Press “I’m ready” to join playback.')
        : 'Press “I’m ready” when you are ready. Playback begins when everyone in the room is ready.';
    if (!ready || !started) { applyingRemote = true; player.pause(); applyingRemote = false; return; }
    const elapsed = state.playing
        ? Math.max(0, Date.now() + serverClockOffsetMs - Number(state.server_time_ms || Date.now())) / 1000 * Number(state.playback_rate || 1)
        : 0;
    const target = Math.max(0, Number(state.position || 0) + elapsed);
    applyingRemote = true;
    const baseRate = Number(state.playback_rate || 1);
    const drift = target - player.currentTime;
    if (Number.isFinite(target) && (!state.playing || Math.abs(drift) > 0.8)) {
        if (Math.abs(drift) > 0.12) player.currentTime = target;
        player.playbackRate = baseRate;
    } else if (state.playing && Math.abs(drift) > 0.08) {
        const correction = Math.max(-0.08, Math.min(0.08, drift * 0.12));
        player.playbackRate = baseRate * (1 + correction);
    } else {
        player.playbackRate = baseRate;
    }
    playbackRate.value = String(state.playback_rate || 1);
    const result = state.playing ? player.play() : Promise.resolve(player.pause());
    Promise.resolve(result).catch(() => { statusElement.innerText = 'Tap the video once if your browser blocks playback.'; }).finally(() => { applyingRemote = false; });
    if (!isPeriodicSync) statusElement.innerText = state.playing ? 'Playing together.' : 'Paused for everyone.';
}

function sendCommand(action, values = {}) {
    if (!started || !ready || applyingRemote || socket?.readyState !== WebSocket.OPEN) return;
    const commandId = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    socket.send(JSON.stringify({ type: 'command', action, ...values, command_id: commandId }));
}

readyButton.onclick = () => {
    if (ready || socket?.readyState !== WebSocket.OPEN) return;
    ready = true; readyButton.disabled = true; readyButton.innerText = 'Ready';
    statusElement.innerText = 'You are ready. Waiting for everyone else...';
    socket.send(JSON.stringify({ type: 'ready' }));
};
document.getElementById('copy-link').onclick = async () => {
    try { await navigator.clipboard.writeText(linkElement.value); statusElement.innerText = 'Room link copied.'; }
    catch (_) { linkElement.select(); document.execCommand('copy'); statusElement.innerText = 'Room link copied.'; }
};
function updateControls() {
    const duration = Number.isFinite(player.duration) ? player.duration : 0;
    timeline.max = String(duration);
    timeline.value = String(Math.min(duration, player.currentTime || 0));
    playerTime.innerText = `${formatTime(player.currentTime)} / ${formatTime(duration)}`;
    playToggle.innerText = player.paused ? '▶' : 'Ⅱ';
    playToggle.setAttribute('aria-label', player.paused ? 'Play' : 'Pause');
    muteToggle.innerText = player.muted || player.volume === 0 ? '🔇' : '🔊';
    videoFrame.classList.toggle('paused', player.paused);
}
function formatTime(value) {
    if (!Number.isFinite(value) || value < 0) return '0:00';
    const seconds = Math.floor(value); const minutes = Math.floor(seconds / 60); const rest = String(seconds % 60).padStart(2, '0');
    return `${minutes}:${rest}`;
}
function attemptLocalPlayback() {
    if (!ready || !started) { statusElement.innerText = 'Press “I’m ready” before controlling playback.'; return; }
    if (player.paused) player.play().catch(() => {}); else player.pause();
}
playToggle.onclick = attemptLocalPlayback;
videoFrame.onclick = event => { if (!event.target.closest('.timeline-shell, .player-back-button')) attemptLocalPlayback(); };
player.addEventListener('play', () => { updateControls(); sendCommand('play'); });
player.addEventListener('pause', () => { updateControls(); sendCommand('pause'); });
player.addEventListener('timeupdate', updateControls);
player.addEventListener('loadedmetadata', updateControls);
player.addEventListener('durationchange', updateControls);
player.addEventListener('seeked', () => { updateControls(); if (!isScrubbing) sendCommand('seek', { position: player.currentTime }); });
timeline.addEventListener('pointerdown', () => { isScrubbing = true; });
timeline.addEventListener('input', () => { player.currentTime = Number(timeline.value); updateControls(); });
timeline.addEventListener('pointerup', () => { isScrubbing = false; sendCommand('seek', { position: player.currentTime }); });
timeline.addEventListener('pointermove', updateTimelinePreview);
timeline.addEventListener('pointerenter', () => { timelinePreview.style.display = 'block'; });
timeline.addEventListener('pointerleave', () => { timelinePreview.style.display = 'none'; });
muteToggle.onclick = () => { player.muted = !player.muted; updateControls(); };
volumeControl.oninput = () => { player.volume = Number(volumeControl.value); player.muted = player.volume === 0; updateControls(); };
settingsToggle.onclick = event => { event.stopPropagation(); settingsMenu.classList.toggle('open'); };
playbackRate.onchange = () => { player.playbackRate = Number(playbackRate.value); sendCommand('rate', { playback_rate: player.playbackRate }); };
captionsToggle.onchange = () => { [...player.textTracks].forEach(track => { track.mode = captionsToggle.checked ? 'showing' : 'disabled'; }); };
fullscreenToggle.onclick = async () => {
    if (document.fullscreenElement) return document.exitFullscreen();
    if (videoFrame.requestFullscreen) await videoFrame.requestFullscreen();
    else if (player.webkitEnterFullscreen) player.webkitEnterFullscreen();
};
document.addEventListener('click', event => { if (!settingsMenu.contains(event.target) && event.target !== settingsToggle) settingsMenu.classList.remove('open'); });
videoFrame.addEventListener('pointermove', revealControls);
videoFrame.addEventListener('mousemove', revealControls);
function revealControls() { timelineShell.classList.add('controls-visible'); clearTimeout(videoFrame.controlsTimer); videoFrame.controlsTimer = setTimeout(() => { if (!player.paused) timelineShell.classList.remove('controls-visible'); }, 2500); }
async function loadSubtitles(path) {
    try {
        const result = await apiRequest(`/media/subtitle?video_path=${encodeURIComponent(path)}`);
        let trackUrl;
        if (result?.path) {
            trackUrl = `/api/media/file/${result.path.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(token)}`;
        } else {
            const embeddedUrl = `/api/media/embedded-subtitle?video_path=${encodeURIComponent(path)}&token=${encodeURIComponent(token)}`;
            const embeddedResponse = await fetch(embeddedUrl, { cache: 'no-store' });
            if (embeddedResponse.status === 204) return;
            if (!embeddedResponse.ok) {
                const error = new Error(`Embedded subtitle request failed (${embeddedResponse.status})`);
                error.status = embeddedResponse.status;
                throw error;
            }
            trackUrl = embeddedUrl;
        }
        if (trackUrl === subtitleUrl) return;
        subtitleUrl = trackUrl;
        const track = document.createElement('track'); track.kind = 'subtitles'; track.label = 'Subtitles'; track.srclang = 'en'; track.src = trackUrl; track.default = false;
        player.appendChild(track); captionsOption.classList.remove('hidden');
    } catch (error) { if (error.status !== 404) console.warn('Unable to load subtitles', error); }
}
function applySpriteFrame(timeSeconds) {
    if (!previewManifest || !previewSpriteUrls.length) return;
    const frame = Math.max(0, Math.floor(timeSeconds / Number(previewManifest.interval_seconds || 1)));
    const capacity = Number(previewManifest.columns) * Number(previewManifest.rows);
    const sheetIndex = Math.min(Math.floor(frame / capacity), previewSpriteUrls.length - 1);
    const cell = frame % capacity; const column = cell % Number(previewManifest.columns); const row = Math.floor(cell / Number(previewManifest.columns));
    timelinePreview.style.backgroundImage = `url("${previewSpriteUrls[sheetIndex]}")`;
    timelinePreview.style.backgroundSize = `${previewManifest.columns * 100}% ${previewManifest.rows * 100}%`;
    timelinePreview.style.backgroundPosition = `${previewManifest.columns === 1 ? 0 : (column / (previewManifest.columns - 1)) * 100}% ${previewManifest.rows === 1 ? 0 : (row / (previewManifest.rows - 1)) * 100}%`;
}
async function loadTimelinePreview(path) {
    try {
        const data = (await apiRequest('/previews')).find(item => item.media_path === path);
        if (!data?.sheets?.length) return;
        previewManifest = data;
        previewSpriteUrls = data.sheets.map(spritePath => `/api/media/file/${spritePath.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(token)}`);
        timeline.max = String(data.duration_seconds || player.duration || 0);
        applySpriteFrame(0);
    } catch (_) { /* Timeline remains usable without preview sheets. */ }
}
function updateTimelinePreview(event) {
    if (!previewManifest) return;
    const rect = timeline.getBoundingClientRect();
    const fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width));
    timelinePreview.style.left = `${fraction * 100}%`;
    applySpriteFrame(fraction * Number(timeline.max));
}
updateControls();
function showRemoved() { document.getElementById('removed-modal').classList.remove('hidden'); readyButton.disabled = true; player.pause(); }
document.getElementById('removed-ok').onclick = () => { location.href = '/'; };
