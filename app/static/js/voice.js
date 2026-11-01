/* HailFrequency — Voice Controller */

class VoiceController {
    constructor() {
        this.audio = new AudioCapture();
        this.micButton = null;
        this.statusText = null;
        this.responseCard = null;
        this.waveformBars = [];
        this.animationFrame = null;
        this._initDone = false;
        this._held = false;
        this._autoPaused = false;
        this._autoPauseServerId = null;
        this._autoPauseDevice = null;
        this._browseHistory = [];
    }

    init() {
        this.micButton = document.getElementById("mic-button");
        this.statusText = document.getElementById("status-text");
        this.responseCard = document.getElementById("response-card");
        this.waveformBars = Array.from(document.querySelectorAll(".waveform-bar"));

        if (!this.micButton) return;

        // HOLD-TO-TALK: pointerdown to start, pointerup to stop and send.
        // _held tracks the physical press so startRecording() can tell
        // whether the finger is still down once the mic stream arrives
        // (it isn't after a first-run permission dialog).
        this.micButton.addEventListener("pointerdown", (e) => {
            e.preventDefault();
            this._held = true;
            this.startRecording();
        });

        this.micButton.addEventListener("pointerup", (e) => {
            e.preventDefault();
            this._held = false;
            if (this.audio.isRecording) this.stopAndSend();
        });

        // Handle pointer leaving the button while held
        this.micButton.addEventListener("pointerleave", () => {
            this._held = false;
            if (this.audio.isRecording) this.stopAndSend();
        });

        // Prevent context menu on long press (mobile)
        this.micButton.addEventListener("contextmenu", (e) => e.preventDefault());

        // First-run discoverability: until the first command lands, show an
        // example phrase instead of the bare "hold to speak" prompt
        try {
            if (!localStorage.getItem("hf_used_voice") && this.statusText) {
                const hints = [
                    'Try: "Play a random sci-fi movie"',
                    'Try: "What’s new?"',
                    'Try: "Shuffle Frasier"',
                    'Try: "What can I say?"',
                ];
                this.statusText.textContent =
                    hints[Math.floor(Math.random() * hints.length)];
            }
        } catch { /* private mode */ }
    }

    async startRecording() {
        // Fire-and-forget: pre-warm Ollama model while user speaks
        // (debounced — no point re-warming on every mic press)
        const now = Date.now();
        if (!this._lastWarmup || now - this._lastWarmup > 30000) {
            this._lastWarmup = now;
            apiFetch("/api/voice/warmup", { method: "POST" }).catch(() => {});
        }

        // Auto-pause playback so the room is quiet for STT
        this._autoPaused = false;
        const np = typeof NowPlayingCard !== "undefined" ? NowPlayingCard._currentData : null;
        if (np && np.state !== "Paused" && np.server_id) {
            this._autoPaused = true;
            this._autoPauseServerId = np.server_id;
            this._autoPauseDevice = np.device_name || null;
            const payload = { server_id: np.server_id };
            if (np.device_name) payload.device = np.device_name;
            apiFetch("/api/playback/pause", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            }).catch(() => {});
        }

