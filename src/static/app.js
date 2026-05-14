// ─── DOM refs ─────────────────────────────────────────────────────────────────

const authScreen    = document.getElementById('authScreen');
const appLayout     = document.getElementById('appLayout');
const authUsername  = document.getElementById('authUsername');
const authPassword  = document.getElementById('authPassword');
const authError     = document.getElementById('authError');
const authSubmit    = document.getElementById('authSubmit');
const headerUsername = document.getElementById('headerUsername');

const messagesDiv       = document.getElementById('messages');
const messageInput      = document.getElementById('messageInput');
const sendButton        = document.getElementById('sendButton');
const imageInput        = document.getElementById('imageInput');
const imagePreview      = document.getElementById('imagePreview');
const conversationList  = document.getElementById('conversationList');
const newChatBtn        = document.getElementById('newChatBtn');
const sidebarToggle     = document.getElementById('sidebarToggle');
const sidebar           = document.getElementById('sidebar');

let currentImage    = null;
let currentFiles    = []; // code files
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
    authSubmit.textContent = tab === 'login' ? 'Login' : 'Register';
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
            // Auto-login after register
            authError.style.color = '#4caf50';
            authError.textContent = 'Account created! Logging in...';
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
    // Reset all chat state so the next user starts clean
    currentThreadId = null;
    currentImage = null;
    clearFiles();
    conversationList.innerHTML = '';
    messagesDiv.innerHTML = '<div class="message assistant">Hello! I\'m your personal AI assistant. How can I help you today?</div>';
    imagePreview.classList.remove('show');
    imageInput.value = '';
    messageInput.value = '';
}

function showApp(username) {
    authScreen.style.display = 'none';
    appLayout.style.display = 'flex';
    headerUsername.textContent = username;
    loadConversations();
}

// Try to restore session on load
async function initSession() {
    try {
        const res = await fetch('/auth/me');
        if (res.ok) {
            const user = await res.json();
            showApp(user.username);
        } else if (res.status === 401) {
            // Try refresh
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

// Intercept 401s globally and redirect to login
async function apiFetch(url, options = {}) {
    let res = await fetch(url, options);
    if (res.status === 401) {
        // Try silent token refresh first
        const refreshRes = await fetch('/auth/refresh', { method: 'POST' });
        if (refreshRes.ok) {
            res = await fetch(url, options); // retry
        } else {
            showAuth();
            throw new Error('Session expired');
        }
    }
    return res;
}

// Enter key on auth inputs
authPassword.addEventListener('keydown', e => {
    if (e.key === 'Enter') submitAuth();
});
authUsername.addEventListener('keydown', e => {
    if (e.key === 'Enter') authPassword.focus();
});

// ─── Sidebar toggle ───────────────────────────────────────────────────────────

sidebarToggle.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
});

// ─── Conversations ────────────────────────────────────────────────────────────

async function loadConversations() {
    const res = await apiFetch('/conversations');
    const conversations = await res.json();

    conversationList.innerHTML = '';

    if (conversations.length === 0) {
        conversationList.innerHTML = '<div class="no-convs">No conversations yet.</div>';
        return;
    }

    for (const conv of conversations) {
        appendConversationItem(conv);
    }

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
    deleteBtn.textContent = '🗑';
    deleteBtn.title = 'Delete conversation';
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
    currentThreadId = threadId;

    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.threadId === threadId);
    });

    messagesDiv.innerHTML = `<div class="message assistant">Loading...</div>`;

    try {
        const res = await apiFetch(`/conversations/${threadId}/messages`);
        const messages = await res.json();

        messagesDiv.innerHTML = '';

        if (messages.length === 0) {
            messagesDiv.innerHTML = `<div class="message assistant">Conversation: <strong>${title}</strong>. What's on your mind?</div>`;
            return;
        }

        for (const msg of messages) {
            const div = document.createElement('div');
            div.className = `message ${msg.role}`;

            if (msg.parts) {
                // Multi-part message (text + image)
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
                        const img = document.createElement('img');
                        img.src = part.url;
                        div.appendChild(img);
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
                            chip.style.cursor = 'pointer';
                            chip.addEventListener('click', () => downloadFile(part.name, part.content));
                        }
                        filesDiv.appendChild(chip);
                    }
                }
            } else {
                // Plain text message
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

    // Remove "no conversations" placeholder if present
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
        messagesDiv.innerHTML = '<div class="message assistant">Select or create a conversation to get started.</div>';
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
            // Handle as image (only last image wins, matching original behaviour)
            const reader = new FileReader();
            reader.onload = function(e) {
                currentImage = e.target.result;
                imagePreview.src = currentImage;
                imagePreview.classList.add('show');
            };
            reader.readAsDataURL(file);
        } else if (file.name.endsWith('.zip')) {
            await handleZipFile(file);
        } else {
            // Handle as code file
            const text = await file.text();
            const ext = file.name.split('.').pop().toLowerCase();
            const lang = LANG_MAP[ext] || ext;
            currentFiles.push({ name: file.name, lang, content: text });
            renderFileChip(file.name, currentFiles.length - 1);
        }
    }

    // Reset input so the same file can be re-selected if removed
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
        if (currentFiles.length === 0) fileChipsContainer.classList.remove('show');
    });

    chip.appendChild(icon);
    chip.appendChild(label);
    chip.appendChild(remove);
    fileChipsContainer.appendChild(chip);
    fileChipsContainer.classList.add('show');
}

