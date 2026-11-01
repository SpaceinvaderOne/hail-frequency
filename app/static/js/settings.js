/* HailFrequency — Settings Panel */

const KOKORO_VOICES = [
    "af_alloy", "af_aoede", "af_bella", "af_heart", "af_jessica",
    "af_kore", "af_nicole", "af_nova", "af_river", "af_sarah", "af_sky",
    "am_adam", "am_michael",
    "bf_emma", "bf_isabella",
    "bm_george", "bm_lewis",
];

const SettingsPanel = {
    _data: null,
    _editing: null, // "speaches" | "ollama" | "tts" | "server:<id>" | "add-server" | "password"
    _serviceStatus: {}, // { speaches: 'unknown', ollama: 'unknown', 'server:abc': 'unknown' }

    async load() {
        const container = document.getElementById("settings-content");
        if (!container) return;
        try {
            const resp = await apiFetch("/api/settings");
            if (!resp) return;
            this._data = await resp.json();
            this._initStatus();
            this.render(container);
        } catch {
            container.innerHTML = '<div class="empty-state">Failed to load settings.</div>';
        }
    },

    _initStatus() {
        this._serviceStatus = {};
        if (this._data.speaches_url) this._serviceStatus.speaches = "unknown";
        if (this._data.ollama_url) this._serviceStatus.ollama = "unknown";
        if (this._data.tts_enabled && this._data.tts_url) this._serviceStatus.tts = "unknown";
        (this._data.servers || []).forEach((s) => {
            this._serviceStatus[`server:${s.id}`] = "unknown";
        });
    },

    _setStatus(key, status) {
        this._serviceStatus[key] = status;
        const dot = document.querySelector(`.status-dot[data-key="${key}"]`);
        if (dot) {
            dot.className = `status-dot ${status === "unknown" ? "" : status}`;
        }
    },

    async _runTest(button, testFn, statusKey) {
        const originalText = button.textContent;
        button.disabled = true;
        button.textContent = "Testing\u2026";
        this._setStatus(statusKey, "testing");
        try {
            const result = await testFn();
            button.textContent = "\u2713 Connected";
            button.style.color = "var(--success)";
            this._setStatus(statusKey, "connected");
            setTimeout(() => { button.textContent = originalText; button.style.color = ""; button.disabled = false; }, 2000);
            return result;
        } catch {
            button.textContent = "\u2717 Failed";
            button.style.color = "var(--danger)";
            this._setStatus(statusKey, "failed");
            setTimeout(() => { button.textContent = originalText; button.style.color = ""; button.disabled = false; }, 2000);
            return null;
        }
    },

    render(container) {
        container.innerHTML = "";
        // Non-admins get Appearance + account info only — the backend
        // enforces this too; hiding it here is just honest UI
        if (this._data.role === "user") {
            this.renderAppearance(container);
            this._renderLearningSection(container);
            this._renderUserAccount(container);
            this.renderAbout(container);
            return;
        }
        this._renderTestAll(container);
        this.renderServers(container);
        this.renderOllama(container);
        this._renderLearningSection(container);
        this.renderSpeaches(container);
        this.renderTTS(container);
        this.renderAppearance(container);
        this.renderUsers(container);
        this.renderAccount(container);
        this.renderDebugLogging(container);
        this.renderAbout(container);
    },

    renderAbout(container) {
        const about = document.createElement("div");
        about.className = "about-footer";
        const ver = this._data.version || "dev";
        const date = this._data.build_date ? ` · ${this._data.build_date}` : "";
        const name = document.createElement("div");
        name.className = "about-name";
        name.textContent = "HailFrequency";
        const meta = document.createElement("div");
        meta.className = "about-meta";
        meta.textContent = `${ver}${date}`;
        const link = document.createElement("a");
        link.className = "about-link";
        link.href = "https://github.com/SpaceinvaderOne/hail-frequency";
        link.target = "_blank";
        link.rel = "noopener";
        link.textContent = "GitHub / report an issue";
        about.appendChild(name);
        about.appendChild(meta);
        about.appendChild(link);
        container.appendChild(about);
    },

    _renderTestAll(container) {
        const bar = document.createElement("div");
        bar.className = "test-all-bar";
        const btn = document.createElement("button");
        btn.className = "test-all-btn";
        btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg> Test All';
        btn.addEventListener("click", async () => {
            btn.disabled = true;
            btn.innerHTML = '<span class="spinner" style="width:14px;height:14px;border-width:2px;"></span> Testing\u2026';
            const tests = [];
            if (this._data.speaches_url) {
                this._setStatus("speaches", "testing");
                tests.push(
                    postJSON("/api/settings/test-speaches").then(async (r) => {
                        const d = r && await r.json();
                        this._setStatus("speaches", d && d.connected ? "connected" : "failed");
                    }).catch(() => this._setStatus("speaches", "failed"))
                );
            }
            if (this._data.ollama_url) {
                this._setStatus("ollama", "testing");
                tests.push(
                    postJSON("/api/settings/test-ollama").then(async (r) => {
                        const d = r && await r.json();
                        this._setStatus("ollama", d && d.connected ? "connected" : "failed");
                    }).catch(() => this._setStatus("ollama", "failed"))
                );
            }
            if (this._data.tts_enabled && this._data.tts_url) {
                this._setStatus("tts", "testing");
                tests.push(
                    postJSON("/api/settings/test-tts").then(async (r) => {
                        const d = r && await r.json();
                        this._setStatus("tts", d && d.connected ? "connected" : "failed");
                    }).catch(() => this._setStatus("tts", "failed"))
                );
            }
            (this._data.servers || []).forEach((s) => {
                const key = `server:${s.id}`;
                this._setStatus(key, "testing");
                tests.push(
                    apiFetch(`/api/settings/test-media/${s.id}`).then(async (r) => {
                        const d = r && await r.json();
                        this._setStatus(key, d && d.connected ? "connected" : "failed");
                    }).catch(() => this._setStatus(key, "failed"))
                );
            });
            await Promise.allSettled(tests);
            btn.disabled = false;
            btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M2.5 22v-6h6M2 11.5a10 10 0 0 1 18.8-4.3M22 12.5a10 10 0 0 1-18.8 4.2"/></svg> Test All';
        });
        bar.appendChild(btn);
        container.appendChild(bar);
    },

    // ── Helpers ──────────────────────────────────────────────────────

    _createSection(title, statusKey) {
        const section = document.createElement("div");
        section.className = "settings-section";
        const header = document.createElement("div");
        header.className = "section-header";
        const h3 = document.createElement("h3");
        h3.textContent = title;
        if (statusKey && this._serviceStatus[statusKey] !== undefined) {
            const dot = document.createElement("span");
            const status = this._serviceStatus[statusKey];
            dot.className = `status-dot ${status === "unknown" ? "" : status}`;
            dot.dataset.key = statusKey;
            h3.appendChild(document.createTextNode(" "));
            h3.appendChild(dot);
        }
        header.appendChild(h3);
        section.appendChild(header);
        return { section, header };
    },

    _addEditBtn(header, onClick) {
        const btn = document.createElement("button");
        btn.className = "btn btn-secondary btn-sm section-edit-btn";
        btn.textContent = "Edit";
        btn.addEventListener("click", onClick);
        header.appendChild(btn);
    },

    _addRow(parent, label, value) {
        const row = document.createElement("div");
        row.className = "settings-row";
        const labelEl = document.createElement("span");
        labelEl.className = "label";
        labelEl.textContent = label;
        const valueEl = document.createElement("span");
        valueEl.className = "value";
        valueEl.textContent = value;
        row.appendChild(labelEl);
        row.appendChild(valueEl);
        parent.appendChild(row);
    },

    _makeInput(labelText, id, type, value, placeholder) {
        const group = document.createElement("div");
        group.className = "form-group";
        const label = document.createElement("label");
        label.textContent = labelText;
        label.htmlFor = id;
        group.appendChild(label);

        let input;
        if (type === "select") {
            input = document.createElement("select");
        } else {
            input = document.createElement("input");
            input.type = type;
            if (placeholder) input.placeholder = placeholder;
            if (value) input.value = value;
        }
        input.id = id;
        group.appendChild(input);
        return { group, input };
    },

    _makeBtn(text, className, onClick) {
        const btn = document.createElement("button");
        btn.className = className;
        btn.textContent = text;
        if (onClick) btn.addEventListener("click", onClick);
        return btn;
    },

    _makeActions(...buttons) {
        const row = document.createElement("div");
        row.className = "settings-actions";
        buttons.forEach((b) => row.appendChild(b));
        return row;
    },

    _showResult(container, success, message) {
        let el = container.querySelector(".test-result");
        if (!el) {
            el = document.createElement("div");
            container.appendChild(el);
        }
        el.className = `test-result ${success ? "success" : "error"}`;
        el.textContent = message;
    },

    _formatSize(bytes) {
        if (!bytes) return "";
        const gb = bytes / (1024 * 1024 * 1024);
        if (gb >= 1) return ` (${gb.toFixed(1)} GB)`;
        const mb = bytes / (1024 * 1024);
        return ` (${mb.toFixed(0)} MB)`;
    },

    _renderUserAccount(container) {
        const { section } = this._createSection("Account");
        this._addRow(section, "Signed in as", this._data.username || "");
        const note = document.createElement("div");
        note.className = "form-helper";
        note.textContent = "Server and voice settings are managed by the administrator.";
        section.appendChild(note);
        const btnRow = document.createElement("div");
        btnRow.className = "settings-actions";
        btnRow.appendChild(this._makeBtn("Log Out", "btn btn-secondary btn-sm", async () => {
            await fetch("/api/auth/logout", { method: "POST" });
            window.location.reload();
        }));
        section.appendChild(btnRow);
        container.appendChild(section);
    },

    renderAppearance(container) {
        const { section } = this._createSection("Appearance");
        const grid = document.createElement("div");
        grid.className = "theme-picker";

        const themes = [
            { id: "lcars", name: "LCARS", desc: "Starship console" },
            { id: "holo", name: "Holo", desc: "Hologram briefing" },
            { id: "nostromo", name: "Nostromo", desc: "Phosphor CRT terminal" },
            { id: "discovery", name: "Discovery", desc: "Red-eye mainframe" },
            { id: "dradis", name: "DRADIS", desc: "Colonial combat console" },
            { id: "2019", name: "2019", desc: "Neon noir" },
            { id: "thermal", name: "Thermal", desc: "Heat-signature hunter" },
            { id: "1997", name: "1997", desc: "Night-vision HUD" },
            { id: "1138", name: "1138", desc: "Clinical white void" },
        ];
        const current = this._data.ui_theme || "lcars";

        themes.forEach((t) => {
            const card = document.createElement("button");
            card.type = "button";
            card.className = `theme-card theme-preview-${t.id}${t.id === current ? " selected" : ""}`;

            const swatch = document.createElement("div");
            swatch.className = "theme-swatch";
            for (let i = 0; i < 3; i++) {
                const chip = document.createElement("span");
                chip.className = `theme-chip chip-${i}`;
                swatch.appendChild(chip);
            }
            const name = document.createElement("div");
            name.className = "theme-name";
            name.textContent = t.name;
            const desc = document.createElement("div");
            desc.className = "theme-desc";
            desc.textContent = t.desc;

            card.appendChild(swatch);
            card.appendChild(name);
            card.appendChild(desc);

            card.addEventListener("click", async () => {
                applyTheme(t.id);
                this._data.ui_theme = t.id;
                grid.querySelectorAll(".theme-card").forEach((c) =>
                    c.classList.remove("selected"));
                card.classList.add("selected");
                try {
                    await apiFetch("/api/settings/theme", {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ theme: t.id }),
                    });
                } catch {
                    // Applied locally; server sync will catch up next save
                }
            });
            grid.appendChild(card);
        });

        section.appendChild(grid);
        container.appendChild(section);
    },

    _cancelEdit() {
        this._editing = null;
        const container = document.getElementById("settings-content");
        if (container) this.render(container);
    },

    _startEdit(name) {
        this._editing = name;
        const container = document.getElementById("settings-content");
        if (container) this.render(container);
    },

    _keepAliveLabel(value) {
        const labels = { "10s": "10s", "30s": "30s", "60s": "1 min", "300s": "5 min", "-1s": "Always" };
        return labels[value] || value;
    },

    // ── Speaches Section ────────────────────────────────────────────

    renderSpeaches(container) {
        const { section, header } = this._createSection("Speaches (STT)", "speaches");

        if (this._editing === "speaches") {
            this._renderSpeachesEdit(section);
        } else {
            this._addEditBtn(header, () => this._startEdit("speaches"));
            this._addRow(section, "URL", this._data.speaches_url || "Not configured");
            this._addRow(section, "Model", this._data.speaches_model || "Not set");
        }
        container.appendChild(section);
    },

    _renderSpeachesEdit(section) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        const { group: urlGroup, input: urlInput } = this._makeInput(
            "Speaches URL", "edit-speaches-url", "text",
            this._data.speaches_url || "", "http://192.168.1.50:8000"
        );
        form.appendChild(urlGroup);

        // Test button
        const testBtn = this._makeBtn("Test Connection", "btn btn-secondary btn-full btn-sm", async () => {
            const url = urlInput.value.trim();
            if (!url) return;
            const result = await this._runTest(testBtn, async () => {
                const resp = await postJSON("/api/settings/test-speaches", { url });
                if (!resp) throw new Error();
                const data = await resp.json();
                if (!data.connected) throw new Error();
                this._showResult(form, true, "Connected!");
                await this._loadSpeachesModels(form);
                return true;
            }, "speaches");
            if (!result) this._showResult(form, false, "Connection failed.");
        });
        form.appendChild(testBtn);

        // Model dropdown
        const { group: modelGroup, input: modelSelect } = this._makeInput(
            "STT Model", "edit-speaches-model", "select"
        );
        const currentOpt = document.createElement("option");
        currentOpt.value = this._data.speaches_model || "";
        currentOpt.textContent = this._data.speaches_model || "Select model...";
        currentOpt.selected = true;
        modelSelect.appendChild(currentOpt);
        form.appendChild(modelGroup);

        // Auto-load models if URL configured
        if (this._data.speaches_url) {
            this._loadSpeachesModels(form);
        }

        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(this._makeBtn("Save", "btn btn-primary btn-full", async () => {
            const url = urlInput.value.trim();
            const model = modelSelect.value;
            if (!url || !model) return;
            try {
                await apiFetch("/api/settings/speaches", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url, model }),
                });
                this._data.speaches_url = url;
                this._data.speaches_model = model;
                this._cancelEdit();
            } catch {
                this._showResult(form, false, "Failed to save.");
            }
        }));
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        form.appendChild(actions);
        section.appendChild(form);
    },

    async _loadSpeachesModels(form) {
        const select = form.querySelector("#edit-speaches-model");
        if (!select) return;
        try {
            const resp = await apiFetch("/api/settings/speaches/models");
            if (!resp) return;
            const data = await resp.json();
            if (data.models && data.models.length) {
                const current = this._data.speaches_model;
                select.innerHTML = "";
                data.models.forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m.id;
                    opt.textContent = m.name;
                    if (m.id === current) opt.selected = true;
                    select.appendChild(opt);
                });
            }
        } catch {
            // Keep current model as only option
        }
    },

    // ── TTS (Voice Response) Section ─────────────────���──────────────

    renderTTS(container) {
        const statusKey = "tts";
        const { section, header } = this._createSection("Voice Response (TTS)", this._data.tts_enabled ? statusKey : null);

        if (this._editing === "tts") {
            this._renderTTSEdit(section);
        } else {
            this._addEditBtn(header, () => this._startEdit("tts"));
            if (this._data.tts_enabled) {
                this._addRow(section, "Status", "Enabled");
                this._addRow(section, "URL", this._data.tts_url || "Not configured");
                this._addRow(section, "Model", this._data.tts_model || "Not set");
                this._addRow(section, "Voice", this._data.tts_voice || "Not set");
            } else {
                this._addRow(section, "Status", "Disabled");
            }
        }
        container.appendChild(section);
    },

    _renderTTSEdit(section) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        // Enable toggle
        const toggleGroup = document.createElement("div");
        toggleGroup.className = "form-group";
        toggleGroup.style.maxWidth = "100%";
        const toggleRow = document.createElement("div");
        toggleRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:8px;";
        const enableToggle = document.createElement("input");
        enableToggle.type = "checkbox";
        enableToggle.checked = !!this._data.tts_enabled;
        enableToggle.style.cssText = "cursor:pointer;";
        const toggleLabel = document.createElement("label");
        toggleLabel.style.cssText = "margin:0;font-size:13px;color:var(--text-secondary);cursor:pointer;";
        toggleLabel.textContent = "Enable voice responses";
        toggleLabel.addEventListener("click", () => { enableToggle.click(); });
        toggleRow.appendChild(enableToggle);
        toggleRow.appendChild(toggleLabel);
        toggleGroup.appendChild(toggleRow);
        form.appendChild(toggleGroup);

        // Fields container (hidden when disabled)
        const fieldsContainer = document.createElement("div");
        if (!enableToggle.checked) fieldsContainer.classList.add("hidden");

        enableToggle.addEventListener("change", () => {
            if (enableToggle.checked) {
                fieldsContainer.classList.remove("hidden");
            } else {
                fieldsContainer.classList.add("hidden");
            }
        });

        // URL
        const { group: urlGroup, input: urlInput } = this._makeInput(
            "TTS Service URL", "edit-tts-url", "text",
            this._data.tts_url || "", "http://192.168.1.50:8000"
        );
        fieldsContainer.appendChild(urlGroup);

        // Test Connection
        const testBtn = this._makeBtn("Test Connection", "btn btn-secondary btn-full btn-sm", async () => {
            const url = urlInput.value.trim();
            if (!url) return;
            const result = await this._runTest(testBtn, async () => {
                const resp = await postJSON("/api/settings/test-tts", { url });
                if (!resp) throw new Error();
                const data = await resp.json();
                if (!data.connected) throw new Error();
                this._showResult(fieldsContainer, true, "Connected!");
                await this._loadTTSModels(fieldsContainer, url);
                return true;
            }, "tts");
            if (!result) this._showResult(fieldsContainer, false, "Connection failed.");
        });
        fieldsContainer.appendChild(testBtn);

        // Model dropdown
        const { group: modelGroup, input: modelSelect } = this._makeInput(
            "Model", "edit-tts-model", "select"
        );
        const currentModelOpt = document.createElement("option");
        currentModelOpt.value = this._data.tts_model || "";
        currentModelOpt.textContent = this._data.tts_model || "Select model...";
        currentModelOpt.selected = true;
        modelSelect.appendChild(currentModelOpt);
        fieldsContainer.appendChild(modelGroup);

        // Voice field container (populated dynamically based on model)
        const voiceContainer = document.createElement("div");
        voiceContainer.id = "tts-voice-container";
        fieldsContainer.appendChild(voiceContainer);
        this._renderVoiceField(voiceContainer, null, this._data.tts_voice || "af_heart");

        // Reload voices when model changes
        modelSelect.addEventListener("change", () => {
            const url = urlInput.value.trim();
            const model = modelSelect.value;
            if (url && model) {
                this._loadTTSVoices(fieldsContainer, url, model);
            }
        });

        // Auto-load models (and voices) if URL configured
        if (this._data.tts_url) {
            this._loadTTSModels(fieldsContainer, this._data.tts_url);
        }

        // Test Voice
        const testVoiceBtn = this._makeBtn("Test Voice", "btn btn-secondary btn-full btn-sm", async () => {
            const url = urlInput.value.trim();
            const model = modelSelect.value;
            const voiceEl = fieldsContainer.querySelector("#edit-tts-voice");
            const voice = voiceEl ? voiceEl.value.trim() : "";
            if (!url || !model || !voice) return;
            testVoiceBtn.disabled = true;
            testVoiceBtn.textContent = "Generating\u2026";
            try {
                const resp = await apiFetch("/api/settings/test-tts-voice", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url, model, voice }),
                });
                if (!resp || !resp.ok) {
                    this._showResult(fieldsContainer, false, "Voice test failed.");
                    return;
                }
                const data = await resp.json();
                if (data.audio_url) {
                    const audio = new Audio(data.audio_url);
                    audio.play().catch((e) => console.warn("TTS test playback failed:", e));
                    this._showResult(fieldsContainer, true, "Playing test audio...");
                }
            } catch {
                this._showResult(fieldsContainer, false, "Voice test error.");
            }
            testVoiceBtn.disabled = false;
            testVoiceBtn.textContent = "Test Voice";
        });
        fieldsContainer.appendChild(testVoiceBtn);

        form.appendChild(fieldsContainer);

        // Save / Cancel
        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(this._makeBtn("Save", "btn btn-primary btn-full", async () => {
            const enabled = enableToggle.checked;
            const url = urlInput.value.trim();
            const model = modelSelect.value;
            const voiceEl = fieldsContainer.querySelector("#edit-tts-voice");
            const voice = voiceEl ? voiceEl.value.trim() : "";
            if (enabled && (!url || !model || !voice)) {
                this._showResult(form, false, "Please fill in all TTS fields.");
                return;
            }
            try {
                await apiFetch("/api/settings/tts", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ enabled, url, model, voice }),
                });
                this._data.tts_enabled = enabled;
                this._data.tts_url = url;
                this._data.tts_model = model;
                this._data.tts_voice = voice;
                this._cancelEdit();
            } catch {
                this._showResult(form, false, "Failed to save.");
            }
        }));
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        form.appendChild(actions);
        section.appendChild(form);
    },

    async _loadTTSModels(container, url) {
        const select = container.querySelector("#edit-tts-model");
        if (!select) return;
        try {
            const resp = await postJSON("/api/settings/tts/models", url ? { url } : {});
            if (!resp) return;
            const data = await resp.json();
            if (data.models && data.models.length) {
                const current = this._data.tts_model;
                select.innerHTML = "";
                data.models.forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m.id;
                    opt.textContent = m.name;
                    if (m.id === current) opt.selected = true;
                    select.appendChild(opt);
                });
            }
        } catch {
            // Keep current model as only option
        }
        // Auto-load voices for the selected model
        const modelEl = container.querySelector("#edit-tts-model");
        const urlEl = container.querySelector("#edit-tts-url");
        if (modelEl && modelEl.value && urlEl) {
            this._loadTTSVoices(container, urlEl.value.trim(), modelEl.value);
        }
    },

    _renderVoiceField(container, voices, currentValue) {
        container.innerHTML = "";
        if (voices && voices.length) {
            const { group, input: select } = this._makeInput("Voice", "edit-tts-voice", "select");
            const voiceIds = voices.map((v) => v.id);
            if (currentValue && !voiceIds.includes(currentValue)) {
                const opt = document.createElement("option");
                opt.value = currentValue;
                opt.textContent = currentValue;
                select.appendChild(opt);
            }
            voices.forEach((v) => {
                const opt = document.createElement("option");
                opt.value = v.id;
                opt.textContent = v.name || v.id;
                if (v.id === currentValue) opt.selected = true;
                select.appendChild(opt);
            });
            if (!currentValue) select.value = "af_heart";
            container.appendChild(group);
        } else {
            const { group } = this._makeInput(
                "Voice", "edit-tts-voice", "text",
                currentValue || "", "Enter voice name"
            );
            const helper = document.createElement("div");
            helper.className = "form-helper";
            helper.textContent = "Voice name supported by your TTS model";
            group.appendChild(helper);
            container.appendChild(group);
        }
    },

    async _loadTTSVoices(container, url, model) {
        const voiceContainer = container.querySelector("#tts-voice-container");
        if (!voiceContainer) return;
        const currentVoiceEl = container.querySelector("#edit-tts-voice");
        const currentVoice = currentVoiceEl ? currentVoiceEl.value.trim() : (this._data.tts_voice || "af_heart");

        // Tier 1: Kokoro — hardcoded, instant
        if (model && model.toLowerCase().includes("kokoro")) {
            const voices = KOKORO_VOICES.map((v) => ({ id: v, name: v }));
            this._renderVoiceField(voiceContainer, voices, currentVoice);
            return;
        }

        // Tier 2: Try /v1/voices API
        if (url) {
            try {
                const resp = await postJSON("/api/settings/tts/voices", { url });
                if (resp && resp.ok) {
                    const data = await resp.json();
                    if (data.voices && data.voices.length) {
                        this._renderVoiceField(voiceContainer, data.voices, currentVoice);
                        return;
                    }
                }
            } catch { /* fall through to text input */ }
        }

        // Tier 3: Text input fallback
        this._renderVoiceField(voiceContainer, null, currentVoice);
    },

    // ── Ollama Section ──────────────────────────────────────────────

    renderOllama(container) {
        const { section, header } = this._createSection("Ollama (LLM)", "ollama");

        if (this._editing === "ollama") {
            this._renderOllamaEdit(section);
        } else {
            this._addEditBtn(header, () => this._startEdit("ollama"));
            this._addRow(section, "URL", this._data.ollama_url || "Not configured");
            this._addRow(section, "Model", this._data.ollama_model || "Not set");
            const keepAliveDisplay = this._data.ollama_keep_alive_enabled
                ? this._keepAliveLabel(this._data.ollama_keep_alive_value || "30s")
                : "Ollama default";
            this._addRow(section, "Idle timeout", keepAliveDisplay);
            const engineLabels = {
                gpu: "GPU accelerated",
                cpu: "CPU — first-time parses are slow",
                unknown: "Unknown (no parse yet)",
            };
            this._addRow(section, "Engine",
                engineLabels[this._data.ollama_engine] || "Unknown (no parse yet)");
        }
        container.appendChild(section);
    },

    _renderOllamaEdit(section) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        const { group: urlGroup, input: urlInput } = this._makeInput(
            "Ollama URL", "edit-ollama-url", "text",
            this._data.ollama_url || "", "http://192.168.1.50:11434"
        );
        form.appendChild(urlGroup);

        const testBtn = this._makeBtn("Test Connection", "btn btn-secondary btn-full btn-sm", async () => {
            const url = urlInput.value.trim();
            if (!url) return;
            const result = await this._runTest(testBtn, async () => {
                const resp = await postJSON("/api/settings/test-ollama", { url });
                if (!resp) throw new Error();
                const data = await resp.json();
                if (!data.connected) throw new Error();
                this._showResult(form, true, "Connected!");
                await this._loadOllamaModels(form, url);
                return true;
            }, "ollama");
            if (!result) this._showResult(form, false, "Connection failed.");
        });
        form.appendChild(testBtn);

        const { group: modelGroup, input: modelSelect } = this._makeInput(
            "Model", "edit-ollama-model", "select"
        );
        const currentOpt = document.createElement("option");
        currentOpt.value = this._data.ollama_model || "";
        currentOpt.textContent = this._data.ollama_model || "Select model...";
        currentOpt.selected = true;
        modelSelect.appendChild(currentOpt);
        form.appendChild(modelGroup);

        this._loadOllamaModels(form, this._data.ollama_url);

        // ── Idle timeout toggle + segmented buttons ──
        const keepAliveGroup = document.createElement("div");
        keepAliveGroup.className = "form-group";
        keepAliveGroup.style.maxWidth = "100%";

        const keepAliveToggleRow = document.createElement("div");
        keepAliveToggleRow.style.cssText = "display:flex;align-items:center;gap:8px;margin-bottom:8px;";
        const keepAliveToggle = document.createElement("input");
        keepAliveToggle.type = "checkbox";
        keepAliveToggle.checked = !!this._data.ollama_keep_alive_enabled;
        keepAliveToggle.style.cssText = "cursor:pointer;";
        const keepAliveLabel = document.createElement("label");
        keepAliveLabel.style.cssText = "margin:0;font-size:13px;color:var(--text-secondary);cursor:pointer;";
        keepAliveLabel.textContent = "Custom idle timeout";
        keepAliveLabel.addEventListener("click", () => { keepAliveToggle.click(); });
        keepAliveToggleRow.appendChild(keepAliveToggle);
        keepAliveToggleRow.appendChild(keepAliveLabel);
        keepAliveGroup.appendChild(keepAliveToggleRow);

        const segRow = document.createElement("div");
        segRow.className = "keep-alive-buttons";
        const currentVal = this._data.ollama_keep_alive_value || "30s";
        let selectedKeepAlive = currentVal;

        const options = [
            { label: "10s", value: "10s" },
            { label: "30s", value: "30s" },
            { label: "1 min", value: "60s" },
            { label: "5 min", value: "300s" },
            { label: "Always", value: "-1s" },
        ];

        const alwaysWarning = document.createElement("div");
        alwaysWarning.className = "keep-alive-warning hidden";
        alwaysWarning.textContent = "Model will stay loaded in VRAM indefinitely. Only recommended if you have plenty of available memory.";

        options.forEach((opt) => {
            const btn = document.createElement("button");
            btn.type = "button";
            btn.className = "keep-alive-btn" + (opt.value === currentVal ? " active" : "");
            btn.textContent = opt.label;
            btn.addEventListener("click", () => {
                selectedKeepAlive = opt.value;
                segRow.querySelectorAll(".keep-alive-btn").forEach((b) => b.classList.remove("active"));
                btn.classList.add("active");
                if (opt.value === "-1s") {
                    alwaysWarning.classList.remove("hidden");
                } else {
                    alwaysWarning.classList.add("hidden");
                }
            });
            segRow.appendChild(btn);
        });

        keepAliveGroup.appendChild(segRow);
        keepAliveGroup.appendChild(alwaysWarning);

        if (!keepAliveToggle.checked) {
            segRow.classList.add("hidden");
            alwaysWarning.classList.add("hidden");
        } else if (currentVal === "-1s") {
            alwaysWarning.classList.remove("hidden");
        }

        keepAliveToggle.addEventListener("change", () => {
            if (keepAliveToggle.checked) {
                segRow.classList.remove("hidden");
                if (selectedKeepAlive === "-1s") alwaysWarning.classList.remove("hidden");
            } else {
                segRow.classList.add("hidden");
                alwaysWarning.classList.add("hidden");
            }
        });

        form.appendChild(keepAliveGroup);

        // ── Save / Cancel ──
        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(this._makeBtn("Save", "btn btn-primary btn-full", async () => {
            const url = urlInput.value.trim();
            const model = modelSelect.value;
            if (!url || !model) return;
            try {
                await apiFetch("/api/settings/ollama", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        url,
                        model,
                        keep_alive_enabled: keepAliveToggle.checked,
                        keep_alive_value: keepAliveToggle.checked ? selectedKeepAlive : (this._data.ollama_keep_alive_value || "30s"),
                    }),
                });
                this._data.ollama_url = url;
                this._data.ollama_model = model;
                this._data.ollama_keep_alive_enabled = keepAliveToggle.checked;
                this._data.ollama_keep_alive_value = keepAliveToggle.checked ? selectedKeepAlive : (this._data.ollama_keep_alive_value || "30s");
                this._cancelEdit();
            } catch {
                this._showResult(form, false, "Failed to save.");
            }
        }));
        actions.appendChild(this._makeBtn("Disable AI parser", "btn btn-secondary btn-sm", async () => {
            try {
                await apiFetch("/api/settings/ollama", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: "", model: "" }),
                });
                this._data.ollama_url = "";
                this._data.ollama_model = "";
                this._cancelEdit();
            } catch {
                this._showResult(form, false, "Failed to disable AI parser.");
            }
        }));
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        form.appendChild(actions);
        section.appendChild(form);
    },

    async _loadOllamaModels(form, url) {
        const select = form.querySelector("#edit-ollama-model");
        if (!select) return;
        try {
            const resp = await apiFetch("/api/settings/ollama/models", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ url: url || "" }),
            });
            if (!resp) return;
            const data = await resp.json();
            if (data.models && data.models.length) {
                const current = this._data.ollama_model;
                select.innerHTML = "";
                data.models.forEach((m) => {
                    const opt = document.createElement("option");
                    opt.value = m.name;
                    opt.textContent = m.name + this._formatSize(m.size);
                    if (m.name === current) opt.selected = true;
                    select.appendChild(opt);
                });
            }
        } catch {
            // Keep current model as only option
        }
    },

    // ── Media Servers Section ───────────────────────────────────────

    renderServers(container) {
        const servers = this._data.servers || [];

        servers.forEach((server) => {
            const editKey = `server:${server.id}`;
            const { section, header } = this._createSection(server.name, editKey);

            // Type badge next to title
            const badge = document.createElement("span");
            badge.className = "server-type-badge";
            badge.textContent = server.type;
            header.querySelector("h3").appendChild(document.createTextNode(" "));
            header.querySelector("h3").appendChild(badge);

            if (this._editing === editKey) {
                this._renderServerEdit(section, server);
            } else {
                this._addEditBtn(header, () => this._startEdit(editKey));
                this._addRow(section, "URL", server.url);
                this._addRow(section, "API Key", server.api_key_redacted || "********");
                if (server.user_name) this._addRow(section, "User", server.user_name);
                if (server.is_default) this._addRow(section, "Default", "Yes");
                if (server.aliases && server.aliases.length) {
                    const aliasNames = server.aliases.map((a) => a.alias).filter(Boolean);
                    const aliasText = aliasNames.length > 2
                        ? `${aliasNames.length} aliases: ${aliasNames.join(", ")}`
                        : aliasNames.join(", ");
                    if (aliasText) this._addRow(section, "Aliases", aliasText);
                }
            }
            container.appendChild(section);
        });

        // Add Server button or form
        if (this._editing === "add-server") {
            const { section } = this._createSection("Add Media Server");
            this._renderAddServerForm(section);
            container.appendChild(section);
        } else {
            const addCard = document.createElement("div");
            addCard.className = "add-server-card";
            addCard.textContent = "+ Add Media Server";
            addCard.addEventListener("click", () => this._startEdit("add-server"));
            container.appendChild(addCard);
        }
    },

    _renderServerEdit(section, server) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        const { group: nameGroup, input: nameInput } = this._makeInput(
            "Name", "edit-srv-name", "text", server.name, "Server name"
        );
        form.appendChild(nameGroup);

        // Type read-only
        this._addRow(form, "Type", server.type);

        const { group: urlGroup, input: urlInput } = this._makeInput(
            "URL", "edit-srv-url", "text", server.url, "http://..."
        );
        form.appendChild(urlGroup);

        const { group: keyGroup, input: keyInput } = this._makeInput(
            "API Key", "edit-srv-key", "text", "", "Leave blank to keep current"
        );
        form.appendChild(keyGroup);

        // Test Connection button
        const statusKey = `server:${server.id}`;
        const testBtn = this._makeBtn("Test Connection", "btn btn-secondary btn-full btn-sm", async () => {
            await this._runTest(testBtn, async () => {
                const resp = await apiFetch(`/api/settings/test-media/${server.id}`);
                if (!resp) throw new Error();
                const data = await resp.json();
                if (!data.connected) throw new Error();
                return true;
            }, statusKey);
        });
        form.appendChild(testBtn);

        // Re-discover button (uses stored credentials)
        const discoverBtn = this._makeBtn("Re-discover Users & Devices", "btn btn-secondary btn-full btn-sm", async () => {
            discoverBtn.disabled = true;
            discoverBtn.textContent = "Discovering\u2026";
            try {
                const resp = await apiFetch(`/api/settings/discover/${server.id}`);
                if (!resp) return;
                const data = await resp.json();
                if (data.success) {
                    this._showResult(form, true, "Discovery complete!");
                    if (data.users && data.users.length) {
                        userSelect.innerHTML = "";
                        data.users.forEach((u) => {
                            const opt = document.createElement("option");
                            opt.value = u.id;
                            opt.textContent = u.name;
                            if (u.id === server.user_id) opt.selected = true;
                            userSelect.appendChild(opt);
                        });
                    }
                    if (data.devices && data.devices.length) {
                        this._populateDiscoveredAliases(aliasContainer, data.devices, server.aliases || []);
                    }
                } else {
                    this._showResult(form, false, data.error || "Discovery failed.");
                }
            } catch {
                this._showResult(form, false, "Connection error.");
            }
            discoverBtn.disabled = false;
            discoverBtn.textContent = "Re-discover Users & Devices";
        });
        form.appendChild(discoverBtn);

        // User select
        const { group: userGroup, input: userSelect } = this._makeInput(
            "User", "edit-srv-user", "select"
        );
        if (server.user_name) {
            const opt = document.createElement("option");
            opt.value = server.user_id || "";
            opt.textContent = server.user_name;
            opt.selected = true;
            userSelect.appendChild(opt);
        }
        form.appendChild(userGroup);

        // Default checkbox
        const defaultGroup = document.createElement("div");
        defaultGroup.className = "form-group";
        const defaultLabel = document.createElement("label");
        defaultLabel.style.cssText = "display:flex;align-items:center;gap:8px;cursor:pointer;";
        const defaultCheck = document.createElement("input");
        defaultCheck.type = "checkbox";
        defaultCheck.checked = server.is_default;
        defaultLabel.appendChild(defaultCheck);
        defaultLabel.appendChild(document.createTextNode("Default server"));
        defaultGroup.appendChild(defaultLabel);
        form.appendChild(defaultGroup);

        // Device aliases
        const aliasLabel = document.createElement("div");
        aliasLabel.className = "label";
        aliasLabel.textContent = "Device Aliases";
        aliasLabel.style.cssText = "margin-top:8px;margin-bottom:4px;font-size:13px;color:var(--text-secondary);";
        form.appendChild(aliasLabel);

        const aliasContainer = document.createElement("div");
        aliasContainer.className = "alias-list";
        (server.aliases || []).forEach((a) => {
            this._addAliasRow(aliasContainer, a.alias, a.pattern);
        });
        form.appendChild(aliasContainer);

        form.appendChild(this._makeBtn("+ Add Alias", "btn btn-secondary btn-sm alias-add", () => {
            this._addAliasRow(aliasContainer, "", "");
        }));

        // Password prompt (hidden until needed)
        const pwPrompt = document.createElement("div");
        pwPrompt.className = "password-confirm hidden";
        const pwLabel = document.createElement("span");
        pwLabel.className = "label";
        pwLabel.textContent = "Enter password to confirm:";
        const pwInput = document.createElement("input");
        pwInput.type = "password";
        pwInput.placeholder = "Current password";
        pwInput.className = "pw-confirm-input";
        pwPrompt.appendChild(pwLabel);
        pwPrompt.appendChild(pwInput);
        form.appendChild(pwPrompt);

        // Actions
        const saveBtn = this._makeBtn("Save", "btn btn-primary btn-full", async () => {
            const fields = {
                name: nameInput.value.trim(),
                url: urlInput.value.trim(),
                api_key: keyInput.value.trim() || null,
                user_id: userSelect.value || null,
                user_name: userSelect.options[userSelect.selectedIndex]?.textContent || null,
                is_default: defaultCheck.checked,
            };

            // If changing API key, require password
            if (fields.api_key && !pwInput.value) {
                pwPrompt.classList.remove("hidden");
                pwInput.focus();
                pwInput.dataset.action = "save";
                return;
            }
            if (fields.api_key) {
                fields.current_password = pwInput.value;
            }

            saveBtn.disabled = true;
            try {
                const resp = await apiFetch(`/api/settings/media-server/${server.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(fields),
                });
                if (!resp) return;
                if (!resp.ok) {
                    const data = await resp.json();
                    this._showResult(form, false, data.error || "Failed to save.");
                    saveBtn.disabled = false;
                    return;
                }

                // Save aliases
                const aliases = this._getAliases(aliasContainer);
                await apiFetch(`/api/settings/device-aliases/${server.id}`, {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ server_id: server.id, aliases }),
                });

                this._editing = null;
                await this.load();
            } catch {
                this._showResult(form, false, "Save failed.");
                saveBtn.disabled = false;
            }
        });

        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(saveBtn);
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        const deleteBtn = this._makeBtn("Delete Server", "btn-delete-text", async () => {
            if (!pwInput.value) {
                pwPrompt.classList.remove("hidden");
                pwInput.focus();
                pwInput.dataset.action = "delete";
                return;
            }
            deleteBtn.disabled = true;
            try {
                const resp = await apiFetch(`/api/settings/media-server/${server.id}`, {
                    method: "DELETE",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ current_password: pwInput.value }),
                });
                if (!resp) return;
                if (resp.ok) {
                    this._editing = null;
                    await this.load();
                } else {
                    const data = await resp.json();
                    this._showResult(form, false, data.error || "Failed to delete.");
                }
            } catch {
                this._showResult(form, false, "Delete failed.");
            }
            deleteBtn.disabled = false;
        });
        actions.appendChild(deleteBtn);
        form.appendChild(actions);
        section.appendChild(form);
    },

    _populateDiscoveredAliases(container, devices, existingAliases) {
        // Merge discovered devices with existing aliases
        container.innerHTML = "";
        const aliasMap = {};
        existingAliases.forEach((a) => { aliasMap[a.pattern] = a.alias; });

        devices.forEach((d) => {
            const existing = aliasMap[d.device_name] || "";
            this._addAliasRow(container, existing, d.device_name);
        });

        // Keep aliases for devices not currently active
        existingAliases.forEach((a) => {
            if (!devices.some((d) => d.device_name === a.pattern)) {
                this._addAliasRow(container, a.alias, a.pattern);
            }
        });
    },

    _addAliasRow(container, alias, pattern) {
        const row = document.createElement("div");
        row.className = "alias-item";

        const aliasInput = document.createElement("input");
        aliasInput.type = "text";
        aliasInput.className = "alias-name";
        aliasInput.placeholder = "Alias (e.g. bedroom)";
        aliasInput.value = alias;

        const patternInput = document.createElement("input");
        patternInput.type = "text";
        patternInput.className = "alias-pattern";
        patternInput.placeholder = "Device name pattern";
        patternInput.value = pattern;

        const removeBtn = document.createElement("button");
        removeBtn.className = "btn btn-sm alias-remove";
        removeBtn.textContent = "\u00d7";
        removeBtn.addEventListener("click", () => row.remove());

        row.appendChild(aliasInput);
        row.appendChild(patternInput);
        row.appendChild(removeBtn);
        container.appendChild(row);
    },

    _getAliases(container) {
        const aliases = [];
        container.querySelectorAll(".alias-item").forEach((row) => {
            const alias = row.querySelector(".alias-name").value.trim();
            const pattern = row.querySelector(".alias-pattern").value.trim();
            if (alias && pattern) {
                aliases.push({ alias, pattern });
            }
        });
        return aliases;
    },

    // ── Add Server Form ─────────────────────────────────────────────

    _renderAddServerForm(section) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        const { group: nameGroup, input: nameInput } = this._makeInput(
            "Name", "add-srv-name", "text", "", "My Emby Server"
        );
        form.appendChild(nameGroup);

        const { group: typeGroup, input: typeSelect } = this._makeInput(
            "Type", "add-srv-type", "select"
        );
        ["emby", "jellyfin"].forEach((t) => {
            const opt = document.createElement("option");
            opt.value = t;
            opt.textContent = t.charAt(0).toUpperCase() + t.slice(1);
            typeSelect.appendChild(opt);
        });
        form.appendChild(typeGroup);

        const { group: urlGroup, input: urlInput } = this._makeInput(
            "URL", "add-srv-url", "text", "", "http://192.168.1.50:8096"
        );
        form.appendChild(urlGroup);

        const { group: keyGroup, input: keyInput } = this._makeInput(
            "API Key", "add-srv-key", "text", "", "Your API key"
        );
        form.appendChild(keyGroup);

        // User select (hidden until test)
        const { group: userGroup, input: userSelect } = this._makeInput(
            "User", "add-srv-user", "select"
        );
        userGroup.classList.add("hidden");

        // Device aliases (hidden until test)
        const aliasSection = document.createElement("div");
        aliasSection.className = "hidden";
        const aliasLabel = document.createElement("div");
        aliasLabel.className = "label";
        aliasLabel.textContent = "Device Aliases";
        aliasLabel.style.cssText = "margin-bottom:4px;font-size:13px;color:var(--text-secondary);";
        aliasSection.appendChild(aliasLabel);
        const aliasContainer = document.createElement("div");
        aliasContainer.className = "alias-list";
        aliasSection.appendChild(aliasContainer);

        // Save button (hidden until test)
        const saveBtn = this._makeBtn("Add Server", "btn btn-primary btn-full hidden", async () => {
            const name = nameInput.value.trim() || `My ${typeSelect.value} Server`;
            const url = urlInput.value.trim();
            const apiKey = keyInput.value.trim();
            if (!url || !apiKey) return;

            saveBtn.disabled = true;
            try {
                const resp = await apiFetch("/api/settings/media-server", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name,
                        type: typeSelect.value,
                        url,
                        api_key: apiKey,
                        user_id: userSelect.value || null,
                        user_name: userSelect.options[userSelect.selectedIndex]?.textContent || null,
                        is_default: defaultCheck.checked,
                    }),
                });
                if (!resp) return;
                const data = await resp.json();
                if (data.success && data.server_id) {
                    const aliases = this._getAliases(aliasContainer);
                    if (aliases.length) {
                        await apiFetch(`/api/settings/device-aliases/${data.server_id}`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ server_id: data.server_id, aliases }),
                        });
                    }
                    this._editing = null;
                    await this.load();
                } else {
                    this._showResult(form, false, data.error || "Failed to add server.");
                }
            } catch {
                this._showResult(form, false, "Save failed.");
            }
            saveBtn.disabled = false;
        });

        // Test button
        const testBtn = this._makeBtn("Test Connection", "btn btn-secondary btn-full btn-sm", async () => {
            const url = urlInput.value.trim();
            const apiKey = keyInput.value.trim();
            if (!url || !apiKey) return;
            testBtn.disabled = true;
            testBtn.textContent = "Testing\u2026";
            try {
                const resp = await apiFetch("/api/settings/test-media-server", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({
                        name: nameInput.value.trim() || `My ${typeSelect.value} Server`,
                        type: typeSelect.value,
                        url,
                        api_key: apiKey,
                    }),
                });
                if (!resp) { testBtn.disabled = false; testBtn.textContent = "Test Connection"; return; }
                const data = await resp.json();
                if (data.success) {
                    testBtn.textContent = "\u2713 Connected";
                    testBtn.style.color = "var(--success)";
                    this._showResult(form, true, "Connected!");
                    saveBtn.classList.remove("hidden");
                    if (data.users && data.users.length) {
                        userSelect.innerHTML = "";
                        data.users.forEach((u) => {
                            const opt = document.createElement("option");
                            opt.value = u.id;
                            opt.textContent = u.name;
                            userSelect.appendChild(opt);
                        });
                        userGroup.classList.remove("hidden");
                    }
                    if (data.devices && data.devices.length) {
                        aliasContainer.innerHTML = "";
                        data.devices.forEach((d) => {
                            this._addAliasRow(aliasContainer, "", d.device_name);
                        });
                        aliasSection.classList.remove("hidden");
                    }
                    setTimeout(() => { testBtn.textContent = "Test Connection"; testBtn.style.color = ""; testBtn.disabled = false; }, 2000);
                } else {
                    testBtn.textContent = "\u2717 Failed";
                    testBtn.style.color = "var(--danger)";
                    this._showResult(form, false, data.error || "Connection failed.");
                    setTimeout(() => { testBtn.textContent = "Test Connection"; testBtn.style.color = ""; testBtn.disabled = false; }, 2000);
                }
            } catch {
                testBtn.textContent = "\u2717 Failed";
                testBtn.style.color = "var(--danger)";
                this._showResult(form, false, "Connection error.");
                setTimeout(() => { testBtn.textContent = "Test Connection"; testBtn.style.color = ""; testBtn.disabled = false; }, 2000);
            }
        });
        form.appendChild(testBtn);
        form.appendChild(userGroup);
        form.appendChild(aliasSection);

        // Default checkbox
        const defaultGroup = document.createElement("div");
        defaultGroup.className = "form-group";
        const defaultLabel = document.createElement("label");
        defaultLabel.style.cssText = "display:flex;align-items:center;gap:8px;cursor:pointer;";
        const defaultCheck = document.createElement("input");
        defaultCheck.type = "checkbox";
        defaultCheck.checked = true;
        defaultLabel.appendChild(defaultCheck);
        defaultLabel.appendChild(document.createTextNode("Default server"));
        defaultGroup.appendChild(defaultLabel);
        form.appendChild(defaultGroup);

        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(saveBtn);
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        form.appendChild(actions);
        section.appendChild(form);
    },

    // ── Account Section ─────────────────────────────────────────────

    renderUsers(container) {
        const { section } = this._createSection("Users");
        const list = document.createElement("div");
        list.className = "empty-state";
        list.textContent = "Loading users...";
        section.appendChild(list);
        container.appendChild(section);

        (async () => {
            const resp = await apiFetch("/api/users");
            if (!resp || !resp.ok) {
                list.textContent = "Failed to load users.";
                return;
            }
            const data = await resp.json();
            const users = data.users || [];

            // Media accounts from every Emby/Jellyfin server, in parallel.
            // A server that's down just contributes nothing.
            const servers = this._data.servers || [];
            const discovered = await Promise.all(servers.map(async (s) => {
                try {
                    const r = await apiFetch(`/api/settings/discover/${s.id}`);
                    if (!r || !r.ok) return { server: s, users: [], ok: false };
                    const d = await r.json();
                    return { server: s, users: d.users || [], ok: true };
                } catch {
                    return { server: s, users: [], ok: false };
                }
            }));
            // Only claim someone is gone from the servers if every server answered
            const allDiscoveryOk = discovered.length > 0 && discovered.every((d) => d.ok);

            list.className = "";
            list.innerHTML = "";

            // ONE unified people list: app users + media accounts that have
            // never signed in, every row with the same state control
            const byIdentity = new Map();
            users.forEach((u) => (u.identities || []).forEach(
                (i) => byIdentity.set(`${i.server_id}:${i.media_user_id}`, u)));
            const byName = new Map();
            users.forEach((u) => byName.set((u.username || "").toLowerCase(), u));

            const people = new Map();
            users.forEach((u) => people.set(u.username.toLowerCase(), {
                user: u,
                username: u.username,
                servers: new Set((u.identities || []).map((i) => i.server_name)),
                presentOn: new Set(),
            }));
            discovered.forEach(({ server, users: accounts }) => {
                accounts.forEach((mu) => {
                    const app = byIdentity.get(`${server.id}:${mu.id}`)
                        || byName.get((mu.name || "").toLowerCase());
                    const key = (app ? app.username : mu.name).toLowerCase();
                    if (!people.has(key)) {
                        people.set(key, { user: null, username: mu.name, servers: new Set(), presentOn: new Set() });
                    }
                    people.get(key).servers.add(server.name);
                    people.get(key).presentOn.add(server.name);
                });
            });

            const rows = Array.from(people.values()).sort((a, b) => {
                const al = a.user && a.user.is_local ? 0 : 1;
                const bl = b.user && b.user.is_local ? 0 : 1;
                return al - bl || a.username.localeCompare(b.username);
            });

            rows.forEach((p) => {
                const u = p.user;
                const serverList = Array.from(p.servers).join(", ");
                const row = document.createElement("div");
                row.className = "user-row";

                const nameWrap = document.createElement("div");
                const name = document.createElement("span");
                name.className = "user-name";
                name.textContent = p.username;
                const meta = document.createElement("span");
                meta.className = "user-meta";
                const orphaned = u && !u.is_local && allDiscoveryOk && p.presentOn.size === 0;
                if (u && u.is_local) {
                    meta.textContent = "Local admin";
                } else if (orphaned) {
                    meta.textContent = "No longer exists on any server";
                } else if (!u) {
                    meta.textContent = `${serverList} — never signed in`;
                } else if (u.disabled && !(u.identities || []).length) {
                    meta.textContent = "Blocked — never signed in";
                } else if (u.disabled) {
                    meta.textContent = `${serverList} — disabled`;
                } else {
                    meta.textContent = serverList || "no linked servers";
                }
                nameWrap.appendChild(name);
                nameWrap.appendChild(meta);
                row.appendChild(nameWrap);

                const isSelf = u && u.username === this._data.username;
                if ((u && u.is_local) || isSelf) {
                    list.appendChild(row);
                    return;
                }

                // Current state; never-signed-in accounts default to
                // Disabled — nobody gets in until an admin enables them
                const isDisabled = u ? !!u.disabled : true;
                const role = u ? u.role : "user";
                const activeKey = isDisabled ? "disabled"
                    : (role === "admin" ? "admin" : "user");

                const controls = document.createElement("div");
                controls.className = "user-controls";
                const seg = document.createElement("div");
                seg.className = "keep-alive-buttons";
                [
                    { key: "disabled", label: "Disabled", body: { disabled: true } },
                    { key: "user", label: "Standard", body: { disabled: false, role: "user" } },
                    { key: "admin", label: "Admin", body: { disabled: false, role: "admin" } },
                ].forEach((s) => {
                    const b = document.createElement("button");
                    b.className = "keep-alive-btn" + (s.key === activeKey ? " active" : "");
                    b.textContent = s.label;
                    b.addEventListener("click", async () => {
                        if (s.key === activeKey) return;
                        if (s.key === "admin" && !window.confirm(
                            `Make "${p.username}" an admin?\n\nThey will control servers, ` +
                            `API keys and users — protected only by their media-server password.`)) return;
                        if (u) {
                            await apiFetch(`/api/users/${u.id}`, {
                                method: "PUT",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(s.body),
                            });
                        } else {
                            await apiFetch("/api/users/provision", {
                                method: "POST",
                                headers: { "Content-Type": "application/json" },
                                body: JSON.stringify({
                                    username: p.username,
                                    disabled: !!s.body.disabled,
                                    role: s.body.role || "user",
                                }),
                            });
                        }
                        this.load();
                    });
                    seg.appendChild(b);
                });
                controls.appendChild(seg);

                // Removal is ONLY for orphans (their media account is gone).
                // For everyone else, Disabled is the correct off-switch —
                // deleting a live person would just resurrect them via
                // discovery, and deleting a blocked one would UNblock them.
                if (orphaned) {
                    const del = document.createElement("button");
                    del.className = "user-delete";
                    del.textContent = "Remove";
                    del.addEventListener("click", async () => {
                        if (!window.confirm(
                            `Remove "${p.username}"?\n\nThis account no longer exists on any ` +
                            `media server. Removing tidies it out of HailFrequency; their ` +
                            `history stays in the log.`)) return;
                        await apiFetch(`/api/users/${u.id}`, { method: "DELETE" });
                        this.load();
                    });
                    controls.appendChild(del);
                }
                row.appendChild(controls);
                list.appendChild(row);
            });
        })();
    },

    renderAccount(container) {
        const { section } = this._createSection("Account");

        this._addRow(section, "Username", this._data.username || "admin");

        if (this._editing === "password") {
            this._renderPasswordChange(section);
        } else {
            // Delegated accounts (even promoted admins) have no local
            // password — theirs lives on the media server
            if (!this._data.local) {
                const note = document.createElement("div");
                note.className = "form-helper";
                note.textContent = "Your password is managed by your media server.";
                section.appendChild(note);
            }
            const btnRow = document.createElement("div");
            btnRow.className = "settings-actions";
            if (this._data.local) {
                btnRow.appendChild(this._makeBtn("Change Password", "btn btn-secondary btn-sm",
                    () => this._startEdit("password"),
                ));
            }
            btnRow.appendChild(this._makeBtn("Log Out", "btn btn-secondary btn-sm", async () => {
                await fetch("/api/auth/logout", { method: "POST" });
                window.location.reload();
            }));
            section.appendChild(btnRow);
        }
        container.appendChild(section);
    },

    _renderPasswordChange(section) {
        const form = document.createElement("div");
        form.className = "settings-edit-form";

        const { input: currentInput } = this._makeInput(
            "Current Password", "edit-pw-current", "password", "", "Current password"
        );
        form.appendChild(currentInput.parentElement);

        const { input: newInput } = this._makeInput(
            "New Password (min 8 characters)", "edit-pw-new", "password", "", "New password"
        );
        form.appendChild(newInput.parentElement);

        const { input: confirmInput } = this._makeInput(
            "Confirm New Password", "edit-pw-confirm", "password", "", "Confirm password"
        );
        form.appendChild(confirmInput.parentElement);

        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(this._makeBtn("Save Password", "btn btn-primary btn-full", async () => {
            const current = currentInput.value;
            const newPw = newInput.value;
            const confirm = confirmInput.value;

            if (newPw.length < 8) {
                this._showResult(form, false, "Password must be at least 8 characters.");
                return;
            }
            if (newPw !== confirm) {
                this._showResult(form, false, "Passwords do not match.");
                return;
            }
            try {
                const resp = await apiFetch("/api/settings/password", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ current_password: current, new_password: newPw }),
                });
                if (!resp) return;
                if (resp.ok) {
                    this._showResult(form, true, "Password changed. Redirecting to login...");
                    setTimeout(() => window.location.reload(), 1500);
                } else {
                    const data = await resp.json();
                    this._showResult(form, false, data.error || "Failed to change password.");
                }
            } catch {
                this._showResult(form, false, "Connection error.");
            }
        }));
        actions.appendChild(this._makeBtn("Cancel", "btn-cancel-text", () => this._cancelEdit()));
        form.appendChild(actions);
        section.appendChild(form);
    },

    // ── Voice Learning Section ──────────────────────────────────────

    _renderLearningSection(container) {
        const isAdmin = this._data.role === "admin";
        const viewable = isAdmin || this._data.learned_phrases_viewable;
        if (!viewable) return;
        const { section } = this._createSection("Voice Learning");
        if (isAdmin) {
            this._addRow(section, "Learned phrases",
                String(this._data.learned_phrases_count ?? 0));
        }
        const actions = document.createElement("div");
        actions.className = "settings-actions-stacked";
        actions.appendChild(this._makeBtn("View learned phrases",
            "btn btn-secondary btn-sm", () => this._showLearnedView()));
        if (isAdmin) {
            actions.appendChild(this._makeBtn("Clear all", "btn btn-secondary btn-sm",
                async () => {
                    const resp = await apiFetch(
                        "/api/settings/learned-phrases/clear",
                        { method: "POST" });
                    if (resp && resp.ok) this.load();
                }));
            const toggleBtn = this._makeBtn(
                this._data.learned_phrases_public
                    ? "Restrict viewing to admins"
                    : "Allow all users to view",
                "btn btn-secondary btn-sm",
                async () => {
                    const resp = await apiFetch(
                        "/api/settings/learned-phrases/visibility", {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                public: !this._data.learned_phrases_public,
                            }),
                        });
                    if (resp && resp.ok) this.load();
                });
            actions.appendChild(toggleBtn);
        }
        section.appendChild(actions);
        container.appendChild(section);
    },

    async _showLearnedView() {
        const resp = await apiFetch("/api/settings/learned-phrases");
        if (!resp || !resp.ok) return;
        const data = await resp.json();
        const isAdmin = this._data.role === "admin";
        const container = document.getElementById("settings-content");
        if (!container) return;
        container.innerHTML = "";
        const view = document.createElement("div");
        view.className = "learned-view";

        const header = document.createElement("div");
        header.className = "learned-view-header";
        const back = document.createElement("a");
        back.href = "#";
        back.textContent = "← Back";
        back.addEventListener("click", (e) => {
            e.preventDefault();
            this.load();
        });
        const title = document.createElement("span");
        title.textContent = `LEARNED PHRASES (${data.phrases.length})`;
        header.appendChild(back);
        header.appendChild(title);
        view.appendChild(header);

        const filter = document.createElement("input");
        filter.type = "search";
        filter.placeholder = "Filter phrases...";
        filter.className = "learned-filter";
        view.appendChild(filter);

        const list = document.createElement("div");
        list.className = "learned-list";
        view.appendChild(list);

        const renderRows = () => {
            list.innerHTML = "";
            const q = filter.value.trim().toLowerCase();
            data.phrases
                .filter((p) => !q
                    || p.phrase.toLowerCase().includes(q)
                    || this._summarize(p.intent).toLowerCase().includes(q))
                .forEach((p) => {
                    const row = document.createElement("div");
                    row.className = "learned-row";
                    const phrase = document.createElement("div");
                    phrase.className = "learned-phrase";
                    phrase.textContent = `"${p.phrase}"`;
                    const meta = document.createElement("div");
                    meta.className = "learned-meta";
                    meta.textContent = [
                        this._summarize(p.intent),
                        `used ${p.use_count}×`,
                        p.last_used_at || "",
                    ].filter(Boolean).join(" · ");
                    row.appendChild(phrase);
                    row.appendChild(meta);
                    if (isAdmin) {
                        const del = document.createElement("button");
                        del.className = "learned-delete";
                        del.textContent = "✕";
                        del.title = "Forget this phrase";
                        del.addEventListener("click", async () => {
                            const r = await apiFetch(
                                "/api/settings/learned-phrases/delete", {
                                    method: "POST",
                                    headers: { "Content-Type": "application/json" },
                                    body: JSON.stringify({ phrase: p.phrase }),
                                });
                            if (r && r.ok) {
                                data.phrases = data.phrases.filter(
                                    (x) => x.phrase !== p.phrase);
                                title.textContent =
                                    `LEARNED PHRASES (${data.phrases.length})`;
                                renderRows();
                            }
                        });
                        row.appendChild(del);
                    }
                    list.appendChild(row);
                });
        };
        filter.addEventListener("input", renderRows);
        renderRows();
        container.appendChild(view);
    },

    _summarize(intent) {
        if (!intent || !intent.action) return "";
        const a = intent.action;
        const map = {
            play_search: () => `search & play "${intent.query || ""}"`,
            play_episode: () => `play ${intent.series || "?"} ` +
                `${intent.season ? "S" + intent.season : ""}` +
                `${intent.episode ? "E" + intent.episode : ""}`,
            play_season: () =>
                `play ${intent.series || "?"} season ${intent.season ?? "?"}`,
            play_next: () => `next episode of ${intent.series || "?"}`,
            seek_forward: () => `skip forward ${intent.amount || ""}`,
            seek_back: () => `skip back ${intent.amount || ""}`,
            seek: () => `jump to ${intent.position || "?"}`,
            set_volume: () => `volume to ${intent.amount || "?"}`,
            shuffle: () => `shuffle ${intent.series || intent.genre || intent.type || ""}`,
            play_collection: () => `play collection "${intent.query || ""}"`,
            sleep_timer: () => `sleep timer ${intent.amount || ""}`,
        };
        const fn = map[a];
        return (fn ? fn() : a.replaceAll("_", " ")).trim();
    },

    // ── Debug Logging Section ───────────────────────────────────────

    renderDebugLogging(container) {
        const { section } = this._createSection("Debug Logging");

        const row = document.createElement("div");
        row.className = "settings-row";

        const label = document.createElement("span");
        label.className = "label";
        label.textContent = "Write verbose logs to file";
        row.appendChild(label);

        const toggle = document.createElement("input");
        toggle.type = "checkbox";
        toggle.checked = !!this._data.debug_logging;
        toggle.style.cssText = "cursor:pointer;";
        toggle.addEventListener("change", async () => {
            toggle.disabled = true;
            try {
                const resp = await apiFetch("/api/settings/debug-logging", {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ enabled: toggle.checked }),
                });
                if (!resp || !resp.ok) {
                    toggle.checked = !toggle.checked;
                } else {
                    this._data.debug_logging = toggle.checked;
                }
            } catch {
                toggle.checked = !toggle.checked;
            }
            toggle.disabled = false;
        });
        row.appendChild(toggle);
        section.appendChild(row);

        const hint = document.createElement("div");
        hint.style.cssText = "font-size:12px;color:var(--text-muted);margin-top:4px;";
        hint.textContent = "Logs saved to data directory with automatic rotation (20 MB max).";
        section.appendChild(hint);

        container.appendChild(section);
    },
};
