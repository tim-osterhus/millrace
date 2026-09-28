// Private observation only. Stock tools, spawn arguments and scheduling are unchanged.
import cp from 'node:child_process';
import { syncBuiltinESMExports } from 'node:module';
import { writeSync, readFileSync, closeSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const parameters = new URL(import.meta.url).searchParams;
if ([...parameters.keys()].sort().join(',') !== 'bytes,fd,line,profile,records') {
  throw new Error('observer arguments');
}
const fd = Number(parameters.get('fd'));
const maximumRecords = Number(parameters.get('records'));
const maximumBytes = Number(parameters.get('bytes'));
const maximumLine = Number(parameters.get('line'));
const profile = parameters.get('profile');
if (![fd, maximumRecords, maximumBytes, maximumLine].every(Number.isSafeInteger)
    || fd < 3 || Math.min(maximumRecords, maximumBytes, maximumLine) < 1
    || !/^[a-f0-9]{64}$/.test(profile)) throw new Error('observer arguments');
const sha = value => createHash('sha256').update(value).digest('hex');
const environmentHash = env => sha(JSON.stringify(Object.entries(env).sort(([a], [b]) => a < b ? -1 : a > b ? 1 : 0)));
let sequence = 0, bytes = 0, rpcCount = 0, spawnCount = 0;
let failed = false;
const active = new Map();
const declared = new Set();
const spawned = new Set();
function emit(kind, payload) {
  if (failed) return;
  const data = Buffer.from(JSON.stringify({ v: 'pi-group-observer.v1', seq: sequence, kind, payload }) + '\n');
  if (sequence >= maximumRecords || data.length > maximumLine || bytes + data.length > maximumBytes) {
    failed = true;
    process.exitCode = 1;
    throw new Error('observer bound');
  }
  try {
    for (let offset = 0; offset < data.length;) {
      try { offset += writeSync(fd, data, offset); }
      catch (error) { if (error.code !== 'EINTR') throw error; }
    }
  } catch (error) {
    failed = true;
    process.exitCode = 1;
    throw error;
  }
  sequence++;
  bytes += data.length;
}
const fault = reason => emit('fault', { reason });
function stack() {
  const previous = Error.prepareStackTrace;
  try {
    Error.prepareStackTrace = (_, frames) => frames;
    return new Error().stack.slice(1, 10).map(frame => ({
      file: frame.getFileName(), line: frame.getLineNumber(), column: frame.getColumnNumber(),
    }));
  } finally { Error.prepareStackTrace = previous; }
}
const originalKill = process.kill;
process.kill = function(pid, signal) {
  emit('control', { pid, signal: typeof signal === 'string' ? signal : String(signal ?? 'SIGTERM'), frames: stack() });
  return Reflect.apply(originalKill, this, arguments);
};
const originalWrite = process.stdout.write;
process.stdout.write = function(chunk, encodingOrCallback, callback) {
  const originalArguments = arguments;
  const cb = typeof encodingOrCallback === 'function' ? encodingOrCallback : callback;
  if (typeof cb !== 'function') {
    fault('stdout without callback');
    return Reflect.apply(originalWrite, this, originalArguments);
  }
  const encoding = typeof encodingOrCallback === 'string' ? encodingOrCallback : undefined;
  const text = Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk);
  // Pinned output-guard writes exactly one LF-terminated record, or empty flush.
  let event = null;
  if (text) {
    if (!text.endsWith('\n') || text.slice(0, -1).includes('\n') || Buffer.byteLength(text) > maximumLine) {
      fault('stdout framing');
    } else {
      try { event = JSON.parse(text); }
      catch { fault('stdout JSON'); }
    }
  }
  function done(error) {
    if (error) fault('stdout callback failed');
    else if (text) {
      emit('rpc', { index: rpcCount++, sha256: sha(text) });
      if (event?.type === 'tool_execution_start' && event.toolName === 'bash') {
        const args = event.args;
        if (typeof event.toolCallId !== 'string' || declared.has(event.toolCallId)
            || !args || typeof args.command !== 'string'
            || Object.keys(args).some(key => !['command', 'timeout'].includes(key))) {
          fault('bash start shape');
        } else {
          declared.add(event.toolCallId);
          active.set(event.toolCallId, args);
          emit('start', { call: event.toolCallId, args_sha256: sha(args.command), timeout: args.timeout ?? null });
        }
      } else if (event?.type === 'tool_execution_end' && event.toolName === 'bash') {
        if (!active.delete(event.toolCallId)) fault('bash end identity');
        emit('end', { call: event.toolCallId });
      }
    }
    return Reflect.apply(cb, this, arguments);
  }
  try { return originalWrite.call(this, chunk, encoding, done); }
  catch (error) { fault('stdout write failed'); throw error; }
};
const originalSpawn = cp.spawn;
cp.spawn = function(shell, args, options) {
  const command = Array.isArray(args) && typeof args.at(-1) === 'string' ? args.at(-1) : '';
  const matches = [...active].filter(([, value]) => value.command === command);
  const call = matches.length === 1 ? matches[0][0] : null;
  const child = Reflect.apply(originalSpawn, this, arguments);
  if (!child.pid) {
    fault('spawn without PID');
    return child;
  }
  const pid = child.pid;
  spawnCount++;
  if (!call || spawned.has(call)) fault('ambiguous or duplicate spawn');
  if (call) spawned.add(call);
  if (!options || Object.keys(options).sort().join(',') !== 'cwd,detached,env,stdio,windowsHide') fault('spawn options');
  emit('spawn', { call, pid, command_sha256: sha(command), cwd: options?.cwd ?? '',
    env_sha256: environmentHash(options?.env ?? {}), shell, args: Array.isArray(args) ? args.slice(0, -1) : [],
    detached: options?.detached === true, stdio: options?.stdio ?? [], windows_hide: options?.windowsHide === true });
  for (const name of ['stdout', 'stderr']) {
    const stream = child[name];
    if (!stream) { fault('missing child stream'); continue; }
    let ended = false;
    stream.once('end', () => { ended = true; emit('stream_end', { pid, name }); });
    const originalDestroy = stream.destroy;
    stream.destroy = function() {
      emit('stream_destroy', { pid, name, ended });
      return Reflect.apply(originalDestroy, this, arguments);
    };
  }
  child.once('error', error => emit('spawn_error', { pid, code: error.code ?? 'unknown' }));
  child.once('exit', (code, signal) => {
    let group = 'present';
    try { originalKill.call(process, -pid, 0); }
    catch (error) { group = error.code === 'ESRCH' ? 'absent' : 'unknown'; }
    emit('root_exit', { pid, code, signal, group });
  });
  child.once('close', (code, signal) => emit('child_close', { pid, code, signal }));
  return child;
};
syncBuiltinESMExports();
emit('hello', { pid: process.pid, cwd: process.cwd(),
  bridge_sha256: sha(readFileSync(fileURLToPath(import.meta.url))), profile_sha256: profile,
  env_sha256: environmentHash(process.env), platform: process.platform, node: process.version });
process.once('exit', code => {
  if (!failed) emit('bye', { code, rpc_count: rpcCount, spawn_count: spawnCount });
  closeSync(fd);
});
