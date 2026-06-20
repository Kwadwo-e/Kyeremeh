const app = document.getElementById("app");

const state = {
  user: null,
  adminTab: "exams",
  stats: null,
  exams: [],
  candidates: [],
  examiners: [],
  results: [],
  connectedDevices: [],
  auditData: { students: [], examiners: [], superAdmin: { summary: {}, alerts: [] } },
  auditView: "students",
  selectedExamId: "",
  editingExamId: "",
  editingQuestionId: "",
  editingCandidateId: "",
  editingExaminerId: "",
  showPreview: false,
  attemptData: null,
  questionIndex: 0,
  timerId: null,
  autoSubmitting: false,
  pendingAutoSubmit: false,
  logThrottle: {},
  networkInfo: null,
  networkError: "",
  publicActiveExams: null,
  publicExamError: "",
  message: "",
  messageType: "info",
  connectionStatus: navigator.onLine ? "online" : "offline",
  connectionMessage: "",
};

function esc(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

const QR_VERSION_CONFIG = [
  null,
  { dataCodewords: 19, eccCodewords: 7 },
  { dataCodewords: 34, eccCodewords: 10 },
  { dataCodewords: 55, eccCodewords: 15 },
  { dataCodewords: 80, eccCodewords: 20 },
  { dataCodewords: 108, eccCodewords: 26 },
];

const QR_EXP = new Array(512);
const QR_LOG = new Array(256);
let qrFieldValue = 1;
for (let index = 0; index < 255; index += 1) {
  QR_EXP[index] = qrFieldValue;
  QR_LOG[qrFieldValue] = index;
  qrFieldValue <<= 1;
  if (qrFieldValue & 0x100) qrFieldValue ^= 0x11d;
}
for (let index = 255; index < QR_EXP.length; index += 1) {
  QR_EXP[index] = QR_EXP[index - 255];
}

function appendQrBits(bits, value, length) {
  for (let index = length - 1; index >= 0; index -= 1) {
    bits.push((value >>> index) & 1);
  }
}

function qrGfMultiply(left, right) {
  if (left === 0 || right === 0) return 0;
  return QR_EXP[QR_LOG[left] + QR_LOG[right]];
}

function qrGeneratorPolynomial(degree) {
  let coefficients = [1];
  for (let degreeIndex = 0; degreeIndex < degree; degreeIndex += 1) {
    const next = new Array(coefficients.length + 1).fill(0);
    coefficients.forEach((coefficient, index) => {
      next[index] ^= coefficient;
      next[index + 1] ^= qrGfMultiply(coefficient, QR_EXP[degreeIndex]);
    });
    coefficients = next;
  }
  return coefficients.slice(1);
}

function qrErrorCorrection(dataCodewords, eccCodewords) {
  const generator = qrGeneratorPolynomial(eccCodewords);
  const remainder = new Array(eccCodewords).fill(0);
  dataCodewords.forEach((codeword) => {
    const factor = codeword ^ remainder.shift();
    remainder.push(0);
    generator.forEach((coefficient, index) => {
      remainder[index] ^= qrGfMultiply(coefficient, factor);
    });
  });
  return remainder;
}

function qrDataCodewords(text) {
  const bytes = Array.from(new TextEncoder().encode(text));
  const version = QR_VERSION_CONFIG.findIndex((config, index) => {
    if (!config) return false;
    return 4 + 8 + bytes.length * 8 <= config.dataCodewords * 8 && index > 0;
  });
  if (version < 1) {
    throw new Error("The link is too long for this QR code.");
  }

  const config = QR_VERSION_CONFIG[version];
  const bits = [];
  appendQrBits(bits, 0x4, 4);
  appendQrBits(bits, bytes.length, 8);
  bytes.forEach((byte) => appendQrBits(bits, byte, 8));
  const capacityBits = config.dataCodewords * 8;
  appendQrBits(bits, 0, Math.min(4, capacityBits - bits.length));
  while (bits.length % 8 !== 0) bits.push(0);

  const codewords = [];
  for (let index = 0; index < bits.length; index += 8) {
    codewords.push(bits.slice(index, index + 8).reduce((value, bit) => (value << 1) | bit, 0));
  }
  for (let pad = 0xec; codewords.length < config.dataCodewords; pad ^= 0xfd) {
    codewords.push(pad);
  }

  return { version, codewords, eccCodewords: config.eccCodewords };
}

function qrFormatBits(mask) {
  let data = (1 << 3) | mask;
  let bits = data << 10;
  for (let index = 14; index >= 10; index -= 1) {
    if ((bits >>> index) & 1) bits ^= 0x537 << (index - 10);
  }
  return ((data << 10) | bits) ^ 0x5412;
}

function qrMaskApplies(mask, row, col) {
  if (mask === 0) return (row + col) % 2 === 0;
  if (mask === 1) return row % 2 === 0;
  if (mask === 2) return col % 3 === 0;
  if (mask === 3) return (row + col) % 3 === 0;
  if (mask === 4) return (Math.floor(row / 2) + Math.floor(col / 3)) % 2 === 0;
  if (mask === 5) return ((row * col) % 2) + ((row * col) % 3) === 0;
  if (mask === 6) return (((row * col) % 2) + ((row * col) % 3)) % 2 === 0;
  return (((row + col) % 2) + ((row * col) % 3)) % 2 === 0;
}

function placeQrFormat(modules, reserved, mask) {
  const size = modules.length;
  const bits = qrFormatBits(mask);
  const bit = (index) => Boolean((bits >>> index) & 1);
  const set = (row, col, value) => {
    modules[row][col] = value;
    reserved[row][col] = true;
  };

  for (let index = 0; index <= 5; index += 1) set(index, 8, bit(index));
  set(7, 8, bit(6));
  set(8, 8, bit(7));
  set(8, 7, bit(8));
  for (let index = 9; index < 15; index += 1) set(8, 14 - index, bit(index));

  for (let index = 0; index < 8; index += 1) set(8, size - 1 - index, bit(index));
  for (let index = 8; index < 15; index += 1) set(size - 15 + index, 8, bit(index));
  set(size - 8, 8, true);
}

function qrPenalty(modules) {
  const size = modules.length;
  let penalty = 0;

  const runPenalty = (line) => {
    let total = 0;
    let color = line[0];
    let length = 1;
    for (let index = 1; index < line.length; index += 1) {
      if (line[index] === color) {
        length += 1;
      } else {
        if (length >= 5) total += 3 + length - 5;
        color = line[index];
        length = 1;
      }
    }
    if (length >= 5) total += 3 + length - 5;
    return total;
  };

  for (let row = 0; row < size; row += 1) {
    penalty += runPenalty(modules[row]);
  }
  for (let col = 0; col < size; col += 1) {
    penalty += runPenalty(modules.map((row) => row[col]));
  }

  for (let row = 0; row < size - 1; row += 1) {
    for (let col = 0; col < size - 1; col += 1) {
      const value = modules[row][col];
      if (
        modules[row][col + 1] === value &&
        modules[row + 1][col] === value &&
        modules[row + 1][col + 1] === value
      ) {
        penalty += 3;
      }
    }
  }

  const finderPattern = "10111010000";
  const reversedFinderPattern = "00001011101";
  const addFinderPenalty = (line) => {
    const text = line.map((value) => (value ? "1" : "0")).join("");
    let total = 0;
    for (let index = 0; index <= text.length - 11; index += 1) {
      const chunk = text.slice(index, index + 11);
      if (chunk === finderPattern || chunk === reversedFinderPattern) total += 40;
    }
    return total;
  };
  for (let row = 0; row < size; row += 1) penalty += addFinderPenalty(modules[row]);
  for (let col = 0; col < size; col += 1) penalty += addFinderPenalty(modules.map((row) => row[col]));

  const dark = modules.flat().filter(Boolean).length;
  penalty += Math.floor(Math.abs(dark * 20 - size * size * 10) / (size * size)) * 10;
  return penalty;
}

function makeQrModules(text) {
  const { version, codewords, eccCodewords } = qrDataCodewords(text);
  const size = version * 4 + 17;
  const modules = Array.from({ length: size }, () => new Array(size).fill(false));
  const reserved = Array.from({ length: size }, () => new Array(size).fill(false));
  const setFunction = (row, col, value) => {
    if (row < 0 || col < 0 || row >= size || col >= size) return;
    modules[row][col] = Boolean(value);
    reserved[row][col] = true;
  };

  const placeFinder = (top, left) => {
    for (let row = -1; row <= 7; row += 1) {
      for (let col = -1; col <= 7; col += 1) {
        const inFinder = row >= 0 && row <= 6 && col >= 0 && col <= 6;
        const dark = inFinder && (
          row === 0 || row === 6 || col === 0 || col === 6 ||
          (row >= 2 && row <= 4 && col >= 2 && col <= 4)
        );
        setFunction(top + row, left + col, dark);
      }
    }
  };

  const placeAlignment = (centerRow, centerCol) => {
    for (let row = -2; row <= 2; row += 1) {
      for (let col = -2; col <= 2; col += 1) {
        setFunction(centerRow + row, centerCol + col, Math.max(Math.abs(row), Math.abs(col)) !== 1);
      }
    }
  };

  placeFinder(0, 0);
  placeFinder(0, size - 7);
  placeFinder(size - 7, 0);
  for (let index = 8; index < size - 8; index += 1) {
    setFunction(6, index, index % 2 === 0);
    setFunction(index, 6, index % 2 === 0);
  }
  if (version > 1) placeAlignment(size - 7, size - 7);
  placeQrFormat(modules, reserved, 0);

  const data = codewords.concat(qrErrorCorrection(codewords, eccCodewords));
  const dataBits = [];
  data.forEach((codeword) => appendQrBits(dataBits, codeword, 8));
  let bitIndex = 0;
  for (let right = size - 1; right >= 1; right -= 2) {
    if (right === 6) right -= 1;
    for (let vertical = 0; vertical < size; vertical += 1) {
      const row = ((right + 1) & 2) === 0 ? size - 1 - vertical : vertical;
      for (let offset = 0; offset < 2; offset += 1) {
        const col = right - offset;
        if (reserved[row][col]) continue;
        modules[row][col] = Boolean(dataBits[bitIndex] || 0);
        bitIndex += 1;
      }
    }
  }

  let bestModules = null;
  let bestReserved = null;
  let bestMask = 0;
  let bestPenalty = Infinity;
  for (let mask = 0; mask < 8; mask += 1) {
    const candidate = modules.map((row) => row.slice());
    const candidateReserved = reserved.map((row) => row.slice());
    for (let row = 0; row < size; row += 1) {
      for (let col = 0; col < size; col += 1) {
        if (!candidateReserved[row][col] && qrMaskApplies(mask, row, col)) {
          candidate[row][col] = !candidate[row][col];
        }
      }
    }
    placeQrFormat(candidate, candidateReserved, mask);
    const penalty = qrPenalty(candidate);
    if (penalty < bestPenalty) {
      bestPenalty = penalty;
      bestMask = mask;
      bestModules = candidate;
      bestReserved = candidateReserved;
    }
  }

  placeQrFormat(bestModules, bestReserved, bestMask);
  return bestModules;
}

function qrSvg(text) {
  const modules = makeQrModules(text);
  const quietZone = 4;
  const size = modules.length + quietZone * 2;
  const blocks = [];
  modules.forEach((row, rowIndex) => {
    row.forEach((dark, colIndex) => {
      if (dark) blocks.push(`M${colIndex + quietZone},${rowIndex + quietZone}h1v1h-1z`);
    });
  });
  return `
    <svg class="qr-code" role="img" aria-label="QR code for ${esc(text)}" viewBox="0 0 ${size} ${size}" xmlns="http://www.w3.org/2000/svg">
      <rect width="${size}" height="${size}" fill="#fff"/>
      <path d="${blocks.join(" ")}" fill="#132326"/>
    </svg>
  `;
}

function compactNumber(value) {
  const number = Number(value || 0);
  return Number.isInteger(number) ? String(number) : number.toFixed(2);
}

function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ");
  return date.toLocaleString([], {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatTime(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).replace("T", " ");
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function defaultDateTimeLocal() {
  const date = new Date(Date.now() + 10 * 60 * 1000);
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function defaultExpiryDateTimeLocal(scheduledAt = defaultDateTimeLocal(), minutes = 60) {
  const base = new Date(scheduledAt);
  const date = Number.isNaN(base.getTime()) ? new Date(Date.now() + 70 * 60 * 1000) : base;
  date.setMinutes(date.getMinutes() + Number(minutes || 60));
  date.setMinutes(date.getMinutes() - date.getTimezoneOffset());
  return date.toISOString().slice(0, 16);
}

function formatRemaining(seconds) {
  const safe = Math.max(0, Number(seconds || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const secs = Math.floor(safe % 60);
  if (hours) {
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
}

function localInputValue(value) {
  if (!value) return defaultDateTimeLocal();
  return String(value).replace(" ", "T").slice(0, 16);
}

function setMessage(message, type = "info") {
  state.message = message;
  state.messageType = type;
}

function messageHtml() {
  if (!state.message) return "";
  const type = state.messageType === "error" ? " error" : "";
  return `<div class="notice${type}">${esc(state.message)}</div>`;
}

function connectionBannerHtml() {
  if (state.connectionStatus === "offline") {
    return `
      <div class="connection-banner error">
        This device appears offline. Stay connected to the exam Wi-Fi/LAN until the exam is submitted.
      </div>
    `;
  }
  if (state.connectionStatus === "server" && state.connectionMessage) {
    return `<div class="connection-banner warn">${esc(state.connectionMessage)}</div>`;
  }
  return "";
}

function updateConnectionBanner() {
  const banner = document.getElementById("connectionBanner");
  if (banner) banner.innerHTML = connectionBannerHtml();
}

function setConnectionStatus(status, message = "") {
  state.connectionStatus = status;
  state.connectionMessage = message;
  updateConnectionBanner();
}

async function loadNetworkInfo() {
  try {
    const response = await fetch("/api/network-info", { credentials: "same-origin" });
    if (!response.ok) throw new Error(`Network link lookup failed with status ${response.status}`);
    state.networkInfo = await response.json();
    state.networkError = "";
  } catch (error) {
    state.networkInfo = null;
    state.networkError = error.message || "Could not prepare the QR link.";
  }
}

async function loadConnectedDevices() {
  try {
    const response = await api("/api/admin/devices");
    state.connectedDevices = Array.isArray(response.devices) ? response.devices : [];
  } catch (error) {
    state.connectedDevices = [];
    state.networkError = error.message || "Could not load connected devices.";
  }
}

function networkAccessHtml() {
  const info = state.networkInfo;
  const url = info?.url || "";
  const urls = info?.urls || [];
  let qrMarkup = `<div class="qr-placeholder">QR</div>`;
  let qrError = "";
  if (url) {
    try {
      qrMarkup = qrSvg(url);
    } catch (error) {
      qrError = error.message;
    }
  }

  const status = info?.lanReady
    ? `<span class="status-pill good">Ready</span>`
    : `<span class="status-pill warn">Check host</span>`;
  const extraUrls = urls.filter((item) => item !== url);

  return `
    <section class="panel network-panel">
      <div class="panel-header">
        <div>
          <h2>Scan to Connect</h2>
          <p>Administrator-only sharing link for students on the same Wi-Fi or LAN.</p>
        </div>
        ${info ? status : ""}
      </div>
      <div class="network-grid">
        <div class="qr-wrap">${qrMarkup}</div>
        <div class="network-details">
          <label>Exam link
            <input id="networkUrlInput" value="${esc(url || "Preparing QR code...")}" readonly>
          </label>
          <div class="inline-actions">
            <button class="button-secondary" type="button" data-copy-network-url ${url ? "" : "disabled"}>Copy Link</button>
            <button class="button-plain" type="button" data-refresh-network>Refresh</button>
          </div>
          ${info?.message ? `<p class="muted">${esc(info.message)}</p>` : ""}
          ${state.networkError ? `<div class="notice error">${esc(state.networkError)}</div>` : ""}
          ${qrError ? `<div class="notice error">${esc(qrError)}</div>` : ""}
          ${!info?.lanReady && info ? `<div class="notice">Start or restart the server with <strong>HOST=0.0.0.0</strong> so other devices can connect.</div>` : ""}
          ${extraUrls.length ? `
            <div class="alternate-links">
              <strong>Other detected links</strong>
              ${extraUrls.map((item) => `<code>${esc(item)}</code>`).join("")}
            </div>
          ` : ""}
        </div>
      </div>
    </section>
  `;
}

function bindNetworkAccess() {
  document.querySelectorAll("[data-refresh-network]").forEach((button) => {
    button.addEventListener("click", async () => {
      button.disabled = true;
      await loadNetworkInfo();
      if (state.user?.role === "admin") {
        await loadConnectedDevices();
      }
      if (state.user?.role === "admin") {
        await renderAdmin(state.adminTab);
      } else {
        renderAuth();
      }
    });
  });

  document.querySelectorAll("[data-copy-network-url]").forEach((button) => {
    button.addEventListener("click", async () => {
      const url = state.networkInfo?.url || "";
      if (!url) return;
      try {
        await navigator.clipboard.writeText(url);
        button.textContent = "Copied";
        setTimeout(() => {
          button.textContent = "Copy Link";
        }, 1400);
      } catch (_) {
        const input = document.getElementById("networkUrlInput");
        if (input) {
          input.focus();
          input.select();
        }
      }
    });
  });
}

async function api(path, options = {}) {
  const headers = options.headers ? { ...options.headers } : {};
  const fetchOptions = {
    method: options.method || "GET",
    credentials: "same-origin",
    headers,
  };

  if (options.body instanceof FormData) {
    fetchOptions.body = options.body;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    fetchOptions.body = JSON.stringify(options.body);
  }

  let response;
  try {
    response = await fetch(path, fetchOptions);
    if (state.connectionStatus !== "offline") setConnectionStatus("online");
  } catch (error) {
    const message = "ChiefExam cannot reach the exam server. Keep this device on the exam Wi-Fi/LAN and try again.";
    setConnectionStatus(navigator.onLine ? "server" : "offline", message);
    throw new Error(message);
  }
  const contentType = response.headers.get("Content-Type") || "";
  if (!response.ok) {
    let message = `Request failed with status ${response.status}`;
    if (contentType.includes("application/json")) {
      const data = await response.json().catch(() => ({}));
      message = data.error || message;
      if (data.offline) {
        setConnectionStatus("server", message);
      }
    }
    if (response.status === 401) {
      state.user = null;
      clearExamTimer();
      renderAuth();
    }
    throw new Error(message);
  }
  if (contentType.includes("application/json")) {
    return response.json();
  }
  return response;
}

function clearExamTimer() {
  if (state.timerId) {
    clearInterval(state.timerId);
    state.timerId = null;
  }
  state.autoSubmitting = false;
}

function chrome(content) {
  const user = state.user;
  const subtitle = user
    ? user.role === "admin"
      ? "Super Administrator host backend"
      : `${user.fullName} (${user.indexNumber})`
    : "Assessment Tool for Nursing, Midwifery, and Allied Health";
  app.innerHTML = `
    <header class="topbar">
      <div class="brand">
        <div class="brand-mark">CE</div>
        <div>
          <h1>ChiefExam</h1>
          <p>${esc(subtitle)}</p>
        </div>
      </div>
      <div class="top-actions">
        <div class="author-block" aria-label="Author">
          <strong>Author</strong>
          <span>KYEREMEH</span>
        </div>
        ${user ? `<button class="button-secondary" id="logoutButton" type="button">Log out</button>` : ""}
      </div>
    </header>
    <div id="connectionBanner" class="connection-banner-region" aria-live="polite">${connectionBannerHtml()}</div>
    <main class="page">${content}</main>
    <footer class="site-footer">&copy; 2026 Evans Kwadwo Kyeremeh. +233249305925. All rights reserved.</footer>
  `;
  const logoutButton = document.getElementById("logoutButton");
  if (logoutButton) {
    logoutButton.addEventListener("click", logout);
  }
}

function renderAuth(mode = "landing") {
  clearExamTimer();
  const view = mode === "candidate" || mode === "admin" ? mode : "landing";

  if (view === "landing") {
    chrome(`
      <section class="landing-home">
        ${landingActiveExamsTableHtml()}
        <div class="auth-choice" aria-label="Select portal">
          <button class="auth-choice-button" id="studentAuthButton" type="button">STUDENT</button>
          <button class="auth-choice-button admin-choice" id="adminAuthButton" type="button">ADMIN</button>
        </div>
      </section>
    `);

    document.getElementById("studentAuthButton").addEventListener("click", () => {
      setMessage("");
      renderAuth("candidate");
    });
    document.getElementById("adminAuthButton").addEventListener("click", () => {
      setMessage("");
      renderAuth("admin");
    });
    refreshLandingActiveExams();
    return;
  }

  const authPanels = view === "candidate"
    ? `
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Candidate Login</h2>
            <p>Use your index number and password.</p>
          </div>
        </div>
        <form class="form-grid" id="candidateLoginForm">
          <label>Index number
            <input name="indexNumber" autocomplete="username" required>
          </label>
          <label>Password
            <input name="password" type="password" autocomplete="current-password" required>
          </label>
          <button type="submit">Enter Exam Area</button>
        </form>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Candidate Registration</h2>
            <p>Register once with your examination identifier.</p>
          </div>
        </div>
        <form class="form-grid" id="candidateRegisterForm">
          <label>Full name
            <input name="fullName" autocomplete="name" required>
          </label>
          <label>Index number
            <input name="indexNumber" autocomplete="username" required>
          </label>
          <label>Password
            <input name="password" type="password" autocomplete="new-password" minlength="8" required>
          </label>
          <label>Confirm password
            <input name="confirmPassword" type="password" autocomplete="new-password" minlength="8" required>
          </label>
          <button type="submit">Create Account</button>
        </form>
      </section>
    `
    : `
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Administrator Login</h2>
            <p>Administrator access.</p>
          </div>
        </div>
        <form class="form-grid" id="adminLoginForm">
          <label>Username
            <input name="username" autocomplete="username" required>
          </label>
          <label>Password
            <input name="password" type="password" autocomplete="current-password" required>
          </label>
          <button type="submit">Open Backend</button>
        </form>
      </section>
    `;

  chrome(`
    <div class="auth-toolbar">
      <button class="button-plain" id="authBackButton" type="button">Back</button>
    </div>
    <div class="auth-grid">${authPanels}</div>
    <div class="stack auth-message">${messageHtml()}</div>
  `);

  document.getElementById("authBackButton").addEventListener("click", () => {
    setMessage("");
    renderAuth();
  });

  if (view === "candidate") {
    document.getElementById("candidateLoginForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget));
      try {
        const response = await api("/api/candidate/login", { method: "POST", body: data });
        state.user = response.user;
        setMessage("");
        await renderCandidateHome();
      } catch (error) {
        setMessage(error.message, "error");
        renderAuth("candidate");
      }
    });

    document.getElementById("candidateRegisterForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      const data = Object.fromEntries(new FormData(event.currentTarget));
      try {
        await api("/api/candidate/register", { method: "POST", body: data });
        setMessage("Registration submitted. An administrator must accept the account before login.", "info");
        renderAuth("candidate");
      } catch (error) {
        setMessage(error.message, "error");
        renderAuth("candidate");
      }
    });
    return;
  }

  document.getElementById("adminLoginForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    try {
      const response = await api("/api/admin/login", { method: "POST", body: data });
      state.user = response.user;
      setMessage("");
      await renderAdmin("exams");
    } catch (error) {
      setMessage(error.message, "error");
      renderAuth("admin");
    }
  });
}

function landingActiveExamsTableHtml() {
  return `
    <section class="landing-active-exams" id="landingActiveExams" aria-label="Active exams">
      ${landingActiveExamsContentHtml()}
    </section>
  `;
}

function landingActiveExamsContentHtml() {
  return `
    <div class="landing-notice-header">
      <div>
        <span class="status-pill good">Active exams</span>
        <h2>Examination Notice Board</h2>
      </div>
    </div>
    <div class="landing-exam-list">
      ${landingActiveExamsRowsHtml()}
    </div>
  `;
}

function landingActiveExamsRowsHtml() {
  if (state.publicExamError) {
    return `<div class="landing-exam-empty">Unable to load active exams.</div>`;
  }
  if (state.publicActiveExams === null) {
    return `<div class="landing-exam-empty">Loading active exams...</div>`;
  }
  if (!state.publicActiveExams.length) {
    return `<div class="landing-exam-empty">No active exams.</div>`;
  }
  return state.publicActiveExams.map((exam, index) => `
    <article class="landing-exam-card accent-${(index % 3) + 1}">
      <div class="landing-exam-title">
        <span>Title of exam</span>
        <strong>${esc(exam.title)}</strong>
      </div>
      <div class="landing-exam-meta">
        <div>
          <span>Starting time</span>
          <strong>${esc(formatDate(exam.scheduledAt))}</strong>
        </div>
        <div>
          <span>Ending time</span>
          <strong>${esc(formatDate(exam.expiresAt))}</strong>
        </div>
        <div>
          <span>Managed by</span>
          <strong>${esc(exam.examinerName || "Administrator")}</strong>
        </div>
      </div>
    </article>
  `).join("");
}

async function refreshLandingActiveExams() {
  try {
    const response = await api("/api/public/active-exams");
    state.publicActiveExams = Array.isArray(response.exams) ? response.exams : [];
    state.publicExamError = "";
  } catch (error) {
    state.publicActiveExams = [];
    state.publicExamError = error.message || "Unable to load active exams.";
  }
  const table = document.getElementById("landingActiveExams");
  if (table) {
    table.innerHTML = landingActiveExamsContentHtml();
  }
}

async function logout() {
  try {
    await api("/api/logout", { method: "POST" });
  } catch (_) {
    // The local UI still clears if the server session has already expired.
  }
  state.user = null;
  state.attemptData = null;
  clearExamTimer();
  setMessage("");
  renderAuth();
}

async function renderCandidateHome(message = "") {
  clearExamTimer();
  state.attemptData = null;
  if (message) setMessage(message, "info");
  let exams = [];
  try {
    exams = (await api("/api/candidate/exams")).exams;
  } catch (error) {
    setMessage(error.message, "error");
  }
  const profile = state.user || {};
  chrome(`
    <section class="candidate-strip">
      <div>
        <span class="muted">Candidate</span>
        <strong>${esc(profile.fullName)}</strong>
      </div>
      <div>
        <span class="muted">Index number</span>
        <strong>${esc(profile.indexNumber)}</strong>
      </div>
    </section>
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Assigned Examinations</h2>
          <p>${exams.length ? "Select an available exam." : "No active exam is assigned to this account."}</p>
        </div>
      </div>
      ${messageHtml()}
      <div class="exam-list">
        ${exams.map(candidateExamItemHtml).join("")}
      </div>
    </section>
  `);
  document.querySelectorAll("[data-start-exam]").forEach((button) => {
    button.addEventListener("click", () => startExam(button.dataset.startExam));
  });
}

function candidateExamItemHtml(exam) {
  const usedAttempts = Number(exam.submittedAttempts || 0);
  const maxAttempts = Number(exam.maxAttempts || 1);
  const statusClass = exam.attemptStatus === "submitted" ? "good" : exam.available ? "warn" : "bad";
  const statusText =
    exam.attemptStatus === "submitted"
      ? "Attempts used"
      : exam.attemptStatus === "in_progress"
        ? "In progress"
        : exam.available
          ? "Available"
          : exam.expired
            ? "Expired"
            : "Scheduled";
  const buttonText = exam.attemptStatus === "in_progress" ? "Continue" : usedAttempts ? "Start Next" : "Start";
  const disabled = !exam.available ? "disabled" : "";
  const actionId = exam.attemptStatus === "in_progress" ? exam.id : exam.id;
  return `
    <article class="exam-item">
      <div>
        <h3>${esc(exam.title)}</h3>
        <div class="exam-meta">
          <span>Exam starts ${esc(formatDate(exam.scheduledAt))}</span>
          <span>Exam ends ${esc(formatDate(exam.expiresAt))}</span>
          <span>${esc(exam.timeLimitMinutes)} minutes</span>
          <span>${esc(usedAttempts)} / ${esc(maxAttempts)} attempts used</span>
          <span>${esc(exam.questionCount)} questions</span>
          <span>${esc(compactNumber(exam.totalMarks))} marks</span>
          <span class="status-pill ${statusClass}">${statusText}</span>
        </div>
      </div>
      <button type="button" data-start-exam="${esc(actionId)}" ${disabled}>${buttonText}</button>
    </article>
  `;
}

async function startExam(examId) {
  try {
    const response = await api(`/api/candidate/exams/${encodeURIComponent(examId)}/start`, { method: "POST" });
    await loadAttempt(response.attemptId);
  } catch (error) {
    setMessage(error.message, "error");
    await renderCandidateHome();
  }
}

async function loadAttempt(attemptId) {
  try {
    const data = await api(`/api/candidate/attempts/${encodeURIComponent(attemptId)}`);
    state.attemptData = data;
    state.questionIndex = 0;
    state.pendingAutoSubmit = false;
    renderAttempt();
  } catch (error) {
    setMessage(error.message, "error");
    await renderCandidateHome();
  }
}

function examIsActive() {
  return state.attemptData && state.attemptData.attempt.status === "in_progress";
}

function renderAttempt() {
  const data = state.attemptData;
  if (!data) return;
  clearExamTimer();
  const candidate = data.candidate;
  const exam = data.exam;
  const attempt = data.attempt;
  const questions = data.questions;
  const current = questions[state.questionIndex] || questions[0];
  const answered = attempt.answers || {};

  if (attempt.status === "submitted") {
    chrome(`
      <section class="panel stack">
        <div class="panel-header">
          <div>
            <h2>${esc(exam.title)}</h2>
            <p>Submitted ${esc(formatDate(attempt.submittedAt))}</p>
          </div>
        </div>
        <div class="candidate-strip">
          <div><span class="muted">Candidate</span><strong>${esc(candidate.fullName)}</strong></div>
          <div><span class="muted">Index number</span><strong>${esc(candidate.indexNumber)}</strong></div>
          <div><span class="muted">Attempt</span><strong>${esc(attempt.attemptNumber || 1)} / ${esc(exam.maxAttempts || 1)}</strong></div>
          <div><span class="muted">Score</span><strong>${esc(compactNumber(attempt.score))} / ${esc(compactNumber(attempt.totalMarks))}</strong></div>
          <div><span class="muted">Percentage</span><strong>${esc(compactNumber(attempt.percentage))}%</strong></div>
        </div>
        <button type="button" id="backToCandidateHome">Back to exams</button>
      </section>
    `);
    document.getElementById("backToCandidateHome").addEventListener("click", () => renderCandidateHome());
    return;
  }

  chrome(`
    <section class="exam-header">
      <div class="candidate-strip">
        <div><span class="muted">Candidate</span><strong>${esc(candidate.fullName)}</strong></div>
        <div><span class="muted">Index number</span><strong>${esc(candidate.indexNumber)}</strong></div>
        <div><span class="muted">Exam</span><strong>${esc(exam.title)}</strong></div>
        <div><span class="muted">Attempt</span><strong>${esc(attempt.attemptNumber || 1)} / ${esc(exam.maxAttempts || 1)}</strong></div>
        <div><span class="muted">Exam ends</span><strong>${esc(formatDate(exam.expiresAt))}</strong></div>
        <div><span class="muted">Time remaining</span><strong class="timer" id="examTimer">--:--</strong></div>
      </div>
    </section>
    ${messageHtml()}
    <section class="exam-shell">
      <aside class="panel stack">
        <h2 class="section-title">Questions</h2>
        <div class="question-nav">
          ${questions.map((question, index) => `
            <button
              type="button"
              class="${index === state.questionIndex ? "current" : ""} ${answered[question.id] ? "answered" : ""}"
              data-question-index="${index}"
              aria-label="Question ${index + 1}"
            >${index + 1}</button>
          `).join("")}
        </div>
      </aside>
      <section class="panel question-panel">
        ${current ? questionHtml(current, answered[current.id]) : "<p>No questions found.</p>"}
      </section>
    </section>
  `);

  document.querySelectorAll("[data-question-index]").forEach((button) => {
    button.addEventListener("click", () => {
      state.questionIndex = Number(button.dataset.questionIndex);
      renderAttempt();
    });
  });
  document.querySelectorAll("input[name='answer']").forEach((input) => {
    input.addEventListener("change", () => saveAnswer(current.id, input.value));
  });
  const previous = document.getElementById("previousQuestion");
  const next = document.getElementById("nextQuestion");
  const submit = document.getElementById("submitAttempt");
  if (previous) {
    previous.addEventListener("click", () => {
      state.questionIndex = Math.max(0, state.questionIndex - 1);
      renderAttempt();
    });
  }
  if (next) {
    next.addEventListener("click", () => {
      state.questionIndex = Math.min(questions.length - 1, state.questionIndex + 1);
      renderAttempt();
    });
  }
  if (submit) {
    submit.addEventListener("click", () => submitAttempt(false));
  }
  startExamTimer();
}

function questionHtml(question, selectedAnswer) {
  const total = state.attemptData.questions.length;
  const locked = state.pendingAutoSubmit;
  return `
    <p class="muted">Question ${state.questionIndex + 1} of ${total} · ${esc(compactNumber(question.marks))} mark${Number(question.marks) === 1 ? "" : "s"}</p>
    <p class="question-text">${esc(question.text)}</p>
    <div class="option-list">
      ${question.options.map((option) => `
        <label class="option-row">
          <input type="radio" name="answer" value="${esc(option.optionKey)}" ${selectedAnswer === option.optionKey ? "checked" : ""} ${locked ? "disabled" : ""}>
          <span><strong>${esc(option.displayKey)}.</strong> ${esc(option.text)}</span>
        </label>
      `).join("")}
    </div>
    <div class="question-actions">
      <div class="inline-actions">
        <button class="button-secondary" id="previousQuestion" type="button" ${state.questionIndex === 0 ? "disabled" : ""}>Previous</button>
        <button class="button-secondary" id="nextQuestion" type="button" ${state.questionIndex >= total - 1 ? "disabled" : ""}>Next</button>
      </div>
      <button class="button-danger" id="submitAttempt" type="button">${locked ? "Retry Submit" : "Submit Exam"}</button>
    </div>
  `;
}

async function saveAnswer(questionId, answer) {
  if (!examIsActive()) return;
  state.attemptData.attempt.answers[questionId] = answer;
  try {
    await api(`/api/candidate/attempts/${encodeURIComponent(state.attemptData.attempt.id)}/answers`, {
      method: "POST",
      body: { questionId, answer },
    });
    renderAttempt();
  } catch (error) {
    if (examIsActive()) {
      setMessage(`${error.message} Your selected answer remains on this device, but it has not reached the server yet. Reconnect to the exam Wi-Fi/LAN before submitting.`, "error");
      renderAttempt();
      return;
    }
    setMessage(error.message, "error");
    await renderCandidateHome();
  }
}

function startExamTimer() {
  if (!examIsActive()) return;
  const timer = document.getElementById("examTimer");
  const dueAt = new Date(state.attemptData.attempt.dueAt).getTime();
  const tick = () => {
    const remaining = Math.ceil((dueAt - Date.now()) / 1000);
    if (timer) timer.textContent = formatRemaining(remaining);
    if (remaining <= 0 && !state.pendingAutoSubmit) {
      submitAttempt(true);
    }
  };
  tick();
  state.timerId = setInterval(tick, 1000);
}

async function submitAttempt(auto) {
  if (!examIsActive() || state.autoSubmitting) return;
  if (!auto && !window.confirm("Submit this exam now? You cannot continue answering after submission.")) {
    return;
  }
  if (!auto) state.pendingAutoSubmit = false;
  state.autoSubmitting = true;
  clearExamTimer();
  try {
    const id = state.attemptData.attempt.id;
    const result = await api(`/api/candidate/attempts/${encodeURIComponent(id)}/submit`, {
      method: "POST",
      body: { auto },
    });
    const message = auto
      ? `Time expired. Your exam was submitted automatically. Score: ${compactNumber(result.score)} / ${compactNumber(result.totalMarks)}.`
      : `Exam submitted. Score: ${compactNumber(result.score)} / ${compactNumber(result.totalMarks)}.`;
    state.attemptData = null;
    state.pendingAutoSubmit = false;
    await renderCandidateHome(message);
  } catch (error) {
    state.autoSubmitting = false;
    if (auto) {
      state.pendingAutoSubmit = true;
      setMessage(`Time has ended, but ChiefExam could not reach the server to submit automatically. Keep this device on the exam Wi-Fi/LAN and press Retry Submit. ${error.message}`, "error");
    } else {
      setMessage(`ChiefExam could not submit the exam. Keep this device on the exam Wi-Fi/LAN and try again. ${error.message}`, "error");
    }
    renderAttempt();
  }
}

function logExamEvent(type, details = {}) {
  if (!examIsActive()) return;
  const now = Date.now();
  if (state.logThrottle[type] && now - state.logThrottle[type] < 3000) return;
  state.logThrottle[type] = now;
  fetch(`/api/candidate/attempts/${encodeURIComponent(state.attemptData.attempt.id)}/events`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ type, details }),
    keepalive: true,
  }).catch(() => {});
}

