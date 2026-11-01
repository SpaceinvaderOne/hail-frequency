/* HailFrequency — Main App Controller */

const HF = {
    authenticated: false,
    setupComplete: false,
    currentTab: "voice",
    role: "user",           // "admin" | "user", set at login/status
    activeDevice: "",       // device name, restored by loadActiveDevices
    activeServerId: null,   // server the selected device belongs to
    defaultServerId: null,
};

// ── Theming ─────────────────────────────────────────────────────────

const HF_THEMES = ["lcars", "holo", "nostromo", "discovery", "dradis", "2019", "thermal", "1997", "1138"];

function applyTheme(theme) {
    if (!HF_THEMES.includes(theme)) theme = "lcars";
    document.body.dataset.theme = theme;
    try { localStorage.setItem("hf_theme", theme); } catch { /* private mode */ }
}

// XSS-safe text helper
function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
}

// Fetch wrapper that handles 401
async function apiFetch(url, options = {}) {
    const resp = await fetch(url, options);
    if (resp.status === 401) {
        HF.authenticated = false;
        renderLogin();
        return null;
    }
    return resp;
}

// POST JSON helper (service test endpoints are POST-only)
function postJSON(url, payload = {}) {
    return apiFetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
    });
}

// ── Rendering ──────────────────────────────────────────────────────

function renderApp() {
    const app = document.getElementById("app");
    app.innerHTML = `
        <header class="app-header">
            <div class="lcars-elbow"></div>
            <span class="app-title">HailFrequency</span>
            <img src="/static/img/icon.png" alt="HailFrequency" class="logo">
            <span class="connection-status" id="conn-status"></span>
        </header>
        <div class="lcars-color-bar">
            <div class="lcars-bar-seg seg-lavender"></div>
            <div class="lcars-bar-seg seg-amber"></div>
            <div class="lcars-bar-seg seg-salmon"></div>
            <div class="lcars-bar-seg seg-gold"></div>
            <div class="lcars-bar-seg seg-blue"></div>
        </div>
        <main class="app-main">
            <div class="lcars-sidebar">
                <div class="lcars-side-block sb-lavender"></div>
                <div class="lcars-side-block sb-salmon"></div>
                <div class="lcars-side-block sb-amber"></div>
                <div class="lcars-side-block sb-gold"></div>
                <div class="lcars-side-block sb-blue"></div>
            </div>
            <div class="app-content">
                <div class="tab-content voice-tab active" id="tab-voice">
                    <div class="voice-content-area">
                        <div class="device-selector" id="device-selector">
                            <select id="active-device">
                                <option value="">Loading devices...</option>
                            </select>
                            <button class="device-refresh" id="device-refresh" aria-label="Refresh devices">
                                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M23 4v6h-6"/><path d="M1 20v-6h6"/>
                                    <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
                                </svg>
                            </button>
                        </div>
                        <div class="now-playing-card" id="now-playing"></div>
                        <div class="response-card" id="response-card">
                            <div class="transcript"></div>
                            <div class="result"></div>
                        </div>
                    </div>
                    <div class="voice-mic-area">
                        <div class="status-text" id="status-text">Hold mic to speak</div>
                        <div class="mic-container">
                            <button class="mic-button" id="mic-button" aria-label="Hold to talk">
                                <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"/>
                                    <path d="M19 10v2a7 7 0 0 1-14 0v-2"/>
                                    <line x1="12" y1="19" x2="12" y2="23"/>
                                    <line x1="8" y1="23" x2="16" y2="23"/>
                                </svg>
                            </button>
                            <div class="mic-pulse"></div>
                            <div class="mic-pulse"></div>
                        </div>
                        <div class="waveform" id="waveform"></div>
                    </div>
                </div>
                <div class="tab-content browse-tab" id="tab-browse">
                    <div class="browse-search">
                        <input id="browse-search-input" type="search"
                               placeholder="Search your library..."
                               autocomplete="off">
                    </div>
                    <div id="browse-content" class="browse-content"></div>
                </div>
                <div class="tab-content history-tab" id="tab-history">
                    <div class="empty-state" id="history-content">Loading history...</div>
                </div>
                <div class="tab-content settings-tab" id="tab-settings">
                    <div id="settings-content"></div>
                </div>
            </div>
        </main>
        <nav class="bottom-nav">
            <button data-tab="voice" class="active nav-amber">Voice</button>
            <button data-tab="browse" class="nav-salmon">Browse</button>
            <button data-tab="history" class="nav-blue">History</button>
            <button data-tab="settings" class="nav-gold">Settings</button>
        </nav>
    `;

    if (typeof HFBrowse !== "undefined") HFBrowse.initSearch();

    // Create waveform bars
    const waveform = document.getElementById("waveform");
    for (let i = 0; i < 32; i++) {
        const bar = document.createElement("div");
        bar.className = "waveform-bar";
        waveform.appendChild(bar);
    }

    // Tab navigation
    document.querySelectorAll(".bottom-nav button").forEach((btn) => {
        btn.addEventListener("click", () => switchTab(btn.dataset.tab));
    });

    // Device selector
    document.getElementById("device-refresh").addEventListener("click", loadActiveDevices);
    document.getElementById("active-device").addEventListener("change", (e) => {
        applyDeviceSelection(e.target.value);
    });

    // Init modules
    if (typeof VoiceController !== "undefined") {
        window.voiceCtrl = new VoiceController();
        window.voiceCtrl.init();
    }

    checkConnection();
    loadActiveDevices();

    // Start now-playing polling (voice tab is active by default)
    if (typeof NowPlayingCard !== "undefined") {
        NowPlayingCard.startPolling();
    }
}

