# SPDX-License-Identifier: GPL-3.0-or-later
"""Draw the atlas plan (sheets, tiles, names, size, px/cm) into a PNG.

Needs a GPU context, so it returns None in background runs.
"""
import colorsys
import os

import bpy
import numpy as np

PANEL = 640     # px for a max-size sheet
GAP = 30
HEAD = 44


def _ortho(w, h):
    from mathutils import Matrix
    return Matrix(((2.0 / w, 0, 0, -1), (0, 2.0 / h, 0, -1), (0, 0, 1, 0), (0, 0, 0, 1)))


def _rect(shader, x, y, w, h, color):
    from gpu_extras.batch import batch_for_shader
    pts = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
    batch = batch_for_shader(shader, "TRIS", {"pos": pts}, indices=((0, 1, 2), (0, 2, 3)))
    shader.uniform_float("color", color)
    batch.draw(shader)


def _short(name):
    for end in ("_Mat", "Mat"):
        if name.endswith(end) and len(name) > len(end):
            return name[: -len(end)].rstrip("_")
    return name


def _k(size):
    return f"{size // 1024}K" if size >= 1024 and size % 1024 == 0 else str(size)


def image_name(state):
    prefix = state.prefix.strip()
    return f"{prefix}_Layout" if prefix else "Atlas_Layout"


def render(context):
    """Render the current plan. Returns the Image, or None (background / nothing planned)."""
    if bpy.app.background:
        return None
    import blf
    import gpu
    from mathutils import Matrix

    state = context.scene.om3d_atlas
    sets = [s for s in state.sets if s.size]
    if not sets:
        return None
    members = {}
    for it in state.items:
        if not it.exclude and it.set_name.strip() and it.tile:
            members.setdefault(it.set_name.strip(), []).append(it)
    max_sheet = max(int(state.max_size), max(s.size for s in sets))
    W = GAP + len(sets) * (PANEL + GAP)
    H = GAP + HEAD + PANEL + GAP

    off = gpu.types.GPUOffScreen(W, H)
    with off.bind():
        fb = gpu.state.active_framebuffer_get()
        fb.clear(color=(0.11, 0.11, 0.12, 1.0))
        gpu.state.blend_set("ALPHA")
        with gpu.matrix.push_pop():
            gpu.matrix.load_matrix(Matrix.Identity(4))
            with gpu.matrix.push_pop_projection():
                gpu.matrix.load_projection_matrix(_ortho(W, H))
                shader = gpu.shader.from_builtin("UNIFORM_COLOR")
                shader.bind()
                font = 0
                for i, st in enumerate(sets):
                    ox = GAP + i * (PANEL + GAP)
                    oy = GAP
                    sc = PANEL / float(max_sheet)
                    side = st.size * sc
                    _rect(shader, ox, oy, side, side, (0.22, 0.22, 0.24, 1))
                    blf.size(font, 22)
                    blf.color(font, 0.95, 0.95, 0.95, 1)
                    blf.position(font, ox, oy + PANEL + 14, 0)
                    kind = "masked" if st.masked else "opaque"
                    blf.draw(font, f"{st.name}  {_k(st.size)}  {kind}  {round(st.fill * 100)}% used")
                    for j, it in enumerate(sorted(members.get(st.name, []), key=lambda x: (-x.tile, x.name))):
                        r, g, b = colorsys.hsv_to_rgb((i * 0.29 + j * 0.13) % 1.0, 0.55, 0.75)
                        bx, by, bs = ox + it.px * sc, oy + it.py * sc, it.tile * sc
                        low = 0 <= it.td_tile < state.target_td
                        if low:
                            r, g, b = 0.85, 0.25, 0.22
                        _rect(shader, bx + 1, by + 1, bs - 2, bs - 2, (r, g, b, 1))
                        size = max(11, min(22, int(bs / 9)))
                        blf.size(font, size)
                        blf.color(font, 0.05, 0.05, 0.05, 1)
                        blf.position(font, bx + 8, by + bs - size - 8, 0)
                        blf.draw(font, _short(it.name))
                        blf.position(font, bx + 8, by + bs - 2 * size - 14, 0)
                        td = f"{it.td_tile:.1f} px/cm" if it.td_tile >= 0 else "-"
                        blf.draw(font, f"{_k(it.tile)}  {td}{'  LOW' if low else ''}")
                    for (x0, y0, w0, h0) in ((ox, oy, side, 2), (ox, oy + side - 2, side, 2),
                                             (ox, oy, 2, side), (ox + side - 2, oy, 2, side)):
                        _rect(shader, x0, y0, w0, h0, (0.9, 0.9, 0.9, 1))
        buf = fb.read_color(0, 0, W, H, 4, 0, "UBYTE")
        gpu.state.blend_set("NONE")
    off.free()

    px = np.array(buf.to_list(), dtype=np.float32).reshape(H, W, 4) / 255.0
    px[..., 3] = 1.0
    name = image_name(state)
    old = bpy.data.images.get(name)
    if old is not None:
        bpy.data.images.remove(old)
    img = bpy.data.images.new(name, W, H)
    img.pixels.foreach_set(px.ravel())
    img["om3d_atlas_layout"] = True
    folder = bpy.path.abspath(state.output_dir)
    if bpy.data.filepath or not state.output_dir.startswith("//"):
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, name + ".png")
        img.filepath_raw = path
        img.file_format = "PNG"
        img.save()
        state.layout_path = path
    else:
        state.layout_path = ""     # unsaved .blend: keep the image in memory only
    return img


def show(context, img):
    """Show the image in an Image Editor: an existing one, or a new window."""
    for win in context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "IMAGE_EDITOR":
                area.spaces.active.image = img
                return True
    before = {w.as_pointer() for w in context.window_manager.windows}
    try:
        bpy.ops.wm.window_new()
    except RuntimeError:
        return False
    for win in context.window_manager.windows:
        if win.as_pointer() not in before:
            area = max(win.screen.areas, key=lambda a: a.width * a.height)
            area.type = "IMAGE_EDITOR"
            area.spaces.active.image = img
            return True
    return False
