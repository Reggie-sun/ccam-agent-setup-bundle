#!/usr/bin/node
// MCP bridge: Chrome profiles are never selected implicitly.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawn, spawnSync} from 'node:child_process';
import {createInterface} from 'node:readline';
import {randomUUID} from 'node:crypto';

const base = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const manifest = JSON.parse(fs.readFileSync(path.join(base, 'config/manifest.json'), 'utf8'));
const mcpBin = path.join(base, 'runtime/node_modules/chrome-devtools-mcp/build/src/bin/chrome-devtools-mcp.js');
const authKeys = JSON.parse(fs.readFileSync(path.join(base, 'config/auth-keys.json'), 'utf8'));
const helper = path.join(base, 'code/chrome-browsers.py');
const filesystemRoots = [process.cwd(), ...JSON.parse(process.env.CLAUDE_WORKSPACES || '[]')];

function backendArgs(endpoint) {
  return [mcpBin, '--wsEndpoint=' + (endpoint || 'ws://127.0.0.1:1/devtools/browser/selection-required'),
    '--no-usage-statistics', '--no-performance-crux', ...filesystemRoots.map(root => '--filesystem-root=' + root)];
}

function spawnBackend(endpoint) {
  const env = {...process.env};
  for (const key of authKeys) delete env[key];
  return spawn(manifest.node, backendArgs(endpoint), {stdio: ['pipe', 'pipe', 'inherit'], env});
}

function reply(id, result) {
  process.stdout.write(JSON.stringify({jsonrpc: '2.0', id, result}) + '\n');
}

function errorReply(id, code, message) {
  process.stdout.write(JSON.stringify({jsonrpc: '2.0', id: id ?? null,
    error: {code, message}}) + '\n');
}

function toolError(id, message) {
  reply(id, {content: [{type: 'text', text: message}], isError: true});
}

function helperRequest(operation, profileId) {
  const env = {...process.env};
  for (const key of authKeys) delete env[key];
  const args = [helper, operation];
  if (profileId) args.push(profileId);
  const result = spawnSync('/usr/bin/python3', args, {encoding: 'utf8', env, timeout: 30000, maxBuffer: 1024 * 1024});
  let data;
  try { data = JSON.parse(result.stdout); }
  catch { throw new Error('浏览器管理器没有返回有效结果。'); }
  if (result.error || result.status !== 0) throw new Error(data.error || '浏览器操作失败。');
  return data;
}

function browserEndpoint(value) {
  try {
    const url = new URL(value);
    return url.protocol === 'ws:' && url.hostname === '127.0.0.1' && Number(url.port) > 0 &&
      Number(url.port) <= 65535 && /^\/devtools\/browser\/[A-Za-z0-9_-]+$/.test(url.pathname) &&
      !url.username && !url.password && !url.search && !url.hash;
  } catch { return false; }
}

const managementTools = [
  {name: 'browser_profiles', description: 'List the registered Chrome profiles. Select the intended browser explicitly with browser_select before using any browser automation tool; no profile is selected by default.', inputSchema: {type: 'object', properties: {}, additionalProperties: false}},
  {name: 'browser_select', description: 'Select exactly the registered Chrome profile requested by the user. Browser automation remains disabled until this succeeds. Changing profiles closes this MCP connection to the previous browser; it does not close that Chrome window.', inputSchema: {type: 'object', properties: {profile_id: {type: 'string', description: 'The profile id returned by browser_profiles.'}}, required: ['profile_id'], additionalProperties: false}},
];

let backend;
let selected = null;
let initialization;
let cachedTools;
const pending = new Map();
const serialId = 'router-' + randomUUID();
let nextId = 0;

function failBackend(child, reason) {
  for (const [key, entry] of pending) {
    if (entry.child === child) {
      pending.delete(key);
      entry.reject(reason);
    }
  }
  if (backend === child) {
    backend = undefined;
    selected = null;
  }
}

function request(child, message, timeout = 30000) {
  if (!child || child.exitCode !== null) return Promise.reject(new Error('浏览器连接不可用，请重新选择浏览器。'));
  const key = JSON.stringify(message.id);
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      pending.delete(key);
      reject(new Error('浏览器连接超时，请重新选择浏览器。'));
    }, timeout);
    pending.set(key, {child, resolve: value => {clearTimeout(timer); resolve(value);}, reject: error => {clearTimeout(timer); reject(error);}});
    child.stdin.write(JSON.stringify(message) + '\n', error => {
      if (error && pending.has(key)) {
        pending.delete(key);
        clearTimeout(timer);
        reject(error);
      }
    });
  });
}

function watch(child) {
  let buffer = '';
  child.stdout.setEncoding('utf8');
  child.stdout.on('data', chunk => {
    buffer += chunk;
    let newline;
    while ((newline = buffer.indexOf('\n')) >= 0) {
      const line = buffer.slice(0, newline);
      buffer = buffer.slice(newline + 1);
      let message;
      try { message = JSON.parse(line); } catch { continue; }
      if (message.id !== undefined && message.id !== null) {
        const key = JSON.stringify(message.id);
        const item = pending.get(key);
        if (item?.child === child) {
          pending.delete(key);
          item.resolve(message);
        }
      } else if (backend === child) {
        process.stdout.write(JSON.stringify(message) + '\n');
      }
    }
  });
  child.on('error', error => failBackend(child, error));
  child.on('exit', () => failBackend(child, new Error('浏览器连接已关闭，请重新选择浏览器。')));
}

