const chatWindow = document.getElementById('chatWindow');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const userEmailInput = document.getElementById('userEmail');
const streamToggle = document.getElementById('streamToggle');

// Initialize markdown parser options
marked.setOptions({
    breaks: true,
    gfm: true,
    headerIds: false
});

// Auto-detect text direction (RTL for Arabic/Hebrew, LTR otherwise)
function detectDir(text) {
    const rtlRegex = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0590-\u05FF]/;
    const trimmed = (text || '').trim();
    return rtlRegex.test(trimmed.charAt(0)) ? 'rtl' : 'ltr';
}

let currentThreadId = null;
let allThreads = [];
let useStreaming = false;

const LS_USER_EMAIL = 'sheep_ui_user_email';
const LS_USE_STREAMING = 'sheep_ui_use_streaming';

const INTRO_MSG = `
    <div class="message assistant-message intro-msg" dir="rtl">
        <div class="message-content">
            <p>مرحباً! أنا مساعدك الذكي. كيف يمكنني مساعدتك اليوم؟</p>
        </div>
    </div>`;

// Initialize on page load
window.addEventListener('DOMContentLoaded', async () => {
    // Restore UI state
    const savedEmail = localStorage.getItem(LS_USER_EMAIL);
    if (savedEmail && userEmailInput) userEmailInput.value = savedEmail;

    const savedStreaming = localStorage.getItem(LS_USE_STREAMING);
    useStreaming = savedStreaming === 'true';
    if (streamToggle) streamToggle.checked = useStreaming;

    if (userEmailInput) {
        userEmailInput.addEventListener('change', () => {
            localStorage.setItem(LS_USER_EMAIL, userEmailInput.value.trim());
        });
    }
    if (streamToggle) {
        streamToggle.addEventListener('change', () => {
            useStreaming = !!streamToggle.checked;
            localStorage.setItem(LS_USE_STREAMING, String(useStreaming));
        });
    }

    await fetchThreads();  // load history first
    startNewThread();      // then show fresh chat
});

//  Sidebar toggle (mobile) 
function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

//  New Chat
function startNewThread() {
    // Generate a fresh thread ID for the new conversation
    currentThreadId = "ui_sess_" + Math.random().toString(36).substring(2, 9);

    // Show the welcome message in the chat window
    chatWindow.innerHTML = INTRO_MSG;
    userInput.focus();

    // Highlight active thread in sidebar (none will match since this one
    // hasn't been sent to the server yet — that's intentional)
    renderThreadList();

    // Close sidebar on mobile
    if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.remove('open');
    }
}

