// Every page's door to main.js: the console, the handle and the agent's app.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("fibona", {
  send: (channel, ...args) => ipcRenderer.send(channel, ...args),
  invoke: (channel, ...args) => ipcRenderer.invoke(channel, ...args),
  on: (channel, fn) => ipcRenderer.on(channel, (_, ...args) => fn(...args)),
});
