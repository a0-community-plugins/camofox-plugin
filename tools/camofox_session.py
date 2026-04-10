"""CamoFox session tool — display toggling, cookie management, session persistence."""

import json
from helpers.tool import Tool, Response

from usr.plugins.camofox_browser.helpers.client import (
    CamofoxClient,
    CamofoxConnectionError,
    CamofoxApiError,
    CamofoxAuthError,
)
from usr.plugins.camofox_browser.helpers.config import get_config
from usr.plugins.camofox_browser.helpers.user_id import resolve_user_id
from usr.plugins.camofox_browser.helpers import state as shared_state

# Module-level singleton client.
_client_instance: CamofoxClient | None = None


def _get_client() -> CamofoxClient:
    global _client_instance
    if _client_instance is None:
        cfg = get_config()
        _client_instance = CamofoxClient(
            base_url=cfg["server_url"],
            api_key=cfg.get("api_key", ""),
            admin_key=cfg.get("admin_key", ""),
        )
    return _client_instance


class CamofoxSession(Tool):
    """CamoFox session management.

    REST-based actions (always work if server is running):
        toggle_display, import_cookies, export_cookies, destroy

    CLI-based actions (require camofox CLI — optional):
        save_session, load_session, list_sessions, delete_session
    """

    async def execute(self, **kwargs) -> Response:
        action = self.args.get("action", "")
        user_id = resolve_user_id(self.agent)

        try:
            result = await self._dispatch(action, user_id)
            return Response(message=result, break_loop=False)
        except CamofoxConnectionError as e:
            return Response(
                message=f"CamoFox server unreachable: {e}",
                break_loop=False,
            )
        except (CamofoxAuthError, CamofoxApiError) as e:
            return Response(
                message=f"CamoFox error: {e}",
                break_loop=False,
            )
        except Exception as e:
            return Response(
                message=f"CamoFox session error: {e}",
                break_loop=False,
            )

    async def _dispatch(self, action: str, user_id: str) -> str:
        client = _get_client()
        await client.ensure_initialized()
        a = self.args

        # --- REST-based actions (no CLI needed) ---

        if action == "toggle_display":
            headless = a.get("headless", True)
            resp = await client.post(
                f"/sessions/{user_id}/toggle-display",
                data={"headless": headless},
            )
            vnc_url = resp.get("vncUrl")
            mode = str(resp.get("headless", headless))
            if mode in ("True", "true"):
                display_mode = "headless"
            elif mode == "virtual":
                display_mode = "virtual"
                # Node.js server doesn't return vncUrl — construct it from config
                if not vnc_url:
                    cfg = get_config()
                    server_url = cfg.get("server_url", "http://localhost:9377")
                    port = server_url.rstrip("/").split(":")[-1]
                    if not port.isdigit():
                        port = "6080"
                    vnc_url = f"http://localhost:6080/vnc.html?autoconnect=true&resize=scale"
            else:
                display_mode = "headed"
            shared_state.set_vnc(user_id, vnc_url, display_mode)
            if vnc_url and display_mode in ("virtual", "headed"):
                return (
                    f"Browser now visible via VNC. All previous tabs are invalidated — "
                    f"create new tabs and snapshot before interacting.\nVNC URL: {vnc_url}"
                )
            return (
                "Browser switched to headless mode. All previous tabs are invalidated — "
                "create new tabs and snapshot before interacting."
            )

        elif action == "import_cookies":
            cookies = a.get("cookies", [])
            resp = await client.post(
                f"/sessions/{user_id}/cookies",
                data={"cookies": cookies},
            )
            return json.dumps(resp, indent=2)

        elif action == "export_cookies":
            tab_id = a.get("tabId", "")
            resp = await client.get(f"/tabs/{tab_id}/cookies?userId={user_id}")
            return json.dumps(resp, indent=2)

        elif action == "destroy":
            resp = await client.delete(f"/sessions/{user_id}")
            return json.dumps(resp, indent=2)

        # --- CLI-based actions (optional, graceful degradation) ---

        elif action in ("save_session", "load_session", "list_sessions", "delete_session"):
            return await self._cli_action(action, user_id)

        else:
            return (
                f"Unknown action: {action!r}. Available: "
                "toggle_display, import_cookies, export_cookies, "
                "save_session, load_session, list_sessions, delete_session, destroy"
            )

    async def _cli_action(self, action: str, user_id: str) -> str:
        """Handle CLI-dependent session actions with graceful fallback."""
        # Lazy import to avoid crashing if CLI is unavailable
        from usr.plugins.camofox_browser.helpers.cli import (
            CamofoxCli,
            CamofoxCliNotFoundError,
            CamofoxCliError,
        )

        try:
            cli = CamofoxCli(default_user=user_id)
        except Exception:
            cli = None

        a = self.args

        if action == "save_session":
            if cli is None:
                return self._cli_unavailable_message("save_session",
                    "Use import_cookies/export_cookies instead for cookie persistence via REST API.")
            try:
                name = a.get("name", "")
                args = ["session", "save", name]
                if a.get("tabId"):
                    args.append(a["tabId"])
                return json.dumps(await cli.execute(*args), indent=2)
            except CamofoxCliNotFoundError:
                return self._cli_unavailable_message("save_session",
                    "Use export_cookies to save cookies via REST API instead.")
            except CamofoxCliError as e:
                return f"CLI error: {e}"

        elif action == "load_session":
            if cli is None:
                return self._cli_unavailable_message("load_session",
                    "Use import_cookies to restore cookies via REST API instead.")
            try:
                name = a.get("name", "")
                args = ["session", "load", name]
                if a.get("tabId"):
                    args.append(a["tabId"])
                return json.dumps(await cli.execute(*args), indent=2)
            except CamofoxCliNotFoundError:
                return self._cli_unavailable_message("load_session",
                    "Use import_cookies to restore cookies via REST API instead.")
            except CamofoxCliError as e:
                return f"CLI error: {e}"

        elif action == "list_sessions":
            if cli is None:
                return self._cli_unavailable_message("list_sessions")
            try:
                return json.dumps(await cli.execute("session", "list"), indent=2)
            except (CamofoxCliNotFoundError, CamofoxCliError) as e:
                return f"CLI unavailable for list_sessions: {e}"

        elif action == "delete_session":
            if cli is None:
                return self._cli_unavailable_message("delete_session",
                    "Use destroy to clean up the entire session via REST API instead.")
            try:
                name = a.get("name", "")
                return json.dumps(await cli.execute("session", "delete", name, "--force"), indent=2)
            except (CamofoxCliNotFoundError, CamofoxCliError) as e:
                return f"CLI error: {e}"

        return f"Unknown CLI action: {action}"

    @staticmethod
    def _cli_unavailable_message(action: str, alternative: str = "") -> str:
        msg = (
            f"The '{action}' action requires the CamoFox CLI which is not available in this environment. "
        )
        if alternative:
            msg += alternative
        else:
            msg += "This action is not critical for browsing — you can proceed with open, snapshot, click, and other REST-based actions."
        return msg

    def get_log_object(self):
        return self.agent.context.log.log(
            type="tool",
            heading=f"icon://swap_horiz {self.agent.agent_name}: CamoFox Session",
            content="",
            kvps=self.args,
        )
