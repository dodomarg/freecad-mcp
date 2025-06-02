import FreeCAD
import FreeCADGui
import ObjectsFem

import contextlib
import queue
import base64
import io
import os
import tempfile
import threading
from dataclasses import dataclass, field
from typing import Any
from xmlrpc.server import SimpleXMLRPCServer

from PySide import QtCore

from .parts_library import get_parts_list, insert_part_from_library
from .serialize import serialize_object

rpc_server_thread = None
rpc_server_instance = None

# GUI task queue
rpc_request_queue = queue.Queue()
rpc_response_queue = queue.Queue()

# Selection Buffer - stores the current selection snapshot
selection_buffer = []
selection_buffer_timestamp = None


def process_gui_tasks():
    while not rpc_request_queue.empty():
        task = rpc_request_queue.get()
        res = task()
        if res is not None:
            rpc_response_queue.put(res)
    QtCore.QTimer.singleShot(500, process_gui_tasks)


@dataclass
class Object:
    name: str
    type: str | None = None
    analysis: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)


def set_object_property(
    doc: FreeCAD.Document, obj: FreeCAD.DocumentObject, properties: dict[str, Any]
):
    for prop, val in properties.items():
        try:
            if prop in obj.PropertiesList:
                if prop == "Placement" and isinstance(val, dict):
                    if "Base" in val:
                        pos = val["Base"]
                    elif "Position" in val:
                        pos = val["Position"]
                    else:
                        pos = {}
                    rot = val.get("Rotation", {})
                    placement = FreeCAD.Placement(
                        FreeCAD.Vector(
                            pos.get("x", 0),
                            pos.get("y", 0),
                            pos.get("z", 0),
                        ),
                        FreeCAD.Rotation(
                            FreeCAD.Vector(
                                rot.get("Axis", {}).get("x", 0),
                                rot.get("Axis", {}).get("y", 0),
                                rot.get("Axis", {}).get("z", 1),
                            ),
                            rot.get("Angle", 0),
                        ),
                    )
                    setattr(obj, prop, placement)

                elif isinstance(getattr(obj, prop), FreeCAD.Vector) and isinstance(
                    val, dict
                ):
                    vector = FreeCAD.Vector(
                        val.get("x", 0), val.get("y", 0), val.get("z", 0)
                    )
                    setattr(obj, prop, vector)

                elif prop in ["Base", "Tool", "Source", "Profile"] and isinstance(
                    val, str
                ):
                    ref_obj = doc.getObject(val)
                    if ref_obj:
                        setattr(obj, prop, ref_obj)
                    else:
                        raise ValueError(f"Referenced object '{val}' not found.")

                elif prop == "References" and isinstance(val, list):
                    refs = []
                    for ref_name, face in val:
                        ref_obj = doc.getObject(ref_name)
                        if ref_obj:
                            refs.append((ref_obj, face))
                        else:
                            raise ValueError(f"Referenced object '{ref_name}' not found.")
                    setattr(obj, prop, refs)

                else:
                    setattr(obj, prop, val)
            # ShapeColor is a property of the ViewObject
            elif prop == "ShapeColor" and isinstance(val, (list, tuple)):
                setattr(obj.ViewObject, prop, (float(val[0]), float(val[1]), float(val[2]), float(val[3])))

            elif prop == "ViewObject" and isinstance(val, dict):
                for k, v in val.items():
                    if k == "ShapeColor":
                        setattr(obj.ViewObject, k, (float(v[0]), float(v[1]), float(v[2]), float(v[3])))
                    else:
                        setattr(obj.ViewObject, k, v)

            else:
                setattr(obj, prop, val)

        except Exception as e:
            FreeCAD.Console.PrintError(f"Property '{prop}' assignment error: {e}\n")


