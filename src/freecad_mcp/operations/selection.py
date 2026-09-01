"""Operations backing the selection-buffer MCP tools.

The selection buffer is filled by the user in FreeCAD ("Send Selection to MCP"
toolbar button); these operations only read, report and clear it.
"""

import logging

from ..freecad_client import FreeCADConnection
from ..responses import ToolResponse, json_response, text_response


logger = logging.getLogger("FreeCADMCPserver")


def get_selection_buffer_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        res = freecad.get_selection_buffer()
        if res.get("success"):
            return json_response(
                {
                    "count": res.get("count"),
                    "timestamp": res.get("timestamp"),
                    "selections": res.get("selections", []),
                }
            )
        return text_response(f"Failed to get selection buffer: {res.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Failed to get selection buffer: {str(e)}")
        return text_response(f"Failed to get selection buffer: {str(e)}")


def get_buffer_status_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        res = freecad.get_buffer_status()
        if res.get("success"):
            return json_response(
                {
                    "has_selections": res.get("has_selections", False),
                    "count": res.get("count", 0),
                    "timestamp": res.get("timestamp"),
                }
            )
        return text_response(f"Failed to get buffer status: {res.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Failed to get buffer status: {str(e)}")
        return text_response(f"Failed to get buffer status: {str(e)}")


def clear_selection_buffer_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        res = freecad.clear_selection_buffer()
        if res.get("success"):
            return text_response(res.get("message", "Selection buffer cleared."))
        return text_response(f"Failed to clear selection buffer: {res.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Failed to clear selection buffer: {str(e)}")
        return text_response(f"Failed to clear selection buffer: {str(e)}")


def get_selection_workflow_strategy_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        res = freecad.get_selection_workflow_strategy()
        if res.get("success"):
            return text_response(res["strategy"])
        return text_response(f"Failed to get workflow strategy: {res.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Failed to get workflow strategy: {str(e)}")
        return text_response(f"Failed to get workflow strategy: {str(e)}")


def get_coordinate_handling_strategy_operation(freecad: FreeCADConnection) -> ToolResponse:
    try:
        res = freecad.get_coordinate_handling_strategy()
        if res.get("success"):
            return text_response(res["strategy"])
        return text_response(
            f"Failed to get coordinate handling strategy: {res.get('error', 'Unknown error')}"
        )
    except Exception as e:
        logger.error(f"Failed to get coordinate handling strategy: {str(e)}")
        return text_response(f"Failed to get coordinate handling strategy: {str(e)}")
