# SPDX-License-Identifier: GPL-3.0-or-later
"""Tile sizing, quadtree (buddy) packing and Auto Plan. No bpy here, so it
can be unit tested outside Blender.

Tiles are power-of-two squares. Sorted largest first, power-of-two squares
fit a power-of-two sheet whenever their total area fits, so buddy packing is
both simple and optimal.
"""

FLAG_OK = ""
FLAG_LOW = "LOW"          # under target even at the largest allowed tile
FLAG_CLAMP = "CLAMP"      # override larger than the source, clamped
FLAG_NOFIT = "NOFIT"      # set does not fit in the max atlas size


class Settings:
    def __init__(self, max_size=4096, target=10.0, inset=8):
        self.max_size = int(max_size)
        self.target = float(target)
        self.inset = int(inset)


class Entry:
    """One material in the plan."""

    def __init__(self, name, roles, src_res, td_src, set_name="", exclude=False, override=0,
                 masked=None, fill_tile=0):
        self.name = name
        self.roles = tuple(sorted(roles))
        # masked = has a real opacity map. Opaque and masked never share a set in Auto Plan.
        self.masked = ("OP" in self.roles) if masked is None else bool(masked)
        self.src_res = int(src_res)
        self.td_src = td_src
        self.set_name = set_name
        self.exclude = exclude
        self.override = int(override)
        # tile size gap fill grew this material to (0 = none); only used when override is Auto
        self.fill_tile = int(fill_tile)
        # results
        self.tile = 0
        self.td_tile = None
        self.flag = FLAG_OK
        self.x = self.y = 0


def _pow2_floor(n):
    p = 1
    while p * 2 <= n:
        p *= 2
    return p


