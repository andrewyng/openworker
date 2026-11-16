const { contextBridge, ipcRenderer } = require("electron");
contextBridge.exposeInMainWorld("codeAtlas", {
  choose: () => ipcRenderer.invoke("summary:choose"),
  summarize: () => ipcRenderer.invoke("summary:run"),
  cancel: () => ipcRenderer.invoke("summary:cancel"),
  onEvent: (callback) => {
    const handler = (_event, value) => callback(value);
    ipcRenderer.on("summary:event", handler);
    return () => ipcRenderer.removeListener("summary:event", handler);
  },
});