async function loadAdminBase() {
  const baseRequests = [
    api("/api/admin/stats"),
    api("/api/admin/exams"),
    api("/api/admin/candidates"),
  ];
  const [stats, exams, candidates] = await Promise.all(baseRequests);
  state.stats = stats.stats;
  state.exams = exams.exams;
  state.candidates = candidates.candidates;
  state.examiners = [];
  if (!state.selectedExamId && state.exams.length) {
    state.selectedExamId = state.exams[0].id;
  }
}

async function renderAdmin(tab = state.adminTab) {
  clearExamTimer();
  state.adminTab = tab;
  if (state.user?.role !== "admin" && state.adminTab === "settings") {
    state.adminTab = "exams";
  }
  if (state.user?.role !== "admin" && state.adminTab === "connect") {
    state.adminTab = "exams";
  }
  if (state.user?.role !== "admin" && state.adminTab === "audit") {
    state.adminTab = "exams";
  }
  try {
    await loadAdminBase();
  } catch (error) {
    setMessage(error.message, "error");
  }
  if (state.user?.role === "admin" && state.adminTab === "connect") {
    await loadNetworkInfo();
    await loadConnectedDevices();
  }

  let tabHtml = "";
  if (state.adminTab === "exams") tabHtml = examsTabHtml();
  if (state.adminTab === "questions") tabHtml = await questionsTabHtml();
  if (state.adminTab === "results") tabHtml = await resultsTabHtml();
  if (state.adminTab === "candidates") tabHtml = candidatesTabHtml();
  if (state.adminTab === "connect") tabHtml = connectTabHtml();
  if (state.adminTab === "audit") tabHtml = await auditTrailTabHtml();
  if (state.adminTab === "settings") tabHtml = settingsTabHtml();

  chrome(`
    <div class="stats-grid">
      ${statHtml("Candidates", state.stats?.candidates || 0)}
      ${statHtml("Exams", state.stats?.exams || 0)}
      ${statHtml("Active", state.stats?.activeExams || 0)}
      ${statHtml("Submissions", state.stats?.submissions || 0)}
    </div>
    <div class="dashboard-grid">
      <nav class="sidebar" aria-label="Admin sections">
        ${adminTabButton("exams", "Exams")}
        ${adminTabButton("questions", "Questions")}
        ${adminTabButton("results", "Results")}
        ${adminTabButton("candidates", "Candidates")}
        ${state.user?.role === "admin" ? adminTabButton("connect", "Connect Devices") : ""}
        ${state.user?.role === "admin" ? adminTabButton("audit", "Audit Trail") : ""}
        ${state.user?.role === "admin" ? adminTabButton("settings", "Host Settings") : ""}
      </nav>
      <section class="stack">
        ${messageHtml()}
        ${tabHtml}
      </section>
    </div>
  `);
  state.message = "";
  bindAdminShell();
  bindUserAdminActions();
  if (state.adminTab === "exams") bindExamsTab();
  if (state.adminTab === "questions") bindQuestionsTab();
  if (state.adminTab === "results") bindResultsTab();
  if (state.adminTab === "candidates") bindCandidatesTab();
  if (state.adminTab === "connect") bindNetworkAccess();
  if (state.adminTab === "audit") bindAuditTrailTab();
  if (state.adminTab === "settings") bindSettingsTab();
}

