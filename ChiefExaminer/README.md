# KYEREMEH v1.1

KYEREMEH v1.1 is a secure, lightweight online examination web application for a nursing training college. It includes candidate registration, candidate login by index number, an administrator backend, question upload, timed exams, autosave, auto-marking, suspicious-event logging, and Excel results export.

## Features

- Candidate registration with unique index numbers
- Administrator login with username-based access
- Administrator creation, editing, acceptance, suspension, deletion, and password reset for candidates
- Administrator host settings for changing the admin username and password
- Progressive Web App (PWA) install support with cached app shell assets
- Secure password hashing with PBKDF2
- Role-based access for candidates and administrators
- Single active session per candidate account
- Administrator dashboard for exams, candidates, questions, and results
- Manual question entry and `.txt` / `.docx` / `.csv` question import
- Exam start date/time and exam end date/time
- Configurable number of attempts per exam
- Timed exam attempts with visible countdown
- Autosave while candidates answer
- Automatic submission at time expiry
- Objective auto-marking
- Results export as `.xlsx`
- Database backup and restore from the administrator host settings
- Connected-device visibility for active host sessions and in-progress examinations
- Browser event logging for tab hiding, window blur, copy, paste, cut, and right-click attempts
- Responsive browser UI for desktop, tablet, and mobile

## Tech Stack

- Backend: Python standard library HTTP server
- Database: SQLite
- Frontend: HTML, CSS, and JavaScript
- File processing: Python `zipfile` and XML parsing for `.docx`
- Excel export: generated `.xlsx` workbook

This project has no external package dependencies. It uses SQLite so a college can run a working installation without setting up PostgreSQL or MySQL first. The database schema is in `schema.sql`.

## Browser and Computer Compatibility

KYEREMEH v1.1 is a web-based online examination system designed to open and run on standard computers and major modern browsers without complex installation.

The app should be compatible with Windows, macOS, and Linux computers, including desktop computers, laptop computers, and tablets where possible. The interface is responsive so pages adjust to different screen sizes.

The app should work properly on major modern browsers, including Google Chrome, Microsoft Edge, Mozilla Firefox, Safari, and Opera. It should not depend on one specific browser only. It uses standard web technologies such as HTML5, CSS3, JavaScript, secure backend technologies, and a standard database system.

Before deployment, test the app across different browsers and screen sizes. For best examination security, Chrome, Edge, or Firefox may be recommended during exams, but the app should still open in all major browsers.

## Progressive Web App and Offline/LAN Use

ChiefExam is prepared as a Progressive Web App (PWA). It includes a web app manifest, app icons, mobile home-screen metadata, and a service worker that caches the app shell files needed to open the interface.

The app does not use internet-hosted fonts, scripts, images, or CDN files. Once the ChiefExam server is running on the host computer, exams can run on a local Wi-Fi/LAN without internet access. The host computer and all candidate devices must remain connected to the same network during the exam because answers, submissions, timing, audit logs, and results are saved to the local ChiefExam server.

For LAN exams, start the server on the host computer with:

```bash
HOST=0.0.0.0 PORT=8000 python3 server.py
```

Then open the administrator **Connect Devices** page and share the displayed link or QR code with candidates on the same Wi-Fi/LAN.

Supported access targets:

- Windows, macOS, and Linux computers using modern Chrome, Edge, Firefox, Safari, or Opera
- Android phones/tablets using Chrome, Edge, Firefox, or another modern browser
- iPhone and iPad using Safari or another modern iOS browser
- Installed PWA/home-screen use where the browser and network security rules allow it

Important exam-room notes:

- Internet access is not required during exams if the host computer and candidate devices stay on the same Wi-Fi/LAN.
- Do not shut down the host computer or disconnect it from the exam network while an exam is running.
- If a device shows the ChiefExam connection warning, reconnect it to the exam Wi-Fi/LAN before submitting.
- Some browsers only allow service workers/PWA installation on secure origins. The app still works through the normal browser over LAN even when a browser does not offer installation from a local `http://` address.

## Run Locally

From the project folder:

```bash
python3 server.py
```

Then open:

```text
http://127.0.0.1:8000
```

The first screen now shows active examinations. Students and administrators can open the exam system from phones, tablets, or other computers on the same Wi-Fi/LAN.

By default the server listens on `0.0.0.0`, which allows nearby devices on the same network to connect. If you only want access from the host computer, start it with:

```bash
HOST=127.0.0.1 python3 server.py
```

In this Codex workspace, use the bundled Python runtime if `python3` is not available:

```bash
/Users/macbook/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 server.py
```

## How to Build the Windows Installer

The easiest way to create the Windows `.exe` installer is to let GitHub Actions build it on a Windows computer in the cloud. This avoids needing npm, PyInstaller, or Windows build tools on this Mac.

### Build With GitHub Actions

