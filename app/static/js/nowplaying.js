/* HailFrequency — Now Playing Card */

const NowPlayingCard = {
    _pollInterval: null,
    _currentData: null,

    update(data) {
        const card = document.getElementById("now-playing");
        if (!card) return;

        if (!data) {
            this._currentData = null;
            card.classList.remove("visible");
            return;
        }

        this._currentData = data;
        this._render(card, data);
        card.classList.add("visible");
    },

    hide() {
        const card = document.getElementById("now-playing");
        if (card) card.classList.remove("visible");
        this._currentData = null;
    },

    startPolling() {
        this.stopPolling();
        this._poll(); // immediate first poll
        this._pollInterval = setInterval(() => this._poll(), 5000);
    },

    stopPolling() {
        if (this._pollInterval) {
            clearInterval(this._pollInterval);
            this._pollInterval = null;
        }
    },

    async _poll() {
        try {
            const params = new URLSearchParams();
            if (typeof HF !== "undefined" && HF.activeDevice) params.set("device", HF.activeDevice);
            if (typeof HF !== "undefined" && HF.activeServerId) params.set("server_id", HF.activeServerId);
            const qs = params.toString();
            const resp = await apiFetch(`/api/now-playing${qs ? "?" + qs : ""}`);
            if (!resp) return;
            const data = await resp.json();
            this.update(data.now_playing);
        } catch {
            // Silent fail — next poll will retry
        }
    },

    _render(card, data) {
        card.innerHTML = "";

        // Body: poster + info
        const body = document.createElement("div");
        body.className = "np-body";

        // Poster artwork
        if (data.server_id && data.item_id) {
            const img = document.createElement("img");
            img.className = "np-poster";
            img.alt = "";
            img.src = `/api/media/${data.server_id}/image/${data.item_id}`;
            img.onerror = () => {
                const ph = document.createElement("div");
                ph.className = "np-poster-placeholder";
                ph.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M2 17l5-5 4 4 3-3 8 8"/></svg>';
                img.replaceWith(ph);
            };
            body.appendChild(img);
        } else {
            const ph = document.createElement("div");
            ph.className = "np-poster-placeholder";
            ph.innerHTML = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="12" cy="12" r="3"/><path d="M2 17l5-5 4 4 3-3 8 8"/></svg>';
            body.appendChild(ph);
        }

        // Info column
        const info = document.createElement("div");
        info.className = "np-info";

        let title = data.item_name || "Unknown";
        let subtitle = "";

        if (data.series_name) {
            title = data.series_name;
            subtitle = `${data.episode_info || ""} \u201C${data.item_name}\u201D`;
        }

        const titleEl = document.createElement("div");
        titleEl.className = "np-title";
        titleEl.textContent = title;
        info.appendChild(titleEl);

        if (subtitle) {
            const subEl = document.createElement("div");
            subEl.className = "np-subtitle";
            subEl.textContent = subtitle;
            info.appendChild(subEl);
        }

        // Device + state
        const deviceEl = document.createElement("div");
        deviceEl.className = "np-device";

        const dot = document.createElement("span");
        dot.className = `np-dot ${data.state === "Paused" ? "paused" : "playing"}`;
        deviceEl.appendChild(dot);

        const stateText = document.createElement("span");
        stateText.textContent = `${data.state || "Playing"} on ${data.device_name || "unknown"}`;
        deviceEl.appendChild(stateText);

        info.appendChild(deviceEl);

        // Progress bar
        if (data.runtime_ticks > 0) {
            const progress = document.createElement("div");
            progress.className = "np-progress";

            const bar = document.createElement("div");
            bar.className = "np-progress-bar";
            const fill = document.createElement("div");
            fill.className = "np-progress-fill";
            const pct = Math.min(100, (data.position_ticks / data.runtime_ticks) * 100);
            fill.style.width = `${pct}%`;
            bar.appendChild(fill);
            progress.appendChild(bar);

            const timeEl = document.createElement("div");
            timeEl.className = "np-time";
            const elapsed = document.createElement("span");
            elapsed.textContent = `${this._formatTime(data.position_ticks)} / ${this._formatTime(data.runtime_ticks)}`;
            timeEl.appendChild(elapsed);
            const remaining = document.createElement("span");
            remaining.className = "np-remaining";
            remaining.textContent = this._formatRemaining(
                data.runtime_ticks - data.position_ticks
            );
            timeEl.appendChild(remaining);
            progress.appendChild(timeEl);

            info.appendChild(progress);
        }

        body.appendChild(info);
        card.appendChild(body);

        // Transport controls
        const controls = document.createElement("div");
        controls.className = "np-controls";

        const serverId = data.server_id;
        const isPaused = data.state === "Paused";

        controls.appendChild(this._controlBtn(
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 6h2v12H6zm3.5 6l8.5 6V6z"/></svg>',
            () => this._onTransport("previous", serverId)
        ));
        controls.appendChild(this._controlBtn(
            isPaused
                ? '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>'
                : '<svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor"><path d="M6 4h4v16H6zm8 0h4v16h-4z"/></svg>',
            () => this._onTransport(isPaused ? "unpause" : "pause", serverId)
        ));
        controls.appendChild(this._controlBtn(
            '<svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor"><path d="M6 18l8.5-6L6 6v12zM16 6v12h2V6h-2z"/></svg>',
            () => this._onTransport("next", serverId)
        ));
        controls.appendChild(this._controlBtn(
            '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="4" y="4" width="16" height="16" rx="2"/></svg>',
            () => this._onTransport("stop", serverId)
        ));

        card.appendChild(controls);
    },

    _controlBtn(svgHtml, onClick) {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = "np-control-btn";
        btn.innerHTML = svgHtml;
        btn.addEventListener("click", onClick);
        return btn;
    },

    async _onTransport(action, serverId) {
        try {
            const payload = { server_id: serverId };
            if (this._currentData?.device_name) {
                payload.device = this._currentData.device_name;
            }
            await apiFetch(`/api/playback/${action}`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            // Poll immediately to update the card
            setTimeout(() => this._poll(), 500);
        } catch {
            // Silent fail
        }
    },

    _formatRemaining(ticks) {
        const mins = Math.max(0, Math.ceil(ticks / 10000000 / 60));
        if (mins >= 60) {
            const h = Math.floor(mins / 60);
            const m = mins % 60;
            return m ? `${h}h ${m}m left` : `${h}h left`;
        }
        return `${mins} min left`;
    },

    _formatTime(ticks) {
        const totalSeconds = Math.floor(ticks / 10000000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        if (hours > 0) {
            return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
        }
        return `${minutes}:${String(seconds).padStart(2, "0")}`;
    },
};
