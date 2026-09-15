// ─── DOM refs ─────────────────────────────────────────────────────────────────

const authScreen     = document.getElementById('authScreen');
const appLayout      = document.getElementById('appLayout');
const authUsername   = document.getElementById('authUsername');
const authPassword   = document.getElementById('authPassword');
const authError      = document.getElementById('authError');
const authSubmit     = document.getElementById('authSubmit');
const headerUsername = document.getElementById('headerUsername');

const messagesDiv      = document.getElementById('messages');
const messageInput     = document.getElementById('messageInput');
const sendButton       = document.getElementById('sendButton');
const imageInput       = document.getElementById('imageInput');
const conversationList = document.getElementById('conversationList');
const newChatBtn       = document.getElementById('newChatBtn');
const sidebarToggle    = document.getElementById('sidebarToggle');
const sidebar          = document.getElementById('sidebar');

let currentImages   = [];
let currentFiles    = [];
let currentVideo    = null;
let useCodingAgent  = false;
let currentThreadId = null;
let currentTab      = 'login';

const LANG_MAP = {
    py: 'python', js: 'javascript', ts: 'typescript',
    html: 'html', css: 'css', java: 'java', cpp: 'cpp',
    c: 'c', go: 'go', rs: 'rust', md: 'markdown',
    json: 'json', yaml: 'yaml', yml: 'yaml', sh: 'bash',
    txt: 'text'
};

// ─── Auth ─────────────────────────────────────────────────────────────────────

function switchTab(tab) {
    currentTab = tab;
    document.getElementById('loginTab').classList.toggle('active', tab === 'login');
    document.getElementById('registerTab').classList.toggle('active', tab === 'register');
    authSubmit.textContent = tab === 'login' ? 'Sign in' : 'Register';
    authError.textContent = '';
}

