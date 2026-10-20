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