function statHtml(label, value) {
  return `<div class="stat"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>`;
}

function adminTabButton(tab, label) {
  return `<button class="${state.adminTab === tab ? "active" : ""}" type="button" data-admin-tab="${esc(tab)}">${esc(label)}</button>`;
}

function bindAdminShell() {
  document.querySelectorAll("[data-admin-tab]").forEach((button) => {
    button.addEventListener("click", () => renderAdmin(button.dataset.adminTab));
  });
}

function userStatusPill(item) {
  if (item.suspended) {
    return `<span class="status-pill bad">Suspended</span>`;
  }
  return item.approved
    ? `<span class="status-pill good">Accepted</span>`
    : `<span class="status-pill warn">Pending</span>`;
}

function adminUserActionsHtml(item, label, role) {
  if (state.user?.role !== "admin") return "";
  const editAttr = "data-edit-candidate";
  const suspendAction = item.suspended
    ? `<button class="button-secondary" type="button" data-restore-user="${esc(item.id)}">Restore</button>`
    : `<button class="button-secondary" type="button" data-suspend-user="${esc(item.id)}">Suspend</button>`;
  return `
    <div class="inline-actions">
      <button class="button-secondary" type="button" ${editAttr}="${esc(item.id)}">Edit</button>
      <button class="button-secondary" type="button" data-accept-user="${esc(item.id)}" ${item.approved ? "disabled" : ""}>Accept</button>
      ${suspendAction}
      <button class="button-plain" type="button" data-reset-password="${esc(item.id)}" data-reset-label="${esc(label)}">Reset Password</button>
      <button class="button-danger" type="button" data-delete-user="${esc(item.id)}" data-delete-label="${esc(label)}">Delete</button>
    </div>
  `;
}

