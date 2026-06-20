# Desktop Packaging

This project is packaged for Windows with Electron.

The app itself stays as the existing Python server plus static browser UI. The desktop build creates:

1. `dist/chiefexam-server.exe` with PyInstaller.
2. `release/ChiefExam Setup 1.0.0.exe` with electron-builder and NSIS.

## Build With GitHub Actions

1. Push this project to GitHub.
2. Open the repository on GitHub.
3. Go to **Actions**.
4. Choose **Build Windows Installer**.
5. Click **Run workflow**.
6. Wait for the build to finish.
7. Download the **chiefexam-windows-installer** artifact from the finished run.
8. Unzip the artifact to get the Windows `.exe` installer.

## Build Locally On Windows

Install these first:

- Python 3.10 or newer
- Node.js 20 or newer
- npm

Then run from the project root in PowerShell:

```powershell
.\scripts\build-windows-installer.ps1
```

If PowerShell blocks the script, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows-installer.ps1
```

The installer will be written to:

```text
release\
```

## Manual Commands

```powershell
python -m pip install -r requirements-build.txt
npm install
npm run desktop:validate
npm run desktop:build:win
```

## Runtime Data

The Electron launcher stores the SQLite database in Electron's user data folder, not inside the installed application directory. That keeps candidate data, audit logs, results, and backups writable after installation.

## Electron, Tauri, And Neutralino

Electron is implemented here because it handles a bundled Python backend cleanly and can produce an NSIS Windows installer.

Tauri and Neutralino can also be used as desktop shells, but this app is not only static frontend files; it depends on the Python HTTP server. For Tauri or Neutralino, the Python server still needs to be built as a Windows sidecar executable and managed by the shell. That is possible, but it adds another toolchain without improving the installer output for this app.
