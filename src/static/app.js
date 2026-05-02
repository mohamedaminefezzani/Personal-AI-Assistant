const messagesDiv = document.getElementById('messages');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const imageInput = document.getElementById('imageInput');
const imagePreview = document.getElementById('imagePreview');
const conversationList = document.getElementById('conversationList');
const newChatBtn = document.getElementById('newChatBtn');
const sidebarToggle = document.getElementById('sidebarToggle');
const sidebar = document.getElementById('sidebar');

let currentImage = null;
let currentThreadId = null; // No longer hardcoded

// ─── Sidebar toggle ───────────────────────────────────────────────────────────

sidebarToggle.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
});

// ─── Conversation management ──────────────────────────────────────────────────

async function loadConversations() {
    const res = await fetch('/conversations');
    const conversations = await res.json();

    conversationList.innerHTML = '';

    if (conversations.length === 0) {
        conversationList.innerHTML = '<div class="no-convs">No conversations yet.</div>';
        return;
    }

    for (const conv of conversations) {
        appendConversationItem(conv);
    }

    // Auto-select the most recent conversation on load
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

    item.addEventListener('click', () => {
        selectConversation(conv.thread_id, conv.title);
    });

    conversationList.appendChild(item);
}

async function selectConversation(threadId, title) {
    currentThreadId = threadId;

    // Update active state in sidebar
    document.querySelectorAll('.conv-item').forEach(el => {
        el.classList.toggle('active', el.dataset.threadId === threadId);
    });

    // Clear chat and show loading state
    messagesDiv.innerHTML = `<div class="message assistant">Loading...</div>`;

    try {
        const res = await fetch(`/conversations/${threadId}/messages`);
        const messages = await res.json();

        messagesDiv.innerHTML = '';

        if (messages.length === 0) {
            messagesDiv.innerHTML = `<div class="message assistant">Conversation: <strong>${title}</strong>. What's on your mind?</div>`;
            return;
        }

        for (const msg of messages) {
            const div = document.createElement('div');
            div.className = `message ${msg.role}`;
            if (msg.role === 'assistant') {
                div.innerHTML = marked.parse(msg.content);
            } else {
                const span = document.createElement('span');
                span.textContent = msg.content;
                div.appendChild(span);
            }
            messagesDiv.appendChild(div);
        }

        messagesDiv.scrollTop = messagesDiv.scrollHeight;
    } catch (e) {
        messagesDiv.innerHTML = `<div class="message assistant">Failed to load messages.</div>`;
    }
}

async function createNewConversation() {
    const title = `Chat ${new Date().toLocaleString()}`;
    const res = await fetch('/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title })
    });
    const conv = await res.json();

    appendConversationItem(conv);
    selectConversation(conv.thread_id, conv.title);

    // Scroll sidebar to top so new chat is visible
    conversationList.scrollTop = 0;

    messageInput.focus();
}

async function deleteConversation(threadId) {
    await fetch(`/conversations/${threadId}`, { method: 'DELETE' });

    // Remove from sidebar
    const item = conversationList.querySelector(`[data-thread-id="${threadId}"]`);
    if (item) item.remove();

    // If deleted conversation was active, clear the chat
    if (currentThreadId === threadId) {
        currentThreadId = null;
        messagesDiv.innerHTML = '<div class="message assistant">Select or create a conversation to get started.</div>';

        // Auto-select next available conversation
        const first = conversationList.querySelector('.conv-item');
        if (first) {
            first.click();
        }
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

function scroll_to_bottom() {
    setTimeout(() => {
        window.scrollTo(0, document.body.scrollHeight);
    }, 100);
}

messageInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

messageInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        scroll_to_bottom();
        sendMessage();
    }
});

// ─── Send message ─────────────────────────────────────────────────────────────

async function sendMessage() {
    const message = messageInput.value.trim();
    if (!message && !currentImage) return;

    // If no conversation is selected, create one automatically
    if (!currentThreadId) {
        await createNewConversation();
    }

    sendButton.disabled = true;
    messageInput.disabled = true;

    // User message
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

    // Typing indicator
    const typingDiv = document.createElement('div');
    typingDiv.className = 'typing-indicator show';
    typingDiv.innerHTML = '<span>.</span><span>.</span><span>.</span>';
    messagesDiv.appendChild(typingDiv);

    messagesDiv.scrollTop = messagesDiv.scrollHeight;

    messageInput.value = '';
    messageInput.style.height = 'auto';

    const payload = {
        message: message,
        thread_id: currentThreadId,  // ✅ dynamic thread_id
        image: currentImage
    };

    try {
        const response = await fetch('/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
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
            const lines = chunk.split('\n');

            for (const line of lines) {
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
        const errorDiv = document.createElement('div');
        errorDiv.className = 'message assistant';
        errorDiv.textContent = 'Error: ' + error.message;
        messagesDiv.appendChild(errorDiv);
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

loadConversations();
