const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');

const root = path.resolve(__dirname, '..');
function app() {
    const html = fs.readFileSync(path.join(root, 'src/static/index.html'), 'utf8');
    const dom = new JSDOM(html, {runScripts: 'outside-only', url: 'http://localhost/'});
    const window = dom.window;
    window.fetch = async () => ({status: 401, ok: false});
    for (const asset of ['vendor/marked-15.0.12.min.js', 'vendor/purify-3.3.0.min.js',
                         'vendor/jszip-3.10.1.min.js', 'app.js']) {
        window.eval(fs.readFileSync(path.join(root, 'src/static', asset), 'utf8'));
    }
    return dom;
}

test('stream ending without a terminal event reports an interrupted connection', async () => {
    const dom = app();
    await new Promise(setImmediate);
    dom.window.TextDecoder = TextDecoder;
    dom.window.fetch = async () => ({status: 200, ok: true, json: async () => []});
    await dom.window.selectConversation('test-thread', 'Test');
    dom.window.fetch = async () => ({status: 200, ok: true,
        body: {getReader: () => ({read: async () => ({done: true})})}});
    const typing = dom.window.document.createElement('div');
    const answer = dom.window.document.createElement('div');
    await assert.rejects(dom.window.consumeStream('job', 'test-thread', typing, answer),
                         /Connection closed before the result/);
    dom.window.close();
});

test('terminal server error is displayed instead of an empty response', async () => {
    const dom = app();
    await new Promise(setImmediate);
    dom.window.TextDecoder = TextDecoder;
    dom.window.fetch = async () => ({status: 200, ok: true, json: async () => []});
    await dom.window.selectConversation('test-thread', 'Test');
    let sent = false;
    dom.window.fetch = async () => ({status: 200, ok: true, body: {getReader: () => ({read: async () => {
        if (sent) return {done: true};
        sent = true;
        return {done: false, value: Buffer.from('data: {"error":"Conversation summary storage failed","done":true}\n\n')};
    }})}});
    const typing = dom.window.document.createElement('div');
    const answer = dom.window.document.createElement('div');
    await dom.window.consumeStream('job', 'test-thread', typing, answer);
    assert.match(answer.textContent, /summary storage failed/);
    dom.window.close();
});

test('raw model HTML cannot create scripts, event handlers or javascript links', () => {
    const dom = app();
    const output = dom.window.renderMarkdown(
        '<img src=x onerror="alert(1)"><svg onload="alert(2)"></svg>' +
        '<script>alert(3)</script><a href="javascript:alert(4)" onclick="alert(5)">link</a>'
    );
    const element = dom.window.document.createElement('div');
    element.innerHTML = output;
    assert.equal(element.querySelectorAll('script,img,svg,[onerror],[onclick],[onload]').length, 0);
    assert.equal(element.querySelector('a').getAttribute('href'), null);
    dom.window.close();
});

test('HTML code examples remain literal code and copy buttons remain available', () => {
    const dom = app();
    const code = '<img src=x onerror="alert(1)">';
    const element = dom.window.document.createElement('div');
    element.innerHTML = dom.window.renderMarkdown('```html\n' + code + '\n```');
    assert.equal(element.querySelector('img'), null);
    assert.equal(element.querySelector('code').textContent, code);
    assert.ok(element.querySelector('button.copy-btn'));
    dom.window.close();
});

test('missing DOMPurify falls back to escaped readable text', () => {
    const dom = app();
    delete dom.window.DOMPurify;
    const element = dom.window.document.createElement('div');
    element.innerHTML = dom.window.renderMarkdown('<img src=x onerror="alert(1)">\nAnswer');
    assert.equal(element.querySelector('img'), null);
    assert.match(element.textContent, /<img src=x onerror="alert\(1\)">/);
    assert.ok(element.textContent.includes('Answer'));
    dom.window.close();
});

test('ordinary Markdown remains readable and UI actions work without inline handlers', () => {
    const dom = app();
    const output = dom.window.renderMarkdown('**bold** and [safe](https://example.com)');
    assert.match(output, /<strong>bold<\/strong>/);
    assert.match(output, /href="https:\/\/example.com"/);
    assert.equal(dom.window.document.querySelector('[onclick]'), null);
    dom.window.document.getElementById('codeToggle').click();
    assert.ok(dom.window.document.getElementById('codeToggle').classList.contains('active'));
    dom.window.close();
});

test('switching chats clears stale busy state and reconnects queued jobs with empty history', async () => {
    const dom = app();
    await new Promise(setImmediate);
    let queued = false;
    let reconnected = null;
    dom.window.fetch = async url => ({
        ok: true, status: 200,
        json: async () => url.includes('/chat/status/')
            ? {status: queued ? 'queued' : null, task_id: queued ? 'saved-job' : null}
            : [],
    });
    dom.window.consumeStream = async taskId => { reconnected = taskId; };
    dom.window.setThinking(true);
    await dom.window.selectConversation('idle', 'Idle');
    assert.equal(dom.window.document.getElementById('sendButton').disabled, false);
    queued = true;
    await dom.window.selectConversation('queued', 'Queued');
    assert.equal(reconnected, 'saved-job');
    dom.window.close();
});

test('reloaded pending approval shows exact proposal and posts an explicit decision', async () => {
    const dom = app();
    await new Promise(setImmediate);
    dom.window.TextDecoder = TextDecoder;
    let decision = null;
    dom.window.fetch = async (url, options) => {
        if (url.includes('/chat/approval/')) {
            decision = JSON.parse(options.body);
            return {ok: true, status: 200, json: async () => ({status: 'queued'})};
        }
        if (url.includes('/chat/stream/')) {
            let sent = false;
            const event = decision ? {content: 'Completed', done: true} : {
                approvals: [{approval_id: 'proposal', tool: 'write_file_tool',
                args: {filename: 'test.html'}, diff: '+<img src=x onerror=alert(1)>'}], done: true};
            return {ok: true, status: 200, body: {getReader: () => ({read: async () => {
                if (sent) return {done: true};
                sent = true;
                return {done: false, value: Buffer.from('data: ' + JSON.stringify(event) + '\n\n')};
            }})}};
        }
        return {ok: true, status: 200, json: async () => url.includes('/chat/status/')
            ? {status: decision ? 'done' : 'waiting_approval', task_id: 'job'} : []};
    };
    await dom.window.selectConversation('thread', 'Thread');
    const card = dom.window.document.querySelector('.approval-card');
    assert.ok(card);
    assert.equal(card.querySelector('img'), null);
    assert.match(card.querySelector('pre').textContent, /<img/);
    assert.equal(dom.window.document.getElementById('sendButton').disabled, true);
    card.querySelector('button').click();
    await new Promise(setImmediate);
    assert.deepEqual(decision, {approval_id: 'proposal', decision: 'approve'});
    assert.equal(dom.window.document.getElementById('sendButton').disabled, false);
    dom.window.close();
});
