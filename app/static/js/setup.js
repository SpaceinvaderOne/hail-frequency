/* HailFrequency — Setup Wizard */

class SetupWizard {
    constructor() {
        this.currentStep = 0;
        this.totalSteps = 5;
        this.data = {
            serverId: null,
            discoveredDevices: [],
        };
    }

    init(startStep) {
        this.currentStep = startStep || 0;
        this.render();
    }

    // Steps 2-5 require the admin session that step 1 establishes. A wizard
    // reopened in a browser without that cookie gets a 401 — send it back to
    // step 1, where the existing-password field appears. Returns true when
    // the caller should stop handling the response.
    _handleSetupResponse(resp) {
        if (resp.status !== 401) return false;
        this.currentStep = 0;
        this.render();
        return true;
    }

    render() {
        const app = document.getElementById("app");
        const steps = [
            { title: "Set Up Authentication", desc: "Create your login credentials." },
            { title: "Connect Speaches", desc: "Speech-to-text service for voice commands." },
            { title: "Connect Ollama", desc: "LLM for understanding your commands." },
            { title: "Add Media Server", desc: "Connect your Emby or Jellyfin server." },
            { title: "Setup Complete", desc: "Hailing frequencies open." },
        ];

        const step = steps[this.currentStep] || steps[0];

        // Step dots
        let dots = "";
        for (let i = 0; i < this.totalSteps; i++) {
            const cls = i < this.currentStep ? "done" : i === this.currentStep ? "active" : "";
            dots += `<div class="setup-step-dot ${cls}"></div>`;
        }

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
            <div class="setup-screen">
                <div class="setup-steps">${dots}</div>
                <h2 class="setup-title">${escapeHtml(step.title)}</h2>
                <p class="setup-description">${escapeHtml(step.desc)}</p>
                <div class="setup-form" id="setup-form"></div>
            </div>
        `;

        const form = document.getElementById("setup-form");
        switch (this.currentStep) {
            case 0: this.renderAuthStep(form); break;
            case 1: this.renderSpeachesStep(form); break;
            case 2: this.renderOllamaStep(form); break;
            case 3: this.renderMediaServerStep(form); break;
            case 4: this.renderCompleteStep(form); break;
        }
    }

    // ── Step 0: Auth ────────────────────────────────────────────────

    renderAuthStep(form) {
        form.innerHTML = `
            <div class="form-group">
                <label for="setup-user">Username</label>
                <input type="text" id="setup-user" value="admin" autocomplete="username">
            </div>
            <div class="form-group">
                <label for="setup-pass">Password (min 8 characters)</label>
                <input type="password" id="setup-pass" placeholder="Choose a password" autocomplete="new-password">
            </div>
            <div class="form-group">
                <label for="setup-pass2">Confirm Password</label>
                <input type="password" id="setup-pass2" placeholder="Confirm password" autocomplete="new-password">
            </div>
            <div class="error-text hidden" id="setup-error"></div>
            <button class="btn btn-primary btn-full" id="setup-next">Continue</button>
        `;

        document.getElementById("setup-next").addEventListener("click", () => this.submitAuth());
    }

    _showCurrentPasswordField() {
        if (document.getElementById("setup-current-password")) return;
        const form = document.getElementById("setup-form");
        const errorEl = document.getElementById("setup-error");
        const group = document.createElement("div");
        group.className = "form-group";
        group.id = "setup-current-password-group";
        const label = document.createElement("label");
        label.setAttribute("for", "setup-current-password");
        label.textContent = "Existing admin password";
        const input = document.createElement("input");
        input.type = "password";
        input.id = "setup-current-password";
        input.placeholder = "Existing admin password";
        input.autocomplete = "current-password";
        group.appendChild(label);
        group.appendChild(input);
        form.insertBefore(group, errorEl);
    }

    async submitAuth() {
        const username = document.getElementById("setup-user").value || "admin";
        const password = document.getElementById("setup-pass").value;
        const password2 = document.getElementById("setup-pass2").value;
        const currentPassEl = document.getElementById("setup-current-password");
        const currentPassword = currentPassEl ? currentPassEl.value : "";
        const errorEl = document.getElementById("setup-error");
        const btn = document.getElementById("setup-next");

        errorEl.classList.add("hidden");

        if (password.length < 8) {
            errorEl.textContent = "Password must be at least 8 characters.";
            errorEl.classList.remove("hidden");
            return;
        }
        if (password !== password2) {
            errorEl.textContent = "Passwords do not match.";
            errorEl.classList.remove("hidden");
            return;
        }

        btn.disabled = true;
        btn.textContent = "Saving...";

        try {
            const body = { username, password };
            if (currentPassword) body.current_password = currentPassword;
            const resp = await fetch("/api/setup/auth", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body),
            });
            if (resp.ok) {
                this.currentStep = 1;
                this.render();
            } else {
                const data = await resp.json();
                errorEl.textContent = data.error || "Failed to save.";
                errorEl.classList.remove("hidden");
                if (data.claim_required) {
                    this._showCurrentPasswordField();
                }
            }
        } catch {
            errorEl.textContent = "Connection error.";
            errorEl.classList.remove("hidden");
        }
        btn.disabled = false;
        btn.textContent = "Continue";
    }

    // ── Step 1: Speaches ────────────────────────────────────────────

    renderSpeachesStep(form) {
        form.innerHTML = `
            <div class="form-group">
                <label for="speaches-url">Speaches URL</label>
                <input type="text" id="speaches-url" placeholder="http://192.168.1.50:8000">
            </div>
            <button class="btn btn-secondary btn-full btn-sm" id="speaches-test">Test Connection</button>
            <div class="test-result hidden" id="speaches-test-result"></div>
            <div class="form-group hidden" id="speaches-model-group">
                <label for="speaches-model">STT Model</label>
                <select id="speaches-model"></select>
            </div>
            <div class="error-text hidden" id="setup-error"></div>
            <button class="btn btn-primary btn-full hidden" id="setup-next">Continue</button>
        `;

        document.getElementById("speaches-test").addEventListener("click", () => this.testSpeaches());
        document.getElementById("setup-next").addEventListener("click", () => this.submitSpeaches());
    }

    async testSpeaches() {
        const url = document.getElementById("speaches-url").value.trim();
        const resultEl = document.getElementById("speaches-test-result");
        const modelGroup = document.getElementById("speaches-model-group");
        const nextBtn = document.getElementById("setup-next");

        if (!url) return;

        resultEl.className = "test-result";
        resultEl.textContent = "Testing...";
        resultEl.classList.remove("hidden");

        try {
            const resp = await fetch("/api/setup/speaches", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, model: "placeholder" }),
            });
            if (this._handleSetupResponse(resp)) return;
            const data = await resp.json();

            if (resp.ok && data.models) {
                resultEl.className = "test-result success";
                resultEl.textContent = "Connected!";

                const select = document.getElementById("speaches-model");
                select.innerHTML = "";
                data.models.forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m.id || m.name;
                    opt.textContent = m.id || m.name;
                    select.appendChild(opt);
                });
                modelGroup.classList.remove("hidden");
                nextBtn.classList.remove("hidden");
            } else {
                resultEl.className = "test-result error";
                resultEl.textContent = data.error || "Connection failed.";
            }
        } catch {
            resultEl.className = "test-result error";
            resultEl.textContent = "Connection error.";
        }
    }

    async submitSpeaches() {
        const url = document.getElementById("speaches-url").value.trim();
        const model = document.getElementById("speaches-model").value;
        const btn = document.getElementById("setup-next");
        btn.disabled = true;

        try {
            const resp = await fetch("/api/setup/speaches", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, model }),
            });
            if (this._handleSetupResponse(resp)) return;
            if (resp.ok) {
                this.currentStep = 2;
                this.render();
            }
        } catch { /* ignore */ }
        btn.disabled = false;
    }

    // ── Step 2: Ollama ──────────────────────────────────────────────

    renderOllamaStep(form) {
        form.innerHTML = `
            <div class="form-group">
                <label for="ollama-url">Ollama URL</label>
                <input type="text" id="ollama-url" placeholder="http://192.168.1.50:11434">
            </div>
            <button class="btn btn-secondary btn-full btn-sm" id="ollama-test">Test Connection</button>
            <div class="test-result hidden" id="ollama-test-result"></div>
            <div class="form-group hidden" id="ollama-model-group">
                <label for="ollama-model">Model</label>
                <select id="ollama-model"></select>
            </div>
            <div class="error-text hidden" id="setup-error"></div>
            <button class="btn btn-primary btn-full hidden" id="setup-next">Continue</button>
            <button class="btn btn-secondary btn-full btn-sm" id="ollama-skip">
                Skip — I don't have a GPU
            </button>
            <p class="form-hint">Without Ollama, direct commands still work:
            "pause", "play <em>title</em>", "play Foundation season 2
            episode 3", "shuffle Frasier".</p>
            <p class="form-hint">Ollama adds the conversational ones ("that
            Tom Hanks volleyball movie") and naming a device in the command.
            You can add it later in Settings.</p>
        `;

        document.getElementById("ollama-test").addEventListener("click", () => this.testOllama());
        document.getElementById("setup-next").addEventListener("click", () => this.submitOllama());
        document.getElementById("ollama-skip").addEventListener("click",
            () => this.skipOllama());
    }

    async testOllama() {
        const url = document.getElementById("ollama-url").value.trim();
        const resultEl = document.getElementById("ollama-test-result");
        const modelGroup = document.getElementById("ollama-model-group");
        const nextBtn = document.getElementById("setup-next");

        if (!url) return;

        resultEl.className = "test-result";
        resultEl.textContent = "Testing...";
        resultEl.classList.remove("hidden");

        try {
            const resp = await fetch("/api/setup/ollama", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, model: "placeholder" }),
            });
            if (this._handleSetupResponse(resp)) return;
            const data = await resp.json();

            if (resp.ok && data.models) {
                resultEl.className = "test-result success";
                resultEl.textContent = "Connected!";

                const select = document.getElementById("ollama-model");
                select.innerHTML = "";
                data.models.forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m.name;
                    opt.textContent = m.name;
                    select.appendChild(opt);
                });
                modelGroup.classList.remove("hidden");
                nextBtn.classList.remove("hidden");
            } else {
                resultEl.className = "test-result error";
                resultEl.textContent = data.error || "Connection failed.";
            }
        } catch {
            resultEl.className = "test-result error";
            resultEl.textContent = "Connection error.";
        }
    }

    async submitOllama() {
        const url = document.getElementById("ollama-url").value.trim();
        const model = document.getElementById("ollama-model").value;
        const btn = document.getElementById("setup-next");
        btn.disabled = true;

        try {
            const resp = await fetch("/api/setup/ollama", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url, model }),
            });
            if (this._handleSetupResponse(resp)) return;
            if (resp.ok) {
                this.currentStep = 3;
                this.render();
            }
        } catch { /* ignore */ }
        btn.disabled = false;
    }

    async skipOllama() {
        try {
            const resp = await fetch("/api/setup/ollama/skip", { method: "POST" });
            if (this._handleSetupResponse(resp)) return;
            const data = await resp.json();
            if (resp.ok && data.success) {
                this.currentStep = 3;
                this.render();
            }
        } catch { /* leave the wizard on this step */ }
    }

    // ── Step 3: Media Server ────────────────────────────────────────

    renderMediaServerStep(form) {
        form.innerHTML = `
            <div class="form-group">
                <label for="server-name">Server Name</label>
                <input type="text" id="server-name" placeholder="My Emby Server">
            </div>
            <div class="form-group">
                <label for="server-type">Server Type</label>
                <select id="server-type">
                    <option value="emby">Emby</option>
                    <option value="jellyfin">Jellyfin</option>
                </select>
            </div>
            <div class="form-group">
                <label for="server-url">Server URL</label>
                <input type="text" id="server-url" placeholder="http://192.168.1.50:8096">
            </div>
            <div class="form-group">
                <label for="server-key">API Key</label>
                <input type="text" id="server-key" placeholder="Your API key">
            </div>
            <button class="btn btn-secondary btn-full btn-sm" id="server-test">Test Connection</button>
            <div class="test-result hidden" id="server-test-result"></div>
            <div class="form-group hidden" id="server-user-group">
                <label for="server-user">User Account</label>
                <select id="server-user"></select>
            </div>
            <div class="hidden" id="device-alias-section">
                <p class="setup-description" style="margin-top:16px;">Assign friendly names to your devices for voice commands:</p>
                <div class="device-list" id="device-list"></div>
            </div>
            <div class="error-text hidden" id="setup-error"></div>
            <button class="btn btn-primary btn-full hidden" id="setup-next">Continue</button>
        `;

        document.getElementById("server-test").addEventListener("click", () => this.testMediaServer());
        document.getElementById("setup-next").addEventListener("click", () => this.submitMediaServer());
    }

    async testMediaServer() {
        const name = document.getElementById("server-name").value.trim();
        const type = document.getElementById("server-type").value;
        const url = document.getElementById("server-url").value.trim();
        const apiKey = document.getElementById("server-key").value.trim();
        const resultEl = document.getElementById("server-test-result");
        const userGroup = document.getElementById("server-user-group");
        const deviceSection = document.getElementById("device-alias-section");
        const nextBtn = document.getElementById("setup-next");

        if (!url || !apiKey) return;

        resultEl.className = "test-result";
        resultEl.textContent = "Testing...";
        resultEl.classList.remove("hidden");

        try {
            // Test only — does NOT save to DB
            const resp = await fetch("/api/setup/test-media-server", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: name || `My ${type} Server`,
                    type,
                    url,
                    api_key: apiKey,
                }),
            });
            if (this._handleSetupResponse(resp)) return;
            const data = await resp.json();

            if (resp.ok) {
                resultEl.className = "test-result success";
                resultEl.textContent = "Connected!";

                // Show user picker
                if (data.users && data.users.length) {
                    const select = document.getElementById("server-user");
                    select.innerHTML = "";
                    data.users.forEach((u) => {
                        const opt = document.createElement("option");
                        opt.value = u.id;
                        opt.textContent = u.name;
                        select.appendChild(opt);
                    });
                    userGroup.classList.remove("hidden");
                }

                // Show device discovery for alias setup
                if (data.devices && data.devices.length) {
                    this.data.discoveredDevices = data.devices;
                    const deviceList = document.getElementById("device-list");
                    deviceList.innerHTML = "";
                    data.devices.forEach((d) => {
                        const item = document.createElement("div");
                        item.className = "device-item";
                        const nameSpan = document.createElement("span");
                        nameSpan.className = "device-name";
                        nameSpan.textContent = `${d.device_name} (${d.client})`;
                        const input = document.createElement("input");
                        input.type = "text";
                        input.placeholder = "alias";
                        input.dataset.deviceName = d.device_name;
                        item.appendChild(nameSpan);
                        item.appendChild(input);
                        deviceList.appendChild(item);
                    });
                    deviceSection.classList.remove("hidden");
                }

                nextBtn.classList.remove("hidden");
            } else {
                resultEl.className = "test-result error";
                resultEl.textContent = data.error || "Connection failed.";
            }
        } catch {
            resultEl.className = "test-result error";
            resultEl.textContent = "Connection error.";
        }
    }

    async submitMediaServer() {
        const btn = document.getElementById("setup-next");
        btn.disabled = true;

        const name = document.getElementById("server-name").value.trim();
        const type = document.getElementById("server-type").value;
        const url = document.getElementById("server-url").value.trim();
        const apiKey = document.getElementById("server-key").value.trim();
        const userSelect = document.getElementById("server-user");
        const selectedUserId = userSelect ? userSelect.value : null;
        const selectedUserName = userSelect ? userSelect.options[userSelect.selectedIndex]?.textContent : null;

        // Save server to DB with selected user
        try {
            const resp = await fetch("/api/setup/media-server", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    name: name || `My ${type} Server`,
                    type,
                    url,
                    api_key: apiKey,
                    user_id: selectedUserId || null,
                    user_name: selectedUserName || null,
                }),
            });
            if (this._handleSetupResponse(resp)) return;
            const data = await resp.json();

            if (resp.ok) {
                this.data.serverId = data.server_id;

                // Save device aliases
                const aliasInputs = document.querySelectorAll("#device-list input");
                const aliases = [];
                aliasInputs.forEach((input) => {
                    const alias = input.value.trim();
                    if (alias) {
                        aliases.push({
                            alias,
                            pattern: input.dataset.deviceName,
                        });
                    }
                });

                if (aliases.length && this.data.serverId) {
                    const aliasResp = await fetch("/api/setup/device-aliases", {
                        method: "POST",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({
                            server_id: this.data.serverId,
                            aliases,
                        }),
                    });
                    if (this._handleSetupResponse(aliasResp)) return;
                }

                this.currentStep = 4;
                this.render();
            }
        } catch { /* ignore */ }
        btn.disabled = false;
    }

    // ── Step 4: Complete ────────────────────────────────────────────

    renderCompleteStep(form) {
        form.innerHTML = `
            <div style="text-align:center; padding: 24px 0;">
                <img src="/static/img/icon.png" alt="HailFrequency" style="width:80px; height:80px; border-radius:16px; margin-bottom:16px;">
                <p style="font-size:18px; font-weight:600; margin-bottom:8px;">Ready to go!</p>
                <p style="color:var(--text-secondary); font-size:14px;">Your HailFrequency instance is configured.</p>
            </div>
            <button class="btn btn-primary btn-full" id="setup-finish">Open HailFrequency</button>
        `;

        document.getElementById("setup-finish").addEventListener("click", async () => {
            try {
                const resp = await fetch("/api/setup/complete", { method: "POST" });
                if (this._handleSetupResponse(resp)) return;
            } catch { /* ignore */ }
            // Redirect to login
            window.location.reload();
        });
    }
}
