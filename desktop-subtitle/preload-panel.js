const { contextBridge, ipcRenderer } = require("electron");

// Settings panel bridge: read the current state once at load, push one
// apply action per click, ask main to close the window. State resync after
// every apply rides main's executeJavaScript(__hmySetState) push, so nothing
// needs to be awaited here.
contextBridge.exposeInMainWorld("hmyPanel", {
  getState: () => ipcRenderer.invoke("hmy:panel-get-state"),
  apply: (action, value) => ipcRenderer.send("hmy:panel-apply", action, value),
  close: () => ipcRenderer.send("hmy:panel-close"),
  apiGet: () => ipcRenderer.invoke("hmy:panel-api-get"),
  apiSaveTest: (values) => ipcRenderer.invoke("hmy:panel-api-save-test", values),
});
