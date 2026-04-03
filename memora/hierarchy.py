"""Hierarchy helpers for memory and tag organization."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, Dict, List, Optional


def build_tag_hierarchy(tags: List[str]) -> Dict[str, Any]:
    """Build hierarchical tree data from dotted tags."""
    root: Dict[str, Any] = {"name": "root", "path": [], "children": {}, "tags": []}
    for tag in tags:
        parts = tag.split(".")
        node = root
        if not parts:
            continue
        for part in parts:
            children = node.setdefault("children", {})
            if part not in children:
                children[part] = {
                    "name": part,
                    "path": node["path"] + [part],
                    "children": {},
                    "tags": [],
                }
            node = children[part]
        node.setdefault("tags", []).append(tag)
    return _collapse_tag_tree(root)


def _collapse_tag_tree(node: Dict[str, Any]) -> Dict[str, Any]:
    children_map = node.get("children", {})
    children_list = [_collapse_tag_tree(child) for child in children_map.values()]
    node["children"] = children_list
    node["count"] = len(node.get("tags", [])) + sum(child["count"] for child in children_list)
    return {key: value for key, value in node.items() if key != "children" or value}


def extract_hierarchy_path(metadata: Optional[Any]) -> List[str]:
    """Extract canonical hierarchy path from metadata."""
    if not isinstance(metadata, Mapping):
        return []

    hierarchy = metadata.get("hierarchy")
    if isinstance(hierarchy, Mapping):
        raw_path = hierarchy.get("path")
        if isinstance(raw_path, Sequence) and not isinstance(raw_path, (str, bytes)):
            return [str(part) for part in raw_path if part is not None]

    path: List[str] = []
    section = metadata.get("section")
    if section is not None:
        path.append(str(section))
        subsection = metadata.get("subsection")
        if subsection is not None:
            path.append(str(subsection))
    return path


def suggest_hierarchy_from_similar(
    similar_memories: List[Dict[str, Any]],
    get_memory_by_id: Optional[Callable[[int], Optional[Dict[str, Any]]]] = None,
    max_suggestions: int = 3,
    metadata_by_id: Optional[Dict[int, Optional[Dict[str, Any]]]] = None,
) -> List[Dict[str, Any]]:
    """Suggest hierarchy placement from similar memories.

    Either pass metadata_by_id (batch pre-fetched) or get_memory_by_id (per-item callback).
    """
    path_scores: Dict[tuple[str, ...], float] = {}
    path_examples: Dict[tuple[str, ...], List[int]] = {}

    for item in similar_memories:
        if not item:
            continue

        memory_id = item.get("id")
        if memory_id is None:
            continue
        score = item.get("score", 0)

        # Use pre-fetched metadata if available, otherwise fall back to callback
        if metadata_by_id is not None:
            metadata = metadata_by_id.get(memory_id)
        elif get_memory_by_id is not None:
            full_memory = get_memory_by_id(memory_id)
            metadata = full_memory.get("metadata") if full_memory else None
        else:
            continue

        path = extract_hierarchy_path(metadata)
        if not path:
            continue

        path_tuple = tuple(path)
        path_scores[path_tuple] = path_scores.get(path_tuple, 0) + score
        if path_tuple not in path_examples:
            path_examples[path_tuple] = []
        path_examples[path_tuple].append(memory_id)

    if not path_scores:
        return []

    sorted_paths = sorted(path_scores.items(), key=lambda item: item[1], reverse=True)
    suggestions: List[Dict[str, Any]] = []
    for path_tuple, total_score in sorted_paths[:max_suggestions]:
        path_list = list(path_tuple)
        suggestions.append(
            {
                "path": path_list,
                "section": path_list[0] if path_list else None,
                "subsection": "/".join(path_list[1:]) if len(path_list) > 1 else None,
                "confidence": round(total_score / len(similar_memories), 2),
                "similar_memory_ids": path_examples[path_tuple][:3],
            }
        )
    return suggestions


def get_existing_hierarchy_paths(memories: List[Dict[str, Any]]) -> List[List[str]]:
    """Return unique hierarchy paths and parent paths from stored memories."""
    paths_set: set[tuple[str, ...]] = set()
    for memory in memories:
        if memory is None:
            continue
        path = extract_hierarchy_path(memory.get("metadata"))
        if not path:
            continue
        for index in range(1, len(path) + 1):
            paths_set.add(tuple(path[:index]))

    return sorted([list(path) for path in paths_set], key=lambda path: (len(path), path))


def find_similar_paths(new_path: List[str], existing_paths: List[List[str]]) -> List[List[str]]:
    """Find existing hierarchy paths close to a proposed new path."""
    if not new_path or not existing_paths:
        return []

    suggestions: List[List[str]] = []
    new_path_lower = [part.lower() for part in new_path]
    new_parent = new_path[:-1] if len(new_path) > 1 else []
    new_leaf = new_path_lower[-1] if new_path else ""

    for existing in existing_paths:
        existing_lower = [part.lower() for part in existing]
        existing_parent = existing[:-1] if len(existing) > 1 else []
        existing_leaf = existing_lower[-1] if existing else ""

        if existing_parent == new_parent and existing_lower != new_path_lower:
            if new_leaf in existing_leaf or existing_leaf in new_leaf:
                if existing not in suggestions:
                    suggestions.insert(0, existing)
                continue

        if existing_parent == new_parent and existing not in suggestions:
            suggestions.append(existing)
            continue

        if new_leaf and existing_leaf and (new_leaf in existing_leaf or existing_leaf in new_leaf):
            if existing not in suggestions:
                suggestions.append(existing)

    return suggestions[:5]


def build_hierarchy_tree(
    memories: List[Dict[str, Any]],
    *,
    include_root: bool = False,
    compact: bool = True,
) -> Any:
    """Build hierarchy tree grouped by metadata hierarchy path."""
    root: Dict[str, Any] = {
        "name": "root",
        "path": [],
        "memories": [],
        "children": {},
    }

    for memory in memories:
        path = extract_hierarchy_path(memory.get("metadata"))
        node = root

        if not path:
            memory_data = _compact_memory(memory) if compact else dict(memory)
            if not compact:
                memory_data["hierarchy_path"] = node["path"]
            node["memories"].append(memory_data)
            continue

        for part in path:
            children: Dict[str, Any] = node.setdefault("children", {})
            if part not in children:
                children[part] = {
                    "name": part,
                    "path": node["path"] + [part],
                    "memories": [],
                    "children": {},
                }
            node = children[part]

        memory_data = _compact_memory(memory) if compact else dict(memory)
        if not compact:
            memory_data["hierarchy_path"] = node["path"]
        node["memories"].append(memory_data)

    def collapse(node: Dict[str, Any]) -> Dict[str, Any]:
        children_map: Dict[str, Any] = node.get("children", {})
        children_list = [collapse(child) for child in children_map.values()]
        node["children"] = children_list
        node["count"] = len(node.get("memories", [])) + sum(child["count"] for child in children_list)
        return node

    collapsed = collapse(root)
    if include_root:
        return collapsed
    return collapsed["children"]


def _compact_memory(memory: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Build minimal memory payload for hierarchy responses."""
    if memory is None:
        return None

    content = memory.get("content", "")
    preview = content[:80] + "..." if len(content) > 80 else content
    return {
        "id": memory.get("id"),
        "preview": preview,
        "tags": memory.get("tags", []),
    }
