// The same small sandbox endpoint runs the persistent REPL or an ephemeral UI program.
(() => {
  const role=new URL(location.href).searchParams.get('role')==='view'?'view':'runtime';
  let port,sequence=0,agent,booted=false,running=false;
  const pending=new Map(),events=new Map(),uiCallbacks=new Set(),dataCallbacks=new Set(),messageCallbacks=new Set();
  const send=message=>port.postMessage(message);
  const native=new FibonaNativeBridge(message=>send({type:'native',message}));
  const eventNative=new FibonaNativeBridge(message=>send({type:'event-native',message}));
  const viewNative=role==='view'?new FibonaNativeBridge(message=>send({type:'view-native',message})):undefined;
  function request(method,params={}) {
    const id=++sequence;
    return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});try{send({type:'request',id,method,...params});}catch(error){pending.delete(id);reject(error);}});
  }
  const env=new JSEnv({write:(text,level)=>{
    if(env.capture===null){fibona_app_log.push(text);if(fibona_app_log.length>500)fibona_app_log.shift();}
    if(port)send({type:'output',text,level});
  },readInput:prompt=>request('input',{prompt})});
  const subscribeLocal=(callbacks,callback)=>{callbacks.add(callback);return()=>callbacks.delete(callback);};
  env.bind({
    fibona_env:env,fibona_app_log:[],fibona_events:[],fibona_source_code:{},fibona_environment:{},
    fibona_browser:native.root(),
    ...(viewNative?{fibona_view:viewNative.root()}:{}),
    fibona_chrome:Object.assign((path,...args)=>request('chrome',{path,args}),{
      callback:(path,...args)=>request('chrome',{path,args,callback:true}),
    }),
    fibona_show_console:(visible=true)=>request('console',{visible}),
    fibona_set_title:title=>request('title',{title}),
    fibona_ui:code=>request('ui',{code}),fibona_ui_send:(data,viewId)=>request('ui-send',{data,viewId}),
    fibona_send:data=>request('ui-message',{data}),
    fibona_on_ui:callback=>subscribeLocal(uiCallbacks,callback),fibona_on_data:callback=>subscribeLocal(dataCallbacks,callback),
    fibona_on_message:callback=>subscribeLocal(messageCallbacks,callback),
    fibona_on:async(path,callback,...args)=>{
      if(args.length)throw new Error('Fibona events use fixed worker listeners. Filter in your callback; use fibona_view for view-owned native registrations or CDP for additional network data. Blocking listener returns cannot cross asynchronous RPC.');
      if(callback===undefined){
        if(role!=='runtime')throw new Error('Supply an event callback in view code; the agent inbox belongs to the background REPL');
        callback=async(...values)=>{
          const index=fibona_events.push({path,args:values})-1;
          await request('task',{text:'Browser event '+path+' is available in fibona_events['+index+']. Handle it within the current program and preserve unfinished work.'});
        };
      }
      if(typeof callback!=='function')throw new TypeError('Fibona event callback must be a function');
      const subscription=String(++sequence);events.set(subscription,callback);
      try{await request('subscribe',{path,subscription});}catch(error){events.delete(subscription);throw error;}
      return async()=>{await request('unsubscribe',{subscription});events.delete(subscription);};
    },
  });
  globalThis.fibona_host_names=new Set(Object.getOwnPropertyNames(globalThis));
  async function run({sources,boot,environment}) {
    if(running)throw new Error('Fibona is already running');running=true;
    env.bind({fibona_source_code:sources,fibona_environment:environment});
    const mind=new Mind(body=>request('model',{body}),INSTRUCTIONS);
    env.bind({fibona_call_llm_api:(input,params)=>mind.query(input,params)});
    try {
      const first=!booted;booted=true;
      if(boot&&first){send({type:'cell',number:'boot',summary:'Saved startup program',code:boot});const saved=await env.execute(boot);send({type:'result',result:saved});if(saved.error){send({type:'boot-error',text:saved.error});return;}}
      send({type:'boot-ready'});
      agent??=new Agent(mind);
      await agent.run(fibona_bootstrap,{env,onCell:(number,summary,code)=>send({type:'cell',number,summary,code}),onResult:(number,result)=>send({type:'result',number,result})});
    }finally{running=false;send({type:'ended'});}
  }
  addEventListener('message',event=>{
    if(event.source!==parent||event.data?.type!=='fibona_connect'||port||!event.ports[0])return;
    port=event.ports[0];port.onmessage=async({data})=>{
      if(data.type==='native')native.receive(data.message);
      else if(data.type==='event-native')eventNative.receive(data.message);
      else if(data.type==='view-native')viewNative?.receive(data.message);
      else if(data.type==='reply'){
        const waiter=pending.get(data.id);if(!waiter)return;pending.delete(data.id);
        data.error?waiter.reject(Object.assign(new Error(data.error),{name:data.errorName??'Error'})):waiter.resolve(data.value);
      }else if(data.type==='event'){
        try{await events.get(data.subscription)?.(...eventNative.decode(data.args));}catch(error){env.print('Callback error: '+error+'\n');}
      }else if(data.type==='ui-message'||data.type==='browser-message'){
        try{let value;for(const callback of data.type==='ui-message'?uiCallbacks:messageCallbacks){const result=data.type==='ui-message'?await callback(data.data,data.view):await callback(data.message,data.sender);if(result!==undefined)value=result;}send({type:'host-reply',id:data.id,value});}
        catch(error){send({type:'host-reply',id:data.id,error:error?.message??String(error)});}
      }else if(data.type==='ui-data'){
        for(const callback of dataCallbacks){try{await callback(data.data);}catch(error){env.print('UI callback error: '+error+'\n');}}
      }else if(data.type==='run-view'&&role==='view'){
        env.bind({fibona_ui_context:data.view,fibona_environment:data.environment});const result=await env.execute(data.code);send({type:'view-ready',error:result.error});
      }else if(data.type==='run'&&role==='runtime')run(data).catch(error=>send({type:'output',text:'Fibona stopped: '+error+'\n'}));
    };port.start();send({type:'connected'});
  });
  parent.postMessage({type:'fibona_ready'},'*');
})();
