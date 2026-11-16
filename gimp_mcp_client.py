#!/usr/bin/env python3
"""GIMP MCP Client / Server Bridge
Provides a Model Context Protocol (MCP) interface to control GIMP via TCP socket.
Compatible with GIMP 3.x and FastMCP.
"""

import os
import sys
import json
import socket
import logging
from typing import Any, Optional
from mcp.server.fastmcp import FastMCP, Context

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GimpMCPClient")

DEFAULT_HOST = os.environ.get("GIMP_MCP_HOST", "127.0.0.1")
DEFAULT_PORT = int(os.environ.get("GIMP_MCP_PORT", "9876"))


class GimpConnection:
    """Manages persistent or auto-reconnecting TCP socket connection to GIMP plugin."""

    def __init__(self, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT):
        self.host = host
        self.port = port
        self.sock: Optional[socket.socket] = None
        self.file_in = None

    def connect(self):
        if self.sock:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(15.0)
            sock.connect((self.host, self.port))
            self.sock = sock
            self.file_in = sock.makefile('r', encoding='utf-8')
            logger.info(f"Connected to GIMP at {self.host}:{self.port}")
        except Exception as e:
            self.sock = None
            self.file_in = None
            raise ConnectionError(
                f"Could not connect to GIMP at {self.host}:{self.port}. "
                "Ensure GIMP is running and the MCP Server is started "
                "(Filters > Development > Start MCP Server or Tools > Start MCP Server). "
                f"Underlying error: {e}"
            )

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
            self.file_in = None

    def send_command(self, command: str, params: Optional[dict] = None) -> dict:
        if not self.sock:
            self.connect()

        payload = {
            "command": command,
            "type": command,  # Backwards compatibility
            "params": params or {}
        }
        msg = json.dumps(payload) + "\n"

        try:
            self.sock.sendall(msg.encode('utf-8'))
            line = self.file_in.readline()
            if not line:
                raise ConnectionError("Server closed connection unexpectedly")
            return json.loads(line)
        except Exception as e:
            self.close()
            # Try once to reconnect and retry
            try:
                self.connect()
                self.sock.sendall(msg.encode('utf-8'))
                line = self.file_in.readline()
                if not line:
                    raise ConnectionError("Server closed connection on retry")
                return json.loads(line)
            except Exception as retry_err:
                self.close()
                raise ConnectionError(f"Communication error with GIMP: {retry_err}")


# Global connection instance
_gimp_conn: Optional[GimpConnection] = None


def get_connection() -> GimpConnection:
    global _gimp_conn
    if _gimp_conn is None:
        _gimp_conn = GimpConnection()
    return _gimp_conn


def execute_cmd(command: str, params: Optional[dict] = None) -> str:
    """Helper to send command and return formatted JSON string or error."""
    try:
        conn = get_connection()
        res = conn.send_command(command, params)
        if res.get("status") == "success":
            return json.dumps(res.get("result", {}), indent=2)
        else:
            return json.dumps({"error": res.get("message", "Unknown error")}, indent=2)
    except Exception as e:
        return json.dumps({"error": str(e)}, indent=2)


# Initialize FastMCP Server
mcp = FastMCP("GimpMCP", instructions="Control GNU Image Manipulation Program (GIMP 3.x) via Model Context Protocol.")


@mcp.tool()
def gimp_status() -> str:
    """Check connection status, GIMP version, and open images count.

    Returns:
    - JSON string with GIMP version, server status, and count of open images.
    """
    return execute_cmd("ping")


@mcp.tool()
def gimp_list_images() -> str:
    """List all open images in GIMP with their IDs, dimensions, and names.

    Returns:
    - JSON list of images.
    """
    return execute_cmd("image_list")


@mcp.tool()
def gimp_get_image(image_id: Optional[int] = None) -> str:
    """Get detailed information for a specific image, including layers and dimensions.
    If image_id is omitted, returns info for the first active image.

    Parameters:
    - image_id: The ID of the image (optional)

    Returns:
    - JSON object with image width, height, resolution, and layer hierarchy.
    """
    params = {}
    if image_id is not None:
        params["image_id"] = image_id
    return execute_cmd("image_get", params)


@mcp.tool()
def gimp_create_image(width: int = 800, height: int = 600, name: str = "New Image", fill_type: str = "WHITE") -> str:
    """Create a new image in GIMP with a default background layer.

    Parameters:
    - width: Width of the image in pixels (default 800)
    - height: Height of the image in pixels (default 600)
    - name: Name of the image (default 'New Image')
    - fill_type: Fill background with 'WHITE', 'TRANSPARENT', 'FOREGROUND', or 'BACKGROUND'

    Returns:
    - JSON object with new image_id and layer_id.
    """
    return execute_cmd("image_new", {
        "width": width,
        "height": height,
        "name": name,
        "fill_type": fill_type
    })


@mcp.tool()
def gimp_list_layers(image_id: int) -> str:
    """List all layers in an image.

    Parameters:
    - image_id: The ID of the image

    Returns:
    - JSON list of layers with ID, name, dimensions, opacity, and visibility.
    """
    return execute_cmd("layer_list", {"image_id": image_id})