//  DOM helpers 
function appendUserMessage(text) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message user-message';
    msgDiv.setAttribute('dir', detectDir(text));
    msgDiv.innerHTML = `<div class="message-content">${text.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendBotMessage(markdownText) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';
    msgDiv.setAttribute('dir', detectDir(markdownText));
    msgDiv.innerHTML = `<div class="message-content">${marked.parse(markdownText)}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function createBotMessageBubble(initialText = '') {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';
    msgDiv.setAttribute('dir', detectDir(initialText));
    msgDiv.innerHTML = `<div class="message-content">${marked.parse(initialText)}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    return msgDiv;
}

function updateBotMessageBubble(msgDiv, markdownText) {
    const content = msgDiv.querySelector('.message-content');
    msgDiv.setAttribute('dir', detectDir(markdownText));
    if (content) content.innerHTML = marked.parse(markdownText);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendLoader() {
    const loaderDiv = document.createElement('div');
    loaderDiv.className = 'message assistant-message';
    loaderDiv.id = 'loadingIndicator';
    loaderDiv.innerHTML = `
        <div class="message-content loading">
            <span class="loading-text">جاري البحث</span>
            <div class="dot"></div>
            <div class="dot"></div>
            <div class="dot"></div>
        </div>`;
    chatWindow.appendChild(loaderDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function removeLoader() {
    const loader = document.getElementById('loadingIndicator');
    if (loader) loader.remove();
}

// Auto-resize textarea 
userInput.addEventListener('input', function () {
    this.style.height = '48px';
    this.style.height = Math.min(this.scrollHeight, 150) + 'px';
});

// Submit on Enter (without Shift)
userInput.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
    }
});

sendBtn.addEventListener('click', sendMessage);

//  Send Message 
async function sendMessage() {
    const text = userInput.value.trim();
    if (!text) return;

    const userEmail = (userEmailInput?.value || '').trim();
    if (!userEmail) {
        appendBotMessage("⚠️ **Error**: Please enter your email first.");
        userEmailInput?.focus();
        return;
    }

    userInput.value = '';
    userInput.style.height = '48px';
    sendBtn.disabled = true;
    appendUserMessage(text);
    appendLoader();

    try {
        if (!useStreaming) {
            const response = await fetch('/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ message: text, thread_id: currentThreadId, user_email: userEmail })
            });

            const json = await response.json();
            removeLoader();

            if (response.ok && json.status === 'success') {
                appendBotMessage(json.data.assistant_message || '');
                await fetchThreads();
            } else {
                appendBotMessage("⚠️ **Error**: Something went wrong communicating with the server.");
            }
            return;
        }

        // Streaming mode (SSE)
        const response = await fetch('/chat/stream', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: text, thread_id: currentThreadId, user_email: userEmail })
        });

        removeLoader();

        if (!response.ok || !response.body) {
            appendBotMessage("⚠️ **Error**: Failed to start streaming.");
            return;
        }

        const bubble = createBotMessageBubble('');
        let fullText = '';

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let rafScheduled = false;

        const scheduleRender = () => {
            if (rafScheduled) return;
            rafScheduled = true;
            requestAnimationFrame(() => {
                rafScheduled = false;
                updateBotMessageBubble(bubble, fullText);
            });
        };

        while (true) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });

            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';

            for (const frame of frames) {
                const lines = frame.split('\n').map(l => l.trimEnd());
                let eventName = 'message';
                let dataLine = '';

                for (const line of lines) {
                    if (line.startsWith('event:')) eventName = line.slice(6).trim();
                    if (line.startsWith('data:')) dataLine += line.slice(5).trim();
                }

                if (!dataLine) continue;

                let payload = null;
                try { payload = JSON.parse(dataLine); } catch { payload = { raw: dataLine }; }

                if (eventName === 'delta') {
                    const delta = payload?.delta || '';
                    if (delta) {
                        fullText += delta;
                        scheduleRender();
                    }
                } else if (eventName === 'done') {
                    const assistantMessage = payload?.assistant_message;
                    if (typeof assistantMessage === 'string' && assistantMessage.length >= fullText.length) {
                        fullText = assistantMessage;
                        scheduleRender();
                    }
                    await fetchThreads();
                    try { await reader.cancel(); } catch {}
                    return;
                } else if (eventName === 'error') {
                    const msg = payload?.message || 'Streaming error';
                    updateBotMessageBubble(bubble, `⚠️ **Error**: ${msg}`);
                    try { await reader.cancel(); } catch {}
                    return;
                }
            }
        }

        // Stream ended without done
        if (!fullText) updateBotMessageBubble(bubble, "⚠️ **Error**: Stream ended unexpectedly.");
    } catch (e) {
        removeLoader();
        console.error(e);
        appendBotMessage("⚠️ **Error**: Failed to connect to server.");
    } finally {
        sendBtn.disabled = false;
        userInput.focus();
    }
}

//  Conversation History 

async function fetchThreads() {
    try {
        const res = await fetch('/api/history');
        const json = await res.json();
        if (json.status === 'success') {
            // Newest first — the backend returns in insertion order
            allThreads = json.data.slice().reverse();
            renderThreadList();
        }
    } catch (e) {
        console.error("Failed to fetch history:", e);
    }
}

function renderThreadList() {
    const list = document.getElementById('threadList');
    list.innerHTML = '';

    if (allThreads.length === 0) {
        const empty = document.createElement('li');
        empty.className = 'thread-empty';
        empty.textContent = 'لا توجد محادثات سابقة';
        list.appendChild(empty);
        return;
    }

    allThreads.forEach(thread => {
        const li = document.createElement('li');
        li.className = 'thread-item';
        if (thread.id === currentThreadId) {
            li.classList.add('active');
        }
        li.textContent = thread.preview;
        li.title = thread.preview;
        li.onclick = () => switchThread(thread.id);
        list.appendChild(li);
    });
}

async function switchThread(threadId) {
    if (threadId === currentThreadId) return;

    try {
        const res = await fetch(`/api/history/${threadId}`);
        const json = await res.json();

        if (json.status === 'success') {
            currentThreadId = threadId;
            renderThreadList();
            chatWindow.innerHTML = '';

            if (json.data.length === 0) {
                chatWindow.innerHTML = INTRO_MSG;
            } else {
                json.data.forEach(msg => {
                    if (msg.role === 'user') appendUserMessage(msg.content);
                    else appendBotMessage(msg.content);
                });
            }

            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
            }
        }
    } catch (e) {
        console.error("Failed to switch thread:", e);
    }
}
