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
let adminFolderOrderings = [];
let adminSelectedOrderingFolder = '';
let adminOrderingDraft = [];
const adminOrderingDrafts = new Map();
let adminAllowUnload = false;

function hasUnsavedAdminChanges() {
    const permissionsDirty = adminChangesPending && !!selectedAdminUser && !selectedAdminUser.admin_access;
    return permissionsDirty || adminOrderingDrafts.size > 0;
}

function updateAdminSaveButton() {
    document.getElementById('admin-save').innerText = hasUnsavedAdminChanges() ? 'Save(*)' : 'Save';
}

function homeUrl() {
    return window.location.protocol === 'file:' ? 'index.html' : '/';
}

async function initAdminActions() {
    const backLink = document.getElementById('back-link');
    backLink.href = homeUrl();
    backLink.onclick = event => {
        if (!hasUnsavedAdminChanges()) return;
        event.preventDefault();
        document.getElementById('admin-leave-modal').classList.remove('hidden');
    };
    const session = await getAuthenticatedSession();
    if (!session) {
        redirectToLogin();
        return;
    }
    let adminAccess = false;
    try { adminAccess = !!(await apiRequest('/profile')).admin_access; }
    catch (error) {
        if (error.status === 401) {
            redirectToLogin();
            return;
        }
        console.warn('Unable to load profile access', error);
    }
    if (!adminAccess) {
        window.location.replace(homeUrl());
        return;
    }
    document.getElementById('admin-container').classList.remove('hidden');
    await loadAdminData();
}

async function loadAdminData() {
    const status = document.getElementById('admin-status');
    setAdminTab('video');
    status.innerHTML = '<span class="loading-spinner" role="status" aria-label="Loading admin data"></span> Loading users and videos...';
    try {
        [adminUsers, adminVideos, adminFolderOrderings] = await Promise.all([
            apiRequest('/admin/users'),
            apiRequest('/admin/videos'),
            apiRequest('/admin/folder-orderings')
        ]);
        adminUsers = adminUsers.filter(user => !user.admin_access);
        status.innerText = '';
        renderAdminUsers();
        if (adminUsers.length) selectAdminUser(adminUsers[0]);
        renderAdminOrderingFolders();
    } catch (error) {
        status.innerText = `Unable to load admin data: ${error.message}`;
    }
}

function setAdminTab(tab) {
    const videoTab = document.getElementById('admin-video-tab');
    const orderingTab = document.getElementById('admin-ordering-tab');
    const videoPanel = document.getElementById('admin-video-panel');
    const orderingPanel = document.getElementById('admin-ordering-panel');
    const videoActive = tab === 'video';
    videoTab.classList.toggle('active', videoActive);
    orderingTab.classList.toggle('active', !videoActive);
    videoTab.setAttribute('aria-selected', String(videoActive));
    orderingTab.setAttribute('aria-selected', String(!videoActive));
    videoPanel.classList.toggle('hidden', !videoActive);
    orderingPanel.classList.toggle('hidden', videoActive);
}

function adminOrderingName(path) {
    return path.split('/').pop() || 'Library root';
}

function renderAdminOrderingFolders() {
    const select = document.getElementById('admin-ordering-folder');
    if (!select) return;
    select.innerHTML = '';
    adminFolderOrderings.forEach(ordering => {
        const option = document.createElement('option');
        option.value = ordering.folder_path || '';
        option.innerText = ordering.folder_path || 'Library root';
        select.appendChild(option);
    });
    if (!adminFolderOrderings.some(ordering => (ordering.folder_path || '') === adminSelectedOrderingFolder)) {
        adminSelectedOrderingFolder = adminFolderOrderings[0]?.folder_path || '';
    }
    select.value = adminSelectedOrderingFolder;
    const ordering = adminFolderOrderings.find(item => (item.folder_path || '') === adminSelectedOrderingFolder);
    adminOrderingDraft = [...(adminOrderingDrafts.get(adminSelectedOrderingFolder) || ordering?.item_paths || [])];
    renderAdminOrderingItems();
}

