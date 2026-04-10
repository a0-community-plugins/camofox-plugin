"""API handler: CamoFox complete stack startup and shutdown.

This handler manages the FULL CamoFox+VNC stack lifecycle:
  - Start: Node.js server + virtual display toggle + x11vnc watchdog
  - Stop: kill server + browser + x11vnc + watchdog
  - Status: comprehensive health of all stack components

This is the proper solution vs. patching individual pieces.
"""

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

from helpers.api import ApiHandler, Request
from usr.plugins.camofox_browser.helpers.config import get_config, normalize_headless_mode
from usr.plugins.camofox_browser.helpers.client import CamofoxClient, CamofoxConnectionError


SERVER_JS_SEARCH = [
    "/usr/local/lib/node_modules/camofox-browser/dist/src/server.js",
    "/usr/lib/node_modules/camofox-browser/dist/src/server.js",
]

def _get_vnc_resolution():
    """Get VNC resolution from config, returns (width, height, depth_str)."""
    cfg = get_config()
    res = cfg.get("vnc_resolution", "1280x800")
    parts = res.lower().split("x")
    w = int(parts[0]) if len(parts) >= 1 else 1280
    h = int(parts[1]) if len(parts) >= 2 else 800
    return w, h, f"{w}x{h}x24"

SOFTWARE_RENDERING_ENV = {
    "LIBGL_ALWAYS_SOFTWARE": "1",
    "GALLIUM_DRIVER": "llvmpipe",
    "MOZ_DISABLE_OOP_COMPOSITING": "1",
    "MOZ_WEBRENDER": "0",
    "MOZ_DISABLE_RDD_SANDBOX": "1",
    "DISPLAY": ":99",
}

WATCHDOG_SCRIPT = str(Path(__file__).parent.parent / "scripts" / "vnc-watchdog.sh")


