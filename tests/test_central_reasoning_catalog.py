"""Only explicit bounded reasoning metadata crosses the central trust boundary."""
import pytest

from easel.platform.central import CentralClient, CentralError


def model(**patch):
    return {"id": "fixture", "name": "Fixture", "version": "1", "capability": "text",
        "test_only": True, "readiness": "test",
        "rate": {"version": "1", "input": {"numerator": "1", "denominator": "1"},
                 "output": {"numerator": "1", "denominator": "1"}},
        "limits": {"max_input_bytes": 1024, "max_input_tokens": 1024, "max_output_tokens": 1024}, **patch}


@pytest.mark.parametrize("efforts", [[], ["low", "medium", "high"], ["minimal", "xhigh", "max"]])
def test_central_model_view_preserves_explicit_capability_and_no_extra_fields(efforts):
    row = model(reasoning={"efforts": efforts}, secret="must-not-leave")
    result = CentralClient._execution_view("/v2/products/easel/models", {"models": [row]})["models"][0]
    assert result["reasoning"] == {"efforts": efforts}
    assert result["reasoning"]["efforts"] is not efforts
    assert "secret" not in result


def test_old_central_model_view_does_not_invent_reasoning_support():
    assert CentralClient._execution_view("/v2/products/easel/models", {"models": [model()]}) == {"models": [model()]}


@pytest.mark.parametrize("value", [None, False, {}, [], {"efforts": "high"},
    {"efforts": ["off"]}, {"efforts": [True]}, {"efforts": ["high", "high"]},
    {"efforts": ["high"], "secret": "hidden"}, {"efforts": ["high"] * 100}])
def test_invalid_capabilities_fail_closed_without_silently_granting_levels(value):
    with pytest.raises(CentralError):
        CentralClient._execution_view("/v2/products/easel/models", {"models": [model(reasoning=value)]})