class FreeCADRPC:
    """RPC server for FreeCAD"""

    def ping(self):
        return True

    def create_document(self, name="New_Document"):
        rpc_request_queue.put(lambda: self._create_document_gui(name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "document_name": name}
        else:
            return {"success": False, "error": res}

    def create_object(self, doc_name, obj_data: dict[str, Any]):
        obj = Object(
            name=obj_data.get("Name", "New_Object"),
            type=obj_data["Type"],
            analysis=obj_data.get("Analysis", None),
            properties=obj_data.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._create_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def edit_object(self, doc_name: str, obj_name: str, properties: dict[str, Any]) -> dict[str, Any]:
        obj = Object(
            name=obj_name,
            properties=properties.get("Properties", {}),
        )
        rpc_request_queue.put(lambda: self._edit_object_gui(doc_name, obj))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj.name}
        else:
            return {"success": False, "error": res}

    def delete_object(self, doc_name: str, obj_name: str):
        rpc_request_queue.put(lambda: self._delete_object_gui(doc_name, obj_name))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "object_name": obj_name}
        else:
            return {"success": False, "error": res}

    def execute_code(self, code: str) -> dict[str, Any]:
        output_buffer = io.StringIO()
        def task():
            try:
                with contextlib.redirect_stdout(output_buffer):
                    exec(code, globals())
                FreeCAD.Console.PrintMessage("Python code executed successfully.\n")
                return True
            except Exception as e:
                FreeCAD.Console.PrintError(
                    f"Error executing Python code: {e}\n"
                )
                return f"Error executing Python code: {e}\n"

        rpc_request_queue.put(task)
        res = rpc_response_queue.get()
        if res is True:
            return {
                "success": True,
                "message": "Python code execution scheduled. \nOutput: " + output_buffer.getvalue()
            }
        else:
            return {"success": False, "error": res}

    def get_objects(self, doc_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return [serialize_object(obj) for obj in doc.Objects]
        else:
            return []

    def get_object(self, doc_name, obj_name):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            return serialize_object(doc.getObject(obj_name))
        else:
            return None

    def insert_part_from_library(self, relative_path):
        rpc_request_queue.put(lambda: self._insert_part_from_library(relative_path))
        res = rpc_response_queue.get()
        if res is True:
            return {"success": True, "message": "Part inserted from library."}
        else:
            return {"success": False, "error": res}

    def list_documents(self):
        return list(FreeCAD.listDocuments().keys())

    def get_parts_list(self):
        return get_parts_list()

    def get_active_screenshot(self, view_name: str = "Isometric") -> str:
        """Get a screenshot of the active view.
        
        Returns a base64-encoded string of the screenshot or None if a screenshot
        cannot be captured (e.g., when in TechDraw or Spreadsheet view).
        """
        # First check if the active view supports screenshots
        def check_view_supports_screenshots():
            try:
                active_view = FreeCADGui.ActiveDocument.ActiveView
                if active_view is None:
                    FreeCAD.Console.PrintWarning("No active view available\n")
                    return False
                
                view_type = type(active_view).__name__
                has_save_image = hasattr(active_view, 'saveImage')
                FreeCAD.Console.PrintMessage(f"View type: {view_type}, Has saveImage: {has_save_image}\n")
                return has_save_image
            except Exception as e:
                FreeCAD.Console.PrintError(f"Error checking view capabilities: {e}\n")
                return False
                
        rpc_request_queue.put(check_view_supports_screenshots)
        supports_screenshots = rpc_response_queue.get()
        
        if not supports_screenshots:
            FreeCAD.Console.PrintWarning("Current view does not support screenshots\n")
            return None
            
        # If view supports screenshots, proceed with capture
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        rpc_request_queue.put(
            lambda: self._save_active_screenshot(tmp_path, view_name)
        )
        res = rpc_response_queue.get()
        if res is True:
            try:
                with open(tmp_path, "rb") as image_file:
                    image_bytes = image_file.read()
                    encoded = base64.b64encode(image_bytes).decode("utf-8")
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            return encoded
        else:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            FreeCAD.Console.PrintWarning(f"Failed to capture screenshot: {res}\n")
            return None

    def _create_document_gui(self, name):
        doc = FreeCAD.newDocument(name)
        doc.recompute()
        FreeCAD.Console.PrintMessage(f"Document '{name}' created via RPC.\n")
        return True

    def _create_object_gui(self, doc_name, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if doc:
            try:
                if obj.type == "Fem::FemMeshGmsh" and obj.analysis:
                    from femmesh.gmshtools import GmshTools
                    res = getattr(doc, obj.analysis).addObject(ObjectsFem.makeMeshGmsh(doc, obj.name))[0]
                    if "Part" in obj.properties:
                        target_obj = doc.getObject(obj.properties["Part"])
                        if target_obj:
                            res.Part = target_obj
                        else:
                            raise ValueError(f"Referenced object '{obj.properties['Part']}' not found.")
                        del obj.properties["Part"]
                    else:
                        raise ValueError("'Part' property not found in properties.")

                    for param, value in obj.properties.items():
                        if hasattr(res, param):
                            setattr(res, param, value)
                    doc.recompute()

                    gmsh_tools = GmshTools(res)
                    gmsh_tools.create_mesh()
                    FreeCAD.Console.PrintMessage(
                        f"FEM Mesh '{res.Name}' generated successfully in '{doc_name}'.\n"
                    )
                elif obj.type.startswith("Fem::"):
                    fem_make_methods = {
                        "MaterialCommon": ObjectsFem.makeMaterialSolid,
                        "AnalysisPython": ObjectsFem.makeAnalysis,
                    }
                    obj_type_short = obj.type.split("::")[1]
                    method_name = "make" + obj_type_short
                    make_method = fem_make_methods.get(obj_type_short, getattr(ObjectsFem, method_name, None))

                    if callable(make_method):
                        res = make_method(doc, obj.name)
                        set_object_property(doc, res, obj.properties)
                        FreeCAD.Console.PrintMessage(
                            f"FEM object '{res.Name}' created with '{method_name}'.\n"
                        )
                    else:
                        raise ValueError(f"No creation method '{method_name}' found in ObjectsFem.")
                    if obj.type != "Fem::AnalysisPython" and obj.analysis:
                        getattr(doc, obj.analysis).addObject(res)
                else:
                    res = doc.addObject(obj.type, obj.name)
                    set_object_property(doc, res, obj.properties)
                    FreeCAD.Console.PrintMessage(
                        f"{res.TypeId} '{res.Name}' added to '{doc_name}' via RPC.\n"
                    )
 
                doc.recompute()
                return True
            except Exception as e:
                return str(e)
        else:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

    def _edit_object_gui(self, doc_name: str, obj: Object):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        obj_ins = doc.getObject(obj.name)
        if not obj_ins:
            FreeCAD.Console.PrintError(f"Object '{obj.name}' not found in document '{doc_name}'.\n")
            return f"Object '{obj.name}' not found in document '{doc_name}'.\n"

        try:
            # For Fem::ConstraintFixed
            if hasattr(obj_ins, "References") and "References" in obj.properties:
                refs = []
                for ref_name, face in obj.properties["References"]:
                    ref_obj = doc.getObject(ref_name)
                    if ref_obj:
                        refs.append((ref_obj, face))
                    else:
                        raise ValueError(f"Referenced object '{ref_name}' not found.")
                obj_ins.References = refs
                FreeCAD.Console.PrintMessage(
                    f"References updated for '{obj.name}' in '{doc_name}'.\n"
                )
                # delete References from properties
                del obj.properties["References"]
            set_object_property(doc, obj_ins, obj.properties)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj.name}' updated via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _delete_object_gui(self, doc_name: str, obj_name: str):
        doc = FreeCAD.getDocument(doc_name)
        if not doc:
            FreeCAD.Console.PrintError(f"Document '{doc_name}' not found.\n")
            return f"Document '{doc_name}' not found.\n"

        try:
            doc.removeObject(obj_name)
            doc.recompute()
            FreeCAD.Console.PrintMessage(f"Object '{obj_name}' deleted via RPC.\n")
            return True
        except Exception as e:
            return str(e)

    def _insert_part_from_library(self, relative_path):
        try:
            insert_part_from_library(relative_path)
            return True
        except Exception as e:
            return str(e)

    def _save_active_screenshot(self, save_path: str, view_name: str = "Isometric"):
        try:
            view = FreeCADGui.ActiveDocument.ActiveView
            # Check if the view supports screenshots
            if not hasattr(view, 'saveImage'):
                return "Current view does not support screenshots"
                
            if view_name == "Isometric":
                view.viewIsometric()
            elif view_name == "Front":
                view.viewFront()
            elif view_name == "Top":
                view.viewTop()
            elif view_name == "Right":
                view.viewRight()
            elif view_name == "Back":
                view.viewBack()
            elif view_name == "Left":
                view.viewLeft()
            elif view_name == "Bottom":
                view.viewBottom()
            elif view_name == "Dimetric":
                view.viewDimetric()
            elif view_name == "Trimetric":
                view.viewTrimetric()
            else:
                raise ValueError(f"Invalid view name: {view_name}")
            view.fitAll()
            view.saveImage(save_path, 1)
            return True        
        except Exception as e:
            return str(e)   
        
    def _send_selection_to_buffer_gui(self):
        """Capture current selection and store in buffer (GUI thread)"""
        global selection_buffer, selection_buffer_timestamp
        import datetime
        
        try:
            FreeCAD.Console.PrintMessage("Starting selection capture...\n")
            # Get current selection from FreeCAD with subelements
            selection_ex = FreeCADGui.Selection.getSelectionEx()
            FreeCAD.Console.PrintMessage(f"Found {len(selection_ex)} selected objects\n")
            
            # Clear existing buffer and capture new selection
            selection_buffer = []
            
            # Process each selection (object with its subelements)
            for i, sel_obj in enumerate(selection_ex):
                obj = sel_obj.Object
                subelements = sel_obj.SubElementNames
                FreeCAD.Console.PrintMessage(f"Processing object {i}: {obj.Name} with {len(subelements)} subelements\n")
                
                try:
                    # Serialize the main object
                    serialized_obj = serialize_object(obj)
                    
                    # Add subelement information
                    serialized_obj["SubElements"] = {
                        "Names": subelements,
                        "Count": len(subelements)
                    }
                    
                    # Add detailed subelement information if available
                    if subelements:
                        subelement_details = []
                        for sub_name in subelements:
                            sub_info = {
                                "Name": sub_name,
                                "Type": "Unknown"  # Default type
                            }
                            
                            # Determine subelement type
                            if sub_name.startswith("Face"):
                                sub_info["Type"] = "Face"
                            elif sub_name.startswith("Edge"):
                                sub_info["Type"] = "Edge"
                            elif sub_name.startswith("Vertex"):
                                sub_info["Type"] = "Vertex"
                            
                            subelement_details.append(sub_info)
                        
                        serialized_obj["SubElements"]["Details"] = subelement_details
                        FreeCAD.Console.PrintMessage(f"  → Captured subelements: {', '.join(subelements)}\n")
                    
                    selection_buffer.append(serialized_obj)
                    FreeCAD.Console.PrintMessage(f"  ✓ Successfully serialized {obj.Name}\n")
                    
                except Exception as e:
                    FreeCAD.Console.PrintError(f"  ✗ Error serializing {obj.Name}: {e}\n")
                    return {"success": False, "error": f"Serialization error for {obj.Name}: {e}"}
            
            # Update timestamp
            selection_buffer_timestamp = datetime.datetime.now().isoformat()
            count = len(selection_buffer)
            
            FreeCAD.Console.PrintMessage(f"Selection capture completed: {count} objects\n")
            return {"success": True, "count": count, "message": f"Selection buffer updated with {count} objects"}
            
        except Exception as e:
            FreeCAD.Console.PrintError(f"Error capturing selection: {e}\n")
            return {"success": False, "error": str(e)}

    def send_selection_to_buffer(self):
        """Capture current FreeCAD selection and store in buffer (replacing previous)"""
        # Execute directly without queue to avoid deadlock
        result = self._send_selection_to_buffer_gui()
        if isinstance(result, dict) and result.get("success"):
            return result
        else:
            return {"success": False, "error": result}

    def get_selection_buffer(self):
        """Get current selection buffer contents (non-destructive)"""
        global selection_buffer, selection_buffer_timestamp
        return {
            "success": True,
            "selections": selection_buffer,
            "timestamp": selection_buffer_timestamp,
            "count": len(selection_buffer)
        }

    def get_buffer_status(self):
        """Check buffer state"""
        global selection_buffer, selection_buffer_timestamp
        return {
            "success": True,
            "has_selections": len(selection_buffer) > 0,
            "count": len(selection_buffer),
            "timestamp": selection_buffer_timestamp
        }

    def clear_selection_buffer(self):
        """Clear the selection buffer"""
        global selection_buffer, selection_buffer_timestamp
        selection_buffer = []
        selection_buffer_timestamp = None
        return {"success": True, "message": "Selection buffer cleared"}

    def get_selection_workflow_strategy(self):
        """Returns the proper usage strategy for the model"""
        strategy = """
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
        return {"success": True, "strategy": strategy}

    def get_coordinate_handling_strategy(self):
        """Returns best practices for handling coordinates from FreeCAD selections"""
        strategy = """
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
        return {"success": True, "strategy": strategy}
        

def start_rpc_server(host="localhost", port=9875):
    global rpc_server_thread, rpc_server_instance

    if rpc_server_instance:
        return "RPC Server already running."

    rpc_server_instance = SimpleXMLRPCServer(
        (host, port), allow_none=True, logRequests=False
    )
    rpc_server_instance.register_instance(FreeCADRPC())

    def server_loop():
        FreeCAD.Console.PrintMessage(f"RPC Server started at {host}:{port}\n")
        rpc_server_instance.serve_forever()

    rpc_server_thread = threading.Thread(target=server_loop, daemon=True)
    rpc_server_thread.start()

    QtCore.QTimer.singleShot(500, process_gui_tasks)

    return f"RPC Server started at {host}:{port}."


def stop_rpc_server():
    global rpc_server_instance, rpc_server_thread

    if rpc_server_instance:
        rpc_server_instance.shutdown()
        rpc_server_thread.join()
        rpc_server_instance = None
        rpc_server_thread = None
        FreeCAD.Console.PrintMessage("RPC Server stopped.\n")
        return "RPC Server stopped."

    return "RPC Server was not running."


class StartRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Start RPC Server", "ToolTip": "Start RPC Server"}

    def Activated(self):
        msg = start_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class StopRPCServerCommand:
    def GetResources(self):
        return {"MenuText": "Stop RPC Server", "ToolTip": "Stop RPC Server"}

    def Activated(self):
        msg = stop_rpc_server()
        FreeCAD.Console.PrintMessage(msg + "\n")

    def IsActive(self):
        return True


class SendSelectionToMCPCommand:
    def GetResources(self):
        return {
            "MenuText": "Send Selection to MCP", 
            "ToolTip": "Capture current selection and send to MCP server buffer"
        }

    def Activated(self):
        global rpc_server_instance
        if rpc_server_instance:
            # Use the RPC instance to send selection to buffer
            rpc = FreeCADRPC()
            result = rpc.send_selection_to_buffer()
            if result["success"]:
                FreeCAD.Console.PrintMessage(f"✓ {result['message']}\n")
            else:
                FreeCAD.Console.PrintError(f"✗ Failed to send selection: {result['error']}\n")
        else:
            FreeCAD.Console.PrintWarning("⚠ RPC Server is not running. Please start it first.\n")

    def IsActive(self):
        # Only active when there's a selection and RPC server is running
        return (FreeCADGui.Selection.hasSelection() and 
                rpc_server_instance is not None)


FreeCADGui.addCommand("Start_RPC_Server", StartRPCServerCommand())
FreeCADGui.addCommand("Stop_RPC_Server", StopRPCServerCommand())
FreeCADGui.addCommand("Send_Selection_to_MCP", SendSelectionToMCPCommand())