function bindUserAdminActions() {
  if (state.user?.role !== "admin") return;
  document.querySelectorAll("[data-accept-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api(`/api/admin/users/${encodeURIComponent(button.dataset.acceptUser)}/accept`, {
          method: "POST",
        });
        setMessage("Registration accepted.", "info");
        await renderAdmin(state.adminTab);
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin(state.adminTab);
      }
    });
  });
  document.querySelectorAll("[data-delete-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = button.dataset.deleteLabel || "this registration";
      if (!window.confirm(`Delete ${label}? This cannot be undone.`)) return;
      try {
        await api(`/api/admin/users/${encodeURIComponent(button.dataset.deleteUser)}`, {
          method: "DELETE",
        });
        setMessage("Registration deleted.", "info");
        await renderAdmin(state.adminTab);
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin(state.adminTab);
      }
    });
  });
  document.querySelectorAll("[data-suspend-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Suspend this account and close active sessions?")) return;
      try {
        await api(`/api/admin/users/${encodeURIComponent(button.dataset.suspendUser)}/suspend`, { method: "POST" });
        setMessage("Account suspended.", "info");
        await renderAdmin(state.adminTab);
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin(state.adminTab);
      }
    });
  });
  document.querySelectorAll("[data-restore-user]").forEach((button) => {
    button.addEventListener("click", async () => {
      try {
        await api(`/api/admin/users/${encodeURIComponent(button.dataset.restoreUser)}/restore`, { method: "POST" });
        setMessage("Account restored.", "info");
        await renderAdmin(state.adminTab);
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin(state.adminTab);
      }
    });
  });
  document.querySelectorAll("[data-reset-password]").forEach((button) => {
    button.addEventListener("click", async () => {
      const label = button.dataset.resetLabel || "this account";
      const password = window.prompt(`Enter a new temporary password for ${label}.`);
      if (password === null) return;
      if (password.length < 8) {
        setMessage("Password must be at least 8 characters.", "error");
        await renderAdmin(state.adminTab);
        return;
      }
      try {
        await api(`/api/admin/users/${encodeURIComponent(button.dataset.resetPassword)}/reset-password`, {
          method: "POST",
          body: { newPassword: password, confirmPassword: password },
        });
        setMessage("Password reset.", "info");
        await renderAdmin(state.adminTab);
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin(state.adminTab);
      }
    });
  });
}

