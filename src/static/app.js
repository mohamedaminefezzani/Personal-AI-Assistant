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
let currentThreadId = null;
let currentTab      = 'login';

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

// ─── Image handling ───────────────────────────────────────────────────────────

imageInput.addEventListener('change', function(e) {
    const file = e.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            currentImage = e.target.result;
            imagePreview.src = currentImage;
            imagePreview.classList.add('show');
        };
        reader.readAsDataURL(file);
    }
});

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
    if (!message && !currentImage) return;

    if (!currentThreadId) await createNewConversation();

    sendButton.disabled = true;
    messageInput.disabled = true;

    const userMessageDiv = document.createElement('div');
    userMessageDiv.className = 'message user';

    if (message) {
        const textSpan = document.createElement('span');
        textSpan.textContent = message;
        userMessageDiv.appendChild(textSpan);
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
            body: JSON.stringify({ message, thread_id: currentThreadId, image: currentImage })
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
    sendButton.disabled = false;
    messageInput.disabled = false;
    messageInput.focus();
    messagesDiv.scrollTop = messagesDiv.scrollHeight;
}

// ─── Init ─────────────────────────────────────────────────────────────────────

initSession();
