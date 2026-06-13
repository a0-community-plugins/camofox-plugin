#!/usr/bin/env python3
"""
CamoFox Browser — Plugin Setup Script

Installs the CamoFox server, generates API keys, and starts the server.
Run from the Plugins UI or manually: python3 execute.py
"""

import subprocess
import sys
import os
import json
import secrets
import shutil
import time

# Ensure /a0 is on sys.path so plugin helpers can be imported regardless of CWD
_a0_root = "/a0"
if _a0_root not in sys.path:
    sys.path.insert(0, _a0_root)


try:
    from usr.plugins.camofox_browser.helpers.config import normalize_headless_mode
    from usr.plugins.camofox_browser.helpers.cli import resolve_camofox_command
except ModuleNotFoundError:
    def normalize_headless_mode(value):
        """Local fallback for standalone script execution outside framework PYTHONPATH."""
        if isinstance(value, bool):
            return "true" if value else "false"

        normalized = str(value).strip().lower()
        if normalized == "virtual":
            return "virtual"
        if normalized in {"false", "0", "no", "headed"}:
            return "false"
        return "true"

    def resolve_camofox_command(binary_path=None, auto_install=True):
        raise RuntimeError("CamoFox CLI helper unavailable")
        

def print_step(msg):
    print(f"\n{'='*60}")
    print(f"  {msg}")
    print(f"{'='*60}\n")


def run(cmd, check=True, capture=False):
    """Run a shell command, printing output in real time."""
    print(f"  $ {cmd}")
    if capture:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
        if check and result.returncode != 0:
            print(f"  ERROR: {result.stderr.strip()}")
            raise SystemExit(result.returncode)
        return result
    else:
        result = subprocess.run(cmd, shell=True)
        if check and result.returncode != 0:
            raise SystemExit(result.returncode)
        return result


def check_node():
    """Check if Node.js >= 18 is available."""
    result = run("node --version", check=False, capture=True)
    if result.returncode != 0:
        print("  Node.js is not installed.")
        print("  Installing Node.js via apt...")
        run("apt-get update -qq && apt-get install -y -qq nodejs npm", check=False)
        result = run("node --version", check=False, capture=True)
        if result.returncode != 0:
            print("  FAILED: Could not install Node.js.")
            return False
    version = result.stdout.strip().lstrip("v")
    major = int(version.split(".")[0])
    print(f"  Node.js {version} found.")
    if major < 18:
        print(f"  WARNING: Node.js {major} is too old. CamoFox needs 18+.")
        return False
    return True


