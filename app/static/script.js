const chatWindow = document.getElementById('chatWindow');
const userInput = document.getElementById('userInput');
const sendBtn = document.getElementById('sendBtn');
const userIdInput = document.getElementById('userId');
const streamToggle = document.getElementById('streamToggle');

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const LS_USER_ID = 'sheep_ui_user_id';
const LS_USE_STREAMING = 'sheep_ui_use_streaming';
const MAX_STREAM_RECONNECTS = 3;
const INTRO_MSG = `
    <div class="message assistant-message intro-msg" dir="auto">
        <div class="message-content">
            <p>Hello. I am your livestock AI assistant. Enter a backend user ID to load history, then start chatting.</p>
        </div>
    </div>`;

let currentThreadId = null;
let allThreads = [];
let useStreaming = false;
let threadListNotice = 'Enter a backend user ID to load conversations.';

if (window.marked?.setOptions) {
    window.marked.setOptions({
        breaks: true,
        gfm: true,
        headerIds: false
    });
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

function renderMarkdown(text) {
    const safeText = escapeHtml(text);
    if (window.marked?.parse) {
        return window.marked.parse(safeText);
    }

    return `<p>${safeText.replace(/\n/g, '<br>')}</p>`;
}

function detectDir(text) {
    const rtlRegex = /[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF\u0590-\u05FF]/;
    const trimmed = String(text ?? '').trim();
    return rtlRegex.test(trimmed.charAt(0)) ? 'rtl' : 'ltr';
}

function getUserId() {
    return (userIdInput?.value || '').trim();
}

function isValidUserId(userId = getUserId()) {
    return UUID_PATTERN.test(userId);
}

function generateThreadId() {
    if (window.crypto?.randomUUID) {
        return window.crypto.randomUUID();
    }

    return `ui-thread-${Math.random().toString(36).slice(2, 11)}`;
}

function sleep(ms) {
    return new Promise(resolve => window.setTimeout(resolve, ms));
}

function getErrorMessage(payload, fallback) {
    if (payload?.data?.error) return String(payload.data.error);
    if (payload?.data?.details) return String(payload.data.details);
    if (payload?.message) return String(payload.message);
    if (payload?.raw) return String(payload.raw);
    return fallback;
}

function appendUserMessage(text) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message user-message';
    msgDiv.setAttribute('dir', detectDir(text));
    msgDiv.innerHTML = `<div class="message-content">${escapeHtml(text).replace(/\n/g, '<br>')}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendBotMessage(markdownText) {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';
    msgDiv.setAttribute('dir', detectDir(markdownText));
    msgDiv.innerHTML = `<div class="message-content">${renderMarkdown(markdownText)}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function createBotMessageBubble(initialText = '') {
    const msgDiv = document.createElement('div');
    msgDiv.className = 'message assistant-message';
    msgDiv.setAttribute('dir', detectDir(initialText));
    msgDiv.innerHTML = `<div class="message-content">${renderMarkdown(initialText)}</div>`;
    chatWindow.appendChild(msgDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
    return msgDiv;
}

function updateBotMessageBubble(msgDiv, markdownText) {
    const content = msgDiv.querySelector('.message-content');
    msgDiv.setAttribute('dir', detectDir(markdownText));
    if (content) {
        content.innerHTML = renderMarkdown(markdownText);
    }
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function appendLoader() {
    const loaderDiv = document.createElement('div');
    loaderDiv.className = 'message assistant-message';
    loaderDiv.id = 'loadingIndicator';
    loaderDiv.innerHTML = `
        <div class="message-content loading">
            <span class="loading-text">Thinking</span>
            <div class="dot"></div>
            <div class="dot"></div>
            <div class="dot"></div>
        </div>`;
    chatWindow.appendChild(loaderDiv);
    chatWindow.scrollTop = chatWindow.scrollHeight;
}

function removeLoader() {
    const loader = document.getElementById('loadingIndicator');
    if (loader) {
        loader.remove();
    }
}

function renderThreadList() {
    const list = document.getElementById('threadList');
    list.innerHTML = '';

    if (!allThreads.length) {
        const empty = document.createElement('li');
        empty.className = 'thread-empty';
        empty.textContent = threadListNotice;
        list.appendChild(empty);
        return;
    }

    allThreads.forEach(thread => {
        const li = document.createElement('li');
        const label = String(thread.title || thread.id || 'Untitled conversation');

        li.className = 'thread-item';
        if (thread.id === currentThreadId) {
            li.classList.add('active');
        }

        li.textContent = label;
        li.title = label;
        li.onclick = () => switchThread(thread.id);
        list.appendChild(li);
    });
}

function toggleSidebar() {
    document.getElementById('sidebar').classList.toggle('open');
}

function startNewThread() {
    currentThreadId = generateThreadId();
    chatWindow.innerHTML = INTRO_MSG;
    renderThreadList();
    userInput.focus();

    if (window.innerWidth <= 768) {
        document.getElementById('sidebar').classList.remove('open');
    }
}

async function refreshForCurrentUser() {
    const userId = getUserId();
    allThreads = [];

    if (!userId) {
        threadListNotice = 'Enter a backend user ID to load conversations.';
        startNewThread();
        return;
    }

    if (!isValidUserId(userId)) {
        threadListNotice = 'Enter a valid UUID user ID to load conversations.';
        startNewThread();
        return;
    }

    threadListNotice = 'Loading conversations...';
    startNewThread();
    await fetchThreads();
}

async function fetchThreads() {
    const userId = getUserId();

    if (!userId) {
        allThreads = [];
        threadListNotice = 'Enter a backend user ID to load conversations.';
        renderThreadList();
        return;
    }

    if (!isValidUserId(userId)) {
        allThreads = [];
        threadListNotice = 'Enter a valid UUID user ID to load conversations.';
        renderThreadList();
        return;
    }

    try {
        const res = await fetch(`/api/v1/chat/threads?user_id=${encodeURIComponent(userId)}`);
        const json = await res.json().catch(() => null);

        if (!res.ok || json?.status !== true) {
            allThreads = [];
            threadListNotice = getErrorMessage(json, 'Failed to load conversations.');
            renderThreadList();
            return;
        }

        allThreads = Array.isArray(json.data) ? json.data : [];
        threadListNotice = allThreads.length ? '' : 'No previous conversations.';
        renderThreadList();
    } catch (error) {
        allThreads = [];
        threadListNotice = 'Failed to load conversations.';
        renderThreadList();
        console.error('Failed to fetch history:', error);
    }
}

async function pollTaskResult(taskId, timeoutMs = 120000, intervalMs = 1000) {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
        await sleep(intervalMs);

        const response = await fetch(`/api/v1/chat/tasks/${encodeURIComponent(taskId)}`);
        const payload = await response.json().catch(() => null);
        const state = payload?.data?.state;

        if (state === 'SUCCESS') {
            return payload?.data?.result?.assistant_message || '';
        }

        if (state === 'FAILURE') {
            throw new Error(getErrorMessage(payload, 'The queued task failed.'));
        }

        if (!response.ok) {
            throw new Error(getErrorMessage(payload, 'Failed to fetch task status.'));
        }
    }

    throw new Error('Timed out waiting for the queued response.');
}

async function sendOneShotMessage(text, userId) {
    const response = await fetch('/api/v1/chat/one-shot', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: text,
            thread_id: currentThreadId,
            user_id: userId
        })
    });

    const payload = await response.json().catch(() => null);

    if (!response.ok || payload?.status !== 'accepted') {
        throw new Error(getErrorMessage(payload, 'Failed to queue the message.'));
    }

    const taskId = payload?.data?.task_id;
    if (!taskId) {
        throw new Error('The server did not return a task ID.');
    }

    return pollTaskResult(taskId);
}

