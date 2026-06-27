const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('torcsOverlay', {
  hide: () => ipcRenderer.invoke('overlay:hide')
});
