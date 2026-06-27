const { app, BrowserWindow, ipcMain, screen } = require('electron');
const path = require('path');

let overlayWindow;

function getWindowBounds() {
  const primaryDisplay = screen.getPrimaryDisplay();
  const { x, y, width, height } = primaryDisplay.workArea;
  const windowWidth = 900;
  const windowHeight = 160;

  return {
    width: windowWidth,
    height: windowHeight,
    x: Math.round(x + (width - windowWidth) / 2),
    y: Math.round(y + height - windowHeight - 56)
  };
}

function createOverlayWindow() {
  overlayWindow = new BrowserWindow({
    ...getWindowBounds(),
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    fullscreenable: false,
    hasShadow: false,
    title: 'TORCS AI Overlay',
    backgroundColor: '#00000000',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  overlayWindow.setAlwaysOnTop(true, 'screen-saver');
  overlayWindow.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  overlayWindow.loadFile(path.join(__dirname, '..', 'src', 'index.html'));
}

app.whenReady().then(() => {
  createOverlayWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createOverlayWindow();
    }
  });
});

ipcMain.handle('overlay:hide', () => {
  if (overlayWindow) {
    overlayWindow.hide();
  }
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});
