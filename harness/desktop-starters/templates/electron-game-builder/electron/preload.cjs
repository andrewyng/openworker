const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("arcade", {
  action: (input) => ipcRenderer.invoke("arcade:action", input),
  onEvent: (listener) => {
    const receive = (_, event) => listener(event);
    ipcRenderer.on("arcade:event", receive);
    return () => ipcRenderer.removeListener("arcade:event", receive);
  },
});
