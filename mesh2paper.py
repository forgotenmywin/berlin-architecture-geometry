#!/usr/bin/env python3
"""
Photogrammetry mesh -> tiny flat-shaded GLB, for placing on the web map.

Scans are enormous and textured; a map needs neither. The Hegel-Denkmal scan
is 2,000,416 triangles for an object that occupies about eleven pixels at the
map's maximum zoom, and its textures run to 21 MB.

meshoptimizer's simplifier will not get a photogrammetry mesh anywhere near
that budget - it refuses to collapse across the disconnected components such
scans are full of, and stalls around 45,000 triangles however hard you push
`--ratio` and `--error`. So this does vertex clustering instead: snap every
vertex to a grid, weld, drop the degenerate triangles. Crude next to quadric
error metrics, and completely adequate at eleven pixels.

Output matches what `lod2.py glb` writes, so the client needs no second code
path: local metres, Z up, one unlit primitive, COLOR_0 carrying a shading
factor as grey so the material colour can be set per theme.

Usage
-----
  python3 mesh2paper.py in.glb out.glb --target-tris 4000
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
from pathlib import Path

_CTYPE = {5120: ("b", 1), 5121: ("B", 1), 5122: ("h", 2),
          5123: ("H", 2), 5125: ("I", 4), 5126: ("f", 4)}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


# --------------------------------------------------------------------------
# glTF reading
# --------------------------------------------------------------------------

def read_glb(path: Path):
    raw = path.read_bytes()
    if raw[:4] != b"glTF":
        sys.exit(f"{path} is not a binary glTF")
    total = struct.unpack("<I", raw[8:12])[0]
    off, js, bin_ = 12, None, b""
    while off < total:
        clen, ctype = struct.unpack("<I4s", raw[off:off + 8])
        chunk = raw[off + 8:off + 8 + clen]
        if ctype == b"JSON":
            js = json.loads(chunk)
        elif ctype.startswith(b"BIN"):
            bin_ = chunk
        off += 8 + clen
    if js is None:
        sys.exit("no JSON chunk")
    if js.get("extensionsRequired"):
        sys.exit(f"needs extensions this reader lacks: {js['extensionsRequired']}")
    return js, bin_


def accessor(js, bin_, idx):
    """Read one accessor, honouring interleaved bufferViews."""
    a = js["accessors"][idx]
    n = _NCOMP[a["type"]]
    fmt, size = _CTYPE[a["componentType"]]
    count = a["count"]
    bv = js["bufferViews"][a["bufferView"]]
    base = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    stride = bv.get("byteStride") or (size * n)
    out = []
    for i in range(count):
        o = base + i * stride
        out.append(struct.unpack_from(f"<{n}{fmt}", bin_, o))
    return out


def node_matrices(js):
    """World matrix per node, walking the scene graph from its roots."""
    def compose(nd):
        if "matrix" in nd:                        # column-major in glTF
            m = nd["matrix"]
            return [[m[0], m[4], m[8], m[12]],
                    [m[1], m[5], m[9], m[13]],
                    [m[2], m[6], m[10], m[14]],
                    [m[3], m[7], m[11], m[15]]]
        t = nd.get("translation", [0, 0, 0])
        r = nd.get("rotation", [0, 0, 0, 1])      # xyzw
        s = nd.get("scale", [1, 1, 1])
        x, y, z, w = r
        rot = [
            [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
            [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
            [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
        ]
        return [[rot[i][j] * s[j] for j in range(3)] + [t[i]] for i in range(3)] + [[0, 0, 0, 1]]

    def mul(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]

    out = {}

    def walk(i, parent):
        nd = js["nodes"][i]
        m = mul(parent, compose(nd))
        out[i] = m
        for c in nd.get("children", []):
            walk(c, m)

    ident = [[1 if i == j else 0 for j in range(4)] for i in range(4)]
    for root in js["scenes"][js.get("scene", 0)]["nodes"]:
        walk(root, ident)
    return out


def world_triangles(js, bin_):
    """Every triangle in the scene, in world space, Y-up as glTF stores it."""
    mats = node_matrices(js)
    tris = []
    for ni, nd in enumerate(js.get("nodes", [])):
        if "mesh" not in nd or ni not in mats:
            continue
        m = mats[ni]
        for prim in js["meshes"][nd["mesh"]]["primitives"]:
            if prim.get("mode", 4) != 4:
                continue
            pos = accessor(js, bin_, prim["attributes"]["POSITION"])
            idx = ([i[0] for i in accessor(js, bin_, prim["indices"])]
                   if "indices" in prim else list(range(len(pos))))
            world = [(m[0][0] * p[0] + m[0][1] * p[1] + m[0][2] * p[2] + m[0][3],
                      m[1][0] * p[0] + m[1][1] * p[1] + m[1][2] * p[2] + m[1][3],
                      m[2][0] * p[0] + m[2][1] * p[1] + m[2][2] * p[2] + m[2][3])
                     for p in pos]
            for k in range(0, len(idx) - 2, 3):
                tris.append((world[idx[k]], world[idx[k + 1]], world[idx[k + 2]]))
    return tris


# --------------------------------------------------------------------------
# decimation
# --------------------------------------------------------------------------

def cluster(tris, cell):
    """Snap to a grid, weld, drop triangles that collapsed to a line."""
    def key(p):
        return (round(p[0] / cell), round(p[1] / cell), round(p[2] / cell))

    seen, verts, out = {}, [], []
    for a, b, c in tris:
        ka, kb, kc = key(a), key(b), key(c)
        if ka == kb or kb == kc or ka == kc:
            continue
        ids = []
        for k in (ka, kb, kc):
            i = seen.get(k)
            if i is None:
                i = len(verts)
                seen[k] = i
                verts.append((k[0] * cell, k[1] * cell, k[2] * cell))
            ids.append(i)
        out.append(tuple(ids))
    return verts, out


def decimate_to(tris, target):
    """Binary-search the grid size that lands nearest the triangle budget."""
    xs = [p[0] for t in tris for p in t]
    ys = [p[1] for t in tris for p in t]
    zs = [p[2] for t in tris for p in t]
    extent = max(max(xs) - min(xs), max(ys) - min(ys), max(zs) - min(zs))
    lo, hi = extent / 4000, extent / 4
    best = None
    for _ in range(18):
        mid = math.sqrt(lo * hi)
        verts, faces = cluster(tris, mid)
        if best is None or abs(len(faces) - target) < abs(best[2] - target):
            best = (verts, faces, len(faces), mid)
        if len(faces) > target:
            lo = mid
        else:
            hi = mid
    return best[0], best[1], best[3]


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------

def write_glb(path: Path, verts, faces, extras: dict):
    pos, col, idx = [], bytearray(), []
    # Flat shading: one vertex per corner so each face keeps its own tone —
    # the same north-west key light lod2.py uses, so the monument sits in the
    # same light as the buildings around it.
    for a, b, c in faces:
        pa, pb, pc = verts[a], verts[b], verts[c]
        ux, uy, uz = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        vx, vy, vz = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        nx, ny, nz = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        lam = max(0.0, (nx / ln) * -0.45 + (ny / ln) * -0.35 + (nz / ln) * 0.82)
        shade = max(0, min(255, round((0.60 + 0.40 * lam) * 255)))
        for p in (pa, pb, pc):
            idx.append(len(pos) // 3)
            pos.extend(p)
            col.extend((shade, shade, shade, 255))

    buf = bytearray()

    def pad4():
        while len(buf) % 4:
            buf.append(0)

    pad4(); pos_off = len(buf); buf.extend(struct.pack(f"<{len(pos)}f", *pos))
    pad4(); col_off = len(buf); buf.extend(bytes(col))
    pad4(); idx_off = len(buf); buf.extend(struct.pack(f"<{len(idx)}I", *idx))

    xs, ys, zs = pos[0::3], pos[1::3], pos[2::3]
    gltf = {
        "asset": {"version": "2.0", "generator": "mesh2paper.py"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": extras.get("name", "model")}],
        "meshes": [{"primitives": [{
            "attributes": {"POSITION": 0, "COLOR_0": 1}, "indices": 2,
            "material": 0, "mode": 4}]}],
        "materials": [{
            "name": "monument",
            "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                     "metallicFactor": 0, "roughnessFactor": 1},
            "extensions": {"KHR_materials_unlit": {}},
        }],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": len(pos) // 3, "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)], "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5121, "normalized": True,
             "count": len(col) // 4, "type": "VEC4"},
            {"bufferView": 2, "componentType": 5125, "count": len(idx), "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": pos_off, "byteLength": len(pos) * 4, "target": 34962},
            {"buffer": 0, "byteOffset": col_off, "byteLength": len(col), "target": 34962},
            {"buffer": 0, "byteOffset": idx_off, "byteLength": len(idx) * 4, "target": 34963},
        ],
        "buffers": [{"byteLength": len(buf)}],
        "extras": extras,
    }
    js = json.dumps(gltf, separators=(",", ":")).encode()
    while len(js) % 4:
        js += b" "
    pad4()
    out = bytearray(b"glTF")
    out.extend(struct.pack("<II", 2, 12 + 8 + len(js) + 8 + len(buf)))
    out.extend(struct.pack("<I", len(js))); out.extend(b"JSON"); out.extend(js)
    out.extend(struct.pack("<I", len(buf))); out.extend(b"BIN\x00"); out.extend(buf)
    path.write_bytes(bytes(out))


def write_svg(path: Path, verts, faces, azimuth=45.0, elevation=35.264, width=220):
    """Also emit a flat axonometric sprite.

    The map's default view has no WebGL layer loaded at all, so a monument that
    only exists as a mesh cannot be shown there. A pre-rendered sprite can:
    it is a plain <image>, costs nothing, and matches the paper styling.

    Same projection as lod2.py's SVG writer, painter-sorted, no strokes — at
    ~100 px on screen the internal edges would only muddy it.
    """
    a, e = math.radians(azimuth), math.radians(elevation)
    right = (-math.sin(a), math.cos(a), 0.0)
    up = (math.sin(e) * math.cos(a), math.sin(e) * math.sin(a), math.cos(e))
    view = (math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), -math.sin(e))

    def proj(p):
        return (p[0] * right[0] + p[1] * right[1] + p[2] * right[2],
                -(p[0] * up[0] + p[1] * up[1] + p[2] * up[2]))

    tris = []
    for ia, ib, ic in faces:
        pa, pb, pc = verts[ia], verts[ib], verts[ic]
        ux, uy, uz = (pb[0] - pa[0], pb[1] - pa[1], pb[2] - pa[2])
        vx, vy, vz = (pc[0] - pa[0], pc[1] - pa[1], pc[2] - pa[2])
        nx, ny, nz = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
        if (nx * view[0] + ny * view[1] + nz * view[2]) / ln >= 0:
            continue                       # faces away
        lam = max(0.0, (nx / ln) * -0.45 + (ny / ln) * -0.35 + (nz / ln) * 0.82)
        depth = sum(p[0] * view[0] + p[1] * view[1] + p[2] * view[2]
                    for p in (pa, pb, pc)) / 3
        tris.append((depth, [proj(pa), proj(pb), proj(pc)], 0.60 + 0.40 * lam))
    tris.sort(key=lambda t: -t[0])

    xs = [p[0] for _d, pp, _s in tris for p in pp]
    ys = [p[1] for _d, pp, _s in tris for p in pp]
    mnx, mxx, mny, mxy = min(xs), max(xs), min(ys), max(ys)
    scale = width / (mxx - mnx)
    h = (mxy - mny) * scale
    body = []
    for _d, pp, sh in tris:
        pts = " ".join(f"{(p[0]-mnx)*scale:.1f},{(p[1]-mny)*scale:.1f}" for p in pp)
        v = max(0, min(255, round(sh * 255)))
        body.append(f'<polygon points="{pts}" fill="#{v:02x}{v:02x}{v:02x}"/>')
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{h:.0f}" '
        f'viewBox="0 0 {width} {h:.0f}">{"".join(body)}</svg>\n', encoding="utf-8")
    return len(tris), h


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("--target-tris", type=int, default=4000)
    ap.add_argument("--name", default="model")
    ap.add_argument("--credit", default="", help="attribution line, stored in extras")
    ap.add_argument("--svg", help="also write a flat axonometric sprite here")
    ap.add_argument("--svg-width", type=int, default=220)
    ap.add_argument("--svg-tris", type=int, default=600,
                    help="the sprite is ~200 px wide; it needs far fewer "
                         "triangles than the mesh, and every one costs bytes")
    ap.add_argument("--crop-radius", type=float, default=0.0,
                    help="keep only geometry within this many metres of the "
                         "vertical axis, measured before decimation")
    args = ap.parse_args()

    js, bin_ = read_glb(Path(args.input))
    tris = world_triangles(js, bin_)
    print(f"read {len(tris):,} triangles")

    if args.crop_radius > 0:
        # Vertex clustering destroys anything openwork: the Denkmal's cast-iron
        # railing has bars a few centimetres apart, so any cell coarse enough to
        # hit the triangle budget welds them into a solid wall that swallows the
        # monument. Cropping to the monument itself is the honest fix — the
        # railing cannot survive this level of decimation in any useful form.
        xs = [p[0] for t in tris for p in t]
        zs = [p[2] for t in tris for p in t]      # still Y-up here
        cx = (max(xs) + min(xs)) / 2
        cz = (max(zs) + min(zs)) / 2
        r2 = args.crop_radius ** 2
        kept = [t for t in tris
                if all((p[0] - cx) ** 2 + (p[2] - cz) ** 2 <= r2 for p in t)]
        print(f"cropped to {args.crop_radius} m of the axis: "
              f"{len(kept):,} triangles ({len(tris)-len(kept):,} dropped)")
        tris = kept

    verts, faces, cell = decimate_to(tris, args.target_tris)
    print(f"clustered at {cell*100:.1f} cm -> {len(faces):,} triangles, {len(verts):,} verts")

    # glTF is Y-up; everything downstream here is Z-up ENU metres. Also drop
    # the model onto z = 0 so the client can sit it on the ground.
    zup = [(p[0], -p[2], p[1]) for p in verts]
    minz = min(p[2] for p in zup)
    cx = (max(p[0] for p in zup) + min(p[0] for p in zup)) / 2
    cy = (max(p[1] for p in zup) + min(p[1] for p in zup)) / 2
    zup = [(p[0] - cx, p[1] - cy, p[2] - minz) for p in zup]

    w = max(p[0] for p in zup) - min(p[0] for p in zup)
    d = max(p[1] for p in zup) - min(p[1] for p in zup)
    h = max(p[2] for p in zup)
    print(f"extent {w:.2f} x {d:.2f} x {h:.2f} m (centred on origin, sitting on z=0)")

    write_glb(Path(args.output), zup, faces,
              {"name": args.name, "units": "metres, Z up, centred, base at z=0",
               "height_m": round(h, 3), "credit": args.credit})
    print(f"wrote {args.output} ({Path(args.output).stat().st_size/1024:.0f} kB)")

    if args.svg:
        sv, sf, scell = decimate_to(tris, args.svg_tris)
        szup = [(p[0], -p[2], p[1]) for p in sv]
        smin = min(p[2] for p in szup)
        scx = (max(p[0] for p in szup) + min(p[0] for p in szup)) / 2
        scy = (max(p[1] for p in szup) + min(p[1] for p in szup)) / 2
        szup = [(p[0] - scx, p[1] - scy, p[2] - smin) for p in szup]
        n, sh = write_svg(Path(args.svg), szup, sf, width=args.svg_width)
        print(f"wrote {args.svg} ({Path(args.svg).stat().st_size/1024:.0f} kB, "
              f"{n:,} visible faces, {args.svg_width}x{sh:.0f})")


if __name__ == "__main__":
    main()