function buildEventsUrl(eventsUrl, lastEventId) {
    if (!lastEventId || lastEventId === '0-0') {
        return eventsUrl;
    }

    const separator = eventsUrl.includes('?') ? '&' : '?';
    return `${eventsUrl}${separator}last_event_id=${encodeURIComponent(lastEventId)}`;
}

function parseSseFrame(frame) {
    if (!frame || frame.startsWith(':')) {
        return null;
    }

    const lines = frame.split('\n').map(line => line.trimEnd());
    const dataLines = [];
    let eventId = '';
    let eventName = 'message';

    for (const line of lines) {
        if (line.startsWith('id:')) {
            eventId = line.slice(3).trim();
        } else if (line.startsWith('event:')) {
            eventName = line.slice(6).trim();
        } else if (line.startsWith('data:')) {
            dataLines.push(line.slice(5).trimStart());
        }
    }

    if (!dataLines.length) {
        return { eventId, eventName, payload: null };
    }

    const raw = dataLines.join('\n');

    try {
        return {
            eventId,
            eventName,
            payload: JSON.parse(raw)
        };
    } catch {
        return {
            eventId,
            eventName,
            payload: { raw }
        };
    }
}

async function fetchRunState(statusUrl) {
    if (!statusUrl) {
        return null;
    }

    try {
        const response = await fetch(statusUrl);
        if (response.status === 404) {
            return null;
        }

        const payload = await response.json().catch(() => null);
        if (!response.ok || payload?.status !== 'success') {
            return null;
        }

        return payload?.data?.state || null;
    } catch (error) {
        console.error('Failed to fetch stream status:', error);
        return null;
    }
}

