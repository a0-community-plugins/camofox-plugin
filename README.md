# CamoFox Browser

Anti-detection browser integration for Agent Zero. Provides:

- 6 grouped agent tools covering all 48 CamoFox API endpoints
- Floating VNC viewer for CAPTCHA solving and visual debugging
- Session/cookie management and auth vault integration
- Configurable server URL, API key, and geo presets

## Requirements

- CamoFox server running (default: http://localhost:9377)
- Optional: `camofox` CLI for auth vault and scripting
- For visible browser mode: `xvfb`, `x11vnc`, `websockify`, and noVNC assets at `/opt/noVNC`
- The embedded viewer now runs through the normal Agent Zero URL, so users do not need to publish extra VNC ports in Docker

---

## Virtual Display Mode (Xvfb)

When running in Docker or headless Linux environments, CamoFox can use a virtual display via Xvfb for the VNC panel. Key points:

- CamoFox spawns its **own Xvfb displays** (`:100`, `:101`) in virtual mode — separate from any manually started display
- `x11vnc` must be pointed at the **CamoFox display**, not the base `:99` display
- The VNC watchdog script (`scripts/vnc-watchdog.sh`) handles this automatically

### Startup Sequence

```bash
# 1. Start base Xvfb display
nohup Xvfb :99 -screen 0 1920x1080x24 -ac -nolisten tcp > /tmp/xvfb.log 2>&1 &

# 2. Start websockify
nohup /usr/bin/python3 /usr/bin/websockify --web /opt/noVNC 6080 127.0.0.1:5999 > /tmp/websockify.log 2>&1 &

# 3. Start CamoFox server with software rendering env vars
DISPLAY=:99 LIBGL_ALWAYS_SOFTWARE=1 GALLIUM_DRIVER=llvmpipe \
MOZ_DISABLE_OOP_COMPOSITING=1 MOZ_WEBRENDER=0 MOZ_DISABLE_RDD_SANDBOX=1 \
nohup node /usr/local/lib/node_modules/camofox-browser/dist/src/server.js > /tmp/camofox.log 2>&1 &

# 4. Toggle to virtual mode via Agent Zero tool: camofox_session toggle_display headless=virtual

# 5. Start VNC watchdog (auto-detects correct display, keeps x11vnc alive)
bash /path/to/camofox-plugin/scripts/vnc-watchdog.sh >> /tmp/camofox-watchdog.log 2>&1 &
```

---

## Google OAuth / Popup Windows in Xvfb

When using Google OAuth ("Sign in with Google") or any site that opens a popup window, the popup may render as a **black screen** in Xvfb virtual display environments. This is caused by GPU-accelerated compositing being unavailable in headless environments.

### Fix: Apply Firefox User Preferences

A `user.js` template is included in `scripts/user.js.template`. Apply it to your CamoFox browser profile:

```bash
# Find your profile directory
ls /root/.camofox/profiles/

# Copy the template (replace 'a0-agent-0' with your profile name)
cp scripts/user.js.template /root/.camofox/profiles/a0-agent-0/user.js

# Restart the browser session to apply
```

This template:
- **Disables GPU/WebRender acceleration** — fixes black rendering in Xvfb
- **Forces popup windows to open as tabs** (`browser.link.open_newwindow=3`) — prevents Google OAuth and similar popups from opening as separate black windows; they open as normal tabs instead

### Additional: Software Rendering Environment Variables

The plugin's Start button (and the startup sequence above) automatically sets these environment variables on the CamoFox server process, which are inherited by the browser:

```
LIBGL_ALWAYS_SOFTWARE=1
GALLIUM_DRIVER=llvmpipe
MOZ_DISABLE_OOP_COMPOSITING=1
MOZ_WEBRENDER=0
MOZ_DISABLE_RDD_SANDBOX=1
```

### Closing a Stuck Black Popup

If a black popup is already open and can't be closed through the browser UI:

```bash
# Install xdotool if missing
apt-get install -y xdotool

# Find and close popups on the CamoFox display
for wid in $(DISPLAY=:100 xdotool search --name '' 2>/dev/null); do
  name=$(DISPLAY=:100 xdotool getwindowname $wid 2>/dev/null)
  if echo "$name" | grep -qi 'google\|sign in'; then
    DISPLAY=:100 xdotool windowclose $wid 2>/dev/null && echo "Closed: $name"
  fi
done
```

---

## Scripts

### `scripts/vnc-watchdog.sh`

A background watchdog that monitors which display `camoufox-bin` is rendering on and auto-restarts `x11vnc` on the correct display whenever it crashes or changes. Run it after toggling to virtual display mode:

```bash
bash scripts/vnc-watchdog.sh >> /tmp/camofox-watchdog.log 2>&1 &
```

### `scripts/user.js.template`

Firefox user preferences template for fixing popup rendering in Xvfb environments. See [Google OAuth / Popup Windows in Xvfb](#google-oauth--popup-windows-in-xvfb) above.