        try {
            // Permission priming: on the very first use, getUserMedia blocks
            // on the browser's permission dialog, so by the time the stream
            // arrives the press is long over — recording now captures garbage.
            // Detect that case and spend this press on setup only.
            const firstUse = !this.audio._initialized;
            let alreadyGranted = null;
            if (firstUse && navigator.permissions && navigator.permissions.query) {
                try {
                    const st = await navigator.permissions.query({ name: "microphone" });
                    alreadyGranted = st.state === "granted";
                } catch (_) { /* Safari: mic not queryable — fall through to timing */ }
            }

            // Init audio (also re-initializes if stream died after screen lock)
            const t0 = Date.now();
            await this.audio.init();
            const promptShown = firstUse && alreadyGranted !== true && Date.now() - t0 > 500;

            if (promptShown || !this._held) {
                // Dialog ate the press (or the finger is already gone) —
                // don't record; ask for a fresh press instead.
                this.statusText.textContent = "Mic ready — hold to speak";
                this._maybeUnpause();
                return;
            }

            // Resume AudioContext synchronously inside the pointerdown gesture
            // iOS Safari REQUIRES this to be in the gesture handler
            this.audio.resumeContext();

            this.audio.start();
            this.micButton.classList.add("recording");
            this.statusText.textContent = "Listening...";
            this.startWaveform();
        } catch (err) {
            console.error("Mic access error:", err);
            this.statusText.textContent = this._micErrorMessage(err);
            this._maybeUnpause();
        }
    }

    _micErrorMessage(err) {
        // Distinguish the real cause — "denied" hides several distinct problems
        if (!window.isSecureContext || !navigator.mediaDevices) {
            return "Mic needs HTTPS — use the secure address";
        }
        switch (err && err.name) {
            case "NotAllowedError":
            case "PermissionDeniedError":
                return "Mic blocked — check browser & OS permissions";
            case "NotFoundError":
            case "DevicesNotFoundError":
                return "No microphone found";
            case "NotReadableError":
                return "Mic busy — in use by another app?";
            default:
                return "Microphone access denied";
        }
    }

    async stopAndSend() {
        if (this._sending) return;
        this._sending = true;
        this.micButton.classList.remove("recording");
        this.statusText.textContent = "Processing...";
        this.stopWaveform();

        const { blob, mimeType } = await this.audio.stop();

        // Don't send empty recordings
        if (blob.size < 100) {
            this.statusText.textContent = "Hold mic to speak";
            this._sending = false;
            this._maybeUnpause();
            return;
        }

        this._startThinkingTimers();

        // Determine file extension from mimeType
        let ext = ".webm";
        if (mimeType.includes("mp4") || mimeType.includes("aac") || mimeType.includes("m4a")) {
            ext = ".m4a";
        } else if (mimeType.includes("ogg")) {
            ext = ".ogg";
        } else if (mimeType.includes("wav")) {
            ext = ".wav";
        }

        const formData = new FormData();
        formData.append("audio", blob, `recording${ext}`);
        if (HF.activeDevice) {
            formData.append("device", HF.activeDevice);
        }
        if (HF.activeServerId) {
            formData.append("server_id", HF.activeServerId);
        }

        try {
            const resp = await apiFetch("/api/voice", {
                method: "POST",
                body: formData,
            });
            if (!resp) return;
            const data = await resp.json();

            // Clear browse history on new voice command
            this._browseHistory = [];
            this.showResponse(data);

            // Refresh the now-playing card shortly after a successful command.
            // The backend responds immediately (no embedded playback state),
            // so poll once after the media server has had a moment to react.
            if (data.success && typeof NowPlayingCard !== "undefined") {
                setTimeout(() => NowPlayingCard._poll(), 1200);
            }

            // Auto-unpause unless the command changed playback or shows browsable results.
            // All seek variants are handled server-side (backend unpauses atomically after
            // seeking) to avoid a client round-trip race that can leave the client paused.
            const action = data.intent?.action;
            const hasCards = data.disambiguation && data.disambiguation.length > 0;
            const serverHandlesUnpause = action === "seek" || action === "seek_forward" || action === "seek_back";
            if (hasCards || action === "pause" || action === "stop" || serverHandlesUnpause) {
                this._autoPaused = false; // don't unpause later
            } else {
                this._maybeUnpause();
            }
        } catch (err) {
            console.error("Voice request error:", err);
            this.showResponse({
                transcript: "",
                result: "Connection error. Please try again.",
                success: false,
            });
            this._maybeUnpause();
        } finally {
            this._clearThinkingTimers();
            this._sending = false;
        }
    }

    showResponse(data) {
        this._clearThinkingTimers();
        this.statusText.textContent = "Hold mic to speak";
        try { localStorage.setItem("hf_used_voice", "1"); } catch { /* private mode */ }

        if (!this.responseCard) return;

        // Save current data for browse history back navigation
        this._currentResponseData = data;

        const transcriptEl = this.responseCard.querySelector(".transcript");
        const resultEl = this.responseCard.querySelector(".result");

        transcriptEl.textContent = data.transcript || "";

        // Check for disambiguation
        if (data.disambiguation && data.disambiguation.length > 0) {
            resultEl.textContent = "";
            resultEl.innerHTML = "";

            // Back navigation if we have browse history
            if (this._browseHistory.length > 0) {
                const backLink = document.createElement("div");
                backLink.className = "disambig-back";
                backLink.textContent = "\u2190 Back";
                backLink.addEventListener("click", () => this._browseBack());
                resultEl.appendChild(backLink);
            }

            const header = document.createElement("div");
            header.className = "disambig-header";
            header.textContent = data.result || "Multiple matches found:";
            resultEl.appendChild(header);

            const container = document.createElement("div");
            container.className = "disambig-cards";

            data.disambiguation.forEach((candidate) => {
                const card = document.createElement("button");
                card.className = "disambig-card";
                card.type = "button";

                // Watch state dot for episodes
                if (candidate.watch_state && candidate.watch_state !== "unwatched") {
                    const dot = document.createElement("span");
                    dot.className = `disambig-watch-dot ${candidate.watch_state === "watched" ? "watched" : "in-progress"}`;
                    const titleRow = document.createElement("div");
                    titleRow.className = "disambig-title-row";
                    titleRow.appendChild(dot);
                    const titleEl = document.createElement("span");
                    titleEl.className = "disambig-title";
                    titleEl.textContent = candidate.title;
                    titleRow.appendChild(titleEl);
                    card.appendChild(titleRow);
                } else {
                    const titleEl = document.createElement("div");
                    titleEl.className = "disambig-title";
                    titleEl.textContent = candidate.title;
                    card.appendChild(titleEl);
                }

                if (candidate.subtitle) {
                    const subEl = document.createElement("div");
                    subEl.className = "disambig-subtitle";
                    subEl.textContent = candidate.subtitle;
                    card.appendChild(subEl);
                }

                card.addEventListener("click", () => {
                    this.selectDisambiguation(card, candidate, container);
                });

                container.appendChild(card);
            });

            resultEl.appendChild(container);
        } else {
            // "Which device?" clarification carries the device names in
            // intent.options — show them, not just the bare question
            let text = data.result || "";
            const opts = data.intent && data.intent.options;
            if (Array.isArray(opts) && opts.length) {
                text = `${text} ${opts.join(", ")}`.trim();
            }
            resultEl.textContent = text;
            if (data.learned) {
                const learned = document.createElement("div");
                learned.className = "learned-note";
                learned.textContent = "✓ Learned — instant next time";
                resultEl.appendChild(learned);
            }
        }

        this.responseCard.classList.toggle("error", !data.success && !data.disambiguation);
        this.responseCard.classList.add("visible");

        // Play TTS audio if present
        this._playTTS(data.tts_audio_url);
    }

    _playTTS(url) {
        if (!url) return;
        try {
            const audio = new Audio(url);
            audio.play().catch((e) => console.warn("TTS playback failed:", e));
        } catch (e) {
            console.warn("TTS audio error:", e);
        }
    }

    _startThinkingTimers() {
        this._clearThinkingTimers();
        const engine = (HF.ollamaEngine) || "unknown";
        this._thinkTimers = [
            setTimeout(() => {
                this.statusText.textContent = "Thinking...";
            }, 2500),
        ];
        if (engine === "cpu") {
            this._thinkTimers.push(setTimeout(() => {
                this.statusText.textContent =
                    "Still thinking — CPU mode is slow the first time. " +
                    "I'll remember this phrase.";
            }, 12000));
        } else {
            this._thinkTimers.push(setTimeout(() => {
                this.statusText.textContent = "The AI parser is being slow...";
            }, 45000));
        }
    }

    _clearThinkingTimers() {
        (this._thinkTimers || []).forEach(clearTimeout);
        this._thinkTimers = [];
    }

    _maybeUnpause() {
        if (!this._autoPaused) return;
        this._autoPaused = false;
        const payload = { server_id: this._autoPauseServerId };
        if (this._autoPauseDevice) payload.device = this._autoPauseDevice;
        apiFetch("/api/playback/unpause", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        }).catch(() => {});
    }

    _browseBack() {
        if (this._browseHistory.length === 0) return;
        const prev = this._browseHistory.pop();
        this.showResponse(prev);
    }

    async selectDisambiguation(selectedCard, candidate, container) {
        if (this._disambiguating) return;
        this._disambiguating = true;

        const isBrowse = candidate.action && candidate.action.startsWith("browse_");

        // Visual feedback: mark selected, fade others
        container.querySelectorAll(".disambig-card").forEach((c) => {
            if (c === selectedCard) {
                c.classList.add("selected");
            } else {
                c.classList.add("dismissed");
            }
        });

        // Show spinner on selected card
        const spinner = document.createElement("div");
        spinner.className = "spinner";
        spinner.style.cssText = "width:16px;height:16px;border-width:2px;position:absolute;right:14px;top:50%;margin-top:-8px;";
        selectedCard.style.position = "relative";
        selectedCard.appendChild(spinner);

        try {
            let url, payload;

            if (isBrowse) {
                url = "/api/playback/browse";
                payload = {
                    action: candidate.action,
                    item_id: candidate.id,
                    parent_id: candidate.parent_id || null,
                    server_id: candidate.server_id,
                };
            } else {
                url = "/api/playback/disambiguate";
                payload = {
                    item_id: candidate.id,
                    action: candidate.action,
                    server_id: candidate.server_id,
                    device: HF.activeDevice || null,
                };
                // Season cards for "play episode N" carry the series and
                // episode number so one tap plays it directly
                if (candidate.parent_id) payload.parent_id = candidate.parent_id;
                if (candidate.episode != null) payload.episode = candidate.episode;
            }

            const resp = await apiFetch(url, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });

            if (!resp) return;
            const data = await resp.json();

            if (isBrowse && data.disambiguation && data.disambiguation.length > 0) {
                // Drill-down: push current view to history, render new cards
                this._pushBrowseHistory();
                this.showResponse(data);
            } else {
                // Play action or non-browse result. 409 = "which device?"
                // (SessionAmbiguousError) and 400 carry `error`, not `result`.
                const resultEl = this.responseCard.querySelector(".result");
                resultEl.innerHTML = "";
                let text = data.result || data.error || "";
                if (resp.status === 409 && Array.isArray(data.options)) {
                    text = `${data.error || "Which device?"} ${data.options.join(", ")}`;
                }
                resultEl.textContent = text;
                this.responseCard.classList.toggle("error", !data.success);

                // Refresh now-playing card after the server reacts
                if (data.success && typeof NowPlayingCard !== "undefined") {
                    setTimeout(() => NowPlayingCard._poll(), 1200);
                }

                // Play TTS confirmation
                this._playTTS(data.tts_audio_url);
            }
        } catch (err) {
            console.error("Disambiguation error:", err);
            const resultEl = this.responseCard.querySelector(".result");
            resultEl.innerHTML = "";
            resultEl.textContent = "Failed to play selection. Please try again.";
            this.responseCard.classList.add("error");
        } finally {
            this._disambiguating = false;
        }
    }

    _pushBrowseHistory() {
        if (this._currentResponseData) {
            this._browseHistory.push(this._currentResponseData);
        }
    }

    startWaveform() {
        const draw = () => {
            if (!this.audio.isRecording) return;
            const data = this.audio.getFrequencyData();
            const step = Math.floor(data.length / this.waveformBars.length) || 1;
            this.waveformBars.forEach((bar, i) => {
                const value = data[i * step] || 0;
                bar.style.height = `${Math.max(2, (value / 255) * 48)}px`;
            });
            this.animationFrame = requestAnimationFrame(draw);
        };
        draw();
    }

    stopWaveform() {
        if (this.animationFrame) cancelAnimationFrame(this.animationFrame);
        this.waveformBars.forEach((bar) => (bar.style.height = "2px"));
    }
}