function examsTabHtml() {
  const editing = state.exams.find((exam) => exam.id === state.editingExamId);
  const exam = editing || {
    title: "",
    instructions: "",
    scheduledAt: defaultDateTimeLocal(),
    expiresAt: defaultExpiryDateTimeLocal(),
    timeLimitMinutes: 60,
    maxAttempts: 1,
    active: false,
    randomizeQuestions: false,
    randomizeOptions: false,
    assignedIndexNumbers: [],
  };
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>${editing ? "Edit Exam" : "Create Exam"}</h2>
          <p>Set title, timing, activation, randomization, and assigned candidates.</p>
        </div>
        ${editing ? `<button class="button-plain" type="button" id="cancelExamEdit">New Exam</button>` : ""}
      </div>
      <form class="form-grid" id="examForm">
        <label>Exam title
          <input name="title" value="${esc(exam.title)}" required>
        </label>
        <label>Instructions
          <textarea name="instructions">${esc(exam.instructions)}</textarea>
        </label>
        <div class="four-grid">
          <label>Exam start date and time
            <input name="scheduledAt" type="datetime-local" value="${esc(localInputValue(exam.scheduledAt))}" required>
          </label>
          <label>Exam end date and time
            <input name="expiresAt" type="datetime-local" value="${esc(localInputValue(exam.expiresAt || defaultExpiryDateTimeLocal(exam.scheduledAt, exam.timeLimitMinutes)))}" required>
          </label>
          <label>Duration
            <input name="timeLimitMinutes" type="number" min="1" max="600" value="${esc(exam.timeLimitMinutes)}" required>
          </label>
          <label>Number of attempts
            <input name="maxAttempts" type="number" min="1" max="10" value="${esc(exam.maxAttempts || 1)}" required>
          </label>
        </div>
        <label>Assigned index numbers
          <textarea name="assignedIndexNumbers">${esc((exam.assignedIndexNumbers || []).join("\n"))}</textarea>
        </label>
        <div class="three-grid">
          <label class="check-row"><input name="active" type="checkbox" ${exam.active ? "checked" : ""}> <span>Activate exam</span></label>
          <label class="check-row"><input name="randomizeQuestions" type="checkbox" ${exam.randomizeQuestions ? "checked" : ""}> <span>Randomize questions</span></label>
          <label class="check-row"><input name="randomizeOptions" type="checkbox" ${exam.randomizeOptions ? "checked" : ""}> <span>Randomize options</span></label>
        </div>
        <button type="submit">${editing ? "Save Exam" : "Create Exam"}</button>
      </form>
    </section>
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Exam List</h2>
          <p>${state.exams.length} exam${state.exams.length === 1 ? "" : "s"}</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Starts</th>
              <th>Ends</th>
              <th>Time</th>
              <th>Attempts</th>
              <th>Questions</th>
              <th>Assigned</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${state.exams.map((examRow) => `
              <tr>
                <td>${esc(examRow.title)}</td>
                <td>${esc(formatDate(examRow.scheduledAt))}</td>
                <td>${esc(formatDate(examRow.expiresAt))}</td>
                <td>${esc(examRow.timeLimitMinutes)} min</td>
                <td>${esc(examRow.maxAttempts || 1)}</td>
                <td>${esc(examRow.questionCount)} (${esc(compactNumber(examRow.totalMarks))} marks)</td>
                <td>${esc((examRow.assignedIndexNumbers || []).length)}</td>
                <td><span class="status-pill ${examRow.expired ? "bad" : examRow.active ? "good" : ""}">${examRow.expired ? "Expired" : examRow.active ? "Active" : "Inactive"}</span></td>
                <td>
                  <div class="inline-actions">
                    <button class="button-secondary" type="button" data-edit-exam="${esc(examRow.id)}">Edit</button>
                    <button class="button-secondary" type="button" data-exam-questions="${esc(examRow.id)}">Questions</button>
                    <button class="button-danger" type="button" data-delete-exam="${esc(examRow.id)}">Delete</button>
                  </div>
                </td>
              </tr>
            `).join("") || `<tr><td colspan="9">No exams created yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function bindExamsTab() {
  const cancel = document.getElementById("cancelExamEdit");
  if (cancel) {
    cancel.addEventListener("click", () => {
      state.editingExamId = "";
      renderAdmin("exams");
    });
  }
  document.getElementById("examForm").addEventListener("submit", async (event) => {
    event.preventDefault();
    const form = event.currentTarget;
    const payload = {
      title: form.title.value,
      instructions: form.instructions.value,
      scheduledAt: form.scheduledAt.value,
      expiresAt: form.expiresAt.value,
      timeLimitMinutes: Number(form.timeLimitMinutes.value),
      maxAttempts: Number(form.maxAttempts.value),
      assignedIndexNumbers: form.assignedIndexNumbers.value,
      active: form.active.checked,
      randomizeQuestions: form.randomizeQuestions.checked,
      randomizeOptions: form.randomizeOptions.checked,
    };
    const editing = Boolean(state.editingExamId);
    try {
      await api(editing ? `/api/admin/exams/${encodeURIComponent(state.editingExamId)}` : "/api/admin/exams", {
        method: editing ? "PUT" : "POST",
        body: payload,
      });
      setMessage(editing ? "Exam saved." : "Exam created.", "info");
      state.editingExamId = "";
      await renderAdmin("exams");
    } catch (error) {
      setMessage(error.message, "error");
      await renderAdmin("exams");
    }
  });
  document.querySelectorAll("[data-edit-exam]").forEach((button) => {
    button.addEventListener("click", () => {
      state.editingExamId = button.dataset.editExam;
      renderAdmin("exams");
    });
  });
  document.querySelectorAll("[data-exam-questions]").forEach((button) => {
    button.addEventListener("click", () => {
      state.selectedExamId = button.dataset.examQuestions;
      state.editingQuestionId = "";
      renderAdmin("questions");
    });
  });
  document.querySelectorAll("[data-delete-exam]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this exam? Exams with attempts are protected.")) return;
      try {
        await api(`/api/admin/exams/${encodeURIComponent(button.dataset.deleteExam)}`, { method: "DELETE" });
        setMessage("Exam deleted.", "info");
        await renderAdmin("exams");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("exams");
      }
    });
  });
}

async function questionsTabHtml() {
  if (!state.exams.length) {
    return `<section class="panel"><h2 class="section-title">Questions</h2><p class="muted">Create an exam before adding questions.</p></section>`;
  }
  if (!state.selectedExamId) state.selectedExamId = state.exams[0].id;
  let detail = { exam: null, questions: [] };
  try {
    detail = await api(`/api/admin/exams/${encodeURIComponent(state.selectedExamId)}`);
  } catch (error) {
    setMessage(error.message, "error");
  }
  const questions = detail.questions || [];
  const editing = questions.find((question) => question.id === state.editingQuestionId);
  const question = editing || {
    text: "",
    options: { A: "", B: "", C: "", D: "" },
    correctAnswer: "A",
    marks: 1,
    rationale: "",
  };
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Question Bank</h2>
          <p>${esc(detail.exam?.title || "Select an exam")}</p>
        </div>
        <button class="button-secondary" type="button" id="togglePreview">${state.showPreview ? "Hide Preview" : "Preview"}</button>
      </div>
      <label>Exam
        <select id="questionExamSelect">
          ${state.exams.map((exam) => `<option value="${esc(exam.id)}" ${exam.id === state.selectedExamId ? "selected" : ""}>${esc(exam.title)}</option>`).join("")}
        </select>
      </label>
      <form class="form-grid" id="importForm">
        <label>Upload questions (.txt, .docx, or .csv)
          <input name="file" type="file" accept=".txt,.docx,.csv" required>
        </label>
        <button type="submit">Import Questions</button>
      </form>
    </section>

    <section class="panel stack ${state.showPreview ? "" : "hidden"}">
      <div class="panel-header">
        <div>
          <h2>Preview</h2>
          <p>${questions.length} question${questions.length === 1 ? "" : "s"}</p>
        </div>
      </div>
      <div class="preview-box">
        ${questions.map((item, index) => `
          <article class="preview-question">
            <h4>${index + 1}. ${esc(item.text)}</h4>
            <ol type="A">
              <li>${esc(item.options.A)}</li>
              <li>${esc(item.options.B)}</li>
              <li>${esc(item.options.C)}</li>
              <li>${esc(item.options.D)}</li>
            </ol>
            <p class="muted">Answer ${esc(item.correctAnswer)} · ${esc(compactNumber(item.marks))} mark${Number(item.marks) === 1 ? "" : "s"}</p>
          </article>
        `).join("") || `<p class="muted">No questions to preview.</p>`}
      </div>
    </section>

    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>${editing ? "Edit Question" : "Add Question"}</h2>
          <p>Multiple choice with options A to D.</p>
        </div>
        ${editing ? `<button class="button-plain" id="cancelQuestionEdit" type="button">New Question</button>` : ""}
      </div>
      <form class="form-grid" id="questionForm">
        <label>Question
          <textarea name="text" required>${esc(question.text)}</textarea>
        </label>
        <div class="two-grid">
          <label>Option A <input name="optionA" value="${esc(question.options.A)}" required></label>
          <label>Option B <input name="optionB" value="${esc(question.options.B)}" required></label>
          <label>Option C <input name="optionC" value="${esc(question.options.C)}" required></label>
          <label>Option D <input name="optionD" value="${esc(question.options.D)}" required></label>
        </div>
        <div class="two-grid">
          <label>Correct answer
            <select name="correctAnswer">
              ${["A", "B", "C", "D"].map((key) => `<option value="${key}" ${question.correctAnswer === key ? "selected" : ""}>${key}</option>`).join("")}
            </select>
          </label>
          <label>Marks
            <input name="marks" type="number" min="0.1" max="100" step="0.1" value="${esc(question.marks)}" required>
          </label>
        </div>
        <label>Rationale
          <textarea name="rationale">${esc(question.rationale)}</textarea>
        </label>
        <button type="submit">${editing ? "Save Question" : "Add Question"}</button>
      </form>
    </section>

    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Questions</h2>
          <p>${questions.length} saved</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>No.</th>
              <th>Question</th>
              <th>Answer</th>
              <th>Marks</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${questions.map((item, index) => `
              <tr>
                <td>${index + 1}</td>
                <td>${esc(item.text)}</td>
                <td>${esc(item.correctAnswer)}</td>
                <td>${esc(compactNumber(item.marks))}</td>
                <td>
                  <div class="inline-actions">
                    <button class="button-secondary" type="button" data-edit-question="${esc(item.id)}">Edit</button>
                    <button class="button-danger" type="button" data-delete-question="${esc(item.id)}">Delete</button>
                  </div>
                </td>
              </tr>
            `).join("") || `<tr><td colspan="5">No questions added yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function bindQuestionsTab() {
  const select = document.getElementById("questionExamSelect");
  if (select) {
    select.addEventListener("change", () => {
      state.selectedExamId = select.value;
      state.editingQuestionId = "";
      state.showPreview = false;
      renderAdmin("questions");
    });
  }
  const toggle = document.getElementById("togglePreview");
  if (toggle) {
    toggle.addEventListener("click", () => {
      state.showPreview = !state.showPreview;
      renderAdmin("questions");
    });
  }
  const cancel = document.getElementById("cancelQuestionEdit");
  if (cancel) {
    cancel.addEventListener("click", () => {
      state.editingQuestionId = "";
      renderAdmin("questions");
    });
  }
  const importForm = document.getElementById("importForm");
  if (importForm) {
    importForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const formData = new FormData(importForm);
      try {
        const response = await api(`/api/admin/exams/${encodeURIComponent(state.selectedExamId)}/import`, {
          method: "POST",
          body: formData,
        });
        setMessage(`${response.imported} question${response.imported === 1 ? "" : "s"} imported.`, "info");
        await renderAdmin("questions");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("questions");
      }
    });
  }
  const questionForm = document.getElementById("questionForm");
  if (questionForm) {
    questionForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      const form = event.currentTarget;
      const payload = {
        text: form.text.value,
        options: {
          A: form.optionA.value,
          B: form.optionB.value,
          C: form.optionC.value,
          D: form.optionD.value,
        },
        correctAnswer: form.correctAnswer.value,
        marks: Number(form.marks.value),
        rationale: form.rationale.value,
      };
      const editing = Boolean(state.editingQuestionId);
      const path = editing
        ? `/api/admin/exams/${encodeURIComponent(state.selectedExamId)}/questions/${encodeURIComponent(state.editingQuestionId)}`
        : `/api/admin/exams/${encodeURIComponent(state.selectedExamId)}/questions`;
      try {
        await api(path, { method: editing ? "PUT" : "POST", body: payload });
        setMessage(editing ? "Question saved." : "Question added.", "info");
        state.editingQuestionId = "";
        await renderAdmin("questions");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("questions");
      }
    });
  }
  document.querySelectorAll("[data-edit-question]").forEach((button) => {
    button.addEventListener("click", () => {
      state.editingQuestionId = button.dataset.editQuestion;
      renderAdmin("questions");
    });
  });
  document.querySelectorAll("[data-delete-question]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this question?")) return;
      try {
        await api(`/api/admin/exams/${encodeURIComponent(state.selectedExamId)}/questions/${encodeURIComponent(button.dataset.deleteQuestion)}`, {
          method: "DELETE",
        });
        setMessage("Question deleted.", "info");
        await renderAdmin("questions");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("questions");
      }
    });
  });
}

