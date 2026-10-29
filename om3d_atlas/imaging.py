# SPDX-License-Identifier: GPL-3.0-or-later
"""Pixel work with numpy: load, resample into a tile, edge-extend gutter, save.

Arrays are (h, w, 4) float32 with row 0 at the bottom, the same layout as
Image.pixels.
"""
import os

import bpy
import numpy as np


def load(image):
    w, h = image.size
    a = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(a)
    return a.reshape(h, w, 4)


def _half(a):
    h, w = a.shape[:2]
    return a[: h - h % 2, : w - w % 2].reshape(h // 2, 2, w // 2, 2, 4).mean(axis=(1, 3))


def _bilinear(a, out_w, out_h):
    h, w = a.shape[:2]
    if (w, h) == (out_w, out_h):
        return a
    # pixel centres: src = (dst + 0.5) * scale - 0.5
    xs = (np.arange(out_w) + 0.5) * (w / out_w) - 0.5
    ys = (np.arange(out_h) + 0.5) * (h / out_h) - 0.5
    xs = np.clip(xs, 0, w - 1)
    ys = np.clip(ys, 0, h - 1)
    x0 = np.floor(xs).astype(np.int32)
    y0 = np.floor(ys).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (xs - x0)[None, :, None].astype(np.float32)
    fy = (ys - y0)[:, None, None].astype(np.float32)
    top = a[y0][:, x0] * (1 - fx) + a[y0][:, x1] * fx
    bot = a[y1][:, x0] * (1 - fx) + a[y1][:, x1] * fx
    return top * (1 - fy) + bot * fy


def resample(a, size):
    """Square *size* x *size*: box halving while possible, bilinear for the rest."""
    while a.shape[0] >= 2 * size and a.shape[1] >= 2 * size:
        a = _half(a)
    return _bilinear(a, size, size).astype(np.float32, copy=False)


def renormalize(a):
    v = a[..., :3] * 2.0 - 1.0
    v /= np.maximum(np.linalg.norm(v, axis=2, keepdims=True), 1e-6)
    a[..., :3] = v * 0.5 + 0.5
    return a


def tile(src, tile_size, inset, is_normal):
    """Source array -> tile_size square with *inset* px of edge-extended gutter."""
    content = max(tile_size - 2 * inset, 1)
    a = resample(src, content)
    if is_normal:
        a = renormalize(a)
    if inset:
        a = np.pad(a, ((inset, inset), (inset, inset), (0, 0)), mode="edge")
    return a


def blank(size, fill):
    a = np.empty((size, size, 4), np.float32)
    a[:] = fill
    return a


def save(name, arr, path, colorspace, file_format, alpha=False):
    """Write *arr* to *path* and return a FILE image pointing at it.

    alpha=False forces A to 1 and writes RGB. alpha=True keeps straight alpha
    (some diffuse maps carry opacity in A).
    """
    size = arr.shape[0]
    if not alpha:
        arr[..., 3] = 1.0
    img = bpy.data.images.get(name)
    if img is not None and img.users == 0:
        bpy.data.images.remove(img)
        img = None
    if img is None:
        img = bpy.data.images.new(name, size, size, alpha=alpha)
    else:
        img.source = "GENERATED"
        img.generated_width = size
        img.generated_height = size
    img.alpha_mode = "STRAIGHT"
    img.colorspace_settings.name = colorspace
    img.pixels.foreach_set(arr.ravel())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.filepath_raw = path
    img.file_format = file_format
    img.save()
    img.source = "FILE"
    img.filepath = path
    img.reload()
    return img