1. Create a GitHub repository for this project, or open the repository if it already exists.
2. Upload or push the project files to GitHub, including `.github/workflows/windows-installer.yml`, `package.json`, `requirements-build.txt`, `scripts/build-windows-installer.ps1`, `server.py`, `schema.sql`, `public/`, and `desktop/`. Do not upload generated folders such as `data/`, `build/`, `dist/`, `release/`, `node_modules/`, or `__pycache__/`.
3. Open the repository on GitHub.
4. Click the **Actions** tab.
5. Click **Build Windows Installer**.
6. Click **Run workflow**.
7. Click the green **Run workflow** button that appears.
8. Wait for the workflow to finish. This can take several minutes.
9. Open the finished workflow run.
10. Scroll down to **Artifacts**.
11. Download **chiefexam-windows-installer**.
12. Unzip the downloaded file. The Windows installer `.exe` will be inside.

The workflow uses a Windows runner (`windows-latest`), installs Python, installs PyInstaller from `requirements-build.txt`, installs Node.js and npm packages, validates the frontend/backend files, builds the Python backend executable with PyInstaller, packages the Electron desktop app as a Windows installer, and uploads the final `.exe` as a downloadable GitHub Actions artifact.

### Build Locally On Windows

Install these first:

- Python 3.10 or newer
- Node.js 20 or newer
- Git, if you are cloning the project from GitHub

Open PowerShell in the project folder and run this exact command:

```powershell
.\scripts\build-windows-installer.ps1
```

If PowerShell blocks the script, run these two commands instead:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build-windows-installer.ps1
```

The generated installer will be saved in:

```text
release\
```

## Default Administrator

On first run, the app creates one administrator account:

```text
Username: admin
Password: Admin@12345
```

To set a different first-run password, start the server with:

```bash
KYEREMEH_ADMIN_PASSWORD="Use-A-Strong-Password" python3 server.py
```

Set this before the database is created. The database file is stored at:

```text
data/kyeremeh.sqlite3
```

## Administrator Exam Guide

1. Log in through the Admin panel.
2. Create an exam with a title, instructions, exam start date and time, exam end date and time, time limit, and assigned candidate index numbers.
3. Add questions manually or import a `.txt`, `.docx`, or `.csv` file.
4. Preview questions from the Questions tab.
5. Activate the exam when it is ready.
6. Open Results to view submissions and export the Excel file.

## Administrator Host Guide

1. Log in with the administrator account.
2. Accept or delete registered candidates.
3. View all exams, questions, candidates, and results.
4. Maintain overall responsibility for hosting, supervision, account oversight, and result exports.
5. Use Host Settings to change the administrator username and password.

## Candidate Guide

1. Register with full name, index number, and password.
2. Wait for the administrator to accept the account.
3. Log in with index number and password.
4. Start only the exam assigned to that index number while it is open.
5. Select answers. Answers save automatically.
6. Submit manually, or the system submits automatically when time expires.

## Question Upload Format

Use this format in `.txt` files or Word documents:

```text
Question: Which organ is mainly responsible for gaseous exchange?
A. Trachea
B. Bronchi
C. Alveoli
D. Larynx
Answer: C
Marks: 1
Rationale: Gaseous exchange mainly occurs in the alveoli.
```

Multiple questions can be placed one after another.

CSV uploads can use a header row:

```text
Question,A,B,C,D,Answer,Marks,Rationale
Which organ is mainly responsible for gaseous exchange?,Trachea,Bronchi,Alveoli,Larynx,C,1,Gaseous exchange mainly occurs in the alveoli.
```

## Deployment Notes

- Use HTTPS in production.
- Set `KYEREMEH_SECURE_COOKIES=1` when serving over HTTPS.
- Put the app behind Nginx, Apache, Caddy, or a managed platform proxy.
- Back up `data/kyeremeh.sqlite3` regularly.
- For larger schools or multi-campus use, migrate the tables in `schema.sql` to PostgreSQL or MySQL and replace the SQLite connection layer.
- Restrict server access to trusted administrators.
- Use a strong administrator password and rotate it when staff change.

## Security Notes

This app includes reasonable browser-based exam controls: single candidate session, autosave, timed submission, leave/refresh warning, copy/paste/right-click blocking during exams, randomization options, and suspicious-event logs.

A normal browser application cannot reliably force a browser to remain topmost or completely prevent minimizing across all browsers and operating systems. For stronger lockdown, run supervised exams with kiosk mode, Safe Exam Browser, or a dedicated desktop examination wrapper.

## Useful Environment Variables

```text
HOST=0.0.0.0
PORT=8000
KYEREMEH_PUBLIC_URL=http://192.168.1.10:8000
KYEREMEH_DB=data/kyeremeh.sqlite3
KYEREMEH_ADMIN_PASSWORD=Admin@12345
KYEREMEH_SESSION_HOURS=8
KYEREMEH_SECURE_COOKIES=1
```
