"""Qt Command classes for the MCP Addon workbench menu.

Defines the six toolbar/menu entries (Start, Stop, Toggle Auto-Start,
Toggle Remote, Configure Allowed IPs, Send Selection to MCP).

``register_commands()`` and ``schedule_toggle_sync()`` are invoked from
``rpc_server.py`` at import time to preserve current side-effect behavior.
"""

import FreeCAD
import FreeCADGui
from PySide import QtWidgets

from rpc_server.ip_filter import validate_allowed_ips
from rpc_server.selection_buffer import capture_selection
from rpc_server.settings import load_settings, save_settings


class StartRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Start RPC Server", "ToolTip": "Start RPC Server"}

    def Activated(self):
        from . import rpc_server  # late import: avoids circular at module load
        msg = rpc_server.start_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class StopRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Stop RPC Server", "ToolTip": "Stop RPC Server"}

    def Activated(self):
        from . import rpc_server
        msg = rpc_server.stop_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class ToggleRemoteConnectionsCommand:
    def GetResources(self):
        settings = load_settings()
        return {
            "MenuText": "Remote Connections",
            "ToolTip": "Enable or disable remote connections for the RPC server.",
            "Checkable": bool(settings.get("remote_enabled", False)),
        }

    def Activated(self, checked=0):
        from . import rpc_server
        settings = load_settings()
        settings["remote_enabled"] = bool(checked)
        save_settings(settings)

        if settings["remote_enabled"]:
            allowed_ips = settings.get("allowed_ips", "127.0.0.1")
            FreeCAD.Console.PrintMessage(
                f"Remote connections enabled. Allowed IPs: {allowed_ips}\n"
            )
        else:
            FreeCAD.Console.PrintMessage("Remote connections disabled.\n")

        if rpc_server.rpc_server_instance:
            FreeCAD.Console.PrintMessage(
                "Restart the RPC server for changes to take effect.\n"
            )

    def IsActive(self):
        return True


class ConfigureAllowedIPsCommand:
    def GetResources(self):
        return {
            "MenuText": "Configure Allowed IPs",
            "ToolTip": "Set which IP addresses or subnets are allowed to connect to the RPC server.",
        }

    def Activated(self):
        from . import rpc_server
        settings = load_settings()
        current_ips = settings.get("allowed_ips", "127.0.0.1")
        text, ok = QtWidgets.QInputDialog.getText(
            None,
            "Allowed IP Addresses",
            "Enter allowed IP addresses or subnets (comma-separated):\n"
            "Examples: 127.0.0.1, 192.168.1.0/24, 10.0.0.5",
            QtWidgets.QLineEdit.Normal,
            current_ips,
        )
        if ok and text.strip():
            valid, errors = validate_allowed_ips(text.strip())
            if errors:
                QtWidgets.QMessageBox.warning(
                    None,
                    "Invalid IP Configuration",
                    "The following errors were found:\n\n"
                    + "\n".join(f"• {e}" for e in errors)
                    + ("\n\nOnly valid entries will be saved."
                       if valid else "\n\nNo valid entries found. Settings not changed."),
                )
            if not valid:
                FreeCAD.Console.PrintWarning("Allowed IPs not changed — no valid entries.\n")
                return
            normalised = ", ".join(valid)
            settings["allowed_ips"] = normalised
            save_settings(settings)
            FreeCAD.Console.PrintMessage(
                f"Allowed IPs updated to: {normalised}\n"
            )
            if rpc_server.rpc_server_instance:
                FreeCAD.Console.PrintMessage(
                    "Restart the RPC server for changes to take effect.\n"
                )
        else:
            FreeCAD.Console.PrintMessage("Allowed IPs not changed.\n")

    def IsActive(self):
        return True


class ToggleAutoStartCommand:
    def GetResources(self):
        settings = load_settings()
        return {
            "MenuText": "Auto-Start Server",
            "ToolTip": "Automatically start the RPC server when FreeCAD launches.",
            "Checkable": bool(settings.get("auto_start_rpc", False)),
        }

    def Activated(self, checked=0):
        settings = load_settings()
        settings["auto_start_rpc"] = bool(checked)
        save_settings(settings)

        if settings["auto_start_rpc"]:
            FreeCAD.Console.PrintMessage(
                "MCP RPC server will start automatically on next FreeCAD launch.\n"
            )
        else:
            FreeCAD.Console.PrintMessage(
                "MCP RPC server auto-start disabled.\n"
            )

    def IsActive(self):
        return True


class SendSelectionToMCPCommand:
    def GetResources(self):
        return {
            "MenuText": "Send Selection to MCP",
            "ToolTip": "Capture the current selection and store it in the MCP selection buffer.",
        }

    def Activated(self):
        from . import rpc_server
        if not rpc_server.rpc_server_instance:
            FreeCAD.Console.PrintWarning(
                "RPC Server is not running. Start it before sending a selection.\n"
            )
            return
        # Already on the GUI thread: call the capture directly rather than
        # going through dispatch_to_gui, which would deadlock here.
        result = capture_selection()
        if result.get("success"):
            FreeCAD.Console.PrintMessage(result["message"] + "\n")
        else:
            FreeCAD.Console.PrintError(
                f"Failed to send selection: {result.get('error', 'unknown error')}\n"
            )

    def IsActive(self):
        from . import rpc_server
        return bool(
            FreeCADGui.Selection.hasSelection()
            and rpc_server.rpc_server_instance is not None
        )


def register_commands() -> None:
    FreeCADGui.addCommand("Start_RPC_Server", StartRPCServerCommand())
    FreeCADGui.addCommand("Stop_RPC_Server", StopRPCServerCommand())
    FreeCADGui.addCommand("Toggle_Auto_Start", ToggleAutoStartCommand())
    FreeCADGui.addCommand("Toggle_Remote_Connections", ToggleRemoteConnectionsCommand())
    FreeCADGui.addCommand("Configure_Allowed_IPs", ConfigureAllowedIPsCommand())
    FreeCADGui.addCommand("Send_Selection_to_MCP", SendSelectionToMCPCommand())


def schedule_toggle_sync() -> None:
    """Compatibility no-op; toggle state is initialized by ``GetResources``.

    FreeCAD treats the presence of the ``Checkable`` resource as making an
    action checkable and uses its boolean value as the action's initial checked
    state. Loading the saved setting in ``GetResources`` therefore avoids any
    delayed QAction lookup or workbench activation at startup.
    """
