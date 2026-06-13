import { createStore } from "/js/AlpineStore.js";

const API_BASE = "/plugins/camofox_browser";
const HEALTH_POLL_MS = 30000;
const VNC_POLL_MS = 3000;

export const store = createStore("camofox", {
    connected: false,
    vncUrl: null,
    _rawVncUrl: null,          // tracks the untokenized VNC URL to detect real changes
    _vncSessionKey: null,      // increments when backend starts a new visible-browser session
    _vncUrlSetAt: 0,           // timestamp when vncUrl was last set (for token refresh)
    panelVisible: false,
    panelMinimized: false,
    panelPosition: JSON.parse(localStorage.getItem("camofox_panel_pos") || '{"x": null, "y": null}'),
    panelSize: JSON.parse(localStorage.getItem("camofox_panel_size") || '{"w": 800, "h": 600}'),
    displayMode: "headless",
    activeSessions: 0,        // number of active CamoFox sessions
    agentBrowsing: false,     // true when agent has active browser sessions
    agentPaused: false,       // true when display is virtual/headed (waiting for human)
    needsUserAttention: false,
    _healthTimer: null,
    _vncTimer: null,
    _userId: "",

    init() {
        this.startHealthPoll();
    },

    startHealthPoll() {
        this._pollHealth();
        this._healthTimer = setInterval(() => this._pollHealth(), HEALTH_POLL_MS);
    },

    async _pollHealth() {
        try {
            const res = await fetchApi(`${API_BASE}/camofox_health`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "check" }),
            });
            const data = await res.json();
            const wasConnected = this.connected;
            this.connected = data.status === "connected";
            // Extract session count from health pool data
            if (data.pool) {
                const sessions = data.pool.sessions || data.pool.activeSessions || data.pool.active_sessions || 0;
                this.activeSessions = typeof sessions === "number" ? sessions : 0;
            }
            if (this.connected && !wasConnected) this.startVncPoll();
            if (!this.connected && wasConnected) this.stopVncPoll();
        } catch {
            this.connected = false;
            this.activeSessions = 0;
            this.agentBrowsing = false;
            this.needsUserAttention = false;
            this.stopVncPoll();
        }
    },

    startVncPoll() {
        if (this._vncTimer) return;
        this._pollVnc();
        this._vncTimer = setInterval(() => this._pollVnc(), VNC_POLL_MS);
    },

    stopVncPoll() {
        if (this._vncTimer) {
            clearInterval(this._vncTimer);
            this._vncTimer = null;
        }
    },

    async _pollVnc() {
        try {
            const res = await fetchApi(`${API_BASE}/camofox_vnc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ action: "get_state", userId: this._userId || "" }),
            });
            const data = await res.json();
            if (data.ok) {
                const state = data.state || {};
                // Pick up the resolved userId so future polls use the right key
                if (state._userId && !this._userId) {
                    this._userId = state._userId;
                }
                // Update browsing/blocked from server state
                const wasBrowsing = this.agentBrowsing;
                this.agentBrowsing = !!state.browsing;
                this.needsUserAttention = !!state.blocked;
                this.agentPaused = this.needsUserAttention || state.display_mode === "virtual" || state.display_mode === "headed";
                // Log state changes for debugging
                if (this.agentBrowsing !== wasBrowsing) {
                    console.log("[CamoFox]", {browsing: this.agentBrowsing, paused: this.agentPaused, vnc: state.vnc_url, mode: state.display_mode, userId: state._userId});
                }
                this.onDisplayToggle({
                    vncUrl: state.vnc_url || null,
                    vncUrlRaw: state.vnc_url_raw || null,
                    vncSessionKey: state.vnc_session_key || null,
                    displayMode: state.display_mode || "headless",
                });
            }
        } catch { /* silently ignore */ }
    },

    onDisplayToggle(data) {
        const prevUrl = this.vncUrl;
        // Compare the raw (untokenized) URL to detect real changes —
        // the tokenized URL includes an `iat` timestamp that differs on
        // every poll, so comparing it directly would reload the iframe
        // every 3 seconds and cause a reconnection loop.
        const newRaw = data.vncUrlRaw || data.vncUrl || null;
        const oldRaw = this._rawVncUrl;
        const newSessionKey = data.vncSessionKey || null;
        const oldSessionKey = this._vncSessionKey;
        // Refresh when: raw URL actually changed, or the token is
        // approaching expiration (50 min — token TTL is 1 hour), or the
        // backend reports a new visible-browser session over the same URL.
        const tokenAge = Date.now() - this._vncUrlSetAt;
        const needsRefresh =
            newRaw !== oldRaw ||
            newSessionKey !== oldSessionKey ||
            (newRaw && tokenAge > 3_000_000);
        if (needsRefresh) {
            this._rawVncUrl = newRaw;
            this._vncSessionKey = newSessionKey;
            this.vncUrl = data.vncUrl || null;
            this._vncUrlSetAt = newRaw ? Date.now() : 0;
        }
        this.displayMode = data.displayMode || "headless";
        // Auto-show panel when a VNC URL first appears while the agent
        // is actively browsing — no manual click required.
        if (this.vncUrl && !prevUrl && this.agentBrowsing) {
            this.panelVisible = true;
            this.panelMinimized = false;
        }
        if (!this.vncUrl && prevUrl) {
            this.panelVisible = false;
            this.agentPaused = false;
            this.needsUserAttention = false;
        }
    },

    async showBrowser() {
        // If panel already has a live URL, just reveal it
        if (this.vncUrl) {
            this.panelVisible = true;
            this.panelMinimized = false;
            return;
        }
        if (!this.connected) return;
        // If server already reports a visible mode (virtual/headed) and we just
        // haven't received the URL via poll yet, force-poll instead of toggling.
        if (this.displayMode === "virtual" || this.displayMode === "headed" || this._rawVncUrl) {
            this.panelVisible = true;
            this.panelMinimized = false;
            await this._pollVnc();
            return;
        }

        try {
            const res = await fetchApi(`${API_BASE}/camofox_vnc`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    action: "toggle",
                    userId: this._userId || "",
                    display_mode: "virtual",
                }),
            });
            const data = await res.json();
            if (!data.ok) {
                console.error("[CamoFox] Failed to switch to visible mode", data.error);
                return;
            }

            const state = data.state || {};
            if (state._userId) this._userId = state._userId;
            this.agentBrowsing = !!state.browsing;
            this.needsUserAttention = !!state.blocked;
            this.agentPaused = this.needsUserAttention || state.display_mode === "virtual" || state.display_mode === "headed";
            this.onDisplayToggle({
                vncUrl: state.vnc_url || null,
                vncUrlRaw: state.vnc_url_raw || null,
                vncSessionKey: state.vnc_session_key || null,
                displayMode: state.display_mode || "headless",
            });
            this.panelVisible = true;
            this.panelMinimized = false;
        } catch (error) {
            console.error("[CamoFox] Failed to request visible browser", error);
        }
    },

    togglePanel() { this.panelVisible = !this.panelVisible; },
    minimizePanel() { this.panelMinimized = true; },
    restorePanel() { this.panelMinimized = false; },

    resetPosition() {
        this.panelPosition = { x: null, y: null };
        this.panelSize = { w: 800, h: 600 };
        localStorage.removeItem("camofox_panel_pos");
        localStorage.removeItem("camofox_panel_size");
    },

    savePosition(x, y) {
        this.panelPosition = { x, y };
        localStorage.setItem("camofox_panel_pos", JSON.stringify({ x, y }));
    },

    saveSize(w, h) {
        this.panelSize = { w, h };
        localStorage.setItem("camofox_panel_size", JSON.stringify({ w, h }));
    },

    destroy() {
        this.stopVncPoll();
        if (this._healthTimer) {
            clearInterval(this._healthTimer);
            this._healthTimer = null;
        }
    },
});