function renderAdminOrderingItems() {
    const container = document.getElementById('admin-ordering-items');
    if (!container) return;
    container.innerHTML = '';
    adminOrderingDraft.forEach(path => {
        const item = document.createElement('div');
        item.className = 'admin-ordering-item';
        item.draggable = true;
        item.dataset.path = path;
        item.innerText = adminOrderingName(path);
        item.title = path;
        item.ondragstart = event => event.dataTransfer.setData('text/plain', path);
        item.ondragover = event => event.preventDefault();
        item.ondrop = event => {
            event.preventDefault();
            const moved = event.dataTransfer.getData('text/plain');
            const from = adminOrderingDraft.indexOf(moved);
            const to = adminOrderingDraft.indexOf(path);
            if (from < 0 || to < 0 || from === to) return;
            adminOrderingDraft.splice(from, 1);
            adminOrderingDraft.splice(to, 0, moved);
            const original = adminFolderOrderings.find(item => (item.folder_path || '') === adminSelectedOrderingFolder);
            if (sameAccessList(adminOrderingDraft, original?.item_paths || [])) adminOrderingDrafts.delete(adminSelectedOrderingFolder);
            else adminOrderingDrafts.set(adminSelectedOrderingFolder, [...adminOrderingDraft]);
            document.getElementById('admin-status').innerText = 'Unsaved changes';
            updateAdminSaveButton();
            renderAdminOrderingItems();
        };
        container.appendChild(item);
    });
}

async function saveAdminFolderOrderings() {
    for (const [folderPath, itemPaths] of [...adminOrderingDrafts]) {
        const current = adminFolderOrderings.find(ordering => (ordering.folder_path || '') === folderPath);
        if (sameAccessList(itemPaths, current?.item_paths || [])) {
            adminOrderingDrafts.delete(folderPath);
            continue;
        }
        const saved = await apiRequest(`/admin/folder-ordering?folder_path=${encodeURIComponent(folderPath)}`, {
            method: 'PATCH',
            body: JSON.stringify({ item_paths: itemPaths })
        });
        adminFolderOrderings = adminFolderOrderings.map(ordering =>
            (ordering.folder_path || '') === folderPath ? saved : ordering
        );
        adminOrderingDrafts.delete(folderPath);
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
        button.innerText = user.email || user.user_id;
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
    title.innerText = `Videos for ${user.email}`;
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
        const folderParts = parts.slice(1, -1); 
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
    updateAdminSaveButton();
    const videos = document.getElementById('admin-videos');
    videos.innerHTML = '';
    const selectAll = document.getElementById('admin-select-all');
    const deselectAll = document.getElementById('admin-deselect-all');
    const newVideosCheckbox = document.getElementById('admin-new-videos-access');
    const query = adminVideoSearch.trim().toLowerCase();
    const visibleVideos = adminVideos.filter(video => !query || video.path.toLowerCase().includes(query));
    selectAll.disabled = !selectedAdminUser;
    deselectAll.disabled = !selectedAdminUser;
    newVideosCheckbox.checked = adminDraftNewVideosAccess;
    newVideosCheckbox.disabled = !selectedAdminUser;
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
    const permissionsDirty = !!selectedAdminUser && !selectedAdminUser.admin_access && adminChangesPending;
    const changes = permissionsDirty ? adminVideos.filter(video => !sameAccessList(
        adminDraftAccess.get(video.path) || [],
        adminOriginalAccess.get(video.path) || []
    )) : [];
    const newVideosAccessChanged = permissionsDirty && adminDraftNewVideosAccess !== adminOriginalNewVideosAccess;
    if (!changes.length && !newVideosAccessChanged && !adminOrderingDrafts.size) {
        adminChangesPending = false;
        if (selectedAdminUser) renderAdminVideos();
        return true;
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
        await saveAdminFolderOrderings();
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
        renderAdminOrderingFolders();
        return true;
    } catch (error) {
        document.getElementById('admin-status').innerText = `Unable to save changes: ${error.message}`;
        return false;
    } finally {
        loadingModal.classList.add('hidden');
        updateAdminSaveButton();
    }
}

function leaveAdminPage() {
    adminAllowUnload = true;
    window.location.href = homeUrl();
}

window.addEventListener('beforeunload', event => {
    if (adminAllowUnload || !hasUnsavedAdminChanges()) return;
    event.preventDefault();
    event.returnValue = '';
});

document.getElementById('admin-leave-cancel').onclick = () => {
    document.getElementById('admin-leave-modal').classList.add('hidden');
};
document.getElementById('admin-leave-discard').onclick = leaveAdminPage;
document.getElementById('admin-leave-save').onclick = async () => {
    document.getElementById('admin-leave-modal').classList.add('hidden');
    if (await saveAdminChanges()) leaveAdminPage();
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
document.getElementById('admin-video-tab').onclick = () => setAdminTab('video');
document.getElementById('admin-ordering-tab').onclick = () => setAdminTab('ordering');
document.getElementById('admin-ordering-folder').onchange = event => {
    adminSelectedOrderingFolder = event.target.value;
    renderAdminOrderingFolders();
};


initAdminActions();
