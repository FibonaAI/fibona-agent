import { invokeChromeMethod, forwardChromeEvents, getUserScriptsAccess } from './runtime/chrome-bridge.js';
import { encodeBrowserValue, decodeBrowserValue } from './runtime/browser-wire.js';
const viewPorts = new Map();
let creating, resetting, generation = 0;
const sendToRuntime = (command, value) => chrome.runtime.sendMessage({ fibonaTarget: 'runtime-host', command, value });
async function createRuntimeDocument() {
  if (creating) return await creating;
  creating = (async () => {
    const contexts = await chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT']});
    if (!contexts.length) await chrome.offscreen.createDocument({url:'runtime/offscreen.html', reasons:['IFRAME_SCRIPTING','BLOBS','CLIPBOARD','LOCAL_STORAGE'], justification:'Run the programmable JavaScript iframe independently of views and provide native origin APIs.'});
  })();
  try { await creating; } finally { creating = undefined; }
}
async function ensureRuntime() { if (resetting) await resetting; await createRuntimeDocument(); }
async function resetRuntime(options = {}) {
  if (resetting) return await resetting;
  resetting = (async () => {
    generation++;
    for (const port of viewPorts.values()) { try { port.postMessage({type:'resetting'}); } catch { /* View closed. */ } }
    if (creating) await creating;
    if ((await chrome.runtime.getContexts({contextTypes:['OFFSCREEN_DOCUMENT']})).length) await chrome.offscreen.closeDocument();
    await debuggerUpdates;
    const saved = await chrome.storage.session.get('fibona_debuggers');
    await Promise.all((saved.fibona_debuggers ?? []).map(target => chrome.debugger.detach(target).catch(() => {})));
    await chrome.storage.session.remove('fibona_debuggers');
    await createRuntimeDocument();
    await sendToRuntime('initialize', { auto:false, status:options.status ?? 'Stopped', restore:options.restore ?? true, error:options.error });
    for (const port of viewPorts.values()) { try { port.postMessage({type:'reset'}); } catch { /* View closed. */ } }
  })();
  try { await resetting; } finally { resetting = undefined; }
}

chrome.runtime.onConnect.addListener(port => {
  if (port.name !== 'fibona-view' || port.sender?.id !== chrome.runtime.id || !port.sender.url?.startsWith(chrome.runtime.getURL('index.html'))) return;
  const id = port.sender.documentId; viewPorts.set(id, port);
  port.onMessage.addListener(message => {
    (async () => {
      if (message.command === 'stop') { await resetRuntime(); return {}; }
      await ensureRuntime();
      return await sendToRuntime('view', {id,message});
    })().then(response => {
      if (viewPorts.get(id) === port) port.postMessage({type:'reply',id:message.id,...response});
    }, error => {try {port.postMessage({type:'reply',id:message.id,error:error.message,errorName:error.name});}catch{/* View closed. */}});
  });
  port.onDisconnect.addListener(() => {
    if (viewPorts.get(id) !== port) return;
    viewPorts.delete(id); sendToRuntime('detach',{id}).catch(() => {});
  });
});

const events = forwardChromeEvents((path,args) => sendToRuntime('event',{path,args}).catch(() => {}));
let debuggerUpdates = Promise.resolve();
async function callChrome(path,args,callback) {
  const start = generation;
  if (path === 'offscreen.closeDocument') throw new Error('Fibona owns the offscreen document; use Stop to reset it');
  const value = await invokeChromeMethod(path,args,callback);
  if (path === 'debugger.attach' && start !== generation) await chrome.debugger.detach(args[0]).catch(() => {});
  else if (path === 'debugger.attach' || path === 'debugger.detach') {
    debuggerUpdates = debuggerUpdates.then(async () => {
      const saved = await chrome.storage.session.get('fibona_debuggers');
      const targets = (saved.fibona_debuggers ?? []).filter(target => JSON.stringify(target) !== JSON.stringify(args[0]));
      if (path === 'debugger.attach') targets.push(args[0]);
      await chrome.storage.session.set({fibona_debuggers:targets});
    });
    await debuggerUpdates;
  }
  return value;
}
chrome.runtime.onMessage.addListener((message,sender,reply) => {
  if (message?.fibonaTarget === 'browser' && sender.url === chrome.runtime.getURL('runtime/offscreen.html')) {
    (async () => {
      if (message.command === 'reset') { resetRuntime(message.value).catch(console.error); return; }
      if (message.command === 'environment') return {...await chrome.permissions.getAll(),events,surface:'background',userScripts:await getUserScriptsAccess()};
      const {path,args,callback} = decodeBrowserValue(message.value); return await callChrome(path,args,callback);
    })().then(async value => ({value:await encodeBrowserValue(value)})).catch(error => ({error:error.message,errorName:error.name})).then(reply);
    return true;
  }
  if (message?.fibonaTarget === 'views' && sender.url === chrome.runtime.getURL('runtime/offscreen.html')) {
    for (const [id,port] of viewPorts) if (!message.viewId || message.viewId === id) {try{port.postMessage({type:'push',value:message.value});}catch{/* View closed. */}}
    reply({});
  }
  if (!message?.fibonaTarget) {
    sendToRuntime('message',{message,sender}).then(response => reply(response.error ? {error:response.error} : decodeBrowserValue(response.value)),error => reply({error:error.message}));
    return true;
  }
});
chrome.runtime.onUserScriptMessage?.addListener((message,sender,reply) => {
  sendToRuntime('message',{message,sender}).then(response => reply(response.error ? {error:response.error} : decodeBrowserValue(response.value)),error => reply({error:error.message}));return true;
});
chrome.action.onClicked.addListener(async () => {if (!(await chrome.sidePanel.getPanelBehavior()).openPanelOnActionClick) await chrome.tabs.create({url:chrome.runtime.getURL('index.html')});});
chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.create({id:'fibona-recovery',title:'Open Fibona console',contexts:['action']});
  ensureRuntime().then(() => sendToRuntime('initialize',{auto:true})).catch(console.error);
});
chrome.runtime.onStartup.addListener(() => {ensureRuntime().then(() => sendToRuntime('initialize',{auto:true})).catch(console.error);});
chrome.contextMenus.onClicked.addListener(info => {if(info.menuItemId === 'fibona-recovery') chrome.tabs.create({url:chrome.runtime.getURL('index.html?safe=1')});});