async function consumeAsyncStream(eventsUrl, statusUrl) {
    let bubble = null;
    let fullText = '';
    let lastEventId = '0-0';
    let reconnectCount = 0;

    while (reconnectCount <= MAX_STREAM_RECONNECTS) {
        let shouldReconnect = false;
        const response = await fetch(buildEventsUrl(eventsUrl, lastEventId), {
            headers: { Accept: 'text/event-stream' }
        });

        if (response.status === 204) {
            removeLoader();
            if (bubble && fullText) {
                updateBotMessageBubble(
                    bubble,
                    `${fullText}\n\n**Warning**: The stream finished without a final completion event.`
                );
            }
            return;
        }

        if (!response.ok || !response.body) {
            const payload = await response.json().catch(() => null);
            throw new Error(getErrorMessage(payload, 'Failed to connect to the async event stream.'));
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder('utf-8');
        let buffer = '';
        let rafScheduled = false;

        const ensureBubble = () => {
            if (!bubble) {
                bubble = createBotMessageBubble('');
            }
            return bubble;
        };

        const scheduleRender = () => {
            if (rafScheduled || !bubble) {
                return;
            }

            rafScheduled = true;
            requestAnimationFrame(() => {
                rafScheduled = false;
                updateBotMessageBubble(bubble, fullText);
            });
        };

        while (true) {
            const { value, done } = await reader.read();
            if (done) {
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const frames = buffer.split('\n\n');
            buffer = frames.pop() || '';

            for (const frame of frames) {
                const parsed = parseSseFrame(frame);
                if (!parsed) {
                    continue;
                }

                if (parsed.eventId) {
                    lastEventId = parsed.eventId;
                }

                const payload = parsed.payload || {};

                if (parsed.eventName === 'queued' || parsed.eventName === 'started') {
                    continue;
                }

                if (parsed.eventName === 'delta') {
                    removeLoader();
                    ensureBubble();

                    const delta = String(payload?.delta || '');
                    if (delta) {
                        fullText += delta;
                        scheduleRender();
                    }
                    continue;
                }

                if (parsed.eventName === 'done') {
                    removeLoader();
                    ensureBubble();

                    if (typeof payload?.assistant_message === 'string' && payload.assistant_message.length >= fullText.length) {
                        fullText = payload.assistant_message;
                    }

                    updateBotMessageBubble(bubble, fullText);

                    if (payload?.thread_id) {
                        currentThreadId = payload.thread_id;
                    }

                    try {
                        await reader.cancel();
                    } catch {
                    }

                    await fetchThreads();
                    return;
                }

                if (parsed.eventName === 'cancelled') {
                    removeLoader();
                    ensureBubble();

                    const assistantMessage = String(payload?.assistant_message || fullText || '');
                    updateBotMessageBubble(
                        bubble,
                        assistantMessage
                            ? `${assistantMessage}\n\n**Warning**: The stream was cancelled.`
                            : '**Warning**: The stream was cancelled.'
                    );

                    try {
                        await reader.cancel();
                    } catch {
                    }

                    return;
                }

                if (parsed.eventName === 'timeout') {
                    shouldReconnect = true;

                    try {
                        await reader.cancel();
                    } catch {
                    }

                    break;
                }

                if (parsed.eventName === 'error') {
                    removeLoader();
                    ensureBubble();
                    updateBotMessageBubble(
                        bubble,
                        `**Error**: ${getErrorMessage(payload, 'Streaming failed.')}`
                    );

                    try {
                        await reader.cancel();
                    } catch {
                    }

                    return;
                }
            }

            if (shouldReconnect) {
                break;
            }
        }

        const state = await fetchRunState(statusUrl);
        if (state === 'SUCCESS') {
            removeLoader();
            if (bubble) {
                updateBotMessageBubble(
                    bubble,
                    `${fullText}\n\n**Warning**: The run completed but the final event was not received.`
                );
            }
            return;
        }

        if (state === 'CANCELLED') {
            removeLoader();
            const existingBubble = bubble || createBotMessageBubble('');
            updateBotMessageBubble(
                existingBubble,
                fullText
                    ? `${fullText}\n\n**Warning**: The stream was cancelled.`
                    : '**Warning**: The stream was cancelled.'
            );
            return;
        }

        if (state === 'FAILURE') {
            throw new Error('The async streaming run failed.');
        }

        reconnectCount += 1;
        if (!shouldReconnect && reconnectCount > MAX_STREAM_RECONNECTS) {
            break;
        }

        if (reconnectCount <= MAX_STREAM_RECONNECTS) {
            await sleep(1000 * reconnectCount);
        }
    }

    removeLoader();
    if (bubble && fullText) {
        updateBotMessageBubble(
            bubble,
            `${fullText}\n\n**Warning**: Streaming disconnected before completion.`
        );
        return;
    }

    throw new Error('Streaming disconnected before completion.');
}

async function sendStreamingMessage(text, userId) {
    const startResponse = await fetch('/api/v1/chat/stream/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            message: text,
            thread_id: currentThreadId,
            user_id: userId
        })
    });

    const startPayload = await startResponse.json().catch(() => null);
    if (!startResponse.ok || startPayload?.status !== 'accepted') {
        throw new Error(getErrorMessage(startPayload, 'Failed to start async streaming.'));
    }

    const eventsUrl = startPayload?.data?.events_url;
    const statusUrl = startPayload?.data?.status_url;

    if (!eventsUrl) {
        throw new Error('The server did not return an events URL for the stream.');
    }

    await consumeAsyncStream(eventsUrl, statusUrl);
}