async function submitAuth() {
    const username = authUsername.value.trim();
    const password = authPassword.value;
    if (!username || !password) {
        authError.textContent = 'Please fill in all fields.';
        return;
    }

    authSubmit.disabled = true;
    authError.textContent = '';

    try {
        const res = await fetch(`/auth/${currentTab}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ username, password })
        });
        const data = await res.json();

        if (!res.ok) {
            authError.textContent = data.detail || 'Something went wrong.';
            return;
        }

        if (currentTab === 'register') {
            authError.style.color = '#4caf50';
            authError.textContent = 'Account created! Signing in...';
            currentTab = 'login';
            await submitAuth();
            return;
        }

        showApp(data.username);
    } finally {
        authSubmit.disabled = false;
    }
}

async function logout() {
    await fetch('/auth/logout', { method: 'POST' });
    showAuth();
}

function showAuth() {
    authScreen.style.display = 'flex';
    appLayout.style.display = 'none';
    authUsername.value = '';
    authPassword.value = '';
    authError.textContent = '';
    authError.style.color = '';
    currentThreadId = null;
    currentImages = [];
    clearFiles();
    conversationList.innerHTML = '';
    messagesDiv.innerHTML = '<div class="message assistant">Ready. What are we working on?</div>';
    imageInput.value = '';
    messageInput.value = '';
}

function showApp(username) {
    authScreen.style.display = 'none';
    appLayout.style.display = 'flex';
    headerUsername.textContent = username;
    loadConversations();
}

async function initSession() {
    try {
        const res = await fetch('/auth/me');
        if (res.ok) {
            const user = await res.json();
            showApp(user.username);
        } else if (res.status === 401) {
            const refreshRes = await fetch('/auth/refresh', { method: 'POST' });
            if (refreshRes.ok) {
                const user = await refreshRes.json();
                showApp(user.username);
            } else {
                showAuth();
            }
        }
    } catch {
        showAuth();
    }
}

async function apiFetch(url, options = {}) {
    let res = await fetch(url, options);
    if (res.status === 401) {
        const refreshRes = await fetch('/auth/refresh', { method: 'POST' });
        if (refreshRes.ok) {
            res = await fetch(url, options);
        } else {
            showAuth();
            throw new Error('Session expired');
        }
    }
    return res;
}

authPassword.addEventListener('keydown', e => { if (e.key === 'Enter') submitAuth(); });
authUsername.addEventListener('keydown', e => { if (e.key === 'Enter') authPassword.focus(); });

// ─── Sidebar ──────────────────────────────────────────────────────────────────

sidebarToggle.addEventListener('click', () => sidebar.classList.toggle('collapsed'));

// ─── Conversations ────────────────────────────────────────────────────────────

async function loadConversations() {
    const res = await apiFetch('/conversations');
    const conversations = await res.json();

    conversationList.innerHTML = '';

    if (conversations.length === 0) {
        conversationList.innerHTML = '<div class="no-convs">No conversations yet.</div>';
        return;
    }

    for (const conv of conversations) appendConversationItem(conv);

    if (!currentThreadId && conversations.length > 0) {
        selectConversation(conversations[0].thread_id, conversations[0].title);
    }
}

function appendConversationItem(conv) {
    const item = document.createElement('div');
    item.className = 'conv-item';
    item.dataset.threadId = conv.thread_id;
    if (conv.thread_id === currentThreadId) item.classList.add('active');

    const titleSpan = document.createElement('span');
    titleSpan.className = 'conv-title';
    titleSpan.textContent = conv.title;
    titleSpan.title = conv.title;

    const deleteBtn = document.createElement('button');
    deleteBtn.className = 'conv-delete';
    deleteBtn.textContent = '✕';
    deleteBtn.title = 'Delete';
    deleteBtn.addEventListener('click', async (e) => {
        e.stopPropagation();
        await deleteConversation(conv.thread_id);
    });

    item.appendChild(titleSpan);
    item.appendChild(deleteBtn);
    item.addEventListener('click', () => selectConversation(conv.thread_id, conv.title));
    conversationList.appendChild(item);
}

async function selectConversation(threadId, title) {
    if (currentThreadId && currentThreadId !== threadId) {
        _cancelActiveStream(currentThreadId);
    }
    currentThreadId = threadId;

    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.threadId === threadId);
    });

    messagesDiv.innerHTML = `<div class="message assistant">Loading…</div>`;

    try {
        const res = await apiFetch(`/conversations/${threadId}/messages`);
        const messages = await res.json();

        messagesDiv.innerHTML = '';

        if (messages.length === 0) {
            messagesDiv.innerHTML = `<div class="message assistant">Ready. What are we working on?</div>`;
            return;
        }

        for (const msg of messages) {
            const div = document.createElement('div');
            div.className = `message ${msg.role}`;

            if (msg.parts) {
                for (const part of msg.parts) {
                    if (part.type === 'text') {
                        if (msg.role === 'assistant') {
                            const inner = document.createElement('div');
                            inner.innerHTML = marked.parse(part.text);
                            div.appendChild(inner);
                        } else {
                            const span = document.createElement('span');
                            span.textContent = part.text;
                            div.appendChild(span);
                        }
                    } else if (part.type === 'image_url') {
                        let imgGrid = div.querySelector('.message-image-grid');
                        if (!imgGrid) {
                            imgGrid = document.createElement('div');
                            imgGrid.className = 'message-image-grid';
                            div.appendChild(imgGrid);
                        }
                        const img = document.createElement('img');
                        img.src = part.url;
                        img.className = 'message-thumb';
                        imgGrid.appendChild(img);
                    } else if (part.type === 'video') {
                        let filesDiv = div.querySelector('.message-files');
                        if (!filesDiv) {
                            filesDiv = document.createElement('div');
                            filesDiv.className = 'message-files';
                            div.appendChild(filesDiv);
                        }
                        const chip = document.createElement('span');
                        chip.className = 'message-file-chip';
                        chip.textContent = `🎬 ${part.filename} (${part.frame_count} frames)`;
                        filesDiv.appendChild(chip);
                    } else if (part.type === 'file') {
                        let filesDiv = div.querySelector('.message-files');
                        if (!filesDiv) {
                            filesDiv = document.createElement('div');
                            filesDiv.className = 'message-files';
                            div.appendChild(filesDiv);
                        }
                        const chip = document.createElement('span');
                        chip.className = 'message-file-chip';
                        chip.textContent = `📄 ${part.name}`;
                        if (part.content) {
                            chip.title = 'Click to download';
                            chip.addEventListener('click', () => downloadFile(part.name, part.content));
                        }
                        filesDiv.appendChild(chip);
                    }
                }
            } else {
                if (msg.role === 'assistant') {
                    div.innerHTML = marked.parse(msg.content);
                } else {
                    const span = document.createElement('span');
                    span.textContent = msg.content;
                    div.appendChild(span);
                }
            }

            messagesDiv.appendChild(div);
        }

        messagesDiv.scrollTop = messagesDiv.scrollHeight;

        // If a generation is still running on this thread, reconnect to it
        await maybeReconnectStream(threadId);
    } catch (e) {
        if (e.message !== 'Session expired') {
            messagesDiv.innerHTML = `<div class="message assistant">Failed to load messages.</div>`;
        }
    }
}

async function createNewConversation() {
    const title = `Chat ${new Date().toLocaleString()}`;
    const res = await apiFetch('/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
    });
    const conv = await res.json();

    const placeholder = conversationList.querySelector('.no-convs');
    if (placeholder) placeholder.remove();

    appendConversationItem(conv);
    selectConversation(conv.thread_id, conv.title);
    conversationList.scrollTop = 0;
    messageInput.focus();
}

async function deleteConversation(threadId) {
    await apiFetch(`/conversations/${threadId}`, { method: 'DELETE' });

    const item = conversationList.querySelector(`[data-thread-id="${threadId}"]`);
    if (item) item.remove();

    if (currentThreadId === threadId) {
        currentThreadId = null;
        messagesDiv.innerHTML = '<div class="message assistant">Select or start a conversation.</div>';
        const first = conversationList.querySelector('.conv-item');
        if (first) first.click();
    }

    if (conversationList.children.length === 0) {
        conversationList.innerHTML = '<div class="no-convs">No conversations yet.</div>';
    }
}

newChatBtn.addEventListener('click', createNewConversation);

// ─── File handling ────────────────────────────────────────────────────────────

const fileChipsContainer = document.getElementById('fileChips');

imageInput.addEventListener('change', async function(e) {
    const files = Array.from(e.target.files);
    if (!files.length) return;

    for (const file of files) {
        if (file.type.startsWith('image/')) {
            if (currentImages.length >= 5) { alert('Maximum 5 images per message.'); continue; }
            const reader = new FileReader();
            reader.onload = function(e) {
                const dataUrl = e.target.result;
                currentImages.push(dataUrl);
                renderImageChip(dataUrl, currentImages.length - 1);
            };
            reader.readAsDataURL(file);
        } else if (file.type.startsWith('video/')) {
            if (currentVideo) { alert('Only one video per message.'); continue; }
            renderVideoChip(file.name, 'Extracting frames…');
            const frames = await extractFrames(file);
            currentVideo = { name: file.name, frames };
            updateVideoChip(file.name, `${frames.length} frames`);
        } else if (file.name.endsWith('.zip')) {
            await handleZipFile(file);
        } else {
            const text = await file.text();
            const ext = file.name.split('.').pop().toLowerCase();
            const lang = LANG_MAP[ext] || ext;
            currentFiles.push({ name: file.name, lang, content: text });
            renderFileChip(file.name, currentFiles.length - 1);
        }
    }

    imageInput.value = '';
});

function renderFileChip(name, index) {
    const chip = document.createElement('div');
    chip.className = 'file-chip';
    chip.dataset.index = index;

    const icon = document.createElement('span');
    icon.className = 'file-chip-icon';
    icon.textContent = '📄';

    const label = document.createElement('span');
    label.className = 'file-chip-name';
    label.textContent = name;
    label.title = name;

    const remove = document.createElement('button');
    remove.className = 'file-chip-remove';
    remove.textContent = '×';
    remove.addEventListener('click', () => {
        currentFiles.splice(index, 1);
        chip.remove();
        if (currentFiles.length === 0 && currentImages.length === 0 && !currentVideo)
            fileChipsContainer.classList.remove('show');
    });

    chip.appendChild(icon);
    chip.appendChild(label);
    chip.appendChild(remove);
    fileChipsContainer.appendChild(chip);
    fileChipsContainer.classList.add('show');
}

function renderImageChip(dataUrl, index) {
    const chip = document.createElement('div');
    chip.className = 'file-chip image-chip';
    chip.dataset.imageIndex = index;

    const thumb = document.createElement('img');
    thumb.className = 'image-chip-thumb';
    thumb.src = dataUrl;

    const remove = document.createElement('button');
    remove.className = 'file-chip-remove';
    remove.textContent = '×';
    remove.addEventListener('click', () => {
        currentImages.splice(index, 1);
        chip.remove();
        if (currentImages.length === 0 && currentFiles.length === 0 && !currentVideo)
            fileChipsContainer.classList.remove('show');
    });

    chip.appendChild(thumb);
    chip.appendChild(remove);
    fileChipsContainer.appendChild(chip);
    fileChipsContainer.classList.add('show');
}

function renderVideoChip(name, subtitle) {
    const chip = document.createElement('div');
    chip.className = 'file-chip video-chip';
    chip.id = 'videoChip';

    const icon = document.createElement('span');
    icon.className = 'file-chip-icon';
    icon.textContent = '🎬';

    const info = document.createElement('div');
    info.className = 'video-chip-info';

    const label = document.createElement('span');
    label.className = 'file-chip-name';
    label.textContent = name;
    label.title = name;

    const sub = document.createElement('span');
    sub.className = 'video-chip-sub';
    sub.id = 'videoChipSub';
    sub.textContent = subtitle;

    info.appendChild(label);
    info.appendChild(sub);

    const remove = document.createElement('button');
    remove.className = 'file-chip-remove';
    remove.textContent = '×';
    remove.addEventListener('click', () => {
        currentVideo = null;
        chip.remove();
        if (currentImages.length === 0 && currentFiles.length === 0)
            fileChipsContainer.classList.remove('show');
    });

    chip.appendChild(icon);
    chip.appendChild(info);
    chip.appendChild(remove);
    fileChipsContainer.appendChild(chip);
    fileChipsContainer.classList.add('show');
}

function updateVideoChip(name, subtitle) {
    const sub = document.getElementById('videoChipSub');
    if (sub) sub.textContent = subtitle;
}

async function extractFrames(file, fps = 10, maxDuration = 10, width = 640, height = 360) {
    return new Promise((resolve) => {
        const video = document.createElement('video');
        video.src = URL.createObjectURL(file);
        video.muted = true;

        video.addEventListener('loadedmetadata', async () => {
            const duration = Math.min(video.duration, maxDuration);
            const interval = 1 / fps;
            const timestamps = [];
            for (let t = 0; t < duration; t += interval)
                timestamps.push(parseFloat(t.toFixed(3)));

            const canvas = document.createElement('canvas');
            canvas.width = width;
            canvas.height = height;
            const ctx = canvas.getContext('2d');
            const frames = [];

            for (const t of timestamps) {
                await new Promise(res => {
                    video.currentTime = t;
                    video.addEventListener('seeked', function onSeeked() {
                        video.removeEventListener('seeked', onSeeked);
                        ctx.drawImage(video, 0, 0, width, height);
                        frames.push(canvas.toDataURL('image/jpeg', 0.75));
                        res();
                    });
                });
            }

            URL.revokeObjectURL(video.src);
            resolve(frames);
        });
    });
}

function clearFiles() {
    currentImages = [];
    currentFiles = [];
    currentVideo = null;
    fileChipsContainer.innerHTML = '';
    fileChipsContainer.classList.remove('show');
}

async function handleZipFile(file) {
    const zip = await JSZip.loadAsync(file);
    const entries = [];
    zip.forEach((relativePath, entry) => {
        if (!entry.dir) entries.push({ path: relativePath, entry });
    });

    const skip = (path) =>
        path.includes('__pycache__') || path.includes('node_modules') ||
        path.includes('.git') || path.endsWith('.pyc') || path.endsWith('.DS_Store');

    const filtered = entries.filter(e => !skip(e.path));
    const tree = buildFileTree(filtered.map(e => e.path));
    const files = [];

    for (const { path, entry } of filtered) {
        try {
            const content = await entry.async('string');
            const ext = path.split('.').pop().toLowerCase();
            const lang = LANG_MAP[ext] || ext;
            files.push({ name: path, lang, content });
        } catch { /* skip binary */ }
    }

    files.unshift({ name: `${file.name} — file tree`, lang: 'text', content: tree, isTree: true });
    currentFiles.push({ name: file.name, isZip: true, files });
    renderFileChip(file.name, currentFiles.length - 1);
}

function buildFileTree(paths) {
    const lines = [`📦 ${paths.length} files\n`];
    const sorted = [...paths].sort();
    for (const path of sorted) {
        const parts = path.split('/');
        const depth = parts.length - 1;
        lines.push(`${'  '.repeat(depth)}📄 ${parts[parts.length - 1]}`);
    }
    return lines.join('\n');
}

// ─── UI helpers ───────────────────────────────────────────────────────────────

messageInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

messageInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

// ─── Send message ─────────────────────────────────────────────────────────────

// ─── Active stream tracking ───────────────────────────────────────────────────
// Keyed by thread_id so switching conversations cancels the previous reader.

const _activeReaders = {};   // thread_id -> ReadableStreamDefaultReader

function _cancelActiveStream(threadId) {
    if (_activeReaders[threadId]) {
        try { _activeReaders[threadId].cancel(); } catch (_) {}
        delete _activeReaders[threadId];
    }
}

// ─── Thinking state ───────────────────────────────────────────────────────────

function setThinking(on) {
    sendButton.disabled = on;
    messageInput.disabled = on;
    if (on) {
        sendButton.classList.add('thinking');
        sendButton.textContent = '…';
    } else {
        sendButton.classList.remove('thinking');
        sendButton.textContent = 'Send';
    }
}

// ─── Core stream consumer ─────────────────────────────────────────────────────
// Reads SSE from /chat/stream/{taskId} and populates typingDiv + assistantDiv.
// Works for both new messages and reconnects.

async function consumeStream(taskId, threadId, typingDiv, assistantMessageDiv) {
    const response = await apiFetch(`/chat/stream/${taskId}`);
    const reader   = response.body.getReader();
    const decoder  = new TextDecoder();

    _activeReaders[threadId] = reader;

    let fullText     = '';
    let typingRemoved = false;
    let buffer       = '';

    try {
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete last line

            for (const line of lines) {
                if (!line.startsWith('data: ')) continue;
                let data;
                try { data = JSON.parse(line.slice(6)); } catch { continue; }

                if (data.error) {
                    if (!typingRemoved) {
                        typingDiv.remove();
                        assistantMessageDiv.style.display = '';
                        typingRemoved = true;
                    }
                    assistantMessageDiv.textContent = 'Error: ' + data.error;
                } else if (data.content) {
                    if (!typingRemoved) {
                        typingDiv.remove();
                        assistantMessageDiv.style.display = '';
                        typingRemoved = true;
                    }
                    fullText = data.content; // server sends full output once
                    assistantMessageDiv.innerHTML = marked.parse(fullText);
                    messagesDiv.scrollTop = messagesDiv.scrollHeight;
                }
            }
        }
    } finally {
        delete _activeReaders[threadId];
        // Ensure typing indicator is gone even on abort
        if (!typingRemoved) {
            typingDiv.remove();
            assistantMessageDiv.style.display = '';
        }
    }
}

// ─── Reconnect to in-progress stream on conversation switch ───────────────────

async function maybeReconnectStream(threadId) {
    const res  = await apiFetch(`/chat/status/${threadId}`);
    const data = await res.json();

    if (data.status !== 'running') return; // nothing to reconnect to

    setThinking(true);

    // Append typing + placeholder assistant bubble below existing messages
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator show';
    typingDiv.innerHTML = '<span></span><span></span><span></span>';
    messagesDiv.appendChild(typingDiv);

    const assistantMessageDiv = document.createElement('div');
    assistantMessageDiv.className = 'message assistant';
    assistantMessageDiv.style.display = 'none';
    messagesDiv.appendChild(assistantMessageDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    try {
        await consumeStream(data.task_id, threadId, typingDiv, assistantMessageDiv);
    } catch (e) {
        if (e.message !== 'Session expired') {
            assistantMessageDiv.style.display = '';
            assistantMessageDiv.textContent = 'Error: ' + e.message;
        }
    } finally {
        setThinking(false);
    }
}

// ─── Send message ─────────────────────────────────────────────────────────────

async function sendMessage() {
    const message = messageInput.value.trim();
    if (!message && currentImages.length === 0 && currentFiles.length === 0 && !currentVideo) return;

    if (!currentThreadId) await createNewConversation();

    // Cancel any stream still running on this thread
    _cancelActiveStream(currentThreadId);

    setThinking(true);

    // Build full message text with file blocks
    let fullMessage = message;
    if (currentFiles.length > 0) {
        const codeBlocks = currentFiles.map(f => {
            if (f.isZip) {
                return f.files.map(zf =>
                    `### File: ${zf.name}\n\`\`\`${zf.lang}\n${zf.content}\n\`\`\``
                ).join('\n\n');
            }
            return `### File: ${f.name}\n\`\`\`${f.lang}\n${f.content}\n\`\`\``;
        }).join('\n\n');
        fullMessage = message ? `${message}\n\n${codeBlocks}` : codeBlocks;
    }

    // ── User bubble ────────────────────────────────────────────────────────────
    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'message user';

    if (message) {
        const textSpan = document.createElement('span');
        textSpan.textContent = message;
        userMessageDiv.appendChild(textSpan);
    }

    if (currentFiles.length > 0) {
        const filesDiv = document.createElement('div');
        filesDiv.className = 'message-files';
        for (const f of currentFiles) {
            const chip = document.createElement('span');
            chip.className = 'message-file-chip';
            chip.textContent = `📄 ${f.name}`;
            chip.title = f.isZip ? `${f.files.length} files` : 'Click to download';
            if (f.isZip) chip.addEventListener('click', () => downloadZip(f));
            else chip.addEventListener('click', () => downloadFile(f.name, f.content));
            filesDiv.appendChild(chip);
        }
        userMessageDiv.appendChild(filesDiv);
    }

    if (currentImages.length > 0) {
        const imgGrid = document.createElement('div');
        imgGrid.className = 'message-image-grid';
        for (const dataUrl of currentImages) {
            const img = document.createElement('img');
            img.src = dataUrl;
            img.className = 'message-thumb';
            imgGrid.appendChild(img);
        }
        userMessageDiv.appendChild(imgGrid);
    }

    if (currentVideo) {
        const filesDiv = document.createElement('div');
        filesDiv.className = 'message-files';
        const chip = document.createElement('span');
        chip.className = 'message-file-chip';
        chip.textContent = `🎬 ${currentVideo.name} (${currentVideo.frames.length} frames)`;
        filesDiv.appendChild(chip);
        userMessageDiv.appendChild(filesDiv);
    }

    messagesDiv.appendChild(userMessageDiv);

    // ── Typing indicator ───────────────────────────────────────────────────────
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator show';
    typingDiv.innerHTML = '<span></span><span></span><span></span>';
    messagesDiv.appendChild(typingDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    messageInput.value = '';
    messageInput.style.height = 'auto';

    // ── Assistant bubble — hidden until first chunk ────────────────────────────
    const assistantMessageDiv = document.createElement('div');
    assistantMessageDiv.className = 'message assistant';
    assistantMessageDiv.style.display = 'none';
    messagesDiv.appendChild(assistantMessageDiv);

    const capturedThreadId = currentThreadId;

    try {
        // Step 1: fire-and-forget POST → get task_id immediately
        const res = await apiFetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                message: fullMessage,
                thread_id: capturedThreadId,
                images: currentImages,
                video_frames: currentVideo ? currentVideo.frames : null,
                video_filename: currentVideo ? currentVideo.name : null,
                video_frame_count: currentVideo ? currentVideo.frames.length : null,
                use_coding_agent: useCodingAgent
            })
        });

        const { task_id } = await res.json();

        // Step 2: open SSE stream for this task
        await consumeStream(task_id, capturedThreadId, typingDiv, assistantMessageDiv);

    } catch (error) {
        typingDiv.remove();
        assistantMessageDiv.style.display = '';
        if (error.message !== 'Session expired') {
            assistantMessageDiv.textContent = 'Error: ' + error.message;
        }
    }

    clearFiles();
    imageInput.value = '';
    setThinking(false);
    messageInput.focus();
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// ─── File download ────────────────────────────────────────────────────────────

