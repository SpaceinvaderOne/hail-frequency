/* HailFrequency — Browse tab
 *
 * A small visual companion to voice: continue watching, next up, recently
 * added, genre chips, search — tap a card, confirm, it plays on the
 * selected device. Deliberately NOT a web-UI rebuild.
 */

const HFBrowse = {
    _stack: [],

    load() {
        this.home();
    },

    _root() {
        return document.getElementById("browse-content");
    },

    _serverQ() {
        const sid = HF.activeServerId || "";
        return sid ? `?server_id=${encodeURIComponent(sid)}` : "";
    },

    _deviceLabel() {
        const select = document.getElementById("active-device");
        if (select && select.value && select.selectedIndex >= 0) {
            const text = select.options[select.selectedIndex].textContent;
            return text.split(" - ")[0];
        }
        return "Auto";
    },

    // ── Home ────────────────────────────────────────────────────────

    async home() {
        const root = this._root();
        if (!root) return;
        this._stack = [];
        root.innerHTML = '<div class="empty-state">Scanning library...</div>';
        try {
            const resp = await apiFetch(`/api/browse/home${this._serverQ()}`);
            if (!resp) return;
            if (!resp.ok) {
                const err = await resp.json().catch(() => ({}));
                root.innerHTML = "";
                root.appendChild(this._empty(err.error || "Couldn't reach the media server."));
                return;
            }
            const data = await resp.json();
            this._renderHome(data);
        } catch {
            root.innerHTML = "";
            root.appendChild(this._empty("Couldn't reach the media server."));
        }
    },

    _renderHome(data) {
        const root = this._root();
        root.innerHTML = "";

        if (data.sessions && data.sessions.length) {
            root.appendChild(this._sessionsStrip(data.sessions));
        }
        const rows = [
            ["Continue Watching", data.continue_watching, true],
            ["Next Up", data.next_up, false],
            ["Recently Added", data.recently_added, false],
        ];
        let any = false;
        for (const [title, cards, resumeRow] of rows) {
            if (cards && cards.length) {
                any = true;
                root.appendChild(this._posterRow(title, cards, resumeRow));
            }
        }
        if (data.genres && data.genres.length) {
            root.appendChild(this._genreChips(data.genres));
        }
        if (!any) {
            root.appendChild(this._empty("Nothing to show yet — play something and check back."));
        }
    },

    _sessionsStrip(sessions) {
        const strip = document.createElement("div");
        strip.className = "browse-sessions";
        sessions.forEach((s) => {
            const row = document.createElement("div");
            row.className = "browse-session-row";
            const state = document.createElement("span");
            state.className = `bs-state ${s.state.toLowerCase()}`;
            state.textContent = s.state === "Playing" ? "▶" : (s.state === "Paused" ? "⏸" : "○");
            const text = document.createElement("span");
            text.className = "bs-text";
            text.textContent = s.item
                ? `${s.item} on ${s.device_name}`
                : `${s.device_name} (${s.client}) idle`;
            row.appendChild(state);
            row.appendChild(text);
            strip.appendChild(row);
        });
        return strip;
    },

    _posterRow(title, cards, resumeRow) {
        const section = document.createElement("div");
        section.className = "browse-section";
        const h = document.createElement("div");
        h.className = "browse-row-title";
        h.textContent = title;
        section.appendChild(h);
        const row = document.createElement("div");
        row.className = "browse-row";
        cards.forEach((c) => row.appendChild(this._posterCard(c, resumeRow)));
        section.appendChild(row);
        return section;
    },

    _posterCard(card, preferResume) {
        const btn = document.createElement("button");
        btn.className = "poster-card";
        btn.type = "button";

        const wrap = document.createElement("div");
        wrap.className = "poster-wrap";
        const img = document.createElement("img");
        img.loading = "lazy";
        img.alt = "";
        img.src = `/api/media/${card.server_id}/image/${card.image_id || card.id}`;
        img.addEventListener("error", () => {
            img.remove();
            const fb = document.createElement("div");
            fb.className = "poster-fallback";
            fb.textContent = (card.title || "?").charAt(0).toUpperCase();
            wrap.prepend(fb);
        });
        wrap.appendChild(img);

        if (card.watch_state === "watched") {
            const dot = document.createElement("span");
            dot.className = "poster-dot watched";
            wrap.appendChild(dot);
        }
        if (card.progress) {
            const bar = document.createElement("div");
            bar.className = "poster-progress";
            const fill = document.createElement("div");
            fill.style.width = `${Math.min(100, card.progress)}%`;
            bar.appendChild(fill);
            wrap.appendChild(bar);
        }
        btn.appendChild(wrap);

        const t = document.createElement("div");
        t.className = "poster-title";
        t.textContent = card.title;
        btn.appendChild(t);
        if (card.subtitle) {
            const s = document.createElement("div");
            s.className = "poster-sub";
            s.textContent = card.subtitle;
            btn.appendChild(s);
        }

        btn.addEventListener("click", () => this._tap(card, preferResume));
        return btn;
    },

    _genreChips(genres) {
        const section = document.createElement("div");
        section.className = "browse-section";
        const h = document.createElement("div");
        h.className = "browse-row-title";
        h.textContent = "Genres";
        section.appendChild(h);
        const wrap = document.createElement("div");
        wrap.className = "genre-chips";
        genres.forEach((g) => {
            const chip = document.createElement("button");
            chip.className = "genre-chip";
            chip.type = "button";
            chip.textContent = g;
            chip.addEventListener("click", () => this.genre(g));
            wrap.appendChild(chip);
        });
        section.appendChild(wrap);
        return section;
    },

    _empty(text) {
        const div = document.createElement("div");
        div.className = "empty-state";
        div.textContent = text;
        return div;
    },

    // ── Drill-ins (genre, search, series) ───────────────────────────

    async genre(name) {
        await this._results(
            name,
            `/api/browse/genre${this._serverQ()}${this._serverQ() ? "&" : "?"}genre=${encodeURIComponent(name)}`,
        );
    },

    async search(q) {
        await this._results(
            `"${q}"`,
            `/api/browse/search${this._serverQ()}${this._serverQ() ? "&" : "?"}q=${encodeURIComponent(q)}`,
        );
    },

    async _results(title, url) {
        const root = this._root();
        root.innerHTML = '<div class="empty-state">Scanning...</div>';
        try {
            const resp = await apiFetch(url);
            if (!resp) return;
            const data = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                this._renderResults(title, [], data.error);
                return;
            }
            this._renderResults(title, data.items || []);
        } catch {
            this._renderResults(title, [], "Couldn't reach the media server.");
        }
    },

    _renderResults(title, cards, errorText) {
        const root = this._root();
        root.innerHTML = "";
        root.appendChild(this._backLink());
        const h = document.createElement("div");
        h.className = "browse-row-title";
        h.textContent = title;
        root.appendChild(h);
        if (errorText || !cards.length) {
            root.appendChild(this._empty(errorText || "Nothing found."));
            return;
        }
        const grid = document.createElement("div");
        grid.className = "browse-grid";
        cards.forEach((c) => grid.appendChild(this._posterCard(c, false)));
        root.appendChild(grid);
    },

    async drillSeries(card) {
        await this._drill(card.title, {
            action: "browse_series",
            item_id: card.id,
            server_id: card.server_id,
        });
    },

    async _drill(title, payload) {
        const root = this._root();
        root.innerHTML = '<div class="empty-state">Loading...</div>';
        try {
            const resp = await apiFetch("/api/playback/browse", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!resp) return;
            const data = await resp.json().catch(() => ({}));
            const items = data.disambiguation || [];
            this._renderList(title, items, data.error);
        } catch {
            this._renderList(title, [], "Couldn't load that.");
        }
    },

    _renderList(title, items, errorText) {
        const root = this._root();
        root.innerHTML = "";
        root.appendChild(this._backLink());
        const h = document.createElement("div");
        h.className = "browse-row-title";
        h.textContent = title;
        root.appendChild(h);
        if (errorText || !items.length) {
            root.appendChild(this._empty(errorText || "Nothing here."));
            return;
        }
        const list = document.createElement("div");
        list.className = "disambig-cards";
        items.forEach((c) => {
            const cardBtn = document.createElement("button");
            cardBtn.className = "disambig-card";
            cardBtn.type = "button";
            const titleRow = document.createElement("div");
            titleRow.className = "disambig-title-row";
            if (c.watch_state && c.watch_state !== "unwatched") {
                const dot = document.createElement("span");
                dot.className = `disambig-watch-dot ${c.watch_state === "watched" ? "watched" : "in-progress"}`;
                titleRow.appendChild(dot);
            }
            const titleEl = document.createElement("span");
            titleEl.className = "disambig-title";
            titleEl.textContent = c.title;
            titleRow.appendChild(titleEl);
            cardBtn.appendChild(titleRow);
            if (c.subtitle) {
                const sub = document.createElement("div");
                sub.className = "disambig-subtitle";
                sub.textContent = c.subtitle;
                cardBtn.appendChild(sub);
            }
            cardBtn.addEventListener("click", () => this._tap(c, false));
            list.appendChild(cardBtn);
        });
        root.appendChild(list);
    },

    _backLink() {
        const back = document.createElement("div");
        back.className = "disambig-back";
        back.textContent = "← Back";
        back.addEventListener("click", () => {
            const prev = this._stack.pop();
            if (prev) {
                prev();
            } else {
                this.home();
            }
        });
        return back;
    },

    _pushCurrent(rerender) {
        this._stack.push(rerender);
    },

    // ── Tap handling ────────────────────────────────────────────────

    _tap(card, preferResume) {
        if (card.action === "browse_series") {
            this._pushCurrent(this._snapshot());
            this.drillSeries(card);
            return;
        }
        if (card.action === "browse_season") {
            this._pushCurrent(this._snapshot());
            this._drill(card.title, {
                action: "browse_season",
                item_id: card.id,
                parent_id: card.parent_id,
                server_id: card.server_id,
            });
            return;
        }
        this._confirm(card, preferResume);
    },

    _snapshot() {
        // Capture the current DOM so Back restores it without refetching
        const root = this._root();
        const nodes = Array.from(root.childNodes);
        return () => {
            root.innerHTML = "";
            nodes.forEach((n) => root.appendChild(n));
        };
    },

    _confirm(card, preferResume) {
        const backdrop = document.createElement("div");
        backdrop.className = "browse-sheet-backdrop";
        const sheet = document.createElement("div");
        sheet.className = "browse-sheet";

        const title = document.createElement("div");
        title.className = "sheet-title";
        title.textContent = card.title;
        sheet.appendChild(title);
        const sub = document.createElement("div");
        sub.className = "sheet-sub";
        sub.textContent = (card.subtitle ? card.subtitle + " • " : "") +
            `Device: ${this._deviceLabel()}`;
        sheet.appendChild(sub);

        const close = () => backdrop.remove();

        const canResume = !!card.resume_ticks;
        if (canResume) {
            const pct = card.progress ? ` (${card.progress}%)` : "";
            sheet.appendChild(this._sheetBtn(`Resume${pct}`, "primary", () => {
                close();
                this._play(card, true);
            }));
            sheet.appendChild(this._sheetBtn("Play from start", "", () => {
                close();
                this._play(card, false);
            }));
        } else {
            sheet.appendChild(this._sheetBtn("Play", "primary", () => {
                close();
                this._play(card, false);
            }));
        }
        sheet.appendChild(this._sheetBtn("Cancel", "cancel", close));

        backdrop.addEventListener("click", (e) => {
            if (e.target === backdrop) close();
        });
        backdrop.appendChild(sheet);
        document.body.appendChild(backdrop);
    },

    _sheetBtn(label, kind, onClick) {
        const b = document.createElement("button");
        b.className = `sheet-btn ${kind}`;
        b.type = "button";
        b.textContent = label;
        b.addEventListener("click", onClick);
        return b;
    },

    async _play(card, resume) {
        this._toast("Sending...");
        try {
            const payload = {
                item_id: card.id,
                action: card.action === "play_next" ? "play_next" : "play_search",
                server_id: card.server_id,
                device: HF.activeDevice || null,
            };
            if (resume) payload.resume = true;
            const resp = await apiFetch("/api/playback/disambiguate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            if (!resp) return;
            const data = await resp.json().catch(() => ({}));
            if (resp.status === 409 && data.options) {
                this._toast(`Which device? Pick one in the Voice tab: ${data.options.join(", ")}`, 5000);
                return;
            }
            this._toast(data.result || data.error || "Done.", 3500);
        } catch {
            this._toast("Connection error.", 3500);
        }
    },

    _toast(text, ms = 2000) {
        let toast = document.getElementById("browse-toast");
        if (!toast) {
            toast = document.createElement("div");
            toast.id = "browse-toast";
            document.body.appendChild(toast);
        }
        toast.textContent = text;
        toast.classList.add("visible");
        clearTimeout(this._toastTimer);
        this._toastTimer = setTimeout(() => toast.classList.remove("visible"), ms);
    },

    initSearch() {
        const input = document.getElementById("browse-search-input");
        if (!input) return;
        input.addEventListener("keydown", (e) => {
            if (e.key === "Enter" && input.value.trim()) {
                this._stack = [];
                this.search(input.value.trim());
                input.blur();
            }
        });
    },
};
