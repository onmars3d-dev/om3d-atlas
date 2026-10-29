# SPDX-License-Identifier: GPL-3.0-or-later
"""Map types and texture discovery.

A map type is a row in the add-on preferences (key, suffixes, colorspace,
fill colour, is-normal, shader socket). Discovery finds which image in a
material plays which role: node tree first, filename suffix as fallback.
"""
import os

import bpy

# key, suffixes, colorspace, fill RGBA, is_normal, socket
DEFAULT_TYPES = (
    ("D", "_D,_BaseColor,_Albedo,_Diffuse,_Color", "sRGB", (0.0, 0.0, 0.0, 1.0), False, "BASE"),
    ("ORM", "_ORM,_OcclusionRoughnessMetallic", "Non-Color", (1.0, 0.5, 0.0, 1.0), False, "ORM"),
    ("N", "_N,_Normal,_NRM", "Non-Color", (0.5, 0.5, 1.0, 1.0), True, "NORMAL"),
    ("OP", "_OP,_Opacity,_Alpha", "Non-Color", (1.0, 1.0, 1.0, 1.0), False, "ALPHA"),
    ("E", "_E,_Emissive,_Emission", "sRGB", (0.0, 0.0, 0.0, 1.0), False, "EMISSION"),
)

SOCKETS = {
    "BASE": ("Base Color",),
    "ORM": ("Roughness", "Metallic"),
    "NORMAL": ("Normal",),
    "ALPHA": ("Alpha",),
    "EMISSION": ("Emission Color", "Emission"),
}


class MapType:
    __slots__ = ("key", "suffixes", "colorspace", "fill", "is_normal", "socket")

    def __init__(self, key, suffixes, colorspace, fill, is_normal, socket):
        self.key = key
        self.suffixes = [s.strip() for s in suffixes.split(",") if s.strip()]
        self.colorspace = colorspace
        self.fill = tuple(fill)
        self.is_normal = is_normal
        self.socket = socket


def defaults():
    return [MapType(*row) for row in DEFAULT_TYPES]


def from_prefs(prefs):
    """MapType list from the preferences table (defaults when empty or missing)."""
    if prefs is None or not len(prefs.map_types):
        return defaults()
    return [MapType(r.key, r.suffixes, r.colorspace, r.fill, r.is_normal, r.socket)
            for r in prefs.map_types if r.key]


def _principled(nt):
    out = [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL" and n.is_active_output]
    out = out or [n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"]
    bsdf = [n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"]
    if out and out[0].inputs["Surface"].is_linked:
        n = out[0].inputs["Surface"].links[0].from_node
        if n.type == "BSDF_PRINCIPLED":
            return n
    return bsdf[0] if bsdf else None


def _upstream_images(socket, depth=0, through_separate=False, seen=None):
    """Yield (image_node, passed_through_separate) feeding *socket*."""
    if seen is None:
        seen = set()
    if depth > 12:
        return
    for link in socket.links:
        node = link.from_node
        if node.as_pointer() in seen:
            continue
        seen.add(node.as_pointer())
        if node.type == "TEX_IMAGE":
            if node.image:
                yield node, through_separate
            continue
        sep = through_separate or node.type in {"SEPARATE_COLOR", "SEPRGB", "SEPARATE_RGB"}
        for inp in node.inputs:
            if inp.is_linked:
                yield from _upstream_images(inp, depth + 1, sep, seen)


def _stem(image):
    name = os.path.basename(bpy.path.abspath(image.filepath)) if image.filepath else image.name
    return os.path.splitext(name)[0]


def discover(material, types):
    """Return ({key: image_node}, [unclassified image nodes])."""
    found = {}
    if not material or not material.use_nodes or not material.node_tree:
        return found, []
    nt = material.node_tree
    images = [n for n in nt.nodes if n.type == "TEX_IMAGE" and n.image]
    bsdf = _principled(nt)
    used = set()
    if bsdf:
        for t in types:
            if t.key in found or t.socket not in SOCKETS:
                continue
            for sock_name in SOCKETS[t.socket]:
                sock = bsdf.inputs.get(sock_name)
                if not sock or not sock.is_linked:
                    continue
                for node, via_sep in _upstream_images(sock):
                    if t.socket == "ORM" and not via_sep:
                        continue
                    if node.as_pointer() in used:
                        continue
                    found[t.key] = node
                    used.add(node.as_pointer())
                    break
                if t.key in found:
                    break
    # suffix fallback for anything the node pass missed
    by_suffix = sorted(((s.lower(), t) for t in types for s in t.suffixes),
                       key=lambda x: -len(x[0]))
    rest = []
    for node in images:
        if node.as_pointer() in used:
            continue
        stem = _stem(node.image).lower()
        hit = next((t for s, t in by_suffix if stem.endswith(s)), None)
        if hit and hit.key not in found:
            found[hit.key] = node
            used.add(node.as_pointer())
        else:
            rest.append(node)
    return found, rest


def source_resolution(found):
    return max((max(n.image.size) for n in found.values() if n.image), default=0)
