"""
Schémas publiés par FastMCP : ils sont dérivés des signatures (Annotated + Field),
seule source de vérité des paramètres depuis la suppression de server/server.py.
"""

import asyncio
import importlib.util
import pytest
from unittest.mock import MagicMock

from mcp.server.fastmcp.exceptions import ToolError

import main
from betaflight.commands import PID_AXES, PID_GAIN_MAX, RC_CHANNEL_NAMES
from betaflight import rates
from config.settings import MAX_SAMPLING_DURATION_S


@pytest.fixture(scope="module")
def tools() -> dict:
    return {tool.name: tool for tool in asyncio.run(main.app.list_tools())}


def props(tools: dict, name: str) -> dict:
    return tools[name].inputSchema.get("properties", {})


# ── Couverture ────────────────────────────────────────────────────────

def test_every_registered_tool_is_published(tools):
    assert set(tools) == set(main.MCP_TOOLS)


def test_every_parameter_has_a_description(tools):
    missing = [f"{name}.{param}"
               for name, tool in tools.items()
               for param, schema in tool.inputSchema.get("properties", {}).items()
               if not schema.get("description")]
    assert missing == []


def test_every_tool_has_annotations(tools):
    assert [name for name, tool in tools.items() if tool.annotations is None] == []


def test_fallback_server_removed():
    assert importlib.util.find_spec("server.server") is None


def test_registry_has_no_parallel_parameter_metadata():
    assert all(set(meta) <= {"fn", "description", "annotations"} for meta in main.MCP_TOOLS.values())


# ── Annotations MCP ───────────────────────────────────────────────────

WRITE_TOOLS = ("set_pid_values", "set_rates", "save_config", "reboot_fc")


def test_read_tools_are_read_only(tools):
    readers = [name for name in tools if name.startswith(("get_", "measure_", "detect_", "snapshot_"))]
    assert readers
    assert all(tools[name].annotations.readOnlyHint is True for name in readers)


@pytest.mark.parametrize("name", WRITE_TOOLS)
def test_write_tools_are_destructive(tools, name):
    annotations = tools[name].annotations
    assert annotations.readOnlyHint is False
    assert annotations.destructiveHint is True


# ── Contraintes ───────────────────────────────────────────────────────

def test_set_pid_values_schema(tools):
    p = props(tools, "set_pid_values")
    assert p["axis"]["enum"] == list(PID_AXES)
    for gain in ("p", "i", "d"):
        assert p[gain]["minimum"] == 0
        assert p[gain]["maximum"] == PID_GAIN_MAX == 250
    assert set(tools["set_pid_values"].inputSchema["required"]) == {"axis", "p", "i", "d"}


def test_set_rates_schema(tools):
    p = props(tools, "set_rates")
    expected = {f"{axis}_{field}" for axis in rates.AXES for field in rates.RATE_FIELDS}
    assert set(p) == expected | {"throttle_mid", "throttle_expo"}
    assert "required" not in tools["set_rates"].inputSchema
    assert p["throttle_mid"]["anyOf"][0]["maximum"] == 1


@pytest.mark.parametrize("name", ["measure_rc_noise", "detect_rc_mapping", "detect_rc_channel_move"])
def test_sampling_duration_is_bounded(tools, name):
    duration = props(tools, name)["duration_s"]
    assert duration["exclusiveMinimum"] == 0
    assert duration["maximum"] == MAX_SAMPLING_DURATION_S


@pytest.mark.parametrize("name", ["snapshot_rc_delta", "detect_rc_channel_move"])
def test_baseline_is_a_bounded_list_of_integers(tools, name):
    baseline = props(tools, name)["baseline"]
    assert baseline["items"]["type"] == "integer"
    assert baseline["minItems"] == 1
    assert baseline["maxItems"] == len(RC_CHANNEL_NAMES)
    assert "baseline" in tools[name].inputSchema["required"]


def test_measure_rc_noise_channels_are_valid_indices(tools):
    items = props(tools, "measure_rc_noise")["channels"]["anyOf"][0]["items"]
    assert items["minimum"] == 0
    assert items["maximum"] == len(RC_CHANNEL_NAMES) - 1


# ── Rejet avant tout accès au FC ──────────────────────────────────────

@pytest.fixture
def untouchable_fc():
    from server import tools as _tools
    fc         = MagicMock()
    original   = _tools._bf
    _tools._bf = fc
    yield fc
    _tools._bf = original


@pytest.mark.parametrize("name, arguments", [
    ("set_pid_values",   {"axis": "alt",  "p": 40,  "i": 40, "d": 20}),
    ("set_pid_values",   {"axis": "roll", "p": 300, "i": 40, "d": 20}),
    ("set_rates",        {"roll_rate": -5}),
    ("detect_rc_mapping", {"duration_s": 3600}),
    ("snapshot_rc_delta", {"baseline": ["1500"] * 4 + [None]}),
    ("measure_rc_noise",  {"channels": [-1]}),
])
def test_out_of_schema_calls_never_reach_the_fc(untouchable_fc, name, arguments):
    with pytest.raises(ToolError):
        asyncio.run(main.app.call_tool(name, arguments))
    assert untouchable_fc.mock_calls == []