function switchTab(tab) {
    HF.currentTab = tab;
    document.querySelectorAll(".tab-content").forEach((el) => el.classList.remove("active"));
    document.querySelectorAll(".bottom-nav button").forEach((el) => el.classList.remove("active"));
    const tabEl = document.getElementById(`tab-${tab}`);
    if (tabEl) tabEl.classList.add("active");
    const btnEl = document.querySelector(`.bottom-nav button[data-tab="${tab}"]`);
    if (btnEl) btnEl.classList.add("active");

    // Start/stop now-playing polling based on tab
    if (typeof NowPlayingCard !== "undefined") {
        if (tab === "voice") {
            NowPlayingCard.startPolling();
        } else {
            NowPlayingCard.stopPolling();
        }
    }

    // Load tab data on first switch
    if (tab === "history") loadHistory();
    if (tab === "settings" && typeof SettingsPanel !== "undefined") SettingsPanel.load();
    if (tab === "browse") ensureBrowseLoaded(() => HFBrowse.load());
}

// Load browse.js on demand if the cached app shell didn't include its
// script tag (service-worker cache lag), instead of silently doing nothing
function ensureBrowseLoaded(cb) {
    if (typeof HFBrowse !== "undefined") {
        cb();
        return;
    }
    const s = document.createElement("script");
    s.src = "/static/js/browse.js";
    s.onload = () => {
        if (typeof HFBrowse !== "undefined") {
            HFBrowse.initSearch();
            cb();
        }
    };
    document.body.appendChild(s);
}

