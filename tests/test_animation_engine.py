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
        assert any(element["type"] in {"resistor", "capacitor", "diode", "opamp", "voltage_source", "block"}
                   for element in scene["elements"])
        assert any(change.get("opacity") == 1 for step in scene["steps"] for change in step.get("changes", []))


def test_course_templates_use_distinct_physical_visuals():
    opamp_types = {element["type"] for element in build_template("opamp")["elements"]}
    converter_types = {element["type"] for element in build_template("adc")["elements"]}
    transient_types = {element["type"] for element in build_template("time_domain")["elements"]}
    assert "opamp" in opamp_types and "resistor" in opamp_types
    assert "block" in converter_types and "waveform" in converter_types
    assert {"voltage_source", "resistor", "capacitor", "line"} <= transient_types
