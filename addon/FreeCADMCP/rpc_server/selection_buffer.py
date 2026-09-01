"""Selection buffer shared between the FreeCAD GUI and the MCP server.

The user picks objects (and sub-elements) in the FreeCAD GUI and hands them to
the MCP server explicitly through the "Send Selection to MCP" toolbar command.
The captured snapshot lives here until it is overwritten by the next capture or
cleared through ``clear_selection_buffer``, so the model can retry an operation
against the same selection.

``capture_selection`` touches ``FreeCADGui.Selection`` and must therefore run on
the GUI thread: call it directly from a Qt command, or through
``dispatch_to_gui`` from the RPC thread.
"""

import datetime
from typing import Any

import FreeCAD
import FreeCADGui

from rpc_server.serialize import serialize_object


_selection_buffer: list[dict[str, Any]] = []
_selection_buffer_timestamp: str | None = None


def _subelement_type(sub_name: str) -> str:
    for kind in ("Face", "Edge", "Vertex"):
        if sub_name.startswith(kind):
            return kind
    return "Unknown"


def capture_selection() -> dict[str, Any]:
    """Snapshot the current GUI selection into the buffer (GUI thread only).

    Replaces any previous snapshot. Each entry is the object serialized by
    ``serialize_object`` plus a ``SubElements`` block describing the selected
    faces/edges/vertices.
    """
    global _selection_buffer, _selection_buffer_timestamp

    try:
        selection_ex = FreeCADGui.Selection.getSelectionEx()
    except Exception as e:
        FreeCAD.Console.PrintError(f"MCP RPC: error reading selection: {e}\n")
        return {"success": False, "error": str(e)}

    captured: list[dict[str, Any]] = []
    for sel_obj in selection_ex:
        obj = sel_obj.Object
        sub_names = list(sel_obj.SubElementNames)
        try:
            serialized = serialize_object(obj)
        except Exception as e:
            FreeCAD.Console.PrintError(f"MCP RPC: error serializing {obj.Name}: {e}\n")
            return {"success": False, "error": f"Serialization error for {obj.Name}: {e}"}

        serialized["SubElements"] = {
            "Names": sub_names,
            "Count": len(sub_names),
        }
        if sub_names:
            serialized["SubElements"]["Details"] = [
                {"Name": sub_name, "Type": _subelement_type(sub_name)}
                for sub_name in sub_names
            ]
        captured.append(serialized)

    _selection_buffer = captured
    _selection_buffer_timestamp = datetime.datetime.now().isoformat()
    count = len(_selection_buffer)
    FreeCAD.Console.PrintMessage(f"MCP RPC: selection buffer updated with {count} object(s).\n")
    return {
        "success": True,
        "count": count,
        "message": f"Selection buffer updated with {count} objects",
    }


def get_selection_buffer() -> dict[str, Any]:
    """Return the buffered selection without clearing it."""
    return {
        "success": True,
        "selections": _selection_buffer,
        "timestamp": _selection_buffer_timestamp,
        "count": len(_selection_buffer),
    }


def get_buffer_status() -> dict[str, Any]:
    """Return buffer metadata without the selection payload."""
    return {
        "success": True,
        "has_selections": len(_selection_buffer) > 0,
        "count": len(_selection_buffer),
        "timestamp": _selection_buffer_timestamp,
    }


def clear_selection_buffer() -> dict[str, Any]:
    """Drop the buffered selection."""
    global _selection_buffer, _selection_buffer_timestamp
    _selection_buffer = []
    _selection_buffer_timestamp = None
    return {"success": True, "message": "Selection buffer cleared"}


SELECTION_WORKFLOW_STRATEGY = """
Selection Workflow Strategy for FreeCAD MCP:

1. Understand the task required by the user
2. Direct user to select the entities and click "Send Selection to MCP" button
3. Retrieve the selection using get_selection_buffer()
4. Perform the operation until no errors arise
5. Get user feedback if the action was as intended
6. If not satisfied, return to step 1 (skipping step 2 - selection already in buffer)
7. If user is satisfied, call clear_selection_buffer()

This workflow ensures explicit user control while providing reliable data persistence for AI model operations.
Selection data persists until explicitly cleared, allowing for retries and refinements.
"""


COORDINATE_HANDLING_STRATEGY = """
CRITICAL: FreeCAD Coordinate Handling Best Practices

WRONG APPROACH (causes double-transformation):
1. Manually extracting geometry points from sketch local coordinates
2. Then applying obj.Placement.multVec() transformation
3. This double-transforms coordinates and gives incorrect results

CORRECT APPROACH:
1. Use FreeCAD's selection API directly: FreeCADGui.Selection.getSelectionEx()
2. Access sel.SubObjects[j].Point - this already provides GLOBAL coordinates
3. NO manual transformation needed - coordinates are already in global space

EXAMPLE CORRECT CODE:
```python
selection_ex = FreeCADGui.Selection.getSelectionEx()
for sel in selection_ex:
    for i, sub_name in enumerate(sel.SubElementNames):
        if sub_name.startswith('Vertex'):
            # This point is already in global coordinates!
            global_point = sel.SubObjects[i].Point
            # Use global_point directly - no transformation needed
```

VERTEX COORDINATE EXTRACTION:
- sel.SubObjects[i].Point returns FreeCAD.Vector in global coordinates
- For sketch vertices, these are already transformed to 3D space
- For 3D object vertices, these are already in the document coordinate system

COMMON MISTAKE TO AVOID:
- Do NOT use obj.Shape.Vertexes[i].Point and then transform
- Do NOT manually apply placement transformations to selection coordinates
- The selection API handles all coordinate transformations automatically

WHEN TO USE TRANSFORMATIONS:
- Only when working with raw geometry data NOT from selections
- When creating new geometry that needs to be positioned relative to objects
- When working with local coordinate systems for construction purposes
"""


def get_selection_workflow_strategy() -> dict[str, Any]:
    """Return the recommended selection-buffer workflow for the model."""
    return {"success": True, "strategy": SELECTION_WORKFLOW_STRATEGY}


def get_coordinate_handling_strategy() -> dict[str, Any]:
    """Return best practices for using coordinates taken from selections."""
    return {"success": True, "strategy": COORDINATE_HANDLING_STRATEGY}
