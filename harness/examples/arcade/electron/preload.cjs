const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("arcade", {
  action: (input) => ipcRenderer.invoke("arcade:action", input),
  gameProgress: (url, request) => ipcRenderer.invoke("arcade:progress", url, request),
  onEvent: (listener) => {
    const receive = (_, event) => listener(event);
    ipcRenderer.on("arcade:event", receive);
    return () => ipcRenderer.removeListener("arcade:event", receive);
  },
});
