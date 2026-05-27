import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _actual_mcp_tool_names() -> list[str]:
    tree = ast.parse((ROOT / "memora" / "server.py").read_text())
    names = []
    for node in tree.body:
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if (
                isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
                and decorator.func.attr == "tool"
                and isinstance(decorator.func.value, ast.Name)
                and decorator.func.value.id == "mcp"
            ):
                names.append(node.name)
                break
    return names


def _manifest_skill_names() -> list[str]:
    names = []
    in_skills = False
    for line in (ROOT / "agent.yaml").read_text().splitlines():
        if re.match(r"^\S", line):
            in_skills = line == "skills:"
            continue
        if not in_skills:
            continue
        match = re.match(r"\s+- name:\s+([A-Za-z0-9_]+)\s*$", line)
        if match:
            names.append(match.group(1))
    return names


def test_gitagent_manifest_lists_exact_mcp_tool_surface():
    actual = _actual_mcp_tool_names()
    manifest = _manifest_skill_names()

    assert manifest == actual
    assert len(manifest) == len(set(manifest))


def test_gitagent_manifest_version_matches_package_version():
    pyproject = (ROOT / "pyproject.toml").read_text()
    manifest = (ROOT / "agent.yaml").read_text()

    pyproject_version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    manifest_version = re.search(r'^version: "?([^"\n]+)"?', manifest, re.M).group(1)

    assert manifest_version == pyproject_version
