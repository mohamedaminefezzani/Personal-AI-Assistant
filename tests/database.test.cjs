const { test } = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const { PGlite } = require('@electric-sql/pglite');

const root = path.resolve(__dirname, '..');
const localPython = path.join(root, 'venv/Scripts/python.exe');
const python = process.env.PYTHON || (fs.existsSync(localPython) ? localPython : 'python3');
const sql = JSON.parse(execFileSync(python, ['-B', path.join(__dirname, 'export_sql.py')], {encoding: 'utf8'}));
function statement(name, index = 0) {
    let number = 0;
    return sql[name][index].replace(/%s/g, () => '$' + (++number));
}

test('production migrations, job leases, ownership namespaces and archive persistence', async () => {
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'assistant-db-test-'));
    let db = new PGlite(directory);
    const user = '00000000-0000-0000-0000-000000000001';
    try {
        for (const name of ['init_db', 'init_task_runs']) {
            for (const query of sql[name]) await db.exec(query);
        }
        // Startup migrations must also work on an already initialised database.
        for (const name of ['init_db', 'init_task_runs']) {
            for (const query of sql[name]) await db.exec(query);
        }
        await db.query('INSERT INTO users(id, username, hashed_password) VALUES ($1, $2, $3)', [user, 'alice', 'hash']);
        await db.query('INSERT INTO conversations(thread_id, user_id) VALUES ($1, $2)', ['thread', user]);
        const input = {message: 'hello'};
        await db.query(statement('enqueue'), ['job', 'thread', user, input]);
        await assert.rejects(db.query(statement('enqueue'), ['duplicate', 'thread', user, input]), {code: '23505'});
        let result = await db.query(statement('claim'), ['worker-one']);
        assert.equal(result.rows[0].task_id, 'job');
        assert.equal((await db.query(statement('claim'), ['worker-two'])).rows.length, 0);
        await db.query(statement('heartbeat'), ['job', 'worker-one']);
        await db.exec("UPDATE task_runs SET lease_expires_at = NOW() - INTERVAL '1 second'");
        await db.exec(statement('recover_stale'));
        await db.query(statement('finish'), ['done', 'late result', null, 'job', 'worker-one']);
        result = await db.query('SELECT status, input_payload FROM task_runs WHERE task_id = $1', ['job']);
        assert.equal(result.rows[0].status, 'error');
        assert.equal(result.rows[0].input_payload, null);
        await db.query(statement('enqueue'), ['next', 'thread', user, input]);
        await db.query(statement('claim'), ['worker-two']);
        await db.query(statement('finish'), ['done', 'answer', null, 'next', 'worker-two']);

        const payload = {type: 'human', data: {content: 'first message', id: 'm1'}};
        await db.query(statement('archive_messages'), ['thread', 'm1', payload]);
        await db.query(statement('archive_messages'), ['thread', 'm1', payload]);
        assert.equal((await db.query(statement('load_messages'), ['thread'])).rows.length, 1);
        await db.query(statement('enqueue'), ['survives-restart', 'thread', user, input]);
        await db.close();
        db = new PGlite(directory);
        result = await db.query(statement('claim'), ['after-restart']);
        assert.equal(result.rows[0].task_id, 'survives-restart');
        assert.equal(result.rows[0].input_payload.message, 'hello');
        assert.equal((await db.query(statement('load_messages'), ['thread'])).rows[0].payload.data.content, 'first message');
        const approvals = [{interrupt_id: 'interrupt-1', approval_id: 'proposal-1', tool: 'write_file_tool'}];
        await db.query(statement('pause_task'), [approvals, 'survives-restart', 'after-restart']);
        await db.query(statement('emit'), ['survives-restart', 'step-1', {kind: 'delegation_started'}]);
        await db.query(statement('emit'), ['survives-restart', 'step-1', {kind: 'delegation_started'}]);
        await assert.rejects(db.query(statement('enqueue'), ['during-approval', 'thread', user, input]), {code: '23505'});
        await db.close();
        db = new PGlite(directory);
        await db.exec(statement('recover_stale'));
        assert.equal((await db.query(statement('claim'), ['other-worker'])).rows.length, 0);
        const pending = await db.query(statement('decide'), ['survives-restart', user]);
        assert.equal(pending.rows[0].status, 'waiting_approval');
        assert.deepEqual(pending.rows[0].pending_approvals, approvals);
        assert.equal((await db.query(statement('decide'), ['survives-restart', '00000000-0000-0000-0000-000000000002'])).rows.length, 0);
        assert.equal((await db.query(statement('read_events'), ['survives-restart', 0])).rows.length, 1);
        await db.query(statement('decide', 1), [{resume: {'interrupt-1': {decision: 'approve', approval_id: 'proposal-1'}}}, 'survives-restart']);
        const resumed = (await db.query(statement('claim'), ['resumed-worker'])).rows[0];
        assert.equal(resumed.input_payload.message, 'hello');
        assert.equal(resumed.input_payload.resume['interrupt-1'].decision, 'approve');
        await db.query('DELETE FROM conversations WHERE thread_id = $1', ['thread']);
        assert.equal((await db.query(statement('load_messages'), ['thread'])).rows.length, 0);
    } finally {
        await db.close();
        assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
        fs.rmSync(directory, {recursive: true, force: true});
    }
});
