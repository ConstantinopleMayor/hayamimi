const { contextBridge, ipcRenderer } = require("electron");

// Expose a tiny API the injected drag script uses to ask the main process
// for the window position and to move the window while dragging.
contextBridge.exposeInMainWorld("desktopSubtitle", {
  getPosition: () => ipcRenderer.invoke("hmy:get-pos"),
  setPosition: (x, y) => ipcRenderer.send("hmy:set-pos", Math.round(x), Math.round(y)),
  togglePassthrough: () => ipcRenderer.send("hmy:toggle-passthrough"),
  resize: (w, h) => ipcRenderer.send("hmy:resize", Math.round(w), Math.round(h)),
  showMenu: () => ipcRenderer.send("hmy:show-menu"),
  cycleLang: () => ipcRenderer.send("hmy:cycle-lang"),
  onModeChange: (cb) => ipcRenderer.on("hmy:mode", (_e, v) => cb(v)),
});