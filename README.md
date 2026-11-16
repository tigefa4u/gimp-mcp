# GIMP-MCP: Integrating GIMP with AI through Model Context Protocol

**GIMP-MCP** connects the **GNU Image Manipulation Program (GIMP 3.x / 2.10+)** with AI models using the **Model Context Protocol (MCP)**. This integration enables AI assistants (such as Claude, Antigravity, Cursor, and custom MCP clients) to interact directly with GIMP: inspecting open canvases, creating layers, scaling images, applying GEGL filters, executing PDB procedures, and running custom Python scripts in real time.

---

## Table of Contents

- [Features](#features)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Usage](#usage)
  - [1. Start MCP Server in GIMP](#1-start-mcp-server-in-gimp)
  - [2. Run the MCP Client / Bridge](#2-run-the-mcp-client--bridge)
  - [3. Configure in Your AI / MCP Client](#3-configure-in-your-ai--mcp-client)
- [Available MCP Tools](#available-mcp-tools)
- [Architecture](#architecture)
- [Contributing](#contributing)
- [License](#license)

---

## Features

- **Full GIMP 3.x & 2.10+ Compatibility**: Native support for GIMP 3.x GObject Introspection (`gi.repository.Gimp`, `Gegl`, `GLib`, `Gtk`).
- **Interactive Control GUI**: When started from GIMP's menu, a lightweight status window appears showing connection state, live activity logs, and a Stop button without blocking or freezing GIMP.
- **Headless & Batch Support**: Can also run headlessly in batch mode or automated workflows using `gimp-console`.
- **1000+ PDB Procedures**: Execute any procedure from GIMP's Procedural Database with automatic type conversion for images, drawables, files, and colors.
- **GEGL Filters**: Apply filters such as Gaussian Blur, Pixelize, Sepia, and more directly to layers.
- **Full Python Scripting**: Execute arbitrary Python snippets inside GIMP's runtime for advanced compositing.
- **Thread-Safe Architecture**: All external socket calls are dispatched onto GIMP's main GLib event loop via `GLib.idle_add`, preventing UI freezes and crashes.

---

## Prerequisites

- **GIMP 3.0+** (tested on GIMP 3.2.6) or GIMP 2.10.
- **Python 3.10+** with `PyGObject` installed.
- Required Python dependencies:
  ```bash
  pip install -r requirements.txt
  ```

---

## Installation

### Automatic Installation

Run the provided installation script:

```bash
python3 install.py
```

The installer will:
1. Detect your installed GIMP version (e.g. GIMP 3.2.6).
2. Locate the user plug-ins directory (`~/.config/GIMP/3.2/plug-ins/`).
3. Create the plugin directory structure (`gimp_mcp_server/gimp_mcp_server.py`).
4. Set executable permissions and verify registration with GIMP.

To install as a symbolic link (convenient for development):
```bash
python3 install.py --link
```

### Manual Installation (GIMP 3.x)

1. Create a dedicated subdirectory in your GIMP 3 plug-ins folder:
   ```bash
   mkdir -p ~/.config/GIMP/3.2/plug-ins/gimp_mcp_server
   ```
2. Copy `gimp_mcp_server.py` into that directory:
   ```bash
   cp gimp_mcp_server.py ~/.config/GIMP/3.2/plug-ins/gimp_mcp_server/gimp_mcp_server.py
   chmod +x ~/.config/GIMP/3.2/plug-ins/gimp_mcp_server/gimp_mcp_server.py
   ```
3. Restart GIMP.

---

## Usage

### 1. Start MCP Server in GIMP

1. Open GIMP on your desktop.
2. In the top menu, go to:
   - **Filters** > **Development** > **Start MCP Server**  
   *(or **Tools** > **Start MCP Server**)*
3. A small dialog will appear confirming:
   ```
   GIMP MCP Server is active
   Listening on 127.0.0.1:9876
   ```
4. You can keep this window open while working in GIMP. When finished, click **Stop Server** or close the window.

### 2. Run the MCP Client / Bridge

Run the FastMCP bridge process:

```bash
python3 gimp_mcp_client.py
```

By default, it connects to `127.0.0.1:9876`. You can customize the host or port using environment variables:
```bash
GIMP_MCP_PORT=9876 python3 gimp_mcp_client.py
```

### 3. Configure in Your AI / MCP Client

Add GIMP-MCP to your MCP client configuration (e.g., Claude Desktop `claude_desktop_config.json` or Antigravity MCP settings):

```json
{
  "mcpServers": {
    "gimp": {
      "command": "python3",
      "args": ["/home/sugeng/Projects/Github/gimp-mcp/gimp_mcp_client.py"],
      "env": {
        "GIMP_MCP_PORT": "9876"
      }
    }
  }
}
```

---

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `gimp_status` | Check if GIMP is connected, get GIMP version and open canvas count |
| `gimp_list_images` | List all open images with ID, width, height, and title |
| `gimp_get_image` | Inspect active or specific image details, dimensions, and layer tree |
| `gimp_create_image` | Create a new canvas with customizable dimensions and fill color |
| `gimp_list_layers` | List all layers in an image |
| `gimp_create_layer` | Add a new layer with custom dimensions, opacity, and blend mode |
| `gimp_set_layer_visibility` | Toggle visibility of any layer |
| `gimp_set_layer_opacity` | Adjust opacity (0.0 - 100.0) of a layer |
| `gimp_remove_layer` | Delete a layer from an image |
| `gimp_open_file` | Open an existing image file from disk |
| `gimp_save_image` | Save/export image to PNG, JPEG, TIFF, or XCF |
| `gimp_apply_filter` | Apply GEGL filters (e.g. `gegl:gaussian-blur`, `gegl:sepia`) |
| `gimp_execute_pdb` | Execute any of the 1,000+ GIMP Procedural Database (PDB) commands |
| `gimp_search_pdb` | Search available PDB procedures by keyword |
| `gimp_get_pdb_info` | Inspect procedure documentation, input arguments, and return types |
| `gimp_run_python` | Run arbitrary Python code directly inside GIMP's runtime environment |
| `gimp_refresh_display` | Force GIMP to redraw and flush display changes to screen |

---

## Architecture

```
+------------------------------------+        +----------------------------------------+
|             AI Client              |        |                  GIMP                  |
| (Claude Desktop, Antigravity, etc) |        |                                        |
+-----------------+------------------+        |  +----------------------------------+  |
                  |                           |  |      gimp_mcp_server Plugin      |  |
                  | MCP Protocol (stdio)      |  |  (Filters > Dev > Start Server)  |  |
                  v                           |  |                                  |  |
+------------------------------------+        |  |  +----------------------------+  |  |
|         gimp_mcp_client.py         |        |  |  | TCP Socket Server (:9876)  |  |  |
|              (FastMCP)             |        |  |  +-------------+--------------+  |  |
+-----------------+------------------+        |  +----------------|-----------------+  |
                  |                           |                   |                    |
                  | TCP Socket (JSON lines)   |                   | GLib.idle_add      |
                  +--------------------------->-------------------+                    |
                                              |                   v                    |
                                              |       GIMP Main UI & PDB Core          |
                                              |        (Thread-Safe Execution)         |
                                              +----------------------------------------+
```

---

## Contributing

Contributions to GIMP-MCP are welcome! Whether you are fixing bugs, improving GIMP 3.x API coverage, adding new MCP tools, or updating documentation, here is how you can contribute:

### Development Setup

1. **Fork and clone the repository**:
   ```bash
   git clone https://github.com/<your-username>/gimp-mcp.git
   cd gimp-mcp
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Install the plugin in development (symlink) mode**:
   Using the `--link` flag links your working copy directly into GIMP's plug-ins directory so any code edits in `gimp_mcp_server.py` take effect immediately without having to re-copy:
   ```bash
   python3 install.py --link
   ```

### Development & Testing

1. **Test plug-in registration**:
   Verify that GIMP indexes the plugin without errors:
   ```bash
   gimp-console -c --batch-interpreter python-fu-eval -b "pass" --quit
   ```

2. **Run the FastMCP bridge**:
   ```bash
   python3 gimp_mcp_client.py
   ```

3. **Verify socket communication**:
   With GIMP running (or using `gimp-console` in batch mode), test sending commands to verify socket requests and PDB procedures return valid JSON.

### Submitting Changes

1. **Create a feature branch**:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Commit your changes**:
   Make sure commit messages are descriptive and concise:
   ```bash
   git commit -m "Add feature: your feature description"
   ```

3. **Push to your fork**:
   ```bash
   git push origin feature/your-feature-name
   ```

4. **Submit a Pull Request**:
   Describe the problem you are solving, the changes made, and steps to test.

---

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.