async function resultsTabHtml() {
  const query = state.resultExamId ? `?examId=${encodeURIComponent(state.resultExamId)}` : "";
  try {
    state.results = (await api(`/api/admin/results${query}`)).results;
  } catch (error) {
    setMessage(error.message, "error");
  }
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Candidate Results</h2>
          <p>${state.results.length} submission${state.results.length === 1 ? "" : "s"}</p>
        </div>
        <button type="button" id="exportResults">Export Excel</button>
      </div>
      <label>Exam filter
        <select id="resultExamFilter">
          <option value="">All exams</option>
          ${state.exams.map((exam) => `<option value="${esc(exam.id)}" ${state.resultExamId === exam.id ? "selected" : ""}>${esc(exam.title)}</option>`).join("")}
        </select>
      </label>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Full name</th>
              <th>Index number</th>
              <th>Exam title</th>
              <th>Attempt</th>
              <th>Score</th>
              <th>Percentage</th>
              <th>Submitted</th>
              <th>Time spent</th>
              <th>Events</th>
            </tr>
          </thead>
          <tbody>
            ${state.results.map((row) => `
              <tr>
                <td>${esc(row.candidateFullName)}</td>
                <td>${esc(row.indexNumber)}</td>
                <td>${esc(row.examTitle)}</td>
                <td>${esc(row.attemptNumber || 1)}</td>
                <td>${esc(compactNumber(row.score))} / ${esc(compactNumber(row.totalMarks))}</td>
                <td>${esc(compactNumber(row.percentage))}%</td>
                <td>${esc(formatDate(row.submittedAt))}</td>
                <td>${esc(row.timeSpent)}</td>
                <td><span class="status-pill ${row.suspiciousCount ? "warn" : "good"}">${esc(row.suspiciousCount)}</span></td>
              </tr>
            `).join("") || `<tr><td colspan="9">No submitted results yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function bindResultsTab() {
  const filter = document.getElementById("resultExamFilter");
  if (filter) {
    filter.addEventListener("change", () => {
      state.resultExamId = filter.value;
      renderAdmin("results");
    });
  }
  const exportButton = document.getElementById("exportResults");
  if (exportButton) {
    exportButton.addEventListener("click", () => {
      const query = state.resultExamId ? `?examId=${encodeURIComponent(state.resultExamId)}` : "";
      window.location.href = `/api/admin/results/export.xlsx${query}`;
    });
  }
}

function candidateAccountFormHtml(editing) {
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>${editing ? "Edit Candidate" : "Create Candidate"}</h2>
          <p>${editing ? "Update the candidate name or index number." : "Create an accepted student account directly."}</p>
        </div>
        ${editing ? `<button class="button-plain" id="cancelCandidateEdit" type="button">New Candidate</button>` : ""}
      </div>
      <form class="form-grid" id="candidateAdminForm">
        <input type="hidden" name="role" value="candidate">
        <div class="two-grid">
          <label>Full name
            <input name="fullName" autocomplete="name" value="${esc(editing?.fullName || "")}" required>
          </label>
          <label>Index number
            <input name="indexNumber" autocomplete="username" value="${esc(editing?.indexNumber || "")}" required>
          </label>
        </div>
        ${editing ? "" : `
          <div class="two-grid">
            <label>Password
              <input name="password" type="password" autocomplete="new-password" minlength="8" required>
            </label>
            <label>Confirm password
              <input name="confirmPassword" type="password" autocomplete="new-password" minlength="8" required>
            </label>
          </div>
        `}
        <button type="submit">${editing ? "Save Candidate" : "Create Candidate"}</button>
      </form>
    </section>
  `;
}

function candidatesTabHtml() {
  const editing = state.candidates.find((candidate) => candidate.id === state.editingCandidateId);
  return `
    ${state.user?.role === "admin" ? candidateAccountFormHtml(editing) : ""}
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Candidates</h2>
          <p>${state.candidates.length} registered candidate${state.candidates.length === 1 ? "" : "s"}</p>
        </div>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Full name</th>
              <th>Index number</th>
              <th>Status</th>
              <th>Registered</th>
              ${state.user?.role === "admin" ? "<th>Actions</th>" : ""}
            </tr>
          </thead>
          <tbody>
            ${state.candidates.map((candidate) => `
              <tr>
                <td>${esc(candidate.fullName)}</td>
                <td>${esc(candidate.indexNumber)}</td>
                <td>${userStatusPill(candidate)}</td>
                <td>${esc(formatDate(candidate.createdAt))}</td>
                ${state.user?.role === "admin" ? `<td>${adminUserActionsHtml(candidate, candidate.fullName || candidate.indexNumber, "candidate")}</td>` : ""}
              </tr>
            `).join("") || `<tr><td colspan="${state.user?.role === "admin" ? 5 : 4}">No candidates registered yet.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function bindCandidatesTab() {
  document.querySelectorAll("[data-edit-candidate]").forEach((button) => {
    button.addEventListener("click", () => {
      state.editingCandidateId = button.dataset.editCandidate;
      renderAdmin("candidates");
    });
  });
  const cancel = document.getElementById("cancelCandidateEdit");
  if (cancel) {
    cancel.addEventListener("click", () => {
      state.editingCandidateId = "";
      renderAdmin("candidates");
    });
  }
  const form = document.getElementById("candidateAdminForm");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    const editing = Boolean(state.editingCandidateId);
    try {
      await api(editing ? `/api/admin/users/${encodeURIComponent(state.editingCandidateId)}` : "/api/admin/users", {
        method: editing ? "PUT" : "POST",
        body: data,
      });
      state.editingCandidateId = "";
      setMessage(editing ? "Candidate saved." : "Candidate created.", "info");
      await renderAdmin("candidates");
    } catch (error) {
      setMessage(error.message, "error");
      await renderAdmin("candidates");
    }
  });
}

