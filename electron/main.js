'use strict'

const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const { mkdirSync } = require('fs')
const http = require('http')
const path = require('path')

// Project root is one level above this file (electron/main.js)
const APP_ROOT = path.resolve(__dirname, '..')

let mainWindow = null
let backendProcess = null
let isQuitting = false

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

// Use http.get (not fetch) for reliable ECONNREFUSED handling across Node versions
function checkBackend() {
  return new Promise((resolve) => {
    const req = http.get('http://127.0.0.1:8000/tasks', (res) => {
      res.resume() // consume body to free the socket
      resolve(res.statusCode < 500)
    })
    req.on('error', () => resolve(false))
    req.setTimeout(1000, () => {
      req.destroy()
      resolve(false)
    })
  })
}

async function waitForBackend(timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (await checkBackend()) return true
    await sleep(500)
  }
  return false
}

// ---------------------------------------------------------------------------
// Backend process management
// ---------------------------------------------------------------------------

function spawnBackend() {
  const dataDir = app.getPath('userData')
  const python = process.env.PYTHON_PATH || 'python3'

  console.log('[forge] Starting backend')
  console.log(`[forge]   cwd:            ${APP_ROOT}`)
  console.log(`[forge]   python:         ${python}`)
  console.log(`[forge]   FORGE_DATA_DIR: ${dataDir}`)

  const proc = spawn(
    python,
    ['-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8000'],
    {
      cwd: APP_ROOT,
      env: {
        ...process.env,
        FORGE_DATA_DIR: dataDir,
      },
      stdio: 'pipe',
    }
  )

  proc.stdout.on('data', (data) => process.stdout.write(`[uvicorn] ${data}`))
  proc.stderr.on('data', (data) => process.stderr.write(`[uvicorn] ${data}`))

  proc.on('error', (err) => {
    if (isQuitting) return
    console.error(`[forge] Failed to spawn backend: ${err.message}`)
    dialog.showErrorBox(
      'Failed to Start Backend',
      `Could not start the Python backend:\n\n${err.message}\n\nEnsure Python 3.11+ is installed and in your PATH.`
    )
    app.quit()
  })

  proc.on('exit', (code, signal) => {
    if (isQuitting) return
    console.error(`[forge] Backend exited unexpectedly (code=${code} signal=${signal})`)
    if (mainWindow && !mainWindow.isDestroyed()) {
      dialog.showErrorBox(
        'Forge Backend Crashed',
        `The Python backend exited unexpectedly (code ${code}).\n\nPlease restart the application.`
      )
    }
    app.quit()
  })

  return proc
}

// ---------------------------------------------------------------------------
// Window creation
// ---------------------------------------------------------------------------

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    title: 'Forge',
    show: false, // revealed only after the backend is ready
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      preload: path.join(__dirname, 'preload.js'),
    },
  })

  mainWindow.setMenuBarVisibility(false)

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

// ---------------------------------------------------------------------------
// App lifecycle
// ---------------------------------------------------------------------------

app.whenReady().then(async () => {
  // Ensure the userData directory exists before uvicorn tries to write there
  mkdirSync(app.getPath('userData'), { recursive: true })

  createWindow()
  backendProcess = spawnBackend()

  const ready = await waitForBackend(30000)

  if (!ready) {
    dialog.showErrorBox(
      'Backend Timeout',
      'The Python backend did not start within 30 seconds.\n\n' +
        'Check that Python 3.11+ is installed and all dependencies from\n' +
        'requirements.txt are available (`pip install -r requirements.txt`).'
    )
    app.quit()
    return
  }

  mainWindow.loadURL('http://127.0.0.1:8000')
  mainWindow.show()
})

app.on('before-quit', () => {
  isQuitting = true
  if (backendProcess) {
    console.log('[forge] Sending SIGTERM to backend')
    backendProcess.kill('SIGTERM')
    backendProcess = null
  }
})

// On Linux/Windows, quit when all windows are closed
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

// macOS: re-create window when dock icon is clicked
app.on('activate', () => {
  if (mainWindow === null && !isQuitting) {
    createWindow()
    mainWindow.loadURL('http://127.0.0.1:8000')
    mainWindow.show()
  }
})
