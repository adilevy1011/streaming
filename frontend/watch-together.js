const params = new URLSearchParams(location.search);
const roomId = params.get('room');
const videoPath = params.get('path');
const token = getAccessToken();
const player = document.getElementById('room-video');
const statusElement = document.getElementById('status');
const waitingElement = document.getElementById('waiting');
const readyButton = document.getElementById('ready-button');
const participantsElement = document.getElementById('participants');
const linkElement = document.getElementById('room-link');
let socket;
let participantId = '';
let owner = false;
let ready = false;
let started = false;
let applyingRemote = false;
let revision = -1;
let loadedVideoPath = '';

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
        if (message.type === 'kicked') { showRemoved(); return; }
        if (message.type === 'error') { statusElement.innerText = message.detail || 'Room error.'; return; }
        if (message.type === 'state') applyRoomState(message);
    };
    socket.onclose = event => {
        if (event.code !== 4406) statusElement.innerText = 'The room connection was lost. Refresh to reconnect.';
        readyButton.disabled = true;
    };
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
    if (Number(state.revision) <= revision) return;
    revision = Number(state.revision);
    if (state.media_path && state.media_path !== loadedVideoPath) {
        loadedVideoPath = state.media_path;
        player.src = `/api/media/file/${loadedVideoPath.split('/').map(encodeURIComponent).join('/')}?token=${encodeURIComponent(token)}`;
    }
    started = state.started === true;
    waitingElement.innerText = started
        ? (ready ? 'Playback is synchronized for everyone.' : 'You joined an active room. Press “I’m ready” to join playback.')
        : 'Press “I’m ready” when you are ready. Playback begins when everyone in the room is ready.';
    if (!ready || !started) { applyingRemote = true; player.pause(); applyingRemote = false; return; }
    const elapsed = state.playing ? Math.max(0, Date.now() - Number(state.server_time_ms || Date.now())) / 1000 * Number(state.playback_rate || 1) : 0;
    const target = Math.max(0, Number(state.position || 0) + elapsed);
    applyingRemote = true;
    if (Number.isFinite(target) && Math.abs(player.currentTime - target) > 0.7) player.currentTime = target;
    player.playbackRate = Number(state.playback_rate || 1);
    const result = state.playing ? player.play() : Promise.resolve(player.pause());
    Promise.resolve(result).catch(() => { statusElement.innerText = 'Tap the video once if your browser blocks playback.'; }).finally(() => { applyingRemote = false; });
    statusElement.innerText = state.playing ? 'Playing together.' : 'Paused for everyone.';
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
player.addEventListener('play', () => sendCommand('play'));
player.addEventListener('pause', () => sendCommand('pause'));
player.addEventListener('seeked', () => sendCommand('seek', { position: player.currentTime }));
player.addEventListener('ratechange', () => sendCommand('rate', { playback_rate: player.playbackRate }));
function showRemoved() { document.getElementById('removed-modal').classList.remove('hidden'); readyButton.disabled = true; player.pause(); }
document.getElementById('removed-ok').onclick = () => { location.href = '/'; };
