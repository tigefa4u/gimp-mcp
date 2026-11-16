#!/usr/bin/env python3
"""Installation script for GIMP-MCP plugin.
Detects installed GIMP version, locates the correct plugin directory,
installs the gimp_mcp_server plugin, and verifies registration.
"""

import os
import sys
import shutil
import subprocess
import glob


def get_gimp_version():
    try:
        out = subprocess.check_output(["gimp", "--version"], stderr=subprocess.STDOUT, text=True).strip()
        return out
    except Exception:
        return None


def find_gimp_plugin_dir():
    home = os.path.expanduser("~")
    # Priority search for GIMP 3.x configs first, then 2.x
    candidates = [
        os.path.join(home, ".config", "GIMP", "3.2", "plug-ins"),
        os.path.join(home, ".config", "GIMP", "3.0", "plug-ins"),
        os.path.join(home, ".var", "app", "org.gimp.GIMP", "config", "GIMP", "3.2", "plug-ins"),
        os.path.join(home, ".var", "app", "org.gimp.GIMP", "config", "GIMP", "3.0", "plug-ins"),
    ]

    for p in candidates:
        if os.path.exists(os.path.dirname(p)):
            os.makedirs(p, exist_ok=True)
            return p

    # Fallback search for any directory matching ~/.config/GIMP/*/plug-ins
    matches = glob.glob(os.path.join(home, ".config", "GIMP", "*", "plug-ins"))
    if matches:
        return sorted(matches, reverse=True)[0]

    # Default to 3.2 if directory structure doesn't exist yet
    default_path = os.path.join(home, ".config", "GIMP", "3.2", "plug-ins")
    os.makedirs(default_path, exist_ok=True)
    return default_path


def install_plugin(symlink=False):
    src_file = os.path.abspath(os.path.join(os.path.dirname(__file__), "gimp_mcp_server.py"))
    if not os.path.exists(src_file):
        print(f"Error: Source file '{src_file}' not found.")
        sys.exit(1)

    gimp_ver = get_gimp_version()
    print(f"Detected GIMP: {gimp_ver or 'Not found in PATH'}")

    plugin_dir = find_gimp_plugin_dir()
    print(f"Target plug-ins directory: {plugin_dir}")

    # In GIMP 3, each plugin must be in its own directory matching the executable name
    target_subdir = os.path.join(plugin_dir, "gimp_mcp_server")
    os.makedirs(target_subdir, exist_ok=True)
    target_file = os.path.join(target_subdir, "gimp_mcp_server.py")

    if os.path.exists(target_file) or os.path.islink(target_file):
        try:
            os.remove(target_file)
        except Exception:
            pass

    if symlink:
        os.symlink(src_file, target_file)
        print(f"Symlinked: {target_file} -> {src_file}")
    else:
        shutil.copy2(src_file, target_file)
        print(f"Copied: {src_file} -> {target_file}")

    # Make executable
    os.chmod(target_file, 0o755)
    print("Permissions set to 0755 (executable)")

    # Test registration with gimp-console if available
    gimp_console = shutil.which("gimp-console-3.2") or shutil.which("gimp-console")
    if gimp_console:
        print("\nVerifying registration with GIMP...")
        try:
            out = subprocess.check_output(
                [gimp_console, "-c", "--batch-interpreter", "python-fu-eval", "-b", "pass", "--quit"],
                stderr=subprocess.STDOUT,
                text=True,
                timeout=10
            )
            if "gimp_mcp_server" in out or "gimp-mcp-server" in out:
                print("✓ Plug-in recognized and registered successfully!")
            else:
                # Check pluginrc
                config_dir = os.path.dirname(plugin_dir)
                pluginrc = os.path.join(config_dir, "pluginrc")
                if os.path.exists(pluginrc) and "gimp-mcp-server" in open(pluginrc, encoding="utf-8", errors="ignore").read():
                    print("✓ Plug-in verified in pluginrc!")
                else:
                    print("✓ Plug-in installed (will be indexed on next GIMP launch).")
        except Exception as e:
            print(f"Note: Verification skipped ({e}). Plug-in is installed.")
    else:
        print("Note: gimp-console not found. Plug-in will be indexed on next GIMP launch.")

    print("\n" + "=" * 50)
    print("Installation Complete!")
    print("=" * 50)
    print("How to use:")
    print("1. Launch GIMP.")
    print("2. Open menu: Filters > Development > Start MCP Server (or Tools > Start MCP Server).")
    print("3. Start the MCP client/bridge:")
    print("     python3 gimp_mcp_client.py")
    print("=" * 50)


if __name__ == "__main__":
    use_symlink = "--link" in sys.argv or "-l" in sys.argv
    install_plugin(symlink=use_symlink)