function clearFiles() {
    currentFiles = [];
    fileChipsContainer.innerHTML = '';
    fileChipsContainer.classList.remove('show');
}

async function handleZipFile(file) {
    const zip = await JSZip.loadAsync(file);

    const entries = [];
    zip.forEach((relativePath, entry) => {
        if (!entry.dir) entries.push({ path: relativePath, entry });
    });

    // Filter out unwanted files
    const skip = (path) =>
        path.includes('__pycache__') ||
        path.includes('node_modules') ||
        path.includes('.git') ||
        path.endsWith('.pyc') ||
        path.endsWith('.DS_Store');

    const filtered = entries.filter(e => !skip(e.path));

    // Build tree summary
    const tree = buildFileTree(filtered.map(e => e.path));

    // Read each file's content
    const files = [];
    for (const { path, entry } of filtered) {
        try {
            const content = await entry.async('string');
            const ext = path.split('.').pop().toLowerCase();
            const lang = LANG_MAP[ext] || ext;
            files.push({ name: path, lang, content });
        } catch {
            // skip binary files that can't be read as string
        }
    }

    // Prepend tree as a special entry
    files.unshift({
        name: `${file.name} — file tree`,
        lang: 'text',
        content: tree,
        isTree: true
    });

    currentFiles.push({
    name: file.name,
    isZip: true,
    files: files  // all files including the tree entry
    });
    renderFileChip(file.name, currentFiles.length - 1);
}

function buildFileTree(paths) {
    const lines = [`📦 ${paths.length} files\n`];
    const sorted = [...paths].sort();

    for (const path of sorted) {
        const parts = path.split('/');
        const depth = parts.length - 1;
        const indent = '  '.repeat(depth);
        lines.push(`${indent}📄 ${parts[parts.length - 1]}`);
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

async function sendMessage() {
    const message = messageInput.value.trim();
    if (!message && !currentImage && currentFiles.length === 0) return;

    if (!currentThreadId) await createNewConversation();

    sendButton.disabled = true;
    messageInput.disabled = true;

    // Build full message: user text + appended code files
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

    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'message user';

    if (message) {
        const textSpan = document.createElement('span');
        textSpan.textContent = message;
        userMessageDiv.appendChild(textSpan);
    }

    // Show file chips in the message bubble
    if (currentFiles.length > 0) {
        const filesDiv = document.createElement('div');
        filesDiv.className = 'message-files';
        for (const f of currentFiles) {
            const chip = document.createElement('span');
            chip.className = 'message-file-chip';
            chip.textContent = `📄 ${f.name}`;
            chip.title = f.isZip ? `${f.files.length} files` : 'Click to download';
            if (f.isZip) {
                chip.addEventListener('click', () => downloadZip(f));
            } else {
                chip.addEventListener('click', () => downloadFile(f.name, f.content));
            }
            filesDiv.appendChild(chip);
        }
        userMessageDiv.appendChild(filesDiv);
    }

    if (currentImage) {
        const img = document.createElement('img');
        img.src = currentImage;
        userMessageDiv.appendChild(img);
    }

    messagesDiv.appendChild(userMessageDiv);

    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator show';
    typingDiv.innerHTML = '<span>.</span><span>.</span><span>.</span>';
    messagesDiv.appendChild(typingDiv);
    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    messageInput.value = '';
    messageInput.style.height = 'auto';

    try {
        const response = await apiFetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: fullMessage, thread_id: currentThreadId, image: currentImage })
        });

        typingDiv.remove();

        const assistantMessageDiv = document.createElement('div');
        assistantMessageDiv.className = 'message assistant';
        messagesDiv.appendChild(assistantMessageDiv);

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let fullText = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            const chunk = decoder.decode(value);
            for (const line of chunk.split('\n')) {
                if (line.startsWith('data: ')) {
                    const data = JSON.parse(line.slice(6));
                    if (data.error) {
                        assistantMessageDiv.textContent = 'Error: ' + data.error;
                    } else if (data.content) {
                        fullText += data.content;
                        assistantMessageDiv.innerHTML = marked.parse(fullText);
                        messagesDiv.scrollTop = messagesDiv.scrollHeight;
                    }
                }
            }
        }
    } catch (error) {
        typingDiv.remove();
        if (error.message !== 'Session expired') {
            const errorDiv = document.createElement('div');
            errorDiv.className = 'message assistant';
            errorDiv.textContent = 'Error: ' + error.message;
            messagesDiv.appendChild(errorDiv);
        }
    }

    currentImage = null;
    imagePreview.classList.remove('show');
    imageInput.value = '';
    clearFiles();
    sendButton.disabled = false;
    messageInput.disabled = false;
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

// ─── Init ─────────────────────────────────────────────────────────────────────

initSession();