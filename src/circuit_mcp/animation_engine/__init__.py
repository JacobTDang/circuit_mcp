"""Safe declarative visual-explanation engine for the command center.

The engine accepts validated scene data. It never accepts executable browser
code from an MCP client.
"""

from .schema import SCENE_SCHEMA_VERSION, SceneValidationError, validate_scene
from .templates import build_template, template_names

__all__ = ["SCENE_SCHEMA_VERSION", "SceneValidationError", "validate_scene", "build_template", "template_names"]
