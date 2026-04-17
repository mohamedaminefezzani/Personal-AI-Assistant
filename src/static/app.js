const messagesDiv = document.getElementById('messages');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const imageInput = document.getElementById('imageInput');
const imagePreview = document.getElementById('imagePreview');

let currentImage = null;

// Handle image selection
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

// Auto-resize textarea
messageInput.addEventListener('input', function() {
    this.style.height = 'auto';
    this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

// Send on Enter
messageInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

async function sendMessage() {
    const message = messageInput.value.trim();
    if (!message && !currentImage) return;

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
    
    if (!message && !currentImage) return;

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
        thread_id: 'web-session-1',
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

        let fullText = ''; // ✅ accumulate content

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

                        // ✅ Render Markdown properly
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