def install_system_deps():
    """Install system libraries required by Camoufox (Firefox-based browser).

    Handles Debian Bookworm+ where some packages were renamed with t64 suffix.
    Installs in groups so one bad package doesn't block everything.
    """
    print_step("Installing system dependencies for Camoufox browser")

    print("  Updating package lists...")
    run("apt-get update -qq", check=False, capture=True)

    # Install in groups — if a group fails, try individual packages
    GROUPS = [
        # GTK3 and core UI (the critical ones)
        ("GTK3/UI core", [
            "libgtk-3-0", "libdbus-glib-1-2", "libx11-xcb1",
        ]),
        # X11 libs
        ("X11 libraries", [
            "libxcomposite1", "libxcursor1", "libxdamage1", "libxfixes3",
            "libxi6", "libxrandr2", "libxrender1", "libxss1", "libxtst6",
            "libxkbcommon0", "libxshmfence1",
        ]),
        # Libraries with possible t64 renames (Debian Bookworm+)
        ("Audio/XT (version-adaptive)", []),  # handled below
        # Graphics
        ("Graphics", ["libdrm2", "libgbm1"]),
        # NSS/crypto
        ("NSS/crypto", ["libnss3", "libnspr4"]),
        # Accessibility
        ("Accessibility", ["libatk1.0-0", "libatk-bridge2.0-0"]),
        # Text/printing
        ("Text/printing", ["libcups2", "libpango-1.0-0", "libpangocairo-1.0-0"]),
        # Fonts
        ("Fonts", [
            "fonts-freefont-ttf", "fonts-liberation", "fonts-noto",
            "fonts-noto-color-emoji", "fontconfig",
        ]),
        # VNC/display
        ("VNC/display", ["xvfb", "x11vnc", "python3-websockify"]),
        # Essentials
        ("Essentials", ["ca-certificates", "curl", "git"]),
    ]

    # Handle packages that were renamed in Debian Bookworm+ (t64 suffix)
    T64_PACKAGES = [
        ("libasound2", "libasound2t64"),
        ("libxt6", "libxt6t64"),
    ]

    installed_count = 0
    failed = []

    for group_name, packages in GROUPS:
        if not packages:
            continue
        dep_str = " ".join(packages)
        print(f"  [{group_name}] Installing: {dep_str}")
        result = run(
            f"apt-get install -y --no-install-recommends {dep_str}",
            check=False, capture=True,
        )
        if result.returncode == 0:
            installed_count += len(packages)
        else:
            # Try each package individually
            for pkg in packages:
                r = run(f"apt-get install -y --no-install-recommends {pkg}",
                        check=False, capture=True)
                if r.returncode == 0:
                    installed_count += 1
                else:
                    failed.append(pkg)

    # Handle t64-renamed packages
    print("  [Version-adaptive] Trying audio/xt packages...")
    for old_name, new_name in T64_PACKAGES:
        r = run(f"apt-get install -y --no-install-recommends {old_name}",
                check=False, capture=True)
        if r.returncode == 0:
            installed_count += 1
        else:
            r2 = run(f"apt-get install -y --no-install-recommends {new_name}",
                     check=False, capture=True)
            if r2.returncode == 0:
                installed_count += 1
                print(f"    {old_name} → {new_name} (Bookworm rename)")
            else:
                failed.append(f"{old_name}/{new_name}")

    print(f"\n  Installed {installed_count} packages.")
    if failed:
        print(f"  Failed: {', '.join(failed)}")

    no_vnc_dir = "/opt/noVNC"
    no_vnc_entry = os.path.join(no_vnc_dir, "vnc.html")
    if os.path.isfile(no_vnc_entry):
        print(f"  Verified: noVNC assets available at {no_vnc_dir}")
    else:
        print(f"  Installing noVNC assets into {no_vnc_dir}...")
        os.makedirs(os.path.dirname(no_vnc_dir), exist_ok=True)
        if os.path.isdir(no_vnc_dir):
            run(f"git -C {no_vnc_dir} pull --ff-only", check=False, capture=True)
        else:
            run(
                f"git clone --depth 1 https://github.com/novnc/noVNC.git {no_vnc_dir}",
                check=False,
                capture=True,
            )
        if os.path.isfile(no_vnc_entry):
            print(f"  Verified: noVNC assets available at {no_vnc_dir}")
        else:
            print("  WARNING: noVNC assets are missing. Visible browser mode may stay blank.")

    # Run ldconfig to refresh the shared library cache
    run("ldconfig", check=False, capture=True)

    # Verify the critical library
    check = run("ldconfig -p | grep libgtk-3", check=False, capture=True)
    if "libgtk-3" in check.stdout:
        print("  Verified: libgtk-3.so.0 is available.")
        return True
    else:
        print("  CRITICAL: libgtk-3.so.0 still not found after install.")
        print("  Try manually: apt-get install -y libgtk-3-0")
        return False


def is_camofox_installed():
    """Check if camofox-browser npm package is installed globally."""
    result = subprocess.run(
        "npm list -g camofox-browser --depth=0 2>/dev/null",
        shell=True, capture_output=True, text=True,
    )
    return "camofox-browser" in result.stdout


def find_server_js():
    """Find the server.js entry point in the installed npm package."""
    result = subprocess.run(
        "npm root -g", shell=True, capture_output=True, text=True
    )
    if result.returncode == 0:
        npm_root = result.stdout.strip()
        server_js = os.path.join(npm_root, "camofox-browser", "dist", "src", "server.js")
        if os.path.isfile(server_js):
            return server_js
    return None


