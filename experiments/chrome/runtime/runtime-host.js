import './native-bridge.js';
import { encodeBrowserValue, decodeBrowserValue } from './browser-wire.js';
const frame = document.getElementById('runtime'), views = new Map(), subscriptions = new Map(), calls = new Map();
const state = {status:'Ready',active:false,waiting:false,console:true,title:'Fibona',ui:'',log:[],sessionId:crypto.randomUUID(),restore:true};
let session, config, environment, initialized, inputWaiter, resolveReady, currentCell, initialInput, sequence = 0, logSequence = 0;
const ready = new Promise(resolve => {resolveReady = resolve;});
// Paths are also the keys exposed to the agent through fibona_source_code.
const sourcePaths = [
  "agent/core.js",
  "agent/bootstrap.js",
  "runtime/runtime.js",
  "runtime/native-bridge.js",
  "runtime/runtime-host.js",
  "runtime/browser-wire.js",
  "runtime/chrome-bridge.js",
  "console/console.js",
  "console/console-activity.js",
  "manifest.json",
  "index.html",
  "runtime/session.html",
  "runtime/offscreen.html",
  "console/console.css",
  "background.js",
  "README.md",
  "icons/fibona.svg"
];
const sourceLoading = Promise.all(sourcePaths.map(async path =>
  [path, await (await fetch(chrome.runtime.getURL(path))).text()]
)).then(Object.fromEntries);
let pushes = Promise.resolve();
function push(value,viewId) {
  const sent = pushes.then(async () => {await chrome.runtime.sendMessage({fibonaTarget:'views',viewId,value:await encodeBrowserValue(value)});});
  pushes = sent.catch(console.error);
  return sent;
}
function update(values) {Object.assign(state,values);push({type:'state',values});}
function log(value) {
  const item={id:++logSequence,time:Date.now(),...value};
  state.log.push(item);push({type:'log',value:item});return item;
}
function finishLog(item,values) {
  Object.assign(item,values,{duration:Date.now()-item.time});push({type:'log-update',value:item});
}
async function browserCall(path,args,callback=false) {
  const reply = await chrome.runtime.sendMessage({fibonaTarget:'browser',command:'chrome',value:await encodeBrowserValue({path,args,callback})});
  if (reply.error) throw Object.assign(new Error(reply.error),{name:reply.errorName});
  return decodeBrowserValue(reply.value);
}
async function refreshEnvironment() {
  const reply=await chrome.runtime.sendMessage({fibonaTarget:'browser',command:'environment'});
  if(reply.error)throw new Error(reply.error);
  environment={...decodeBrowserValue(reply.value),url:chrome.runtime.getURL('runtime/offscreen.html'),sessionId:state.sessionId};
}
function makeClient(id,send) {
  const client = {id,send};
  client.native = new FibonaNativeBridge(message => send({type:'native',message}),globalThis);
  return client;
}
function closeClient(client) {
  client.native.close();
  for (const [key,item] of subscriptions) if (item.client === client) subscriptions.delete(key);
  if (inputWaiter?.client === client) {inputWaiter.reject(new Error('Fibona input view closed'));inputWaiter=undefined;update({waiting:false});}
}
async function initialize(options) {
  if (initialized) return await initialized;
  initialized = (async () => {
    const saved=await browserCall('storage.local.get',[['fibona_settings','fibona_boot']]);config=saved.fibona_settings;
    await refreshEnvironment();
    await ready;
    if (options.status) update({status:options.status,restore:options.restore});
    if (options.error) log({type:'error',text:options.error});
    if (options.auto && config?.apiKey && saved.fibona_boot?.trim()) await startRun(config,true);
  })();
  return await initialized;
}
async function startRun(settings,restore,text) {
  await ready;
  if (state.active) throw new Error('Fibona is already running; attach to the session or Stop first');
  initialInput=text;config=settings;update({active:true,waiting:false,status:'Starting',restore});
  try {
    await browserCall('storage.local.set',[{fibona_settings:config}]);
    await refreshEnvironment();
    const saved=restore?await browserCall('storage.local.get',['fibona_boot']):{};
    session.send({type:'run',sources:await sourceLoading,environment,boot:typeof saved.fibona_boot==='string'?saved.fibona_boot:''});
  } catch(error) {update({active:false,console:true,status:'Could not start'});throw error;}
}
function askRuntime(type,data) {
  const id=++sequence;
  return new Promise((resolve,reject)=>{calls.set(id,{resolve,reject});session.send({type,id,...data});});
}
async function dispatch(request,client) {
  const key=client.id+':'+request.subscription;
  switch(request.method) {
    case 'chrome': return await browserCall(request.path,request.args,request.callback);
    case 'console': update({console:Boolean(request.visible)});return;
    case 'title': update({title:String(request.title)});return;
    case 'ui':
      if(client!==session)throw new Error('Fibona UI is installed from the background REPL');
      state.ui=String(request.code);await push({type:'ui',code:state.ui});return;
    case 'ui-send': await push({type:'ui-data',data:request.data},request.viewId);return;
    case 'ui-message': return await askRuntime('ui-message',{data:request.data,view:views.get(client.id)?.metadata});
    case 'subscribe':
      if(!environment.events.includes(request.path))throw new Error('Fibona event unavailable: '+request.path+'; use fibona_on_message for runtime messages');
      subscriptions.set(key,{path:request.path,subscription:request.subscription,client});return;
    case 'unsubscribe': subscriptions.delete(key);return;
    case 'input':
      if(initialInput!==undefined){const text=initialInput;initialInput=undefined;log({type:'user',text:text+'\n'});return text;}
      if(inputWaiter)throw new Error('Fibona is already waiting for input');
      if(request.prompt)log({type:'output',text:request.prompt+'\n'});
      update({waiting:true,status:'Waiting for input'});
      return await new Promise((resolve,reject)=>{inputWaiter={resolve,reject,client};});
    case 'model': {
      if(!config?.apiKey)throw new Error('Fibona model settings are missing');
      update({status:'Thinking…'});
      const body={model:config.model,...request.body,stream:false,background:false};
      try {
        const response=await fetch(config.baseURL.replace(/\/+$/,'')+'/responses',{method:'POST',headers:{'Content-Type':'application/json',Authorization:'Bearer '+config.apiKey},body:JSON.stringify(body)});
        if(!response.ok)throw new Error('Fibona model request failed: HTTP '+response.status+': '+await response.text());
        const data=await response.json();if(data.error||data.status==='failed')throw new Error(data.error?.message??'Fibona model request failed');
        data.output_text=(data.output??[]).filter(item=>item.type==='message').flatMap(item=>item.content).filter(item=>item.type==='output_text').map(item=>item.text).join('');return data;
      } finally {if(state.active)update({status:'Running'});}
    }
    default:throw new Error('Fibona host method unavailable: '+request.method);
  }
}
addEventListener('message',event=>{
  if(event.source!==frame.contentWindow||event.data?.type!=='fibona_ready'||session)return;
  const channel=new MessageChannel();session=makeClient('runtime',message=>channel.port1.postMessage(message));
  channel.port1.onmessage=async({data})=>{
    if(data.type==='connected')resolveReady();
    else if(data.type==='native')session.native.receive(data.message);
    else if(data.type==='request') {
      try{session.send({type:'reply',id:data.id,value:await dispatch(data,session)});}
      catch(error){session.send({type:'reply',id:data.id,error:error.message,errorName:error.name});}
    } else if(data.type==='host-reply') {
      const waiter=calls.get(data.id);if(!waiter)return;calls.delete(data.id);data.error?waiter.reject(new Error(data.error)):waiter.resolve(data.value);
    } else if(data.type==='boot-error') {
      chrome.runtime.sendMessage({fibonaTarget:'browser',command:'reset',value:{status:'Startup failed; saved program bypassed',restore:false,error:'Startup program failed:\n'+data.text+'\n'}}).catch(console.error);
    } else if(data.type==='ended') {
      update({active:false,waiting:false,status:'Ended'});
      if(inputWaiter){inputWaiter.reject(new Error('Fibona run ended'));inputWaiter=undefined;}
    } else if(data.type==='cell') {
      currentCell=log({...data,status:'running'});update({status:'Running'});
    } else if(data.type==='result') {
      if(currentCell)finishLog(currentCell,{status:data.result.error?'error':data.result.quit?'quit':'ok',result:data.result});
    } else {
      log(data);
    }
  };
  channel.port1.start();frame.contentWindow.postMessage({type:'fibona_connect'},'*',[channel.port2]);
});
async function handleView(id,message) {
  const data=decodeBrowserValue(message.value);
  if(message.command==='attach') {
    await initialize({auto:!data.safeMode});
    if(!views.has(id))views.set(id,{id,metadata:{id,...data},client:makeClient(id,message=>push({type:'frame-message',message},id))});
    return {...state,settings:config,environment,view:views.get(id).metadata};
  }
  const view=views.get(id);if(!view)throw new Error('Fibona view is not attached');
  if(message.command==='state')return {active:state.active,waiting:state.waiting,status:state.status};
  if(message.command==='start')return await startRun(data.settings,state.restore && !view.metadata.safeMode,data.text);
  if(message.command==='output'){log({type:'output',source:id,text:data.text,level:data.level});return;}
  if(message.command==='frame')return await dispatch(data,view.client);
  if(message.command==='native'){view.client.native.receive(data);return;}
  if(message.command==='frame-reset'){closeClient(view.client);view.client=makeClient(id,message=>push({type:'frame-message',message},id));return;}
  if(message.command==='input') {
    if(!inputWaiter)throw new Error('Fibona is not waiting for console input');
    const waiter=inputWaiter;inputWaiter=undefined;log({type:'user',text:data.text+'\n'});update({waiting:false,status:'Running'});waiter.resolve(data.text);return;
  }
  throw new Error('Fibona view command unavailable: '+message.command);
}
chrome.runtime.onMessage.addListener((message,sender,reply)=>{
  if(message?.fibonaTarget!=='runtime-host'||sender.id!==chrome.runtime.id||sender.tab||!sender.url?.startsWith(chrome.runtime.getURL('')))return;
  (async()=>{
    if(message.command==='initialize')return await initialize(message.value);
    if(message.command==='view')return await handleView(message.value.id,message.value.message);
    if(message.command==='detach'){const view=views.get(message.value.id);if(view){views.delete(view.id);closeClient(view.client);}return;}
    if(message.command==='event'){for(const item of subscriptions.values())if(item.path===message.value.path)item.client.send({type:'event',subscription:item.subscription,args:message.value.args});return;}
    if(message.command==='message')return await askRuntime('browser-message',message.value);
  })().then(async value=>({value:await encodeBrowserValue(value)})).catch(error=>({error:error.message,errorName:error.name})).then(reply);return true;
});
frame.src='session.html';