async function closeBackend(child) {
  if (!child || child.exitCode !== null) return;
  child.stdin.end();
  const waitForExit = (milliseconds) => new Promise(resolve => {
    const timer = setTimeout(() => resolve(false), milliseconds);
    child.once('exit', () => {clearTimeout(timer); resolve(true);});
  });
  if (await waitForExit(700)) return;
  child.kill('SIGTERM');
  if (await waitForExit(500)) return;
  child.kill('SIGKILL');
  await waitForExit(500);
}

async function prepareBackend(endpoint, params = initialization) {
  const child = spawnBackend(endpoint);
  watch(child);
  const id = serialId + '-' + (++nextId);
  const response = await request(child, {jsonrpc: '2.0', id, method: 'initialize', params});
  if (response.error) {
    await closeBackend(child);
    throw new Error('无法连接到所选 Chrome。');
  }
  child.stdin.write(JSON.stringify({jsonrpc: '2.0', method: 'notifications/initialized'}) + '\n');
  return {child, response};
}

function registeredBrowserTools(result) {
  if (Array.isArray(result.tools)) result.tools = [...result.tools, ...managementTools];
  return result;
}

async function selectBrowser(message) {
  const id = message.params?.arguments?.profile_id;
  const previous = backend;
  backend = undefined;
  selected = null;
  await closeBackend(previous);
  if (typeof id !== 'string' || !/^cp-[a-f0-9]{12}$/.test(id)) {
    toolError(message.id, '请从 browser_profiles 选择一个有效的 Chrome Profile。');
    return;
  }
  try {
    const result = helperRequest('select', id);
    if (result.profile_id !== id || !browserEndpoint(result.wsEndpoint)) throw new Error('浏览器管理器返回的连接无效，未切换浏览器。');
    const next = await prepareBackend(result.wsEndpoint);
    backend = next.child;
    selected = {id, name: String(result.name || id)};
    reply(message.id, {content: [{type: 'text', text: `当前自动化浏览器：${selected.name} (${id})。` }],
      structuredContent: {profile_id: id, name: selected.name}});
  } catch (error) {
    selected = null;
    toolError(message.id, error instanceof Error ? error.message : '浏览器连接失败，请重新选择。');
  }
}

async function handle(message) {
  if (!message || typeof message !== 'object' || message.jsonrpc !== '2.0') {
    errorReply(message?.id, -32600, 'Invalid Request');
    return;
  }
  if (message.method === 'initialize') {
    if (initialization) {
      errorReply(message.id, -32600, 'Already initialized');
      return;
    }
    initialization = message.params || {};
    const prepared = await prepareBackend(undefined, initialization);
    backend = prepared.child;
    const result = prepared.response;
    result.id = message.id;
    if (result.result) result.result.instructions = 'Use browser_profiles to show available Chrome profiles. Ask the user which browser to automate, call browser_select with that profile id, then use browser automation tools. Never choose a browser implicitly.';
    process.stdout.write(JSON.stringify(result) + '\n');
    return;
  }
  if (!initialization) {
    errorReply(message.id, -32002, 'Initialize this browser router first.');
    return;
  }
  if (message.method === 'notifications/initialized') {
    backend.stdin.write(JSON.stringify(message) + '\n');
    return;
  }
  if (message.method === 'tools/list') {
    if (!backend && cachedTools) { reply(message.id, {tools: cachedTools}); return; }
    if (!backend) {
      const prepared = await prepareBackend(undefined);
      backend = prepared.child;
    }
    const result = await request(backend, message);
    if (result.result) {
      registeredBrowserTools(result.result);
      cachedTools = result.result.tools;
    }
    process.stdout.write(JSON.stringify(result) + '\n');
    return;
  }
  if (message.method === 'tools/call' && message.params?.name === 'browser_profiles') {
    try { reply(message.id, {content: [{type: 'text', text: JSON.stringify(helperRequest('list').profiles, null, 2)}]}); }
    catch (error) { toolError(message.id, error.message); }
    return;
  }
  if (message.method === 'tools/call' && message.params?.name === 'browser_select') {
    await selectBrowser(message);
    return;
  }
  if (message.method === 'tools/call') {
    if (!selected || !backend) {
      toolError(message.id, '尚未选择自动化浏览器。先调用 browser_profiles，再按用户要求调用 browser_select。');
      return;
    }
    const result = await request(backend, message);
    process.stdout.write(JSON.stringify(result) + '\n');
    return;
  }
  if (!backend) {
    errorReply(message.id, -32002, 'Browser connection is unavailable; select a browser again.');
    return;
  }
  const result = await request(backend, message);
  if (result.id !== undefined) process.stdout.write(JSON.stringify(result) + '\n');
}

const input = createInterface({input: process.stdin});
let queue = Promise.resolve();
for await (const line of input) {
  let message;
  try { message = JSON.parse(line); }
  catch { errorReply(null, -32700, 'Parse error'); continue; }
  queue = queue.then(() => handle(message)).catch(error => {
    if (message.id !== undefined) errorReply(message.id, -32603, error instanceof Error ? error.message : 'Browser router failed.');
  });
}
await queue;
await closeBackend(backend);