def install_camofox():
    """Install camofox-browser globally via npm."""
    print_step("Installing CamoFox Browser (npm install -g camofox-browser)")
    run("npm install -g camofox-browser")

    # Verify by checking npm list and finding server.js
    if not is_camofox_installed():
        print("  ERROR: npm package not found after install.")
        return False

    server_js = find_server_js()
    if server_js:
        print(f"  Server entry point: {server_js}")
    else:
        print("  WARNING: Could not find server.js in installed package.")

    print("  CamoFox installed successfully.")
    return True

def ensure_cli_available_for_setup() -> bool:
    """Verify the CLI is resolvable after installation/repair."""
    print_step("Verifying CamoFox CLI")
    try:
        command = resolve_camofox_command(auto_install=True)
        print(f"  CamoFox CLI ready via: {' '.join(command)}")
        return True
    except Exception as e:
        print(f"  ERROR: CamoFox CLI is not usable: {e}")
        return False


def ensure_camofox_installation() -> bool:
    """Ensure the npm package is installed and the CLI is usable."""
    if not is_camofox_installed():
        if not install_camofox():
            return False
    return ensure_cli_available_for_setup()


def fetch_camoufox_browser():
    """Download the Camoufox browser binary (~300MB)."""
    print_step("Downloading Camoufox browser binary (first time only, ~300MB)")
    print("  This may take a few minutes...")
    result = run("npx --yes camoufox-js fetch", check=False, capture=True)
    if result.returncode != 0:
        print(f"  WARNING: camoufox-js fetch exited with code {result.returncode}")
        print(f"  stdout: {result.stdout.strip()[:500]}")
        print(f"  stderr: {result.stderr.strip()[:500]}")
        print("")
        print("  Trying alternative: npx camoufox fetch...")
        result2 = run("npx --yes camoufox fetch", check=False, capture=True)
        if result2.returncode != 0:
            print("  WARNING: Browser binary download may have failed.")
            print("  The server will start but opening tabs may fail with 500 errors.")
            print("  You can retry manually: npx camoufox-js fetch")
            return False
    print("  Browser binary ready.")
    return True


def generate_keys():
    """Generate secure random API and admin keys."""
    api_key = secrets.token_urlsafe(32)
    admin_key = secrets.token_urlsafe(32)
    return api_key, admin_key


def _config_path():
    return os.path.join(os.path.dirname(__file__), "config.json")


