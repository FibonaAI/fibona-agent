// Chrome methods are forwarded by path. Discover events from the installed browser, not a handwritten catalog.
export function resolveChromeMember(path) {
  const parts = path.split('.');
  if (parts.some(part => !/^[a-zA-Z][a-zA-Z0-9_]*$/.test(part) || ['constructor','prototype'].includes(part))) throw new Error('Fibona invalid Chrome API path');
  const name = parts.pop(), owner = parts.reduce((object, key) => object?.[key], chrome);
  if (owner?.[name] === undefined) throw new Error('Fibona Chrome API unavailable: ' + path);
  return { owner, member: owner[name] };
}

// Probe the native user scripts gate; manifest permissions alone do not enable dynamic page code.
export async function getUserScriptsAccess() {
  const settingsURL = 'chrome://extensions/?id=' + chrome.runtime.id;
  try {
    await chrome.userScripts.getScripts();
    return {available:true,settingsURL};
  } catch {
    return {available:false,settingsURL,message:'Fibona webpage scripts unavailable. Open ' + settingsURL + ' and enable Allow User Scripts (Chrome 138+) or Developer mode (Chrome 135–137), then retry. CDP remains available but displays a debugger banner.'};
  }
}

// Callback mode waits for native completion and reads runtime.lastError inside the callback.
export async function invokeChromeMethod(path, args, callback = false) {
  try {
    const {owner,member} = resolveChromeMember(path);
    if (typeof member !== 'function') throw new Error('Fibona Chrome member is not a method: ' + path);
    if (!callback) return await member.apply(owner,args);
    return await new Promise((resolve,reject) => {
      const returned = member.apply(owner,[...args,(...values) => {
        const error = chrome.runtime.lastError;
        if (error) reject(new Error(error.message));
        else resolve(values.length > 1 ? values : values.length ? values[0] : returned);
      }]);
    });
  } catch (error) {
    if (path.startsWith('userScripts.')) {
      const access = await getUserScriptsAccess();
      if (!access.available) throw new Error(access.message);
    }
    throw error;
  }
}

export function forwardChromeEvents(forward) {
  const available = [];
  function visit(object, prefix = '', depth = 0) {
    for (const key of Object.keys(object)) {
      const path = prefix ? prefix + '.' + key : key;
      if (path === 'runtime' || depth > 3) continue; // Runtime messaging needs its own reply protocol.
      let value;
      try { value = object[key]; } catch { continue; }
      if (!value || typeof value !== 'object') continue;
      if (typeof value.addListener === 'function') {
        const listener = (...args) => forward(path, args);
        try {
          // Filtered network events require URLs even for an observational listener.
          if (prefix === 'webRequest') value.addListener(listener, { urls: ['http://*/*','https://*/*'] });
          else value.addListener(listener);
          available.push(path);
        } catch { /* Manifest-gated events are not available in every installation. */ }
      } else visit(value, path, depth + 1);
    }
  }
  visit(chrome);
  return available;
}