async function auditTrailTabHtml() {
  if (state.user?.role !== "admin") {
    return `<section class="panel"><h2 class="section-title">Audit Trail</h2><p class="muted">Only the Super Administrator can view audit records.</p></section>`;
  }
  try {
    const data = await api("/api/admin/audit");
    state.auditData = {
      students: data.students || [],
      examiners: data.examiners || [],
      superAdmin: data.superAdmin || { summary: {}, alerts: [] },
    };
  } catch (error) {
    state.auditData = { students: [], examiners: [], superAdmin: { summary: {}, alerts: [] } };
    setMessage(error.message, "error");
  }
  const students = state.auditData.students || [];
  const examiners = state.auditData.examiners || [];
  const alerts = state.auditData.superAdmin?.alerts || [];
  const totalRecords = students.length + examiners.length + alerts.length;
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Super Administrator Audit Center</h2>
          <p>${totalRecords} audit record${totalRecords === 1 ? "" : "s"} currently visible.</p>
        </div>
        <button class="button-danger" type="button" id="clearAuditTrail" ${totalRecords ? "" : "disabled"}>Clear Records</button>
      </div>
      <div class="audit-tabs" role="tablist" aria-label="Audit sections">
        ${auditViewButton("students", "Student", students.length)}
        ${auditViewButton("examiners", "Administrator", examiners.length)}
        ${auditViewButton("super", "Super Administrator", alerts.length)}
      </div>
      ${auditViewHtml()}
    </section>
  `;
}

function auditViewButton(view, label, count) {
  return `
    <button class="button-plain ${state.auditView === view ? "active" : ""}" type="button" data-audit-view="${esc(view)}">
      ${esc(label)} <span>${esc(count)}</span>
    </button>
  `;
}

function auditViewHtml() {
  if (state.auditView === "examiners") return examinerAuditHtml();
  if (state.auditView === "super") return superAdminAuditHtml();
  return studentAuditHtml();
}

function auditDateCell(value, fallback = "Not recorded") {
  return value ? esc(formatDate(value)) : `<span class="muted">${esc(fallback)}</span>`;
}

function auditUserTypeLabel(value) {
  const labels = {
    admin: "Super Administrator",
    examiner: "Administrator",
    candidate: "Student",
    backend: "Backend",
  };
  return labels[value] || value || "Unknown";
}

function auditSubmissionPill(mode) {
  const text = mode || "In progress";
  const className = text === "Manual" ? "good" : text === "In progress" ? "warn" : "bad";
  return `<span class="status-pill ${className}">${esc(text)}</span>`;
}

function auditScoreHtml(record) {
  if (!record.submittedAt) return `<span class="status-pill warn">Pending</span>`;
  return `${esc(compactNumber(record.score))} / ${esc(compactNumber(record.totalMarks))} (${esc(compactNumber(record.percentage))}%)`;
}

function studentAuditFlags(record) {
  const flags = [];
  if (record.lateLogin) flags.push(`<span class="status-pill warn">Late login</span>`);
  if (record.earlyExit) flags.push(`<span class="status-pill bad">Early exit</span>`);
  if (Number(record.suspiciousCount || 0) > 0) {
    flags.push(`<span class="status-pill warn">${esc(record.suspiciousCount)} suspicious</span>`);
  }
  return flags.join(" ") || `<span class="status-pill good">Clear</span>`;
}

function studentAuditHtml() {
  const students = state.auditData.students || [];
  return `
    <div class="table-wrap audit-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Student</th>
            <th>Index number</th>
            <th>Logged in</th>
            <th>Exam</th>
            <th>Exam started</th>
            <th>Exam ended</th>
            <th>Submitted</th>
            <th>Submission</th>
            <th>Score</th>
            <th>Device used</th>
            <th>IP address</th>
            <th>Flags</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${students.map((record) => `
            <tr>
              <td>${esc(record.name)}</td>
              <td>${esc(record.indexNumber || "")}</td>
              <td>${auditDateCell(record.loginDateTime)}</td>
              <td>${esc(record.examTitle || "")}</td>
              <td>${auditDateCell(record.examStartedAt)}</td>
              <td>${auditDateCell(record.examEndedAt)}</td>
              <td>${auditDateCell(record.submittedAt, "Not submitted")}</td>
              <td>${auditSubmissionPill(record.submissionMode)}</td>
              <td>${auditScoreHtml(record)}</td>
              <td class="audit-device-cell" title="${esc(record.deviceUsed || "")}">${esc(record.deviceUsed || "")}</td>
              <td>${esc(record.ipAddress || "")}</td>
              <td class="audit-flag-list">${studentAuditFlags(record)}</td>
              <td><button class="button-danger" type="button" data-delete-audit="${esc(record.id)}" data-delete-audit-type="student">Delete</button></td>
            </tr>
          `).join("") || `<tr><td colspan="13">No student audit records yet.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function examinerAuditHtml() {
  const examiners = state.auditData.examiners || [];
  return `
    <div class="table-wrap audit-table-wrap">
      <table>
        <thead>
          <tr>
            <th>User type</th>
            <th>Name</th>
            <th>ID / Username</th>
            <th>Activity</th>
            <th>Target</th>
            <th>Date and time</th>
            <th>Time out</th>
            <th>Device used</th>
            <th>IP address</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${examiners.map((record) => `
            <tr>
              <td><span class="status-pill">${esc(auditUserTypeLabel(record.userType))}</span></td>
              <td>${esc(record.name)}</td>
              <td>${esc(record.identifier || "")}</td>
              <td>${esc(record.action || "")}</td>
              <td>${record.target ? esc(record.target) : `<span class="muted">None</span>`}</td>
              <td>${auditDateCell(record.occurredAt)}</td>
              <td>${record.recordType === "login" && !record.timeOut ? `<span class="status-pill warn">Active</span>` : auditDateCell(record.timeOut, "None")}</td>
              <td class="audit-device-cell" title="${esc(record.deviceUsed || "")}">${esc(record.deviceUsed || "")}</td>
              <td>${esc(record.ipAddress || "")}</td>
              <td><button class="button-danger" type="button" data-delete-audit="${esc(record.id)}" data-delete-audit-type="${esc(record.recordType || "login")}">Delete</button></td>
            </tr>
          `).join("") || `<tr><td colspan="10">No administrator audit records yet.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function superAdminAuditHtml() {
  const summary = state.auditData.superAdmin?.summary || {};
  const alerts = state.auditData.superAdmin?.alerts || [];
  const summaryItems = [
    ["Student activity", summary.studentActivity || 0],
    ["Administrator activity", summary.examinerActivity || 0],
    ["Failed logins", summary.failedLogins || 0],
    ["Multiple logins", summary.multipleLogins || 0],
    ["Late logins", summary.lateLogins || 0],
    ["Early exits", summary.earlyExits || 0],
  ];
  return `
    <div class="audit-summary-grid">
      ${summaryItems.map(([label, value], index) => `
        <div class="audit-summary-card color-${index % 4}">
          <strong>${esc(value)}</strong>
          <span>${esc(label)}</span>
        </div>
      `).join("")}
    </div>
    <div class="table-wrap audit-table-wrap">
      <table>
        <thead>
          <tr>
            <th>Category</th>
            <th>User type</th>
            <th>Name</th>
            <th>ID / Index</th>
            <th>Date and time</th>
            <th>Detail</th>
            <th>Device used</th>
            <th>IP address</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${alerts.map((record) => `
            <tr>
              <td><span class="status-pill ${record.category === "Failed login" || record.category === "Early exit" ? "bad" : "warn"}">${esc(record.category)}</span></td>
              <td>${esc(auditUserTypeLabel(record.userType))}</td>
              <td>${esc(record.name || "")}</td>
              <td>${esc(record.identifier || "")}</td>
              <td>${auditDateCell(record.time)}</td>
              <td>${esc(record.detail || "")}</td>
              <td class="audit-device-cell" title="${esc(record.deviceUsed || "")}">${esc(record.deviceUsed || "")}</td>
              <td>${esc(record.ipAddress || "")}</td>
              <td><button class="button-danger" type="button" data-delete-audit="${esc(record.id)}" data-delete-audit-type="${esc(record.recordType || "login")}">Delete</button></td>
            </tr>
          `).join("") || `<tr><td colspan="9">No Super Administrator alerts yet.</td></tr>`}
        </tbody>
      </table>
    </div>
  `;
}

function bindAuditTrailTab() {
  document.querySelectorAll("[data-audit-view]").forEach((button) => {
    button.addEventListener("click", () => {
      state.auditView = button.dataset.auditView;
      renderAdmin("audit");
    });
  });
  const clearButton = document.getElementById("clearAuditTrail");
  if (clearButton) {
    clearButton.addEventListener("click", async () => {
      if (!window.confirm("Delete all audit trail records?")) return;
      try {
        await api("/api/admin/audit", { method: "DELETE" });
        setMessage("Audit trail records deleted.", "info");
        await renderAdmin("audit");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("audit");
      }
    });
  }
  document.querySelectorAll("[data-delete-audit]").forEach((button) => {
    button.addEventListener("click", async () => {
      if (!window.confirm("Delete this audit record?")) return;
      try {
        const type = button.dataset.deleteAuditType || "login";
        await api(`/api/admin/audit/${encodeURIComponent(type)}/${encodeURIComponent(button.dataset.deleteAudit)}`, { method: "DELETE" });
        setMessage("Audit record deleted.", "info");
        await renderAdmin("audit");
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("audit");
      }
    });
  });
}

function connectTabHtml() {
  if (state.user?.role !== "admin") {
    return `<section class="panel"><h2 class="section-title">Connect Devices</h2><p class="muted">Only the administrator can share the access link.</p></section>`;
  }
  return `${networkAccessHtml()}${connectedDevicesHtml()}`;
}

function userTypeLabel(value) {
  const labels = {
    admin: "Super Administrator",
    examiner: "Examiner",
    candidate: "Student",
  };
  return labels[value] || value || "Unknown";
}

function connectedDevicesHtml() {
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Connected Devices</h2>
          <p>${state.connectedDevices.length} active connection${state.connectedDevices.length === 1 ? "" : "s"} on this host.</p>
        </div>
        <button class="button-plain" type="button" data-refresh-network>Refresh</button>
      </div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>User type</th>
              <th>Name</th>
              <th>ID / Username</th>
              <th>Device</th>
              <th>IP address</th>
              <th>Current exam</th>
              <th>Last seen</th>
            </tr>
          </thead>
          <tbody>
            ${state.connectedDevices.map((item) => `
              <tr>
                <td><span class="status-pill ${item.examTitle ? "warn" : ""}">${esc(userTypeLabel(item.userType))}</span></td>
                <td>${esc(item.name)}</td>
                <td>${esc(item.identifier || "")}</td>
                <td class="audit-device-cell" title="${esc(item.deviceUsed || "")}">${esc(item.deviceUsed || "")}</td>
                <td>${esc(item.ipAddress || "")}</td>
                <td>${item.examTitle ? `${esc(item.examTitle)}<br><span class="muted">Due ${esc(formatDate(item.attemptDueAt))}</span>` : `<span class="muted">None</span>`}</td>
                <td>${esc(formatDate(item.lastSeen))}</td>
              </tr>
            `).join("") || `<tr><td colspan="7">No active device connections.</td></tr>`}
          </tbody>
        </table>
      </div>
    </section>
  `;
}

function settingsTabHtml() {
  if (state.user?.role !== "admin") {
    return `<section class="panel"><h2 class="section-title">Host Settings</h2><p class="muted">Only the administrator can change host login settings.</p></section>`;
  }
  return `
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Host Settings</h2>
          <p>Modify the administrator login username and password.</p>
        </div>
      </div>
      <form class="form-grid" id="credentialsForm">
        <label>Administrator username
          <input name="username" autocomplete="username" value="${esc(state.user?.username || "")}" required>
        </label>
        <label>Current password
          <input name="currentPassword" type="password" autocomplete="current-password" required>
        </label>
        <div class="two-grid">
          <label>New password
            <input name="newPassword" type="password" autocomplete="new-password" minlength="8">
          </label>
          <label>Confirm new password
            <input name="confirmPassword" type="password" autocomplete="new-password" minlength="8">
          </label>
        </div>
        <button type="submit">Save Login Settings</button>
      </form>
    </section>
    <section class="panel stack">
      <div class="panel-header">
        <div>
          <h2>Database Backup</h2>
          <p>Download or restore the local SQLite database.</p>
        </div>
        <button type="button" id="downloadBackup">Download Backup</button>
      </div>
      <form class="form-grid" id="restoreForm">
        <label>Restore backup
          <input name="file" type="file" accept=".sqlite3,.db" required>
        </label>
        <button class="button-danger" type="submit">Restore Database</button>
      </form>
    </section>
  `;
}

function bindSettingsTab() {
  const form = document.getElementById("credentialsForm");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(form));
    try {
      const response = await api("/api/admin/settings/credentials", {
        method: "PUT",
        body: data,
      });
      state.user = response.user;
      setMessage("Administrator login settings updated.", "info");
      await renderAdmin("settings");
    } catch (error) {
      setMessage(error.message, "error");
      await renderAdmin("settings");
    }
  });

  const backupButton = document.getElementById("downloadBackup");
  if (backupButton) {
    backupButton.addEventListener("click", () => {
      window.location.href = "/api/admin/backup.sqlite3";
    });
  }

  const restoreForm = document.getElementById("restoreForm");
  if (restoreForm) {
    restoreForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!window.confirm("Restore this backup now? Current data will be replaced.")) return;
      const formData = new FormData(restoreForm);
      try {
        await api("/api/admin/restore", { method: "POST", body: formData });
        setMessage("Database restored. Please log in again if your session changed.", "info");
        state.user = null;
        renderAuth();
      } catch (error) {
        setMessage(error.message, "error");
        await renderAdmin("settings");
      }
    });
  }
}