def tile_options(entry, s):
    top = min(_pow2_floor(max(entry.src_res, 1)), s.max_size)
    lo = max(s.max_size // 8, 1)
    opts = []
    t = top
    while t >= lo:
        opts.append(t)
        t //= 2
    if not opts:
        opts = [top]
    return sorted(opts)


def td_at(entry, tile, s):
    if not entry.td_src or not entry.src_res:
        return None
    return entry.td_src * max(tile - 2 * s.inset, 1) / entry.src_res


def size_tile(entry, s):
    """Pick the smallest tile meeting the target. Sets entry.tile/td_tile/flag."""
    opts = tile_options(entry, s)
    entry.flag = FLAG_OK
    if entry.override:
        t = entry.override
        if t > opts[-1]:
            t = opts[-1]
            entry.flag = FLAG_CLAMP
        entry.tile = t
    else:
        entry.tile = opts[-1]
        for t in opts:
            td = td_at(entry, t, s)
            if td is not None and td >= s.target:
                entry.tile = t
                break
        if entry.fill_tile > entry.tile:
            entry.tile = min(entry.fill_tile, opts[-1])
    entry.td_tile = td_at(entry, entry.tile, s)
    if entry.td_tile is not None and entry.td_tile < s.target and entry.flag == FLAG_OK:
        entry.flag = FLAG_LOW
    return entry.tile


class Sheet:
    def __init__(self, size):
        self.size = size
        self.free = [(0, 0, size)]

    def place(self, t):
        cands = [f for f in self.free if f[2] >= t]
        if not cands:
            return None
        f = min(cands, key=lambda c: (c[2], c[1], c[0]))
        self.free.remove(f)
        x, y, sz = f
        while sz > t:
            h = sz // 2
            self.free += [(x + h, y, h), (x, y + h, h), (x + h, y + h, h)]
            sz = h
        return x, y


def pack(tiles, size):
    """tiles: [(key, t)] -> {key: (x, y)} or None if they don't fit."""
    sheet = Sheet(size)
    out = {}
    for key, t in sorted(tiles, key=lambda kt: (-kt[1], kt[0])):
        if t > size:
            return None
        pos = sheet.place(t)
        if pos is None:
            return None
        out[key] = pos
    return out


def smallest_sheet(tiles, max_size):
    """Smallest power-of-two sheet (<= max_size) that holds every tile."""
    if not tiles:
        return None, {}
    size = max(t for _, t in tiles)
    while size <= max_size:
        placed = pack(tiles, size)
        if placed is not None:
            return size, placed
        size *= 2
    return None, None


def layout(entries, s):
    """Size and place every non-excluded entry inside its set.

    Returns {set_name: {"size": int|None, "members": [Entry], "fill": float}}.
    A set that does not fit gets size None and its entries flag NOFIT.
    """
    sets = {}
    for e in entries:
        if e.exclude or not e.set_name:
            continue
        size_tile(e, s)
        sets.setdefault(e.set_name, []).append(e)
    out = {}
    for name, members in sets.items():
        size, placed = smallest_sheet([(e.name, e.tile) for e in members], s.max_size)
        if size is None:
            for e in members:
                e.flag = FLAG_NOFIT
            out[name] = {"size": None, "members": members, "fill": 0.0,
                         "mixed": len({e.masked for e in members}) > 1}
            continue
        for e in members:
            e.x, e.y = placed[e.name]
        fill = sum(e.tile * e.tile for e in members) / float(size * size)
        mixed = len({e.masked for e in members}) > 1
        out[name] = {"size": size, "members": members, "fill": fill, "mixed": mixed}
    return out


def _ffd(entries, sizes):
    """First-fit decreasing into sheets of fixed *sizes*. Returns [sheet index per entry] or None."""
    sheets = [Sheet(z) for z in sizes]
    where = {}
    for e in sorted(entries, key=lambda e: (-e.tile, e.name)):
        for i, sh in enumerate(sheets):
            if sh.place(e.tile) is not None:
                where[e.name] = i
                break
        else:
            return None
    return where


def _grow_options(e, s):
    """Next tile size up for gap fill, or None (overrides are never grown)."""
    if e.override:
        return None
    top = tile_options(e, s)[-1]
    t = e.tile * 2
    return t if t <= top else None


def _fill_gaps(members, sizes, s):
    """Grow tiles into free space without adding sheets or making any sheet bigger.
    Lowest density first. A tile pushed out by a growth may move to another sheet."""
    while True:
        grew = False
        for e in sorted(members, key=lambda e: (e.td_tile if e.td_tile is not None else 1e9, e.name)):
            t = _grow_options(e, s)
            if t is None:
                continue
            old = e.tile
            e.tile = t
            if _ffd(members, sizes) is not None:
                e.fill_tile = t
                e.td_tile = td_at(e, t, s)
                grew = True
                break
            e.tile = old
        if not grew:
            return _ffd(members, sizes)


def auto_plan(entries, s, fill=True):
    """Assign set names. Opaque and masked materials are planned separately and
    never share a set. First-fit decreasing into max-size sheets, then (fill=True)
    grow tiles into the leftover space."""
    groups = {}
    for e in entries:
        e.fill_tile = 0
        if e.exclude:
            continue
        size_tile(e, s)
        groups.setdefault(e.masked, []).append(e)
    names_used = {}
    for masked in (False, True):
        members = groups.get(masked, [])
        if not members:
            continue
        base = "Masked" if masked else "Main"
        where = _ffd(members, [s.max_size] * len(members))
        n = max(where.values()) + 1
        # shrink each sheet to the smallest that holds its tiles, then fill gaps at those sizes
        sizes = []
        for i in range(n):
            size, _ = smallest_sheet([(e.name, e.tile) for e in members if where[e.name] == i], s.max_size)
            sizes.append(size)
        order = sorted(range(n), key=lambda i: -sizes[i])
        sizes = [sizes[i] for i in order]
        where = _fill_gaps(members, sizes, s) if fill else _ffd(members, sizes)
        names_used[base] = n
        for e in members:
            e.set_name = f"{base}{where[e.name] + 1}" if n > 1 else base
    return entries
