#!/usr/bin/env python3
"""GIMP MCP Server Plugin (GIMP 3.x compatible)
Runs inside GIMP as a native GIMP 3 plug-in and provides a TCP socket server
for the external Model Context Protocol (MCP) bridge.
"""

import gi
gi.require_version('Gimp', '3.0')
gi.require_version('Gegl', '0.4')
from gi.repository import Gimp, Gegl, GLib, Gio, GObject

import os
import sys
import json
import socket
import threading
import queue
import io
import traceback
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("GimpMCPServerPlugin")


class MCPServer:
    """Socket server running inside GIMP to handle external MCP commands."""

    def __init__(self, host="127.0.0.1", port=9876, log_cb=None):
        self.host = host
        self.port = port
        self.log_cb = log_cb
        self.sock = None
        self.is_running = False
        self.server_thread = None

    def log(self, msg):
        logger.info(msg)
        if self.log_cb:
            try:
                GLib.idle_add(self.log_cb, str(msg))
            except Exception:
                pass

    def start(self):
        """Starts socket listener in background thread."""
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock.bind((self.host, self.port))
            self.sock.listen(5)
            self.sock.settimeout(1.0)
            self.is_running = True
            self.log(f"GIMP MCP Server listening on {self.host}:{self.port}")

            self.server_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self.server_thread.start()
            return True
        except Exception as e:
            self.log(f"Failed to start socket server: {e}")
            self.stop()
            return False

    def stop(self):
        """Stops the socket server."""
        self.is_running = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None
        self.log("GIMP MCP Server stopped")

    def _accept_loop(self):
        while self.is_running:
            try:
                client_sock, addr = self.sock.accept()
            except socket.timeout:
                continue
            except Exception as e:
                if self.is_running:
                    self.log(f"Accept error: {e}")
                break

            self.log(f"Client connected from {addr}")
            t = threading.Thread(target=self._handle_client, args=(client_sock,), daemon=True)
            t.start()

    def _handle_client(self, client_sock):
        try:
            # Wrap socket in line reader for newline-delimited JSON
            f_in = client_sock.makefile('r', encoding='utf-8')
            while self.is_running:
                line = f_in.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue

                try:
                    req = json.loads(line)
                except Exception as e:
                    err_resp = {"status": "error", "message": f"Invalid JSON request: {e}"}
                    client_sock.sendall((json.dumps(err_resp) + "\n").encode('utf-8'))
                    continue

                resp = self._dispatch_to_main_thread(req)
                resp_bytes = (json.dumps(resp) + "\n").encode('utf-8')
                client_sock.sendall(resp_bytes)
        except Exception as e:
            self.log(f"Client handler exception: {e}")
        finally:
            try:
                client_sock.close()
            except Exception:
                pass
            self.log("Client disconnected")

    def _dispatch_to_main_thread(self, request):
        """Thread-safely dispatch command execution to GIMP's GLib main loop."""
        res_queue = queue.Queue()

        def _worker():
            try:
                result = self._process_command(request)
                res_queue.put(result)
            except Exception as e:
                res_queue.put({
                    "status": "error",
                    "message": str(e),
                    "traceback": traceback.format_exc()
                })
            return False  # GLib.idle_add callback returns False so it doesn't repeat

        GLib.idle_add(_worker)
        try:
            return res_queue.get(timeout=60.0)
        except queue.Empty:
            return {"status": "error", "message": "Command timed out waiting for GIMP main loop"}

    def _process_command(self, request):
        # Support both 'command' and 'type' keys
        cmd = request.get("command") or request.get("type")
        params = request.get("params", {})
        if not cmd:
            return {"status": "error", "message": "No command specified"}

        self.log(f"Handling command: {cmd}")

        handlers = {
            "ping": self._cmd_ping,
            "status": self._cmd_ping,
            "image_list": self._cmd_image_list,
            "image_get": self._cmd_image_get,
            "image_new": self._cmd_image_new,
            "layer_list": self._cmd_layer_list,
            "layer_new": self._cmd_layer_new,
            "layer_remove": self._cmd_layer_remove,
            "layer_set_visible": self._cmd_layer_set_visible,
            "layer_set_opacity": self._cmd_layer_set_opacity,
            "file_load": self._cmd_file_load,
            "file_save": self._cmd_file_save,
            "apply_filter": self._cmd_apply_filter,
            "pdb_call": self._cmd_pdb_call,
            "pdb_list": self._cmd_pdb_list,
            "pdb_info": self._cmd_pdb_info,
            "run_python": self._cmd_run_python,
            "displays_flush": self._cmd_displays_flush,
            "call_api": self._cmd_call_api,
        }

        handler = handlers.get(cmd)
        if not handler:
            return {"status": "error", "message": f"Unknown command: {cmd}"}

        return handler(params)

    # ------------------ Command Handlers ------------------

    def _cmd_ping(self, params):
        return {
            "status": "success",
            "result": {
                "gimp_version": Gimp.version(),
                "server_running": self.is_running,
                "images_open": len(Gimp.get_images())
            }
        }

    def _cmd_image_list(self, params):
        images = Gimp.get_images()
        out = []
        for img in images:
            out.append({
                "id": img.get_id(),
                "name": img.get_name(),
                "width": img.get_width(),
                "height": img.get_height(),
                "base_type": int(img.get_base_type()),
                "precision": int(img.get_precision()),
            })
        return {"status": "success", "result": out, "count": len(out)}

    def _cmd_image_get(self, params):
        image_id = params.get("image_id")
        if image_id is not None:
            img = Gimp.Image.get_by_id(int(image_id))
        else:
            images = Gimp.get_images()
            img = images[0] if images else None

        if not img:
            return {"status": "error", "message": "Image not found"}

        f = img.get_file()
        file_path = f.get_path() if f else None
        layers = [
            {
                "id": l.get_id(),
                "name": l.get_name(),
                "width": l.get_width(),
                "height": l.get_height(),
                "opacity": l.get_opacity(),
                "visible": l.get_visible(),
            }
            for l in img.get_layers()
        ]

        return {
            "status": "success",
            "result": {
                "id": img.get_id(),
                "name": img.get_name(),
                "width": img.get_width(),
                "height": img.get_height(),
                "file_path": file_path,
                "base_type": int(img.get_base_type()),
                "precision": int(img.get_precision()),
                "layers": layers,
            }
        }

    def _cmd_image_new(self, params):
        width = int(params.get("width", 800))
        height = int(params.get("height", 600))
        base_type = int(params.get("type", Gimp.ImageBaseType.RGB))
        name = params.get("name", "New Image")
        fill_type = params.get("fill_type", "WHITE")

        img = Gimp.Image.new(width, height, base_type)
        img_type = Gimp.ImageType.RGBA_IMAGE if base_type == Gimp.ImageBaseType.RGB else Gimp.ImageType.GRAYA_IMAGE
        layer = Gimp.Layer.new(img, "Background", width, height, img_type, 100.0, Gimp.LayerMode.NORMAL)
        img.insert_layer(layer, None, 0)

        fill_enum_map = {
            "FOREGROUND": Gimp.FillType.FOREGROUND,
            "BACKGROUND": Gimp.FillType.BACKGROUND,
            "WHITE": Gimp.FillType.WHITE,
            "TRANSPARENT": Gimp.FillType.TRANSPARENT,
        }
        f_enum = fill_enum_map.get(str(fill_type).upper(), Gimp.FillType.WHITE)
        layer.edit_fill(f_enum)

        Gimp.displays_flush()
        return {
            "status": "success",
            "result": {
                "image_id": img.get_id(),
                "layer_id": layer.get_id(),
                "width": width,
                "height": height
            }
        }

    def _cmd_layer_list(self, params):
        image_id = params.get("image_id")
        if image_id is None:
            return {"status": "error", "message": "Missing 'image_id'"}
        img = Gimp.Image.get_by_id(int(image_id))
        if not img:
            return {"status": "error", "message": f"Image {image_id} not found"}

        layers = [
            {
                "id": l.get_id(),
                "name": l.get_name(),
                "width": l.get_width(),
                "height": l.get_height(),
                "opacity": l.get_opacity(),
                "visible": l.get_visible(),
            }
            for l in img.get_layers()
        ]
        return {"status": "success", "result": layers}

    def _cmd_layer_new(self, params):
        image_id = params.get("image_id")
        if image_id is None:
            return {"status": "error", "message": "Missing 'image_id'"}
        img = Gimp.Image.get_by_id(int(image_id))
        if not img:
            return {"status": "error", "message": f"Image {image_id} not found"}

        name = params.get("name", "New Layer")
        width = int(params.get("width", img.get_width()))
        height = int(params.get("height", img.get_height()))
        layer_type = int(params.get("type", Gimp.ImageType.RGBA_IMAGE))
        opacity = float(params.get("opacity", 100.0))
        mode = int(params.get("mode", Gimp.LayerMode.NORMAL))
        position = int(params.get("position", 0))

        layer = Gimp.Layer.new(img, name, width, height, layer_type, opacity, mode)
        img.insert_layer(layer, None, position)
        Gimp.displays_flush()

        return {
            "status": "success",
            "result": {
                "layer_id": layer.get_id(),
                "name": layer.get_name(),
                "width": layer.get_width(),
                "height": layer.get_height()
            }
        }

    def _cmd_layer_remove(self, params):
        image_id = params.get("image_id")
        layer_id = params.get("layer_id")
        if image_id is None or layer_id is None:
            return {"status": "error", "message": "Missing image_id or layer_id"}
        img = Gimp.Image.get_by_id(int(image_id))
        layer = Gimp.Item.get_by_id(int(layer_id))
        if not img or not layer:
            return {"status": "error", "message": "Image or layer not found"}

        img.remove_layer(layer)
        Gimp.displays_flush()
        return {"status": "success"}

    def _cmd_layer_set_visible(self, params):
        layer_id = params.get("layer_id")
        visible = bool(params.get("visible", True))
        layer = Gimp.Item.get_by_id(int(layer_id))
        if not layer:
            return {"status": "error", "message": "Layer not found"}
        layer.set_visible(visible)
        Gimp.displays_flush()
        return {"status": "success"}

    def _cmd_layer_set_opacity(self, params):
        layer_id = params.get("layer_id")
        opacity = float(params.get("opacity", 100.0))
        layer = Gimp.Item.get_by_id(int(layer_id))
        if not layer or not hasattr(layer, "set_opacity"):
            return {"status": "error", "message": "Layer not found"}
        layer.set_opacity(opacity)
        Gimp.displays_flush()
        return {"status": "success"}

    def _cmd_file_load(self, params):
        file_path = params.get("path")
        if not file_path:
            return {"status": "error", "message": "Missing file 'path'"}
        file_path = os.path.abspath(os.path.expanduser(file_path))
        if not os.path.exists(file_path):
            return {"status": "error", "message": f"File does not exist: {file_path}"}

        gfile = Gio.File.new_for_path(file_path)
        img = Gimp.file_load(Gimp.RunMode.NONINTERACTIVE, gfile)
        if not img:
            return {"status": "error", "message": f"Failed to load image: {file_path}"}

        Gimp.displays_flush()
        return {
            "status": "success",
            "result": {
                "image_id": img.get_id(),
                "width": img.get_width(),
                "height": img.get_height(),
                "name": img.get_name()
            }
        }

    def _cmd_file_save(self, params):
        image_id = params.get("image_id")
        file_path = params.get("path")
        if image_id is None or not file_path:
            return {"status": "error", "message": "Missing image_id or path"}
        img = Gimp.Image.get_by_id(int(image_id))
        if not img:
            return {"status": "error", "message": f"Image {image_id} not found"}

        file_path = os.path.abspath(os.path.expanduser(file_path))
        parent_dir = os.path.dirname(file_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)

        gfile = Gio.File.new_for_path(file_path)
        ok = Gimp.file_save(Gimp.RunMode.NONINTERACTIVE, img, gfile)
        if not ok:
            return {"status": "error", "message": f"Failed to save image to {file_path}"}

        return {"status": "success", "result": {"path": file_path}}

    def _cmd_apply_filter(self, params):
        drawable_id = params.get("drawable_id") or params.get("layer_id")
        filter_name = params.get("filter") or params.get("operation", "gegl:gaussian-blur")
        props = params.get("properties", {})

        if drawable_id is None:
            return {"status": "error", "message": "Missing 'drawable_id'"}
        drawable = Gimp.Item.get_by_id(int(drawable_id))
        if not drawable:
            return {"status": "error", "message": f"Drawable {drawable_id} not found"}

        try:
            filt = Gimp.DrawableFilter.new(drawable, filter_name, "")
            cfg = filt.get_config()
            for k, v in props.items():
                if cfg.find_property(k):
                    cfg.set_property(k, v)
            drawable.merge_filter(filt)
            Gimp.displays_flush()
            return {"status": "success", "result": f"Filter {filter_name} applied"}
        except Exception as e:
            return {"status": "error", "message": f"Filter application failed: {e}"}

    def _cmd_pdb_call(self, params):
        proc_name = params.get("procedure")
        if not proc_name:
            return {"status": "error", "message": "Missing 'procedure'"}

        pdb = Gimp.get_pdb()
        if not pdb.procedure_exists(proc_name):
            return {"status": "error", "message": f"Procedure not found: {proc_name}"}

        proc = pdb.lookup_procedure(proc_name)
        cfg = proc.create_config()
        args = params.get("args", {})

        # Set properties based on argument specs
        for arg in proc.get_arguments():
            arg_name = arg.name
            type_name = arg.value_type.name
            if arg_name in args:
                val = args[arg_name]
                converted = self._convert_arg(val, type_name)
                cfg.set_property(arg_name, converted)

        res = proc.run(cfg)
        status = res.index(0)

        # Check status (Gimp.PDBStatusType.SUCCESS is 3)
        if status != Gimp.PDBStatusType.SUCCESS:
            err_msg = f"Procedure {proc_name} failed with status code {status}"
            if res.length() > 1:
                err_msg += f": {res.index(1)}"
            return {"status": "error", "message": err_msg}

        ret_vals = []
        for i in range(1, res.length()):
            ret_vals.append(self._serialize_val(res.index(i)))

        Gimp.displays_flush()
        return {
            "status": "success",
            "result": ret_vals[0] if len(ret_vals) == 1 else ret_vals
        }

    def _convert_arg(self, val, type_name):
        if val is None:
            return None
        if type_name in ("gint", "guint", "glong", "gulong", "gint64", "guint64"):
            return int(val)
        elif type_name in ("gdouble", "gfloat"):
            return float(val)
        elif type_name == "gboolean":
            return bool(val)
        elif type_name == "gchararray":
            return str(val)
        elif type_name == "GimpImage":
            return Gimp.Image.get_by_id(int(val)) if isinstance(val, (int, str)) else val
        elif type_name in ("GimpDrawable", "GimpLayer", "GimpChannel", "GimpItem"):
            return Gimp.Item.get_by_id(int(val)) if isinstance(val, (int, str)) else val
        elif type_name == "GFile":
            return Gio.File.new_for_path(str(val))
        elif type_name == "GeglColor":
            if isinstance(val, str):
                return Gegl.Color.new(val)
            elif isinstance(val, (list, tuple)) and len(val) >= 3:
                c = Gegl.Color.new("black")
                alpha = float(val[3]) if len(val) > 3 else 1.0
                c.set_rgba(float(val[0]), float(val[1]), float(val[2]), alpha)
                return c
            return val
        return val

    def _serialize_val(self, val):
        if val is None:
            return None
        if isinstance(val, (int, float, str, bool)):
            return val
        if hasattr(val, "get_id"):
            info = {"id": val.get_id(), "type": val.__class__.__name__}
            if hasattr(val, "get_name"):
                info["name"] = val.get_name()
            return info
        if hasattr(val, "get_rgba"):
            return list(val.get_rgba())
        if hasattr(val, "get_path"):
            return val.get_path()
        return str(val)

    def _cmd_pdb_list(self, params):
        filter_str = params.get("filter", "").lower()
        pdb = Gimp.get_pdb()
        procs = pdb.query_procedures("", "", "", "", "", "", "", "")
        result = []
        for name in procs:
            if filter_str and filter_str not in name.lower():
                continue
            proc = pdb.lookup_procedure(name)
            if not proc:
                continue
            result.append({
                "name": name,
                "blurb": proc.get_blurb() or "",
                "arguments": [p.name for p in proc.get_arguments()],
                "return_values": [p.name for p in proc.get_return_values()]
            })
            if len(result) >= 100:  # Cap at 100 matches to prevent overflow
                break
        return {"status": "success", "result": result, "count": len(result)}

    def _cmd_pdb_info(self, params):
        proc_name = params.get("procedure")
        if not proc_name:
            return {"status": "error", "message": "Missing 'procedure'"}
        pdb = Gimp.get_pdb()
        if not pdb.procedure_exists(proc_name):
            return {"status": "error", "message": f"Procedure not found: {proc_name}"}
        proc = pdb.lookup_procedure(proc_name)
        return {
            "status": "success",
            "result": {
                "name": proc.get_name(),
                "blurb": proc.get_blurb() or "",
                "help": proc.get_help() or "",
                "arguments": [
                    {"name": p.name, "type": p.value_type.name, "blurb": p.get_blurb() or ""}
                    for p in proc.get_arguments()
                ],
                "return_values": [
                    {"name": p.name, "type": p.value_type.name, "blurb": p.get_blurb() or ""}
                    for p in proc.get_return_values()
                ]
            }
        }

    def _cmd_run_python(self, params):
        code = params.get("code")
        if not code:
            return {"status": "error", "message": "Missing 'code'"}

        buf = io.StringIO()
        old_stdout = sys.stdout
        sys.stdout = buf

        locs = {
            "Gimp": Gimp,
            "Gegl": Gegl,
            "GLib": GLib,
            "Gio": Gio,
            "result": None,
        }

        try:
            exec(code, locs)
            sys.stdout = old_stdout
            output = buf.getvalue()
            res = locs.get("result")
            Gimp.displays_flush()
            return {
                "status": "success",
                "output": output,
                "result": self._serialize_val(res)
            }
        except Exception as e:
            sys.stdout = old_stdout
            return {
                "status": "error",
                "message": str(e),
                "traceback": traceback.format_exc()
            }

    def _cmd_displays_flush(self, params):
        Gimp.displays_flush()
        return {"status": "success"}

    def _cmd_call_api(self, params):
        """Backward-compatibility handler for call_api."""
        api_path = params.get("api_path", "")
        args = params.get("args", [])
        kwargs = params.get("kwargs", {})

        # Handle well-known paths
        if api_path in ("Gimp.list_images", "Gimp.get_images"):
            return self._cmd_image_list({})
        elif api_path == "Gimp.displays_flush":
            return self._cmd_displays_flush({})

        # Try dynamic lookup
        try:
            target = Gimp
            for part in api_path.split("."):
                if part == "Gimp":
                    continue
                target = getattr(target, part)

            result = target(*args, **kwargs)
            return {"status": "success", "result": self._serialize_val(result)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


class MCPPlugin(Gimp.PlugIn):
    _server = None

    def do_set_i18n(self, procname):
        return False

    def do_query_procedures(self):
        return ["gimp-mcp-server"]

    def do_create_procedure(self, name):
        procedure = Gimp.Procedure.new(
            self, name, Gimp.PDBProcType.PLUGIN, self.run, None
        )
        procedure.set_menu_label("Start MCP Server")
        procedure.set_documentation(
            "Starts an MCP socket server to control GIMP externally via AI models",
            "Starts a local TCP socket server on port 9876 that enables external AI/MCP clients to inspect and edit images",
            name,
        )
        procedure.set_attribution("GIMP-MCP", "GIMP-MCP", "2024-2026")
        procedure.add_menu_path("<Image>/Filters/Development/")
        procedure.add_menu_path("<Image>/Tools/")

        procedure.add_enum_argument(
            "run-mode", "Run mode", "The run mode",
            Gimp.RunMode, Gimp.RunMode.INTERACTIVE,
            GObject.ParamFlags.READWRITE
        )
        procedure.add_int_argument(
            "port", "Port", "Port to listen on",
            1024, 65535, 9876,
            GObject.ParamFlags.READWRITE
        )
        return procedure

    def run(self, procedure, config, data):
        run_mode = Gimp.RunMode.INTERACTIVE
        port = 9876

        if config:
            if config.find_property("run-mode"):
                run_mode = config.get_property("run-mode")
            if config.find_property("port"):
                port = config.get_property("port")

        env_port = os.environ.get("GIMP_MCP_PORT")
        if env_port and env_port.isdigit():
            port = int(env_port)

        # Check if already running
        if self._server and self._server.is_running:
            return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

        gi.require_version('Gtk', '3.0')
        from gi.repository import Gtk

        has_gui = False
        if run_mode == Gimp.RunMode.INTERACTIVE:
            try:
                gi.require_version('GimpUi', '3.0')
                from gi.repository import GimpUi
                GimpUi.init("gimp_mcp_server.py")
                has_gui, _ = Gtk.init_check()
            except Exception:
                has_gui = False

        if has_gui:
            self._run_gui(procedure, port)
        else:
            self._run_headless(procedure, port)

        return procedure.new_return_values(Gimp.PDBStatusType.SUCCESS, GLib.Error())

    def _run_gui(self, procedure, port):
        from gi.repository import Gtk, Pango

        window = Gtk.Window(title="GIMP MCP Server")
        window.set_default_size(440, 280)
        window.set_position(Gtk.WindowPosition.CENTER)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_border_width(12)
        window.add(vbox)

        # Status header
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        icon = Gtk.Image.new_from_icon_name("network-server", Gtk.IconSize.LARGE_TOOLBAR)
        status_box.pack_start(icon, False, False, 0)

        status_label = Gtk.Label()
        status_label.set_markup(f"<b>GIMP MCP Server is active</b>\nListening on 127.0.0.1:{port}")
        status_box.pack_start(status_label, False, False, 0)
        vbox.pack_start(status_box, False, False, 0)

        # Activity log view
        log_frame = Gtk.Frame(label="Activity Log")
        log_view = Gtk.TextView()
        log_view.set_editable(False)
        log_view.set_cursor_visible(False)
        log_view.modify_font(Pango.FontDescription("monospace 9"))
        text_buf = log_view.get_buffer()

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(140)
        scrolled.add(log_view)
        log_frame.add(scrolled)
        vbox.pack_start(log_frame, True, True, 0)

        def append_log(text):
            end_iter = text_buf.get_end_iter()
            text_buf.insert(end_iter, text + "\n")
            log_view.scroll_to_iter(text_buf.get_end_iter(), 0.0, False, 0.0, 0.0)

        # Server instance
        self._server = MCPServer(port=port, log_cb=append_log)
        self._server.start()

        # Stop button
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_stop = Gtk.Button(label="Stop Server")
        btn_box.pack_end(btn_stop, False, False, 0)
        vbox.pack_start(btn_box, False, False, 0)

        def on_close(*args):
            if self._server:
                self._server.stop()
                self._server = None
            Gtk.main_quit()

        btn_stop.connect("clicked", on_close)
        window.connect("delete-event", on_close)

        window.show_all()
        Gtk.main()

    def _run_headless(self, procedure, port):
        loop = GLib.MainLoop()
        self._server = MCPServer(port=port)
        self._server.start()
        try:
            loop.run()
        except KeyboardInterrupt:
            pass
        finally:
            if self._server:
                self._server.stop()
                self._server = None


Gimp.main(MCPPlugin.__gtype__, sys.argv)