async function sendMessage() {
    const text = userInput.value.trim();
    if (!text) {
        return;
    }

    const userId = getUserId();
    if (!userId) {
        appendBotMessage('**Error**: Enter a backend user ID first.');
        userIdInput?.focus();
        return;
    }

    if (!isValidUserId(userId)) {
        appendBotMessage('**Error**: Enter a valid UUID user ID first.');
        userIdInput?.focus();
        return;
    }

    userInput.value = '';
    userInput.style.height = '48px';
    sendBtn.disabled = true;
    appendUserMessage(text);
    appendLoader();

    try {
        if (useStreaming) {
            await sendStreamingMessage(text, userId);
        } else {
            const assistantMessage = await sendOneShotMessage(text, userId);
            removeLoader();
            appendBotMessage(assistantMessage);
            await fetchThreads();
        }
    } catch (error) {
        removeLoader();
        console.error(error);
        appendBotMessage(`**Error**: ${error?.message || 'Failed to connect to the server.'}`);
    } finally {
        sendBtn.disabled = false;
        userInput.focus();
    }
}

async function switchThread(threadId) {
    if (threadId === currentThreadId) {
        return;
    }

    const userId = getUserId();
    if (!isValidUserId(userId)) {
        appendBotMessage('**Error**: Enter a valid UUID user ID before loading a conversation.');
        userIdInput?.focus();
        return;
    }

    try {
        const res = await fetch(
            `/api/v1/chat/threads/${encodeURIComponent(threadId)}/history?user_id=${encodeURIComponent(userId)}`
        );
        const json = await res.json().catch(() => null);

        if (!res.ok || json?.status !== 'success') {
            throw new Error(getErrorMessage(json, 'Failed to load conversation history.'));
        }

        currentThreadId = threadId;
        renderThreadList();
        chatWindow.innerHTML = '';

        const items = Array.isArray(json.data) ? json.data : [];
        if (!items.length) {
            chatWindow.innerHTML = INTRO_MSG;
        } else {
            items.forEach(message => {
                if (message.role === 'user') {
                    appendUserMessage(message.content);
                } else {
                    appendBotMessage(message.content);
                }
            });
        }

        if (window.innerWidth <= 768) {
            document.getElementById('sidebar').classList.remove('open');
        }
    } catch (error) {
        console.error('Failed to switch thread:', error);
        appendBotMessage(`**Error**: ${error?.message || 'Failed to load conversation history.'}`);
    }
}

window.addEventListener('DOMContentLoaded', async () => {
    const savedUserId = localStorage.getItem(LS_USER_ID);
    if (savedUserId && userIdInput) {
        userIdInput.value = savedUserId;
    }

    const savedStreaming = localStorage.getItem(LS_USE_STREAMING);
    useStreaming = savedStreaming === 'true';
    if (streamToggle) {
        streamToggle.checked = useStreaming;
    }

    if (userIdInput) {
        userIdInput.addEventListener('change', async () => {
            const userId = getUserId();

            if (userId) {
                localStorage.setItem(LS_USER_ID, userId);
            } else {
                localStorage.removeItem(LS_USER_ID);
            }

            await refreshForCurrentUser();
        });
    }

    if (streamToggle) {
        streamToggle.addEventListener('change', () => {
            useStreaming = Boolean(streamToggle.checked);
            localStorage.setItem(LS_USE_STREAMING, String(useStreaming));
        });
    }

    startNewThread();
    if (isValidUserId()) {
        await fetchThreads();
    } else {
        renderThreadList();
    }
});

userInput.addEventListener('input', function () {
    this.style.height = '48px';
    this.style.height = `${Math.min(this.scrollHeight, 150)}px`;
});

userInput.addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        sendMessage();
    }
});

sendBtn.addEventListener('click', sendMessage);
