"""API handler: CamoFox setup — key generation, installation status, server start."""

import secrets
import shutil
import subprocess
import asyncio
import os

from helpers.api import ApiHandler, Request
from usr.plugins.camofox_browser.helpers.config import get_config, normalize_headless_mode
from usr.plugins.camofox_browser.helpers.client import CamofoxClient, CamofoxConnectionError


class CamofoxSetup(ApiHandler):
    """Setup operations: generate keys, check install, start server.

    Input: {"action": "generate_keys" | "check_install" | "start_server" | "full_status"}
    """

    @classmethod
    def requires_auth(cls) -> bool:
        return True

    async def process(self, input: dict, request: Request) -> dict:
        action = input.get("action", "full_status")

        if action == "generate_keys":
            return self._generate_keys()
        elif action == "check_install":
            return await self._check_install()
        elif action == "start_server":
            return await self._start_server(input)
        elif action == "full_status":
            return await self._full_status()
        else:
            return {"ok": False, "error": f"Unknown action: {action!r}"}

    def _generate_keys(self) -> dict:
        """Generate secure random API and admin keys."""
        return {
            "ok": True,
            "api_key": secrets.token_urlsafe(32),
            "admin_key": secrets.token_urlsafe(32),
        }

    @staticmethod
    def _find_server_js() -> str | None:
        """Find the CamoFox server.js from the installed npm package."""
        try:
            result = subprocess.run(
                ["npm", "root", "-g"], capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                path = os.path.join(result.stdout.strip(), "camofox-browser", "dist", "src", "server.js")
                if os.path.isfile(path):
                    return path
        except Exception:
            pass
        return None

    async def _check_install(self) -> dict:
        """Check if CamoFox and Node.js are available."""
        node_ok = shutil.which("node") is not None
        npm_ok = shutil.which("npm") is not None
        server_js = self._find_server_js()
        camofox_ok = server_js is not None
        websockify_ok = shutil.which("websockify") is not None
        novnc_ok = os.path.isfile("/opt/noVNC/vnc.html")

        node_version = ""
        if node_ok:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "node", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                node_version = stdout.decode().strip()
            except Exception:
                pass

        camofox_version = ""
        if camofox_ok and server_js:
            try:
                # Get version from package.json instead of binary
                import json as _json
                pkg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(server_js))), "package.json")
                if os.path.isfile(pkg_path):
                    with open(pkg_path) as f:
                        camofox_version = _json.load(f).get("version", "")
            except Exception:
                pass
        if not camofox_version and camofox_ok:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "npx", "camofox", "--version",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, _ = await proc.communicate()
                camofox_version = stdout.decode().strip()
            except Exception:
                pass

        return {
            "ok": True,
            "node_installed": node_ok,
            "node_version": node_version,
            "npm_installed": npm_ok,
            "camofox_installed": camofox_ok,
            "camofox_version": camofox_version,
            "websockify_installed": websockify_ok,
            "novnc_installed": novnc_ok,
        }

    async def _start_server(self, input: dict) -> dict:
        """Start CamoFox server with optional key env vars."""
        cfg = get_config()
        api_key = input.get("api_key") or cfg.get("api_key", "")
        admin_key = input.get("admin_key") or cfg.get("admin_key", "")
        port = cfg.get("server_url", "http://localhost:9377").split(":")[-1].rstrip("/")

        env = os.environ.copy()
        env["CAMOFOX_PORT"] = str(port)
        env["CAMOFOX_HEADLESS"] = normalize_headless_mode(cfg.get("default_headless", True))
        if api_key:
            env["CAMOFOX_API_KEY"] = api_key
        if admin_key:
            env["CAMOFOX_ADMIN_KEY"] = admin_key
        # Software rendering — required for correct rendering in Xvfb virtual displays
        env["LIBGL_ALWAYS_SOFTWARE"] = "1"
        env["GALLIUM_DRIVER"] = "llvmpipe"
        env["MOZ_DISABLE_OOP_COMPOSITING"] = "1"
        env["MOZ_WEBRENDER"] = "0"
        env["MOZ_DISABLE_RDD_SANDBOX"] = "1"

        # Find server.js from installed npm package
        server_js = self._find_server_js()
        if not server_js:
            return {"ok": False, "error": "CamoFox server.js not found. Run the plugin setup script first."}

        try:
            proc = await asyncio.create_subprocess_exec(
                "node", server_js,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Server stays running — don't wait for it to exit
            await asyncio.sleep(3)
        except Exception as e:
            return {"ok": False, "error": str(e)}

        # Give server a moment to start, then health check
        await asyncio.sleep(2)
        cfg = get_config()
        client = CamofoxClient(
            base_url=cfg["server_url"],
            api_key=api_key,
            admin_key=admin_key,
        )
        try:
            await client.get("/health")
            return {"ok": True, "status": "running", "message": "CamoFox server started successfully."}
        except CamofoxConnectionError:
            return {"ok": True, "status": "starting", "message": "Server start command sent. It may take a few seconds to be ready."}
        finally:
            await client.close()

    async def _full_status(self) -> dict:
        """Combined status: install check + server connectivity."""
        install = await self._check_install()
        cfg = get_config()
        server_status = "unknown"

        if install["camofox_installed"]:
            client = CamofoxClient(
                base_url=cfg["server_url"],
                api_key=cfg.get("api_key", ""),
                admin_key=cfg.get("admin_key", ""),
            )
            try:
                await client.get("/health")
                server_status = "running"
            except CamofoxConnectionError:
                server_status = "stopped"
            except Exception:
                server_status = "error"
            finally:
                await client.close()
        else:
            server_status = "not_installed"

        return {
            "ok": True,
            **install,
            "server_status": server_status,
            "has_api_key": bool(cfg.get("api_key")),
            "has_admin_key": bool(cfg.get("admin_key")),
        }