@mcp.tool()
def gimp_create_layer(
    image_id: int,
    name: str = "New Layer",
    width: int = 0,
    height: int = 0,
    opacity: float = 100.0,
    position: int = 0
) -> str:
    """Add a new layer to an image.

    Parameters:
    - image_id: The target image ID
    - name: Layer name (default 'New Layer')
    - width: Layer width (default 0 matches image width)
    - height: Layer height (default 0 matches image height)
    - opacity: Layer opacity from 0.0 to 100.0
    - position: Stack position in the image (0 is top)

    Returns:
    - JSON object with new layer_id.
    """
    params = {
        "image_id": image_id,
        "name": name,
        "opacity": opacity,
        "position": position
    }
    if width > 0:
        params["width"] = width
    if height > 0:
        params["height"] = height
    return execute_cmd("layer_new", params)


@mcp.tool()
def gimp_set_layer_visibility(layer_id: int, visible: bool = True) -> str:
    """Show or hide a layer.

    Parameters:
    - layer_id: The ID of the layer
    - visible: True to show, False to hide

    Returns:
    - Success or error status.
    """
    return execute_cmd("layer_set_visible", {"layer_id": layer_id, "visible": visible})


@mcp.tool()
def gimp_set_layer_opacity(layer_id: int, opacity: float = 100.0) -> str:
    """Set the opacity of a layer.

    Parameters:
    - layer_id: The ID of the layer
    - opacity: Opacity from 0.0 (transparent) to 100.0 (opaque)

    Returns:
    - Success or error status.
    """
    return execute_cmd("layer_set_opacity", {"layer_id": layer_id, "opacity": opacity})


@mcp.tool()
def gimp_remove_layer(image_id: int, layer_id: int) -> str:
    """Delete a layer from an image.

    Parameters:
    - image_id: The ID of the image
    - layer_id: The ID of the layer to remove

    Returns:
    - Success or error status.
    """
    return execute_cmd("layer_remove", {"image_id": image_id, "layer_id": layer_id})


@mcp.tool()
def gimp_open_file(file_path: str) -> str:
    """Open an image file from disk into GIMP.

    Parameters:
    - file_path: Absolute or relative path to the image file

    Returns:
    - JSON object with opened image_id and dimensions.
    """
    return execute_cmd("file_load", {"path": file_path})


@mcp.tool()
def gimp_save_image(image_id: int, file_path: str) -> str:
    """Save or export an image to disk (PNG, JPEG, TIFF, XCF, etc.).

    Parameters:
    - image_id: The ID of the image to save
    - file_path: Target file path on disk

    Returns:
    - JSON object confirming saved path.
    """
    return execute_cmd("file_save", {"image_id": image_id, "path": file_path})


@mcp.tool()
def gimp_apply_filter(drawable_id: int, filter_name: str = "gegl:gaussian-blur", properties: dict = {}) -> str:
    """Apply a GEGL filter to a drawable/layer.

    Parameters:
    - drawable_id: The ID of the layer/drawable
    - filter_name: GEGL operation name (e.g. 'gegl:gaussian-blur', 'gegl:sepia', 'gegl:pixelize')
    - properties: Dictionary of filter properties (e.g. {'std-dev-x': 5.0, 'std-dev-y': 5.0})

    Returns:
    - Status message.
    """
    return execute_cmd("apply_filter", {
        "drawable_id": drawable_id,
        "filter": filter_name,
        "properties": properties
    })


@mcp.tool()
def gimp_execute_pdb(procedure_name: str, args: dict = {}) -> str:
    """Execute any procedure from the GIMP Procedural Database (PDB).

    Parameters:
    - procedure_name: Name of the procedure (e.g. 'gimp-image-scale', 'gimp-drawable-invert')
    - args: Dictionary of argument names and values

    Returns:
    - JSON string of procedure return values.
    """
    return execute_cmd("pdb_call", {"procedure": procedure_name, "args": args})


@mcp.tool()
def gimp_search_pdb(filter_text: str = "") -> str:
    """Search available procedures in GIMP's Procedural Database (PDB).

    Parameters:
    - filter_text: Search substring to filter procedures by name (e.g. 'scale', 'blur', 'layer')

    Returns:
    - JSON list of matching procedures and their blurbs.
    """
    return execute_cmd("pdb_list", {"filter": filter_text})


@mcp.tool()
def gimp_get_pdb_info(procedure_name: str) -> str:
    """Get detailed documentation and argument specifications for a PDB procedure.

    Parameters:
    - procedure_name: The procedure name to inspect

    Returns:
    - JSON object with documentation, arguments, and return value types.
    """
    return execute_cmd("pdb_info", {"procedure": procedure_name})


@mcp.tool()
def gimp_run_python(code: str) -> str:
    """Execute custom Python code inside GIMP's environment for advanced tasks.
    In the scope: Gimp, Gegl, GLib, Gio. Set variable 'result' to return data.

    Parameters:
    - code: Python code snippet to execute inside GIMP

    Returns:
    - JSON object with captured stdout output and result value.
    """
    return execute_cmd("run_python", {"code": code})


@mcp.tool()
def gimp_refresh_display() -> str:
    """Flush and update GIMP's display windows so all changes are visible immediately.

    Returns:
    - Success status.
    """
    return execute_cmd("displays_flush")


# Backward compatibility tool
@mcp.tool()
def call_api(ctx: Context, api_path: str, args: list = [], kwargs: dict = {}) -> str:
    """Call any GIMP API method dynamically (legacy compatibility).

    Parameters:
    - api_path: The path to the API method
    - args: List of positional arguments
    - kwargs: Dictionary of keyword arguments
    """
    return execute_cmd("call_api", {"api_path": api_path, "args": args, "kwargs": kwargs})


def main():
    mcp.run()


if __name__ == "__main__":
    main()