window.addEventListener("beforeunload", (event) => {
  if (!examIsActive()) return;
  logExamEvent("attempt_leave", { source: "beforeunload" });
  event.preventDefault();
  event.returnValue = "";
});

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    logExamEvent("tab_hidden", { source: "visibilitychange" });
  }
});

window.addEventListener("blur", () => {
  logExamEvent("window_blur", { source: "window" });
});

window.addEventListener("online", () => {
  setConnectionStatus("online");
});

window.addEventListener("offline", () => {
  setConnectionStatus("offline");
});

["copy", "paste", "cut", "contextmenu"].forEach((type) => {
  document.addEventListener(type, (event) => {
    if (!examIsActive()) return;
    event.preventDefault();
    logExamEvent(type, { source: "document" });
  });
});

async function boot() {
  try {
    const response = await api("/api/me");
    state.user = response.user;
    if (state.user.role === "admin") {
      await renderAdmin("exams");
    } else {
      await renderCandidateHome();
    }
  } catch (_) {
    renderAuth();
  }
}

boot();

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    const hadController = Boolean(navigator.serviceWorker.controller);
    let refreshing = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (!hadController || refreshing) return;
      refreshing = true;
      window.location.reload();
    });
    navigator.serviceWorker.register("/service-worker.js")
      .then((registration) => {
        if (typeof registration.update === "function") registration.update();
      })
      .catch(() => {});
  });
}
