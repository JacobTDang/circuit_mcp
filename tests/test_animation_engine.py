import pytest

from circuit_mcp.animation_engine import SceneValidationError, validate_scene
from circuit_mcp.animation_engine import build_template, template_names


def test_scene_outer_contract_is_normalized():
    scene = validate_scene({
        "title": "RC charging", "elements": [{"id": "r1", "type": "resistor"}],
        "steps": [],
    })
    assert scene["schema_version"] == 1
    assert scene["width"] == 960
    assert scene["seed"] == 2300


@pytest.mark.parametrize("bad", [
    {"title": ""},
    {"title": "x", "width": 10},
    {"title": "x", "duration_ms": float("inf")},
    {"title": "x", "script": "alert(1)"},
    {"title": "x", "elements": [{"id": "x", "type": "html"}]},
    {"title": "x", "elements": [{"id": "same", "type": "line"}, {"id": "same", "type": "line"}]},
])
def test_scene_rejects_unbounded_or_executable_shapes(bad):
    with pytest.raises(SceneValidationError):
        validate_scene(bad)


def test_templates_cover_every_official_course_area_and_validate():
    assert len(template_names()) >= 13
    for name in template_names():
        scene = build_template(name)
        assert scene["elements"] and len(scene["steps"]) >= 4