function renderLogin() {
    const app = document.getElementById("app");
    app.innerHTML = `
        <header class="app-header">
            <div class="lcars-elbow"></div>
            <span class="app-title">HailFrequency</span>
            <img src="/static/img/icon.png" alt="HailFrequency" class="logo">
        </header>
        <div class="lcars-color-bar">
            <div class="lcars-bar-seg seg-lavender"></div>
            <div class="lcars-bar-seg seg-amber"></div>
            <div class="lcars-bar-seg seg-salmon"></div>
            <div class="lcars-bar-seg seg-gold"></div>
            <div class="lcars-bar-seg seg-blue"></div>
        </div>
        <div class="login-screen">
            <img src="/static/img/icon.png" alt="HailFrequency" class="logo-large">
            <h1>HailFrequency</h1>
            <p class="tagline">Hailing frequencies open.</p>
            <div class="form-group">
                <label for="login-user">Username</label>
                <input type="text" id="login-user" placeholder="admin" autocomplete="username">
            </div>
            <div class="form-group">
                <label for="login-pass">Password</label>
                <input type="password" id="login-pass" placeholder="Password" autocomplete="current-password">
            </div>
            <div class="error-text hidden" id="login-error"></div>
            <button class="btn btn-primary btn-full" id="login-btn">Log In</button>
        </div>
    `;

    const loginBtn = document.getElementById("login-btn");
    const passInput = document.getElementById("login-pass");

    loginBtn.addEventListener("click", doLogin);
    passInput.addEventListener("keydown", (e) => {
        if (e.key === "Enter") doLogin();
    });
}

async function doLogin() {
    const username = document.getElementById("login-user").value || "admin";
    const password = document.getElementById("login-pass").value;
    const errorEl = document.getElementById("login-error");
    const btn = document.getElementById("login-btn");

    errorEl.classList.add("hidden");
    btn.disabled = true;
    btn.textContent = "Logging in...";

    try {
        const resp = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ username, password }),
        });

        if (resp.ok) {
            const data = await resp.json();
            HF.authenticated = true;
            HF.role = data.role || "admin";
            renderApp();
        } else {
            errorEl.textContent = "Invalid credentials";
            errorEl.classList.remove("hidden");
        }
    } catch {
        errorEl.textContent = "Connection error";
        errorEl.classList.remove("hidden");
    }

    btn.disabled = false;
    btn.textContent = "Log In";
}

async function checkConnection() {
    const dot = document.getElementById("conn-status");
    if (!dot) return;
    try {
        const resp = await fetch("/api/health");
        dot.classList.toggle("disconnected", !resp.ok);
    } catch {
        dot.classList.add("disconnected");
    }
}

// ── Device selector (all servers, grouped) ──────────────────────────

function applyDeviceSelection(value) {
    HF.activeServerId = null;
    HF.activeDevice = "";
    if (value) {
        try {
            [HF.activeServerId, HF.activeDevice] = JSON.parse(value);
        } catch { /* malformed — treat as Auto */ }
    }
    try { sessionStorage.setItem("hf_active_device_v2", value || ""); } catch { /* private mode */ }
}

async function loadActiveDevices() {
    const select = document.getElementById("active-device");
    if (!select) return;

    // Always re-fetch — the default server can change in Settings
    let servers = [];
    try {
        const resp = await apiFetch("/api/settings");
        if (!resp) return;
        const data = await resp.json();
        // Server-stored theme is the source of truth once logged in
        if (data.ui_theme) applyTheme(data.ui_theme);
        servers = data.servers || [];
        // Cache ollama engine type so the voice UI can tailor its status messages
        HF.ollamaEngine = data.ollama_engine || "unknown";
    } catch {
        return;
    }

    const def = servers.find((s) => s.is_default) || servers[0];
    HF.defaultServerId = def ? def.id : null;

    if (!servers.length) {
        select.innerHTML = '<option value="">No server configured</option>';
        applyDeviceSelection("");
        return;
    }

    // Fetch devices from EVERY server in parallel; a down server just
    // contributes no devices rather than breaking the dropdown
    const results = await Promise.all(servers.map(async (s) => {
        try {
            const r = await apiFetch(`/api/media/${s.id}/devices`);
            if (!r || !r.ok) return { server: s, devices: [] };
            const d = await r.json();
            return { server: s, devices: d.devices || [] };
        } catch {
            return { server: s, devices: [] };
        }
    }));

    select.innerHTML = "";
    // Adaptive: with a single server there's no need for group headers or
    // a server name on the Auto option — the UI stays flat and simple
    const single = servers.length === 1;
    const auto = document.createElement("option");
    auto.value = "";
    auto.textContent = single ? "Auto" : `Auto (${def ? def.name : "default"})`;
    select.appendChild(auto);

    let firstDefaultValue = "";
    for (const { server, devices } of results) {
        if (!devices.length) continue;
        let parent = select;
        if (!single) {
            parent = document.createElement("optgroup");
            parent.label = server.name;
            select.appendChild(parent);
        }
        devices.forEach((d) => {
            const opt = document.createElement("option");
            opt.value = JSON.stringify([server.id, d.device_name]);
            opt.textContent = d.now_playing
                ? `${d.device_name} (${d.client}) - ${d.now_playing}`
                : `${d.device_name} (${d.client})`;
            parent.appendChild(opt);
            if (!firstDefaultValue && server.id === HF.defaultServerId) {
                firstDefaultValue = opt.value;
            }
        });
    }

    // Restore previous selection if still present; otherwise first device
    // of the default server; otherwise Auto
    let stored = "";
    try { stored = sessionStorage.getItem("hf_active_device_v2") || ""; } catch { /* private mode */ }
    const values = Array.from(select.querySelectorAll("option")).map((o) => o.value);
    if (stored && values.includes(stored)) {
        select.value = stored;
    } else {
        select.value = firstDefaultValue || "";
    }
    applyDeviceSelection(select.value);
}