class CamofoxStartup(ApiHandler):
    """Full CamoFox+VNC stack lifecycle management.

    Input: {"action": "start" | "stop" | "status" | "restart"}
    """

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict:
        action = input.get("action", "status")

        if action == "start":
            return await self._full_start()
        elif action == "stop":
            return await self._full_stop()
        elif action == "restart":
            await self._full_stop()
            await asyncio.sleep(2)
            return await self._full_start()
        elif action == "status":
            return await self._stack_status()
        else:
            return {"ok": False, "error": f"Unknown action: {action!r}"}

    def _find_server_js(self) -> str | None:
        for path in SERVER_JS_SEARCH:
            if os.path.isfile(path):
                return path
        # Try npm root -g
        try:
            result = subprocess.run(
                ["npm", "root", "-g"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                path = os.path.join(
                    result.stdout.strip(), "camofox-browser", "dist", "src", "server.js"
                )
                if os.path.isfile(path):
                    return path
        except Exception:
            pass
        return None

    def _is_server_running(self) -> bool:
        return bool(subprocess.run(
            ["pgrep", "-f", "node.*server.js"],
            capture_output=True
        ).returncode == 0)

    def _is_watchdog_running(self) -> bool:
        return bool(subprocess.run(
            ["pgrep", "-f", "vnc-watchdog"],
            capture_output=True
        ).returncode == 0)

    def _is_x11vnc_running(self) -> bool:
        return bool(subprocess.run(
            ["pgrep", "-f", "x11vnc"],
            capture_output=True
        ).returncode == 0)

    def _is_xvfb_running(self) -> bool:
        return bool(subprocess.run(
            ["pgrep", "-f", "Xvfb"],
            capture_output=True
        ).returncode == 0)

    def _is_websockify_running(self) -> bool:
        return bool(subprocess.run(
            ["pgrep", "-f", "websockify"],
            capture_output=True
        ).returncode == 0)

    async def _ensure_xvfb(self) -> bool:
        """Ensure Xvfb :99 is running."""
        if self._is_xvfb_running():
            return True
        try:
            _, _, res_str = _get_vnc_resolution()
            await asyncio.create_subprocess_exec(
                "Xvfb", ":99", "-screen", "0", res_str, "-ac", "-nolisten", "tcp",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(2)
            return self._is_xvfb_running()
        except Exception:
            return False

    async def _ensure_websockify(self) -> bool:
        """Start a FRESH single websockify instance, killing any stale ones first.
        
        Always restarts websockify to avoid stale/frozen connections where the
        noVNC client is connected to an old websockify that lost its x11vnc link.
        """
        novnc = "/opt/noVNC"
        if not os.path.isdir(novnc):
            return self._is_websockify_running()  # Can't manage it, just check
        websockify_bin = shutil.which("websockify")
        if not websockify_bin:
            return self._is_websockify_running()
        # Always kill stale instances first (use -9 to ensure clean kill)
        subprocess.run(["pkill", "-9", "-f", "websockify"], capture_output=True)
        await asyncio.sleep(2)  # Wait for port 6080 to be released
        try:
            await asyncio.create_subprocess_exec(
                "python3", websockify_bin, "--web", novnc, "6080", "127.0.0.1:5999",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(3)
            return self._is_websockify_running()
        except Exception:
            return False

    async def _ensure_server(self) -> bool:
        """Ensure CamoFox Node.js server is running with software rendering."""
        if self._is_server_running():
            return True
        server_js = self._find_server_js()
        if not server_js:
            return False
        cfg = get_config()
        env = os.environ.copy()
        env.update(SOFTWARE_RENDERING_ENV)
        _, _, res_str = _get_vnc_resolution()
        env["CAMOFOX_VNC_RESOLUTION"] = res_str
        port = cfg.get("server_url", "http://localhost:9377").split(":")[-1].rstrip("/")
        env["CAMOFOX_PORT"] = str(port)
        env["CAMOFOX_HEADLESS"] = normalize_headless_mode(cfg.get("default_headless", True))
        if cfg.get("api_key"):
            env["CAMOFOX_API_KEY"] = cfg["api_key"]
        if cfg.get("admin_key"):
            env["CAMOFOX_ADMIN_KEY"] = cfg["admin_key"]
        try:
            await asyncio.create_subprocess_exec(
                "node", server_js,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            # Wait for server to be ready
            for _ in range(10):
                await asyncio.sleep(1)
                try:
                    cfg2 = get_config()
                    client = CamofoxClient(
                        base_url=cfg2["server_url"],
                        api_key=cfg2.get("api_key", ""),
                        admin_key=cfg2.get("admin_key", ""),
                    )
                    await client.get("/health")
                    await client.close()
                    return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    async def _toggle_to_virtual(self) -> bool:
        """Force the browser into virtual display mode via the Node.js API."""
        cfg = get_config()
        client = CamofoxClient(
            base_url=cfg["server_url"],
            api_key=cfg.get("api_key", ""),
            admin_key=cfg.get("admin_key", ""),
        )
        try:
            # Step 1: Force headless to sync toggle state
            await client.post("/sessions/a0-agent-0/toggle-display", data={"headless": True})
            await asyncio.sleep(1)
            # Step 2: Toggle to virtual
            result = await client.post("/sessions/a0-agent-0/toggle-display", data={"headless": "virtual"})
            await asyncio.sleep(4)  # Wait for camoufox-bin to spawn
            return True
        except Exception:
            return False
        finally:
            await client.close()

    async def _start_watchdog(self) -> bool:
        """Start the VNC watchdog script."""
        if self._is_watchdog_running():
            return True
        if not os.path.isfile(WATCHDOG_SCRIPT):
            return False
        try:
            await asyncio.create_subprocess_exec(
                "bash", WATCHDOG_SCRIPT,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.sleep(5)  # Give watchdog time to detect display and start x11vnc
            return True
        except Exception:
            return False

    async def _full_start(self) -> dict:
        """Start the complete CamoFox+VNC stack."""
        steps = {}

        # 1. Xvfb
        xvfb_ok = await self._ensure_xvfb()
        steps["xvfb"] = {"ok": xvfb_ok, "msg": "running" if xvfb_ok else "failed to start"}

        # 2. Websockify
        ws_ok = await self._ensure_websockify()
        steps["websockify"] = {"ok": ws_ok, "msg": "running" if ws_ok else "failed or not installed"}

        # 3. CamoFox Node.js server with software rendering
        server_ok = await self._ensure_server()
        steps["server"] = {"ok": server_ok, "msg": "running" if server_ok else "failed to start"}

        if not server_ok:
            return {
                "ok": False,
                "error": "CamoFox server failed to start. Check that Node.js and camofox-browser npm package are installed.",
                "steps": steps,
            }

        # 4. Toggle to virtual display mode
        virtual_ok = await self._toggle_to_virtual()
        steps["virtual_mode"] = {"ok": virtual_ok, "msg": "active" if virtual_ok else "failed to toggle"}

        # 5. Start watchdog (auto-detects CamoFox display and starts x11vnc)
        watchdog_ok = await self._start_watchdog()
        steps["watchdog"] = {"ok": watchdog_ok, "msg": "running" if watchdog_ok else "not started"}
        steps["x11vnc"] = {"ok": self._is_x11vnc_running(), "msg": "running" if self._is_x11vnc_running() else "starting..."}

        # 6. Start openbox WM and maximize browser window to fill Xvfb display
        if virtual_ok:
            try:
                # Get the display camoufox-bin is using
                r = subprocess.run(["pgrep", "-f", "camoufox-bin"], capture_output=True, text=True)
                pids = [p for p in r.stdout.strip().split("\n") if p.strip()]
                disp = ":100"
                for pid in pids:
                    try:
                        env_data = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace")
                        for var in env_data.split("\x00"):
                            if var.startswith("DISPLAY="):
                                disp = var.split("=", 1)[1]
                                break
                    except Exception:
                        pass
                # Force browser window to fill Xvfb display using xdotool
                env_disp = os.environ.copy()
                env_disp["DISPLAY"] = disp
                vnc_w, vnc_h, _ = _get_vnc_resolution()
                await asyncio.sleep(2)
                wins = subprocess.run(
                    ["xdotool", "search", "--name", ""],
                    capture_output=True, text=True, env=env_disp
                ).stdout.strip().split("\n")
                for wid in wins:
                    if not wid.strip():
                        continue
                    name = subprocess.run(
                        ["xdotool", "getwindowname", wid],
                        capture_output=True, text=True, env=env_disp
                    ).stdout.strip().lower()
                    if any(k in name for k in ["firefox", "camoufox", "mozilla"]):
                        subprocess.run(["xdotool", "windowmove", "--sync", wid, "0", "0"],
                                       capture_output=True, env=env_disp)
                        subprocess.run(["xdotool", "windowsize", "--sync", wid, str(vnc_w), str(vnc_h)],
                                       capture_output=True, env=env_disp)
                steps["wm_maximize"] = {"ok": True, "msg": f"maximize on {disp} at {vnc_w}x{vnc_h}"}
            except Exception as e:
                steps["wm_maximize"] = {"ok": False, "msg": str(e)}

        # 7. Open default home tab using xdotool (API endpoint doesn't exist)
        if virtual_ok:
            cfg = get_config()
            home_url = cfg.get("default_home_url", "https://www.google.com")
            if home_url and home_url.strip():
                try:
                    # Find browser display
                    disp = ":100"
                    r = subprocess.run(["pgrep", "-f", "camoufox-bin"], capture_output=True, text=True)
                    for pid in r.stdout.strip().split("\n"):
                        if not pid.strip(): continue
                        try:
                            env_data = open(f"/proc/{pid}/environ", "rb").read().decode("utf-8", "replace")
                            for var in env_data.split("\x00"):
                                if var.startswith("DISPLAY="):
                                    disp = var.split("=", 1)[1]
                                    break
                        except Exception:
                            pass
                    env_disp = os.environ.copy()
                    env_disp["DISPLAY"] = disp
                    # Use xdotool to type URL in browser address bar
                    subprocess.run(["xdotool", "key", "ctrl+l"], capture_output=True, env=env_disp)
                    await asyncio.sleep(0.5)
                    subprocess.run(["xdotool", "type", "--clearmodifiers", home_url.strip()], capture_output=True, env=env_disp)
                    await asyncio.sleep(0.3)
                    subprocess.run(["xdotool", "key", "Return"], capture_output=True, env=env_disp)
                    steps["home_tab"] = {"ok": True, "msg": home_url.strip()}
                except Exception as e:
                    steps["home_tab"] = {"ok": False, "msg": str(e)}

        all_ok = server_ok and virtual_ok
        return {
            "ok": all_ok,
            "message": "CamoFox VNC stack started successfully." if all_ok else "Stack started with warnings — check steps.",
            "vnc_url": "http://localhost:6080/vnc.html?autoconnect=true&resize=scale",
            "steps": steps,
        }

    async def _full_stop(self) -> dict:
        """Stop all CamoFox+VNC stack components."""
        stopped = []

        # Kill watchdog
        r = await asyncio.create_subprocess_exec(
            "pkill", "-f", "vnc-watchdog",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await r.communicate()
        stopped.append("watchdog")

        # Kill x11vnc
        r = await asyncio.create_subprocess_exec(
            "pkill", "-f", "x11vnc",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await r.communicate()
        stopped.append("x11vnc")

        # Kill camoufox-bin
        r = await asyncio.create_subprocess_exec(
            "pkill", "-f", "camoufox-bin",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await r.communicate()
        stopped.append("camoufox-bin")

        # Kill Node.js server
        r = await asyncio.create_subprocess_exec(
            "pkill", "-f", "node.*server.js",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
        )
        await r.communicate()
        stopped.append("server")

        await asyncio.sleep(1)
        return {
            "ok": True,
            "message": "CamoFox VNC stack stopped.",
            "stopped": stopped,
        }

    async def _stack_status(self) -> dict:
        """Return status of all stack components."""
        cfg = get_config()
        server_health = None
        browser_connected = False
        try:
            client = CamofoxClient(
                base_url=cfg["server_url"],
                api_key=cfg.get("api_key", ""),
                admin_key=cfg.get("admin_key", ""),
            )
            server_health = await client.get("/health")
            browser_connected = server_health.get("browserConnected", False)
            await client.close()
        except Exception:
            pass

        return {
            "ok": True,
            "components": {
                "xvfb": self._is_xvfb_running(),
                "websockify": self._is_websockify_running(),
                "server": server_health is not None,
                "browser_connected": browser_connected,
                "x11vnc": self._is_x11vnc_running(),
                "watchdog": self._is_watchdog_running(),
            },
            "server_health": server_health,
        }
