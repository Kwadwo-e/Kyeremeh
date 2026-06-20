const { app, BrowserWindow, dialog, shell } = require("electron");
const { spawn } = require("child_process");
const fs = require("fs");
const http = require("http");
const net = require("net");
const path = require("path");

let mainWindow;
let serverProcess;
let serverPort;
let stoppingBackend = false;

function getFreePort() {
  return new Promise((resolve, reject) => {
    const probe = net.createServer();
    probe.unref();
    probe.on("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const address = probe.address();
      probe.close(() => resolve(address.port));
    });
  });
}

function waitForServer(url, timeoutMs = 15000) {
  const startedAt = Date.now();
  return new Promise((resolve, reject) => {
    const attempt = () => {
      const request = http.get(url, (response) => {
        response.resume();
        resolve();
      });
      request.on("error", () => {
        if (Date.now() - startedAt > timeoutMs) {
          reject(new Error("The local examination server did not start in time."));
          return;
        }
        setTimeout(attempt, 250);
      });
      request.setTimeout(1500, () => {
        request.destroy();
      });
    };
    attempt();
  });
}

function backendCommand() {
  if (app.isPackaged) {
    const binaryName = process.platform === "win32"
      ? "chiefexam-server.exe"
      : "chiefexam-server";
    return {
      command: path.join(process.resourcesPath, "backend", binaryName),
      args: [],
    };
  }

  return {
    command: process.env.PYTHON || "python3",
    args: [path.join(__dirname, "..", "..", "server.py")],
  };
}

async function startBackend() {
  serverPort = await getFreePort();
  const userData = app.getPath("userData");
  const dbDir = path.join(userData, "data");
  fs.mkdirSync(dbDir, { recursive: true });

  const { command, args } = backendCommand();
  serverProcess = spawn(command, args, {
    cwd: app.isPackaged ? process.resourcesPath : path.join(__dirname, "..", ".."),
    env: {
      ...process.env,
      HOST: "127.0.0.1",
      PORT: String(serverPort),
      KYEREMEH_DB: path.join(dbDir, "kyeremeh.sqlite3"),
      KYEREMEH_PUBLIC_URL: `http://127.0.0.1:${serverPort}/`,
      PYTHONUNBUFFERED: "1",
    },
    stdio: app.isPackaged ? "ignore" : "inherit",
    windowsHide: true,
  });

  serverProcess.on("exit", (code) => {
    if (!stoppingBackend && code !== 0 && mainWindow) {
      dialog.showErrorBox(
        "ChiefExam",
        "The local examination server stopped unexpectedly."
      );
    }
  });

  await waitForServer(`http://127.0.0.1:${serverPort}/`);
}

function stopBackend() {
  if (!serverProcess || serverProcess.killed) return;
  stoppingBackend = true;
  serverProcess.kill();
  serverProcess = null;
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 980,
    minHeight: 640,
    title: "ChiefExam",
    icon: path.join(__dirname, "assets", "icon.ico"),
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.loadURL(`http://127.0.0.1:${serverPort}/`);
}

app.whenReady().then(async () => {
  try {
    await startBackend();
    createWindow();
  } catch (error) {
    dialog.showErrorBox("ChiefExam could not start", error.message);
    app.quit();
  }
});

app.on("window-all-closed", () => {
  stopBackend();
  app.quit();
});

app.on("before-quit", () => {
  stopBackend();
});