// ── History tab ─────────────────────────────────────────────────────

async function loadHistory() {
    const container = document.getElementById("history-content");
    if (!container) return;
    try {
        const resp = await apiFetch("/api/history?limit=30");
        if (!resp) return;
        const data = await resp.json();
        if (!data.length) {
            container.innerHTML = '<div class="empty-state">No commands yet. Try speaking!</div>';
            return;
        }
        container.innerHTML = "";
        container.className = "";
        const badgeNames = { fast: "FAST", learned: "LEARNED",
                             ollama: "AI", fallback: "SEARCH" };
        data.forEach((item) => {
            const div = document.createElement("div");
            div.className = `history-item${item.success ? "" : " failed"}`;
            if (item.parse_source && badgeNames[item.parse_source]) {
                const badge = document.createElement("span");
                badge.className = `parse-badge parse-${item.parse_source}`;
                badge.textContent = badgeNames[item.parse_source];
                div.appendChild(badge);
            }
            const ts = document.createElement("div");
            ts.className = "timestamp";
            ts.textContent = item.username
                ? `${item.timestamp || ""}  ·  ${item.username}`
                : (item.timestamp || "");
            const cmd = document.createElement("div");
            cmd.className = "command";
            cmd.textContent = item.transcript || "(no transcript)";
            const res = document.createElement("div");
            res.className = "action-result";
            res.textContent = item.result || "";
            div.appendChild(ts);
            div.appendChild(cmd);
            div.appendChild(res);
            container.appendChild(div);
        });
    } catch {
        container.innerHTML = '<div class="empty-state">Failed to load history.</div>';
    }
}

// ── Init ────────────────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
    // Apply cached theme immediately — covers the login screen too
    let cachedTheme = "lcars";
    try { cachedTheme = localStorage.getItem("hf_theme") || "lcars"; } catch { /* private mode */ }
    applyTheme(cachedTheme);

    // Register service worker
    if ("serviceWorker" in navigator) {
        navigator.serviceWorker.register("/static/sw.js").catch(() => {});
    }

    // Check setup status first (unauthenticated)
    try {
        const setupResp = await fetch("/api/setup/status");
        const setup = await setupResp.json();

        if (!setup.setup_complete) {
            if (typeof SetupWizard !== "undefined") {
                const wizard = new SetupWizard();
                wizard.init(setup.current_step);
            }
            return;
        }
    } catch {
        // If we can't reach the API, show login anyway
    }

    // Check auth status
    try {
        const authResp = await fetch("/api/auth/status");
        const auth = await authResp.json();
        if (auth.authenticated) {
            HF.authenticated = true;
            HF.role = auth.role || "admin";
            renderApp();
            return;
        }
    } catch {
        // Fall through to login
    }

    renderLogin();
});