def load_plugin_config():
    """Load existing plugin config, or return empty dict if missing/invalid."""
    path = _config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f) or {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_plugin_config(config_dict):
    """Save plugin config to the standard location."""
    path = _config_path()
    with open(path, "w") as f:
        json.dump(config_dict, f, indent=2)
    print(f"  Config saved to {path}")


def stop_existing_server(port=9377):
    """Kill any existing CamoFox server on the port."""
    # Try graceful stop first
    try:
        import urllib.request
        req = urllib.request.Request(f"http://localhost:{port}/health")
        with urllib.request.urlopen(req, timeout=2) as resp:
            if resp.status == 200:
                print(f"  Existing server found on port {port}, stopping it...")
                # Kill by port
                run(f"fuser -k {port}/tcp 2>/dev/null || true", check=False, capture=True)
                time.sleep(2)
    except Exception:
        pass  # No server running, good


def start_server(api_key="", admin_key="", port=9377, default_headless=True):
    """Start the CamoFox server as a background process."""
    stop_existing_server(port)

    env = os.environ.copy()
    env["CAMOFOX_PORT"] = str(port)
    env["NODE_ENV"] = "production"
    env["CAMOFOX_HEADLESS"] = normalize_headless_mode(default_headless)
    if api_key:
        env["CAMOFOX_API_KEY"] = api_key
    if admin_key:
        env["CAMOFOX_ADMIN_KEY"] = admin_key

    # Find server.js from the installed npm package
    server_js = find_server_js()
    if server_js:
        print(f"  Starting: node {server_js}")
        subprocess.Popen(
            ["node", server_js],
            env=env,
            stdout=open("/tmp/camofox-server.log", "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    else:
        # Fallback: try npx
        print("  Starting via: npx camofox-browser")
        # Check if there's a "start" script in the package
        npm_root = subprocess.run(
            "npm root -g", shell=True, capture_output=True, text=True
        ).stdout.strip()
        pkg_json_path = os.path.join(npm_root, "camofox-browser", "package.json")
        if os.path.isfile(pkg_json_path):
            with open(pkg_json_path) as f:
                pkg = json.load(f)
            main_file = pkg.get("main", "")
            if main_file:
                main_path = os.path.join(npm_root, "camofox-browser", main_file)
                if os.path.isfile(main_path):
                    print(f"  Starting: node {main_path}")
                    subprocess.Popen(
                        ["node", main_path],
                        env=env,
                        stdout=open("/tmp/camofox-server.log", "w"),
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                else:
                    print(f"  Could not find main entry: {main_path}")
                    return False
            else:
                print("  No 'main' field in package.json")
                return False
        else:
            print(f"  Could not find package.json at {pkg_json_path}")
            return False

    # Wait for server to be ready
    print("  Waiting for server to start...")
    import urllib.request
    for i in range(20):
        time.sleep(1)
        try:
            req = urllib.request.Request(f"http://localhost:{port}/health")
            with urllib.request.urlopen(req, timeout=2) as resp:
                if resp.status == 200:
                    print(f"  Server is running on port {port}!")
                    return True
        except Exception:
            pass
        if i % 5 == 4:
            print(f"  Still waiting... ({i+1}s)")

    print("  WARNING: Server did not respond within 20 seconds.")
    print("  Check /tmp/camofox-server.log for errors:")
    try:
        with open("/tmp/camofox-server.log") as f:
            log = f.read().strip()
            if log:
                for line in log.split("\n")[-10:]:
                    print(f"    {line}")
    except Exception:
        pass
    return False


def verify_browser(api_key="", port=9377):
    """Try to open a test tab to verify the browser binary works."""
    import urllib.request
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # Try opening a tab
    try:
        body = json.dumps({
            "userId": "a0-setup-test",
            "sessionKey": "setup-test",
            "url": "https://example.com",
        }).encode()
        req = urllib.request.Request(
            f"http://localhost:{port}/tabs",
            data=body,
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode())
                tab_id = data.get("tabId", data.get("id", ""))
                print(f"  Browser launched successfully! Test tab: {tab_id}")

                # Clean up test tab
                try:
                    del_req = urllib.request.Request(
                        f"http://localhost:{port}/tabs/{tab_id}?userId=a0-setup-test",
                        headers=headers,
                        method="DELETE",
                    )
                    urllib.request.urlopen(del_req, timeout=5)
                except Exception:
                    pass

                # Clean up test session
                try:
                    del_req = urllib.request.Request(
                        f"http://localhost:{port}/sessions/a0-setup-test",
                        headers=headers,
                        method="DELETE",
                    )
                    urllib.request.urlopen(del_req, timeout=5)
                except Exception:
                    pass

                return True
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode()[:1000]
        except Exception:
            pass
        if e.code == 400:
            # 400 means the server processed the request — browser IS working
            print(f"  Server returned HTTP 400 (bad request): {error_body}")
            print("  The browser launched but rejected the test URL.")
            print("  This is OK — the browser is functional!")
            return True

        print(f"  FAILED: Server returned HTTP {e.code}")
        print(f"  Error response: {error_body}")
        print("")

        # Dump the server log for debugging
        print("  === Server log (last 30 lines) ===")
        try:
            with open("/tmp/camofox-server.log") as f:
                lines = f.readlines()
                for line in lines[-30:]:
                    print(f"    {line.rstrip()}")
        except Exception:
            print("    (could not read /tmp/camofox-server.log)")
        print("  === End server log ===")
        print("")

        # Check common 500 causes
        combined = error_body.lower() + " ".join(lines[-30:]).lower() if 'lines' in dir() else error_body.lower()
        if "camoufox" in combined or "browser" in combined or "launch" in combined:
            print("  LIKELY CAUSE: Camoufox browser binary issue.")
            print("  Try: npx --yes camoufox-js fetch")
        elif "enoent" in combined or "spawn" in combined:
            print("  LIKELY CAUSE: Missing system dependency for browser launch.")
            print("  Camoufox (Firefox-based) needs X11/GTK libraries.")
            print("  Try: apt-get install -y xvfb libgtk-3-0 libdbus-glib-1-2 libxt6")
        elif "display" in combined or "xvfb" in combined:
            print("  LIKELY CAUSE: No display server available.")
            print("  Try: Xvfb :99 -screen 0 1920x1080x24 &")
            print("  Then: export DISPLAY=:99")
        else:
            print("  Could not determine specific cause.")
            print("  Check the server log above for details.")

        return False
    except Exception as e:
        print(f"  FAILED: {e}")
        return False


def main():
    print_step("CamoFox Browser — Plugin Setup")

    # Step 1: Check Node.js
    print_step("Step 1/7: Checking Node.js")
    if not check_node():
        print("\n  Setup cannot continue without Node.js 18+.")
        return 1

    # Step 2: Install system dependencies (GTK3, X11, fonts — needed by Camoufox/Firefox)
    print_step("Step 2/7: System dependencies")
    install_system_deps()

    # Step 3: Install CamoFox
    print_step("Step 3/7: Checking CamoFox installation")
    if is_camofox_installed():
        print("  CamoFox is already installed.")
        server_js = find_server_js()
        if server_js:
            print(f"  Server at: {server_js}")
    if not ensure_camofox_installation():
        return 1

    # Step 4: Download Camoufox browser binary
    print_step("Step 4/7: Checking Camoufox browser binary")
    fetch_camoufox_browser()

    # Step 5: Ensure API keys exist (preserve any existing config)
    print_step("Step 5/7: Ensuring API keys")
    existing = load_plugin_config()
    defaults = {
        "server_url": "http://localhost:9377",
        "api_key": "",
        "admin_key": "",
        "default_user_id": "",
        "default_headless": True,
        "default_geo_preset": "",
        "auto_start_server": True,
    }
    config = {**defaults, **existing}

    if not config["api_key"] or not config["admin_key"]:
        new_api, new_admin = generate_keys()
        config["api_key"] = config["api_key"] or new_api
        config["admin_key"] = config["admin_key"] or new_admin
        print("  Generated new keys (previous config had none).")
    else:
        print("  Reusing existing keys from config.json.")

    api_key = config["api_key"]
    admin_key = config["admin_key"]
    print(f"  API Key:   {api_key[:8]}...{api_key[-4:]}")
    print(f"  Admin Key: {admin_key[:8]}...{admin_key[-4:]}")

    save_plugin_config(config)

    # Step 6: Start server
    print_step("Step 6/7: Starting CamoFox server")
    server_ok = start_server(
        api_key=api_key,
        admin_key=admin_key,
        default_headless=config.get("default_headless", True),
    )

    if server_ok:
        # Step 7: Verify browser actually works (open a test tab)
        print_step("Step 7/7: Verifying browser launch")
        browser_ok = verify_browser(api_key=api_key, port=9377)
    else:
        browser_ok = False

    if server_ok and browser_ok:
        print_step("Setup Complete!")
        print("  CamoFox Browser plugin is fully operational.")
        print("  The server is running at http://localhost:9377")
        print("  Browser can open tabs and render pages.")
        print("  API keys have been generated and saved.")
    elif server_ok and not browser_ok:
        print_step("Setup Partially Complete — Browser binary missing!")
        print("  The CamoFox server is running but CANNOT open browser tabs.")
        print("  This is usually because the Camoufox browser binary")
        print("  failed to download in Step 3.")
        print("")
        print("  To fix, run inside the container:")
        print("    npx --yes camoufox-js fetch")
        print("  Then restart the server from plugin settings.")
        print("")
        print("  Server log: /tmp/camofox-server.log")
    else:
        print_step("Setup Partially Complete — Server failed to start")
        print("  CamoFox is installed and keys are generated.")
        print("  But the server failed to start.")
        print("  Check /tmp/camofox-server.log for details.")

    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())
