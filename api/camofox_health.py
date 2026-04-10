"""API handler: CamoFox server health check, start, and stop."""

import asyncio
import os
import shutil

from helpers.api import ApiHandler, Request

from usr.plugins.camofox_browser.helpers.client import (
    CamofoxClient,
    CamofoxConnectionError,
    CamofoxApiError,
    CamofoxAuthError,
)
from usr.plugins.camofox_browser.helpers.cli import (
    CamofoxCli,
    CamofoxCliNotFoundError,
    CamofoxCliError,
)
from usr.plugins.camofox_browser.helpers.config import get_config


class CamofoxHealth(ApiHandler):
    """Check, start, or stop the CamoFox server.

    Input: {"action": "check" | "start" | "stop"}

    Responses:
        check success  -> {"ok": True, "status": "connected", "pool": {...}}
        check failure  -> {"ok": True, "status": "unreachable", "error": "<msg>"}
        start success  -> {"ok": True, "action": "start", "result": {...}}
        stop success   -> {"ok": True, "action": "stop", "result": {...}}
        error          -> {"ok": False, "error": "<msg>"}
    """

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict:
        action = input.get("action", "check")

        if action == "check":
            return await self._check()
        elif action == "diagnose":
            return await self._diagnose()
        elif action == "start":
            return await self._server_control("start")
        elif action == "stop":
            return await self._server_control("stop")
        else:
            return {"ok": False, "error": f"Unknown action: {action!r}"}

    async def _check(self) -> dict:
        cfg = get_config()
        client = CamofoxClient(
            base_url=cfg["server_url"],
            api_key=cfg.get("api_key", ""),
            admin_key=cfg.get("admin_key", ""),
        )
        try:
            data = await client.get("/health")
            return {"ok": True, "status": "connected", "pool": data}
        except (CamofoxConnectionError, CamofoxApiError, CamofoxAuthError) as e:
            return {"ok": True, "status": "unreachable", "error": str(e)}
        finally:
            await client.close()

    async def _diagnose(self) -> dict:
        """Deep diagnostic: health check + try to open a test tab to verify browser works."""
        cfg = get_config()
        client = CamofoxClient(
            base_url=cfg["server_url"],
            api_key=cfg.get("api_key", ""),
            admin_key=cfg.get("admin_key", ""),
        )
        result = {"ok": True, "checks": {}}
        try:
            # 1. Health check
            health = await client.get("/health")
            result["checks"]["health"] = {"pass": True, "data": health}

            # 2. Try opening a test tab (this is what actually launches the browser)
            try:
                tab_data = await client.post("/tabs", data={
                    "userId": "a0-diagnostic",
                    "sessionKey": "diagnostic",
                    "url": "https://example.com",
                })
                tab_id = tab_data.get("tabId", tab_data.get("id", ""))
                result["checks"]["browser_launch"] = {"pass": True, "tabId": tab_id}

                # 3. Clean up test tab
                try:
                    await client.delete(f"/tabs/{tab_id}?userId=a0-diagnostic")
                except Exception:
                    pass  # cleanup is best-effort

            except CamofoxApiError as e:
                result["checks"]["browser_launch"] = {
                    "pass": False,
                    "error": str(e),
                    "hint": "The browser binary (Camoufox) may not be installed. "
                            "Run the setup script again, or manually run: npx camoufox-js fetch",
                }

            # 4. Check presets (lightweight, verifies routing works)
            try:
                presets = await client.get("/presets")
                result["checks"]["presets"] = {"pass": True, "count": len(presets) if isinstance(presets, (list, dict)) else 0}
            except Exception as e:
                result["checks"]["presets"] = {"pass": False, "error": str(e)}

            # 5. Check the visible-browser runtime pieces used by VNC/noVNC mode.
            has_websockify = bool(shutil.which("websockify"))
            has_novnc = os.path.isfile("/opt/noVNC/vnc.html")
            if has_websockify and has_novnc:
                result["checks"]["viewer_runtime"] = {
                    "pass": True,
                    "websockify": shutil.which("websockify"),
                    "novnc": "/opt/noVNC/vnc.html",
                }
            else:
                missing = []
                if not has_websockify:
                    missing.append("websockify")
                if not has_novnc:
                    missing.append("/opt/noVNC/vnc.html")
                result["checks"]["viewer_runtime"] = {
                    "pass": False,
                    "missing": missing,
                    "hint": "Visible browser mode needs websockify and noVNC assets. Re-run the setup script so the main app can proxy the embedded viewer for you.",
                }

        except CamofoxConnectionError as e:
            result["checks"]["health"] = {"pass": False, "error": str(e)}
        except Exception as e:
            result["checks"]["health"] = {"pass": False, "error": str(e)}
        finally:
            await client.close()

        # Summary
        all_pass = all(c.get("pass") for c in result["checks"].values())
        result["all_pass"] = all_pass
        if not all_pass:
            failing = [k for k, v in result["checks"].items() if not v.get("pass")]
            result["summary"] = f"Failing checks: {', '.join(failing)}"
        else:
            result["summary"] = "All checks passed — CamoFox is fully operational."

        return result

    async def _server_control(self, action: str) -> dict:
        cfg = get_config()
        # Try CamofoxCli first; fall back to direct subprocess control
        try:
            cli = CamofoxCli(
                binary_path=cfg.get("binary_path") or None,
                default_user=cfg.get("default_user_id", ""),
            )
            result = await cli.execute("server", action)
            return {"ok": True, "action": action, "result": result}
        except (CamofoxCliNotFoundError, CamofoxCliError):
            pass  # Fall through to subprocess fallback
        except Exception:
            pass

        # Subprocess fallback (no camofox CLI required)
        if action == "stop":
            try:
                proc = await asyncio.create_subprocess_exec(
                    "pkill", "-f", "node.*server.js",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                # Also kill any lingering camoufox-bin processes
                proc2 = await asyncio.create_subprocess_exec(
                    "pkill", "-f", "camoufox-bin",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc2.communicate()
                return {"ok": True, "action": "stop", "result": "Server stopped via pkill."}
            except Exception as e:
                return {"ok": False, "error": f"Stop failed: {e}"}

        elif action == "start":
            import shutil as _shutil
            # Find server.js
            server_js = None
            try:
                result = await asyncio.create_subprocess_exec(
                    "npm", "root", "-g",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await result.communicate()
                if result.returncode == 0:
                    candidate = os.path.join(stdout.decode().strip(), "camofox-browser", "dist", "src", "server.js")
                    if os.path.isfile(candidate):
                        server_js = candidate
            except Exception:
                pass

            if not server_js:
                return {"ok": False, "error": "server.js not found — cannot start server."}

            port = cfg.get("server_url", "http://localhost:9377").split(":")[-1].rstrip("/")
            env = os.environ.copy()
            env["CAMOFOX_PORT"] = str(port)
            if cfg.get("api_key"):
                env["CAMOFOX_API_KEY"] = cfg["api_key"]
            if cfg.get("admin_key"):
                env["CAMOFOX_ADMIN_KEY"] = cfg["admin_key"]
            # Software rendering env vars
            env["LIBGL_ALWAYS_SOFTWARE"] = "1"
            env["GALLIUM_DRIVER"] = "llvmpipe"
            env["MOZ_DISABLE_OOP_COMPOSITING"] = "1"
            env["MOZ_WEBRENDER"] = "0"
            env["MOZ_DISABLE_RDD_SANDBOX"] = "1"

            try:
                await asyncio.create_subprocess_exec(
                    "node", server_js,
                    env=env,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.sleep(4)
                return {"ok": True, "action": "start", "result": "Server start command sent."}
            except Exception as e:
                return {"ok": False, "error": f"Start failed: {e}"}

        return {"ok": False, "error": f"Unknown action: {action!r}"}