function downloadFile(name, content) {
    const blob = new Blob([content], { type: 'text/plain' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    URL.revokeObjectURL(url);
}

async function downloadZip(f) {
    const zip = new JSZip();
    for (const file of f.files) {
        if (!file.isTree) zip.file(file.name, file.content);
    }
    const blob = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = f.name;
    a.click();
    URL.revokeObjectURL(url);
}

// ─── Marked: code block with copy button ─────────────────────────────────────

const renderer = new marked.Renderer();
renderer.code = function({ text, lang }) {
    const language = lang || '';
    return `
        <div class="code-block">
            <button class="copy-btn">Copy</button>
            <pre><code class="language-${language}">${text}</code></pre>
        </div>
    `;
};
marked.setOptions({ renderer });

messagesDiv.addEventListener('click', function(e) {
    if (e.target.classList.contains('copy-btn')) {
        const code = e.target.nextElementSibling.querySelector('code').innerText;
        navigator.clipboard.writeText(code).then(() => {
            e.target.textContent = 'Copied ✓';
            setTimeout(() => e.target.textContent = 'Copy', 2000);
        });
    }
});

// ─── Coding agent toggle ──────────────────────────────────────────────────────

function toggleCodeMode() {
    useCodingAgent = !useCodingAgent;
    const btn = document.getElementById('codeToggle');
    if (useCodingAgent) {
        btn.classList.add('active');
        btn.title = 'Coding agent active — click to switch back';
    } else {
        btn.classList.remove('active');
        btn.title = 'Use coding agent';
    }
}

// ─── Init ─────────────────────────────────────────────────────────────────────

initSession();
