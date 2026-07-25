#!/usr/bin/env python3
"""
Berlin LoD2 -> isometric vector pipeline.

Pulls the official Berlin 3D building model (CityGML LoD2, real roof geometry),
clips it to the landmarks you list, and emits axonometric SVG ready for
Illustrator, plus OBJ if you'd rather set the camera up in a 3D app.

Source : https://gdi.berlin.de/data/a_lod2/atom/  (1x1 km tiles, EPSG:25833)
License: Datenlizenz Deutschland Zero 2.0 - public-domain equivalent,
         no attribution legally required, commercial use fine.

Subcommands
-----------
  tiles    list which tiles your landmarks need (no download)
  fetch    download + cache those tiles
  build    parse, clip, project, write SVG/OBJ/CSV
  probe    dump stats for one landmark (heights, roof types) - sanity check
  find     locate a building by geometric signature, offline
  geojson  footprints + heights as GeoJSON (prisms; a client extrudes them)
  glb      the real wall + roof surfaces as binary glTF, for web maps

Geometry sources
----------------
Per landmark via "source" in the JSON, or --source for a whole run:

  lod2  (default) official CityGML. Survey-accurate footprints, real roof
        forms, city-wide. Right for nearly everything.
  osm   OpenStreetMap Simple 3D Buildings. Only worth reaching for where LoD2
        generalises a landmark away - in practice that is the Berliner Dom,
        which LoD2 gives as a flat 43 m slab against a real 98 m.
  both  LoD2 body plus OSM detail. Set "base_z_m" to the LoD2 ground level
        first: OSM heights are metres above ground, LoD2 is absolute
        DHHN2016, so without it the OSM parts sit ~32 m underground.

Typical run
-----------
  python3 lod2.py fetch
  python3 lod2.py build --only fernsehturm,berliner-dom
  python3 lod2.py build                      # everything in landmarks.json
  python3 lod2.py --only berliner-dom --source both build
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import struct
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

try:
    from pyproj import Transformer
except ImportError:
    sys.exit("Missing dependency. Run:  pip install -r requirements.txt")

from xml.etree import ElementTree as ET

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data" / "tiles"          # cached CityGML tiles
OSM_DATA = ROOT / "data" / "osm"        # cached OSM extracts
OUT = ROOT / "out"                      # deliverables
ATOM_BASE = "https://gdi.berlin.de/data/a_lod2/atom"
# The main OSM API, not Overpass. A bbox map call is all we need, and it is far
# more reliable than the public Overpass instances, which routinely 504.
OSM_API = "https://api.openstreetmap.org/api/0.6/map"

NS = {
    "core": "http://www.opengis.net/citygml/1.0",
    "bldg": "http://www.opengis.net/citygml/building/1.0",
    "gml": "http://www.opengis.net/gml",
    "gen": "http://www.opengis.net/citygml/generics/1.0",
}
Q_BUILDING = f"{{{NS['bldg']}}}Building"
Q_POSLIST = f"{{{NS['gml']}}}posList"
Q_POS = f"{{{NS['gml']}}}pos"
Q_POLYGON = f"{{{NS['gml']}}}Polygon"
Q_EXTERIOR = f"{{{NS['gml']}}}exterior"
Q_INTERIOR = f"{{{NS['gml']}}}interior"
Q_GMLID = f"{{{NS['gml']}}}id"

SURFACE_KINDS = {"WallSurface": "wall", "RoofSurface": "roof",
                 "GroundSurface": "ground", "ClosureSurface": "closure"}

# AdV roof-form codes -> plain language. Handy when picking what to draw.
ROOF_TYPES = {
    "1000": "flat", "2100": "shed", "2200": "offset shed", "3100": "gable",
    "3200": "hip", "3300": "half-hip", "3400": "mansard", "3500": "pavilion",
    "3600": "conical", "3700": "dome", "3800": "sawtooth", "3900": "barrel",
    "4000": "spire", "5000": "mixed", "9999": "other",
}

# Camera presets: (azimuth deg, elevation deg)
CAMERA_PRESETS = {
    "true-iso": (45.0, 35.264),     # equal foreshortening on all 3 axes
    "illustrator": (45.0, 30.0),    # matches AI's default isometric SSR
    "pixel-2to1": (45.0, 26.565),   # 2:1 game-art isometric, clean pixel slopes
    "planometric": (45.0, 45.0),    # architectural "military" projection
    "low-oblique": (45.0, 15.0),    # dramatic, streetish
}

# Base palette. Muted greys - you'll restyle in Illustrator anyway, but the
# tonal separation makes the geometry readable straight out of the box.
BASE_COLORS = {"roof": (0.78, 0.78, 0.80), "wall": (0.92, 0.92, 0.93),
               "ground": (0.55, 0.55, 0.58), "closure": (0.85, 0.85, 0.86)}

WGS84_TO_UTM33 = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

@dataclass
class Face:
    ring: list[tuple[float, float, float]]          # exterior, closed
    holes: list[list[tuple[float, float, float]]] = field(default_factory=list)
    kind: str = "wall"

    def normal(self) -> tuple[float, float, float]:
        """Newell's method - robust for non-planar-ish polygons."""
        nx = ny = nz = 0.0
        pts = self.ring
        for i in range(len(pts) - 1):
            x1, y1, z1 = pts[i]
            x2, y2, z2 = pts[i + 1]
            nx += (y1 - y2) * (z1 + z2)
            ny += (z1 - z2) * (x1 + x2)
            nz += (x1 - x2) * (y1 + y2)
        m = math.sqrt(nx * nx + ny * ny + nz * nz)
        return (0.0, 0.0, 1.0) if m < 1e-12 else (nx / m, ny / m, nz / m)


@dataclass
class Building:
    gml_id: str
    faces: list[Face] = field(default_factory=list)
    height: float | None = None
    roof_type: str | None = None
    function: str | None = None
    # OSM Simple 3D Buildings only. LoD2 carries its shape in the geometry, so
    # it has no use for these; OSM describes a solid parametrically and a
    # client has to rebuild it. Kept out of the SVG/OBJ paths entirely.
    min_height: float | None = None      # metres above ground the part starts
    roof_height: float | None = None     # of `height`, how much is roof

    def vertices(self):
        for f in self.faces:
            yield from f.ring

    def extents(self):
        """(geometric height, footprint width, footprint depth) in metres.
        Compare geometric height against `height` (the surveyed
        measuredHeight): a big gap means the model has been generalised and
        you've probably lost a dome or spire."""
        vs = list(self.vertices())
        if not vs:
            return 0.0, 0.0, 0.0
        xs, ys, zs = zip(*vs)
        return max(zs) - min(zs), max(xs) - min(xs), max(ys) - min(ys)


class Camera:
    """Axonometric (parallel) camera. No perspective - that's the point."""

    def __init__(self, azimuth_deg: float, elevation_deg: float):
        a, b = math.radians(azimuth_deg), math.radians(elevation_deg)
        # view direction: from camera into the scene, angled downward
        self.d = (math.cos(b) * math.cos(a), math.cos(b) * math.sin(a), -math.sin(b))
        self.right = (-math.sin(a), math.cos(a), 0.0)
        self.up = (math.sin(b) * math.cos(a), math.sin(b) * math.sin(a), math.cos(b))

    def project(self, p):
        r, u = self.right, self.up
        return (p[0] * r[0] + p[1] * r[1] + p[2] * r[2],
                -(p[0] * u[0] + p[1] * u[1] + p[2] * u[2]))   # SVG y grows down

    def depth(self, p):
        d = self.d
        return p[0] * d[0] + p[1] * d[1] + p[2] * d[2]        # bigger = farther


# --------------------------------------------------------------------------
# tiles
# --------------------------------------------------------------------------

def tile_name(east: float, north: float) -> str:
    return f"LoD2_{int(east // 1000)}_{int(north // 1000)}"


def tiles_for(east: float, north: float, radius: float) -> set[str]:
    """Every 1 km tile touched by the bbox around a point."""
    out = set()
    e0, e1 = east - radius, east + radius
    n0, n1 = north - radius, north + radius
    ei, ni = int(e0 // 1000), int(n0 // 1000)
    while ei * 1000 <= e1:
        nj = ni
        while nj * 1000 <= n1:
            out.add(f"LoD2_{ei}_{nj}")
            nj += 1
        ei += 1
    return out


def load_landmarks(path: Path, only: str | None):
    marks = json.loads(path.read_text(encoding="utf-8"))["landmarks"]
    for m in marks:
        m["east"], m["north"] = WGS84_TO_UTM33.transform(m["lon"], m["lat"])
    if only:
        want = {s.strip() for s in only.split(",") if s.strip()}
        missing = want - {m["id"] for m in marks}
        if missing:
            sys.exit(f"Unknown landmark id(s): {', '.join(sorted(missing))}")
        marks = [m for m in marks if m["id"] in want]
    return marks


MISSING_TILES: set[str] = set()          # remember 404s within one run
MIN_HEIGHT = 2.0                         # metres; overridden by --min-height


def _quiet_unlink(p: Path):
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass                                  # network/mounted volumes sometimes refuse


def download_tile(name: str, force=False) -> Path | None:
    DATA.mkdir(parents=True, exist_ok=True)
    hits = list(DATA.glob(f"*{name.replace('LoD2_', 'LoD2_33_')}*.xml"))
    if hits and not force:
        return hits[0]
    if name in MISSING_TILES:
        return None
    url = f"{ATOM_BASE}/{name}.zip"
    # Stage the zip in the system temp dir: some network/mounted volumes refuse
    # the create-then-delete dance.
    zpath = Path(tempfile.gettempdir()) / f"{name}.zip"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "berlin-lod2-iso/1.0"})
        with urllib.request.urlopen(req, timeout=180) as r, open(zpath, "wb") as fh:
            shutil.copyfileobj(r, fh)
    except Exception as exc:                                  # noqa: BLE001
        # The feed only publishes tiles that contain buildings - water, forest
        # and the edges of the city grid legitimately 404.
        print(f"  - {name}: unavailable ({exc})")
        MISSING_TILES.add(name)
        _quiet_unlink(zpath)
        return None
    try:
        with zipfile.ZipFile(zpath) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith((".xml", ".gml"))]
            if not names:
                print(f"  - {name}: zip contained no CityGML")
                return None
            zf.extract(names[0], DATA)
            extracted = DATA / names[0]
    except zipfile.BadZipFile:
        print(f"  - {name}: corrupt download, delete data/tiles and retry")
        _quiet_unlink(zpath)
        return None
    _quiet_unlink(zpath)
    print(f"  + {name} -> {extracted.name} ({extracted.stat().st_size/1e6:.1f} MB)")
    return extracted


# --------------------------------------------------------------------------
# CityGML parsing
# --------------------------------------------------------------------------

def _rings_from_polygon(poly):
    """Return (exterior, [holes]) as lists of (x,y,z)."""
    def coords(container):
        pl = container.find(f".//{Q_POSLIST}")
        if pl is not None and pl.text:
            v = [float(t) for t in pl.text.split()]
            dim = int(pl.get("srsDimension", "3"))
            return [tuple(v[i:i + dim][:3]) for i in range(0, len(v), dim)]
        pts = []
        for p in container.findall(f".//{Q_POS}"):
            if p.text:
                v = [float(t) for t in p.text.split()]
                pts.append(tuple(v[:3]))
        return pts

    ext_el = poly.find(Q_EXTERIOR)
    if ext_el is None:
        return None, []
    ext = coords(ext_el)
    if len(ext) < 3:
        return None, []
    if ext[0] != ext[-1]:
        ext.append(ext[0])
    holes = []
    for h in poly.findall(Q_INTERIOR):
        ring = coords(h)
        if len(ring) >= 3:
            if ring[0] != ring[-1]:
                ring.append(ring[0])
            holes.append(ring)
    return ext, holes


def parse_buildings(xml_path: Path, targets: list[dict]):
    """
    Stream a tile and yield (landmark_id, Building) for anything inside a
    landmark's radius. Streaming keeps memory flat on the bigger tiles.
    """
    ctx = ET.iterparse(str(xml_path), events=("end",))
    for _event, elem in ctx:
        if elem.tag != Q_BUILDING:
            continue

        b = Building(gml_id=elem.get(Q_GMLID, "unknown"))
        h = elem.find(f".//{{{NS['bldg']}}}measuredHeight")
        if h is not None and h.text:
            try:
                b.height = float(h.text)
            except ValueError:
                pass
        rt = elem.find(f".//{{{NS['bldg']}}}roofType")
        if rt is not None and rt.text:
            b.roof_type = rt.text.strip()
        fn = elem.find(f".//{{{NS['bldg']}}}function")
        if fn is not None and fn.text:
            b.function = fn.text.strip()

        # boundedBy surfaces carry the real geometry; lod2Solid just xlinks them.
        for bb in elem.iter(f"{{{NS['bldg']}}}boundedBy"):
            for surf in bb:
                kind = SURFACE_KINDS.get(surf.tag.split("}")[-1])
                if kind is None:
                    continue
                for poly in surf.iter(Q_POLYGON):
                    ext, holes = _rings_from_polygon(poly)
                    if ext:
                        b.faces.append(Face(ring=ext, holes=holes, kind=kind))

        if b.faces:
            # Drop sub-2 m clutter: canopies, kiosks, bin stores. They bulk out
            # the SVG without reading as architecture.
            gh = b.extents()[0]
            if gh < MIN_HEIGHT:
                elem.clear()
                continue
            # assign to every landmark whose disc it touches
            for t in targets:
                r2 = t["radius_m"] ** 2
                te, tn = t["east"], t["north"]
                if any((x - te) ** 2 + (y - tn) ** 2 <= r2 for x, y, _ in b.vertices()):
                    yield t["id"], b

        elem.clear()


# --------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------

def shade(kind: str, normal, light=(-0.45, -0.35, 0.82)):
    """Flat base tone per surface type, nudged by orientation so that
    differently-facing walls read as separate planes."""
    r, g, b = BASE_COLORS.get(kind, (0.85, 0.85, 0.85))
    lam = max(0.0, sum(n * l for n, l in zip(normal, light)))
    f = 0.62 + 0.38 * lam
    to_hex = lambda c: f"{max(0, min(255, round(c * f * 255))):02x}"   # noqa: E731
    return f"#{to_hex(r)}{to_hex(g)}{to_hex(b)}"


def build_svg(buildings, cam, opts, title):
    # 1. cull + project
    drawn = []                    # (depth, bldg_index, svg_path, fill, kind)
    zmin = min((v[2] for b in buildings for v in b.vertices()), default=0.0)

    for bi, b in enumerate(buildings):
        base = min((v[2] for v in b.vertices()), default=zmin) if opts.flatten else zmin
        for f in b.faces:
            if f.kind == "closure" and not opts.keep_closure:
                continue
            if f.kind == "ground" and not opts.keep_ground:
                continue
            n = f.normal()
            if not opts.no_cull and sum(a * c for a, c in zip(n, cam.d)) >= 0:
                continue                                     # faces away, invisible
            def path(ring):
                pts = [cam.project((x, y, z - base)) for x, y, z in ring]
                head = f"M {pts[0][0]:.4f} {pts[0][1]:.4f}"
                rest = " ".join(f"L {p[0]:.4f} {p[1]:.4f}" for p in pts[1:-1])
                return f"{head} {rest} Z"
            d = path(f.ring) + "".join(" " + path(h) for h in f.holes)
            cz = sum(cam.depth((x, y, z - base)) for x, y, z in f.ring[:-1]) / max(1, len(f.ring) - 1)
            drawn.append((cz, bi, d, shade(f.kind, n), f.kind))

    if not drawn:
        return None, 0

    # 2. fit to page
    import re
    nums = [float(v) for _, _, d, _, _ in drawn for v in re.findall(r"-?\d+\.?\d*", d)]
    xs, ys = nums[0::2], nums[1::2]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    w_m, h_m = maxx - minx, maxy - miny
    scale = opts.width_px / w_m if w_m else 1.0
    if opts.height_px and h_m * scale > opts.height_px:
        scale = opts.height_px / h_m
    pad = opts.padding_px
    W, H = w_m * scale + 2 * pad, h_m * scale + 2 * pad

    def xf(d):
        """Re-emit a path in page coordinates."""
        parts = d.split()
        out = []
        i = 0
        while i < len(parts):
            tok = parts[i]
            if tok in ("M", "L"):
                x = (float(parts[i + 1]) - minx) * scale + pad
                y = (float(parts[i + 2]) - miny) * scale + pad
                out.append(f"{tok} {x:.3f} {y:.3f}")
                i += 3
            else:
                out.append(tok)
                i += 1
        return " ".join(out)

    stroke = (f' stroke="{opts.stroke}" stroke-width="{opts.stroke_width}"'
              f' stroke-linejoin="round"' if opts.stroke else "")
    fill_mode = "none" if opts.outline else None

    body = []
    if opts.layers == "type":
        # Illustrator-friendly: three stacked layers. Correct within a building
        # (a roof is never hidden by its own walls from above); across buildings
        # a tall wall can wrongly sit under a short neighbour's roof.
        for kind, label in (("ground", "Ground"), ("wall", "Walls"), ("roof", "Roofs")):
            grp = [t for t in drawn if t[4] == kind]
            if not grp:
                continue
            grp.sort(key=lambda t: -t[0])
            body.append(f'<g id="{label}">')
            for _cz, _bi, d, fill, _k in grp:
                body.append(f'  <path d="{xf(d)}" fill="{fill_mode or fill}"{stroke}/>')
            body.append("</g>")
    else:
        # Geometrically correct painter's algorithm, grouped per building so you
        # still get usable sublayers in Illustrator.
        order = {}
        for cz, bi, *_ in drawn:
            order[bi] = max(order.get(bi, -1e18), cz)
        for bi in sorted(order, key=lambda k: -order[k]):
            grp = sorted([t for t in drawn if t[1] == bi], key=lambda t: -t[0])
            body.append(f'<g id="building_{buildings[bi].gml_id}">')
            for _cz, _b, d, fill, _k in grp:
                body.append(f'  <path d="{xf(d)}" fill="{fill_mode or fill}"{stroke}/>')
            body.append("</g>")

    svg = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:.2f}" height="{H:.2f}" '
        f'viewBox="0 0 {W:.2f} {H:.2f}">\n'
        f'<title>{title}</title>\n'
        f'<desc>Berlin LoD2 (dl-de/zero-2-0). camera az={opts.azimuth} el={opts.elevation} '
        f'scale={scale:.4f} px/m. Extent {w_m:.1f}x{h_m:.1f} m projected.</desc>\n'
        + "\n".join(body) + "\n</svg>\n"
    )
    return svg, len(drawn)


def build_obj(buildings, flatten: bool) -> str:
    """Raw 3D out, in metres, recentred on the scene. Import into Blender /
    C4D / Rhino if you'd rather aim the camera by hand."""
    verts, lines, groups = [], [], {}
    idx: dict[tuple, int] = {}
    allv = [v for b in buildings for v in b.vertices()]
    cx = sum(v[0] for v in allv) / len(allv)
    cy = sum(v[1] for v in allv) / len(allv)
    zmin = min(v[2] for v in allv)
    for b in buildings:
        base = min(v[2] for v in b.vertices()) if flatten else zmin
        groups.setdefault(b.gml_id, [])
        for f in b.faces:
            ids = []
            for x, y, z in f.ring[:-1]:
                key = (round(x, 4), round(y, 4), round(z, 4))
                if key not in idx:
                    verts.append((x - cx, y - cy, z - base))
                    idx[key] = len(verts)
                ids.append(idx[key])
            if len(ids) >= 3:
                groups[b.gml_id].append((f.kind, ids))
    lines.append("# Berlin LoD2 -> OBJ. Z is up, units metres. dl-de/zero-2-0.")
    for x, y, z in verts:
        lines.append(f"v {x:.4f} {y:.4f} {z:.4f}")
    for gid, faces in groups.items():
        lines.append(f"g {gid}")
        for kind, ids in faces:
            lines.append(f"usemtl {kind}")
            lines.append("f " + " ".join(str(i) for i in ids))
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def collect(marks, min_height: float | None = None, source: str | None = None):
    """Returns {landmark_id: [Building]}.

    `source` is per landmark ("source" in landmarks.json), overridable for the
    whole run by --source:

      lod2  official CityGML. Survey-accurate footprints, real roof forms,
            city-wide. The default, and right for nearly everything.
      osm   Simple 3D Buildings. Only worth it where LoD2 generalises a
            landmark away - see the Berliner Dom.
      both  LoD2 plus OSM. Expect overlap; useful for comparing them.
    """
    global MIN_HEIGHT
    if min_height is not None:
        MIN_HEIGHT = min_height

    def src_of(m: dict) -> str:
        return (source or m.get("source") or "lod2").lower()

    found: dict[str, list[Building]] = {m["id"]: [] for m in marks}
    seen: dict[str, set[str]] = {m["id"]: set() for m in marks}

    lod2_marks = [m for m in marks if src_of(m) in ("lod2", "both")]
    tile_map: dict[str, list[dict]] = {}
    for m in lod2_marks:
        for t in tiles_for(m["east"], m["north"], m["radius_m"]):
            tile_map.setdefault(t, []).append(m)
    for tname, ts in sorted(tile_map.items()):
        path = download_tile(tname)
        if path is None:
            continue
        print(f"  . scanning {path.name}")
        for lid, b in parse_buildings(path, ts):
            if b.gml_id not in seen[lid]:
                seen[lid].add(b.gml_id)
                found[lid].append(b)

    for m in marks:
        if src_of(m) not in ("osm", "both"):
            continue
        for b in buildings_from_osm(m):
            if b.gml_id not in seen[m["id"]]:
                seen[m["id"]].add(b.gml_id)
                found[m["id"]].append(b)
    return found


def cmd_tiles(args):
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    need = set()
    for m in marks:
        ts = tiles_for(m["east"], m["north"], m["radius_m"])
        need |= ts
        print(f"{m['id']:26s} E{m['east']:.0f} N{m['north']:.0f}  {' '.join(sorted(ts))}")
    print(f"\n{len(need)} unique tiles, ~{len(need)*3.5:.0f} MB zipped.")


def cmd_fetch(args):
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    need = set()
    for m in marks:
        need |= tiles_for(m["east"], m["north"], m["radius_m"])
    print(f"Fetching {len(need)} tiles into {DATA}")
    for t in sorted(need):
        download_tile(t, force=args.force)
    print("Done.")


def cmd_probe(args):
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    for lid, bl in collect(marks, source=args.source).items():
        print(f"\n=== {lid}: {len(bl)} buildings ===")
        print(f"  {'gml_id':22s} {'height':>7s} {'geom':>7s} {'footprint':>12s} "
              f"{'roof':10s} faces")
        for b in sorted(bl, key=lambda x: -(x.height or 0))[:12]:
            rt = ROOF_TYPES.get(b.roof_type or "", b.roof_type or "?")
            gh, fw, fd = b.extents()
            print(f"  {b.gml_id:22s} {b.height or 0:7.2f} {gh:7.2f} "
                  f"{fw:5.0f} x{fd:5.0f} {rt:10s} {len(b.faces)}")
        if len(bl) > 12:
            print(f"  ... and {len(bl) - 12} more")


def cmd_find(args):
    """Hunt for a building by its geometric signature, offline.

    Useful when you know roughly where something is but not its exact centre,
    or when you want 'every dome within 500 m of here'.
    """
    lat, lon = (float(v) for v in args.near.split(","))
    east, north = WGS84_TO_UTM33.transform(lon, lat)
    probe = [{"id": "search", "name": "search", "east": east, "north": north,
              "radius_m": args.radius}]
    back = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)
    rows = []
    for tname in sorted(tiles_for(east, north, args.radius)):
        path = download_tile(tname)
        if path is None:
            continue
        for _lid, b in parse_buildings(path, probe):
            gh, fw, fd = b.extents()
            if gh < args.min_height or max(fw, fd) < args.min_footprint:
                continue
            if args.roof and ROOF_TYPES.get(b.roof_type or "", "") != args.roof:
                continue
            vs = list(b.vertices())
            cx = sum(v[0] for v in vs) / len(vs)
            cy = sum(v[1] for v in vs) / len(vs)
            blon, blat = back.transform(cx, cy)
            rows.append((gh, b, blat, blon, fw, fd,
                         math.dist((cx, cy), (east, north))))
    rows.sort(key=lambda r: -r[0])
    print(f"\n{len(rows)} matches within {args.radius} m of {lat},{lon}")
    print(f"{'gml_id':22s} {'h':>7s} {'footprint':>12s} {'roof':10s} "
          f"{'dist':>6s}  lat, lon")
    for gh, b, blat, blon, fw, fd, dist in rows[:args.limit]:
        rt = ROOF_TYPES.get(b.roof_type or "", b.roof_type or "?")
        print(f"{b.gml_id:22s} {gh:7.2f} {fw:5.0f} x{fd:5.0f} {rt:10s} "
              f"{dist:6.0f}  {blat:.6f}, {blon:.6f}")


# --------------------------------------------------------------------------
# OSM Simple 3D Buildings - for the handful LoD2 flattens
# --------------------------------------------------------------------------
#
# LoD2 is better than its reputation: probe the Gendarmenmarkt and both Dome
# come through with drums, colonnades and lanterns (500-odd faces each). The
# failure is narrow but total - the Berliner Dom is a flat slab at 43 m against
# a real 98 m.
#
# OSM covers exactly that gap. The Dom is mapped as Simple 3D Buildings:
# `building:part` solids stacked by `min_height`/`height`, with
# `roof:shape=dome` and `roof:height` on the cupolas. Extruding those gives
# clean-edged parametric geometry - which for a line drawing beats a
# photogrammetric mesh, where every edge has to be recovered from noisy
# triangles.
#
# Coverage is the catch, and it is the inverse of LoD2's: superb on the famous
# failures, absent elsewhere. At Gendarmenmarkt OSM has no heights at all, so
# LoD2 stays the better source there. Choose per landmark via "source".

# Roof shapes this understands. Anything else falls back to flat, and the
# build reports what it approximated rather than silently lying.
# Two families, because they are built differently.
#
# LOFT shapes taper toward the footprint's centroid as they rise - a dome, a
# pyramid, a mansard. Built by scaling the ring inward step by step.
#
# RIDGE shapes are height fields over the footprint: every point's height is a
# function of how far it sits from a ridge line. A gable, a barrel vault, a
# lean-to. Built by lifting a triangulated cap and dropping a skirt to the
# eaves, which is what puts vertical ends on a gable rather than hipping it.
_ROOF_LOFT = {"dome", "onion", "pyramidal", "hipped", "cone", "conical", "mansard"}
_ROOF_RIDGE = {"gabled", "gambrel", "round", "barrel", "skillion", "shed", "lean_to"}
_ROOF_SOLID = _ROOF_LOFT | _ROOF_RIDGE
_DOME_SEGMENTS = 8          # profile steps; 8 reads smooth at plate scale


def _osm_bbox(east: float, north: float, radius: float) -> str:
    """UTM disc -> WGS84 bbox string, in the order the OSM API wants."""
    back = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)
    w, s = back.transform(east - radius, north - radius)
    e, n = back.transform(east + radius, north + radius)
    return f"{w:.6f},{s:.6f},{e:.6f},{n:.6f}"


def download_osm(bbox: str) -> Path | None:
    OSM_DATA.mkdir(parents=True, exist_ok=True)
    cache = OSM_DATA / f"{bbox.replace(',', '_')}.osm"
    if cache.exists():
        return cache
    url = f"{OSM_API}?bbox={bbox}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "berlin-lod2-iso/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r, open(cache, "wb") as fh:
            shutil.copyfileobj(r, fh)
    except Exception as exc:                                   # noqa: BLE001
        print(f"  - OSM {bbox}: unavailable ({exc})")
        _quiet_unlink(cache)
        return None
    print(f"  + OSM {bbox} -> {cache.name} ({cache.stat().st_size/1e6:.1f} MB)")
    return cache


def _osm_metres(raw: str | None) -> float | None:
    """Parse an OSM height. Values are metres, sometimes with a unit suffix."""
    if not raw:
        return None
    try:
        return float(raw.strip().split()[0].replace(",", "."))
    except (ValueError, IndexError):
        return None


def _dome_faces(ring, z0: float, z1: float, shape: str) -> list[Face]:
    """Loft a footprint ring into a roof solid between two heights.

    Each step scales the ring toward its centroid and lifts it, so the profile
    is a quarter-circle for a dome and a straight taper for a pyramid. Works on
    any footprint, not just circles - which matters, because the Dom's cupolas
    are mapped as many-sided polygons rather than true circles.
    """
    pts = ring[:-1]
    if len(pts) < 3:
        return []
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    curved = shape in ("dome", "onion", "cone", "conical")
    mansard = shape == "mansard"
    steps = _DOME_SEGMENTS if curved else (2 if mansard else 1)

    def level(i: int):
        t = i / steps
        if curved:
            a = t * math.pi / 2
            return math.cos(a), z0 + (z1 - z0) * math.sin(a)
        if mansard:
            # Steep lower slope, shallow upper deck — the mansard silhouette.
            # Most of the height is gained in the first, inset segment.
            return (1.0, z0) if t == 0 else ((0.72, z0 + (z1 - z0) * 0.72) if t < 1 else (0.0, z1))
        return 1.0 - t, z0 + (z1 - z0) * t

    faces: list[Face] = []
    for i in range(steps):
        s0, za = level(i)
        s1, zb = level(i + 1)
        for j in range(len(pts)):
            x0, y0 = pts[j]
            x1, y1 = pts[(j + 1) % len(pts)]
            a = (cx + (x0 - cx) * s0, cy + (y0 - cy) * s0, za)
            b = (cx + (x1 - cx) * s0, cy + (y1 - cy) * s0, za)
            c = (cx + (x1 - cx) * s1, cy + (y1 - cy) * s1, zb)
            d = (cx + (x0 - cx) * s1, cy + (y0 - cy) * s1, zb)
            ring4 = [a, b, c, d, a] if s1 > 1e-9 else [a, b, c, a]
            faces.append(Face(ring=ring4, kind="roof"))
    return faces


def _min_area_axis(pts) -> float:
    """Angle of the long side of the footprint's minimum-area bounding box.

    OSM's `roof:orientation` is defined relative to the building's longest
    side, so a gable needs that axis before it can be oriented. Testing each
    edge direction is the standard construction and is exact for a rectangle,
    which most of these footprints are.
    """
    best = (float("inf"), 0.0)
    n = len(pts)
    for i in range(n):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % n]
        ang = math.atan2(y1 - y0, x1 - x0)
        ca, sa = math.cos(-ang), math.sin(-ang)
        us = [p[0] * ca - p[1] * sa for p in pts]
        vs = [p[0] * sa + p[1] * ca for p in pts]
        du, dv = max(us) - min(us), max(vs) - min(vs)
        area = du * dv
        if area < best[0]:
            best = (area, ang if du >= dv else ang + math.pi / 2)
    return best[1]


def _ridge_roof_faces(ring, z0: float, z1: float, shape: str, tags: dict) -> list[Face]:
    """Build a roof whose height is a function of distance from a ridge line.

    Covers gabled, gambrel, barrel ('round') and skillion. The cap is the
    footprint triangulated and lifted; a skirt then drops each boundary edge
    back to the eaves, which is what gives a gable its vertical ends instead
    of quietly turning it into a hip.
    """
    pts = ring[:-1]
    if len(pts) < 3:
        return []

    # Ridge direction: an explicit bearing wins, then orientation relative to
    # the long axis, then the long axis itself.
    axis = _min_area_axis(pts)
    raw_dir = _osm_metres(tags.get("roof:direction"))
    if raw_dir is not None:
        # Compass bearing -> vector in (east, north).
        rad = math.radians(raw_dir)
        ridge = (math.sin(rad), math.cos(rad))
    else:
        a = axis
        if (tags.get("roof:orientation") or "along").lower() == "across":
            a += math.pi / 2
        ridge = (math.cos(a), math.sin(a))

    lean = shape in ("skillion", "shed", "lean_to")
    # For a lean-to the height ramps along the slope; otherwise it falls away
    # on both sides of the ridge, so measure perpendicular to it.
    axis_v = ridge if lean else (-ridge[1], ridge[0])
    proj = [p[0] * axis_v[0] + p[1] * axis_v[1] for p in pts]
    lo, hi = min(proj), max(proj)
    span = (hi - lo) or 1.0
    mid = (lo + hi) / 2
    half = span / 2

    def height(t_signed: float) -> float:
        if lean:
            return z0 + (z1 - z0) * ((t_signed - lo) / span)
        t = min(1.0, abs(t_signed - mid) / half)          # 0 at ridge, 1 at eaves
        if shape in ("round", "barrel"):
            return z0 + (z1 - z0) * math.sqrt(max(0.0, 1.0 - t * t))
        if shape == "gambrel":
            # Two pitches: shallow near the ridge, steep near the eaves.
            drop = 0.35 * (t / 0.5) if t < 0.5 else 0.35 + 0.65 * ((t - 0.5) / 0.5)
            return z1 - (z1 - z0) * drop
        return z1 - (z1 - z0) * t                          # gabled

    lifted = [(p[0], p[1], height(pr)) for p, pr in zip(pts, proj)]
    faces: list[Face] = []

    cap, tris = _tri_fan_or_ear(lifted + [lifted[0]])
    for a, b, c in tris:
        faces.append(Face(ring=[cap[a], cap[b], cap[c], cap[a]], kind="roof"))

    # Skirt: fills the vertical gap under the cap at the gable ends.
    for j in range(len(lifted)):
        x0, y0, h0 = lifted[j]
        x1, y1, h1 = lifted[(j + 1) % len(lifted)]
        if h0 - z0 < 1e-6 and h1 - z0 < 1e-6:
            continue
        faces.append(Face(
            ring=[(x0, y0, z0), (x1, y1, z0), (x1, y1, h1), (x0, y0, h0), (x0, y0, z0)],
            kind="roof",
        ))
    return faces


def _osm_part_to_building(gml_id: str, ring, tags: dict, base_z: float) -> Building | None:
    """One `building:part` (or plain `building`) -> a closed solid."""
    height = _osm_metres(tags.get("height"))
    if height is None:
        levels = _osm_metres(tags.get("building:levels"))
        height = levels * 3.0 if levels else None
    if height is None:
        return None
    min_h = _osm_metres(tags.get("min_height")) or 0.0
    shape = (tags.get("roof:shape") or "flat").lower()
    roof_h = _osm_metres(tags.get("roof:height")) or 0.0
    if shape not in _ROOF_SOLID:
        roof_h = 0.0                       # flat, or a shape we do not model

    z_base = base_z + min_h
    z_eaves = base_z + height - roof_h
    z_top = base_z + height
    if z_eaves < z_base:                   # roof:height taller than the part
        z_eaves = z_base

    b = Building(gml_id=gml_id)
    b.height = height
    b.roof_type = shape
    b.function = "osm"
    b.min_height = min_h
    b.roof_height = roof_h

    flat = [(x, y) for x, y, *_ in ring]
    if flat[0] != flat[-1]:
        flat.append(flat[0])

    # Base cap, so the solid is closed for OBJ export.
    b.faces.append(Face(ring=[(x, y, z_base) for x, y in flat], kind="ground"))

    # Walls.
    if z_eaves > z_base + 1e-6:
        for i in range(len(flat) - 1):
            x0, y0 = flat[i]
            x1, y1 = flat[i + 1]
            b.faces.append(
                Face(
                    ring=[
                        (x0, y0, z_base), (x1, y1, z_base),
                        (x1, y1, z_eaves), (x0, y0, z_eaves), (x0, y0, z_base),
                    ],
                    kind="wall",
                )
            )

    # Roof.
    if roof_h > 1e-6 and shape in _ROOF_RIDGE:
        b.faces.extend(_ridge_roof_faces(flat, z_eaves, z_top, shape, tags))
    elif roof_h > 1e-6 and shape in _ROOF_LOFT:
        b.faces.extend(_dome_faces([(x, y) for x, y in flat], z_eaves, z_top, shape))
    else:
        b.faces.append(Face(ring=[(x, y, z_eaves) for x, y in flat], kind="roof"))
    return b


def buildings_from_osm(mark: dict) -> list[Building]:
    """Every OSM building solid whose footprint touches the landmark's disc."""
    bbox = _osm_bbox(mark["east"], mark["north"], mark["radius_m"])
    path = download_osm(bbox)
    if path is None:
        return []

    root = ET.parse(str(path)).getroot()
    nodes = {}
    for n in root.findall("node"):
        e, nn = WGS84_TO_UTM33.transform(float(n.get("lon")), float(n.get("lat")))
        nodes[n.get("id")] = (e, nn)

    def tags_of(el) -> dict:
        return {t.get("k"): t.get("v") for t in el.findall("tag")}

    ways = {}
    for w in root.findall("way"):
        ring = [nodes[nd.get("ref")] for nd in w.findall("nd") if nd.get("ref") in nodes]
        if len(ring) >= 3:
            ways[w.get("id")] = (ring, tags_of(w))

    base_z = float(mark.get("base_z_m", 0.0))
    r2 = mark["radius_m"] ** 2
    te, tn = mark["east"], mark["north"]

    def in_disc(ring) -> bool:
        return any((x - te) ** 2 + (y - tn) ** 2 <= r2 for x, y in ring)

    parts, outlines, skipped = [], [], []
    for wid, (ring, tags) in ways.items():
        if not in_disc(ring):
            continue
        if "building:part" in tags:
            parts.append((wid, ring, tags))
        elif "building" in tags:
            outlines.append((wid, ring, tags))

    # Simple 3D Buildings: where parts exist, they define the massing and the
    # parent outline is only a footprint. Drop any outline that contains one.
    def centroid(ring):
        return (sum(p[0] for p in ring) / len(ring), sum(p[1] for p in ring) / len(ring))

    def bounds(ring):
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return min(xs), min(ys), max(xs), max(ys)

    part_centroids = [centroid(r) for _, r, _ in parts]
    kept_outlines = []
    for wid, ring, tags in outlines:
        x0, y0, x1, y1 = bounds(ring)
        if any(x0 <= px <= x1 and y0 <= py <= y1 for px, py in part_centroids):
            continue
        kept_outlines.append((wid, ring, tags))

    out: list[Building] = []
    for wid, ring, tags in parts + kept_outlines:
        shape = (tags.get("roof:shape") or "flat").lower()
        if shape != "flat" and shape not in _ROOF_SOLID:
            skipped.append(shape)
        b = _osm_part_to_building(f"OSM{wid}", ring, tags, base_z)
        if b:
            out.append(b)

    if skipped:
        forms = ", ".join(sorted(set(skipped)))
        print(f"    ! {mark['id']}: roof form(s) not modelled, drawn flat: {forms}")
    return out


# --------------------------------------------------------------------------
# GLB - real surfaces for the web map
# --------------------------------------------------------------------------
#
# `geojson` ships a footprint and one height, so a client can only extrude a
# prism. That throws away everything that makes a building recognisable: LoD2
# holds 31,114 surfaces for the trip area, 5,597 of them roofs, and the prism
# approximation discards 38% of the geometry and 100% of the roof shape.
#
# This writes the surfaces themselves.
#
# Coordinates are local ENU metres (east / north / up) about a stated
# lon/lat origin, NOT UTM. UTM grid north is up to a degree off true north in
# Berlin, and a web Mercator map is aligned to true north - baking UTM would
# skew the whole scene by ~50 m at the edges. The client projects the origin,
# works out pixels-per-metre, and everything else follows.
#
# COLOR_0 carries shading only, as grey. The actual hue comes from the
# material's baseColorFactor, which glTF multiplies in - so the client can
# retint roofs, walls and monuments per theme without touching the mesh.

GLB_KINDS = ("roof", "wall", "monument")
MONUMENT_MAX_M2 = 10.0      # below this a footprint is a monument object


def _bridge_holes(ring, holes):
    """Splice interior rings into the exterior so one ear clip covers both.

    Each hole is joined to the outer boundary by a zero-width bridge cut at
    the closest pair of vertices, walked around, and closed back. The result
    is a single degenerate-but-simple polygon that ear clipping handles
    without knowing holes existed. Standard construction, and it is why light
    wells are no longer filled in.
    """
    outer = list(ring[:-1])
    for hole in holes:
        hpts = list(hole[:-1])
        if len(hpts) < 3:
            continue
        # Closest vertex pair between the current boundary and this hole.
        best = None
        for i, a in enumerate(outer):
            for j, b in enumerate(hpts):
                d = (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
                if best is None or d < best[0]:
                    best = (d, i, j)
        if best is None:
            continue
        _d, i, j = best
        # Walk the hole in reverse so its winding opposes the exterior.
        loop = hpts[j::-1] + hpts[:j:-1]
        outer = outer[: i + 1] + loop + [loop[0]] + outer[i:]
    return outer + [outer[0]]


def _tri_fan_or_ear(ring, holes=None):
    """Triangulate one planar polygon.

    Returns `(ring, triangles)` — the ring as well, because bridging holes
    rewrites it, so the index triples refer to the returned ring and not the
    one passed in.

    Fan triangulation is only valid for convex polygons, and 4,538 of the
    trip area's faces have more than four sides, so anything past a triangle
    goes through ear clipping instead.
    """
    if holes:
        ring = _bridge_holes(ring, holes)
    n = len(ring) - 1                      # ring is closed
    if n < 3:
        return ring, []
    if n == 3:
        return ring, [(0, 1, 2)]

    # Project onto the dominant plane so the clip runs in 2D.
    ax = ay = az = 0.0
    for i in range(n):
        x1, y1, z1 = ring[i]
        x2, y2, z2 = ring[(i + 1) % n]
        ax += (y1 - y2) * (z1 + z2)
        ay += (z1 - z2) * (x1 + x2)
        az += (x1 - x2) * (y1 + y2)
    nx, ny, nz = abs(ax), abs(ay), abs(az)
    if nx >= ny and nx >= nz:
        flat = [(p[1], p[2]) for p in ring[:n]]
    elif ny >= nz:
        flat = [(p[2], p[0]) for p in ring[:n]]
    else:
        flat = [(p[0], p[1]) for p in ring[:n]]

    def area2(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    total = sum(area2(flat[0], flat[i], flat[i + 1]) for i in range(1, n - 1))
    ccw = total > 0
    idx = list(range(n))
    out = []
    guard = 0
    while len(idx) > 3 and guard < 4 * n:
        guard += 1
        clipped = False
        for k in range(len(idx)):
            i0 = idx[k - 1]
            i1 = idx[k]
            i2 = idx[(k + 1) % len(idx)]
            a, b, c = flat[i0], flat[i1], flat[i2]
            cross = area2(a, b, c)
            if (cross <= 0) if ccw else (cross >= 0):
                continue                    # reflex vertex, not an ear
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = flat[j]
                d1 = area2(a, b, p)
                d2 = area2(b, c, p)
                d3 = area2(c, a, p)
                if (d1 >= 0) == (d2 >= 0) == (d3 >= 0):
                    inside = True
                    break
            if inside:
                continue
            out.append((i0, i1, i2))
            idx.pop(k)
            clipped = True
            break
        if not clipped:
            break                           # degenerate; fall through to a fan
    if len(idx) == 3:
        out.append((idx[0], idx[1], idx[2]))
    elif len(idx) > 3:
        out.extend((idx[0], idx[i], idx[i + 1]) for i in range(1, len(idx) - 1))
    return ring, out


def _glb_bytes(prims: dict, origin: tuple[float, float], datum: float, note: str) -> bytes:
    """Assemble a binary glTF by hand. No dependency, and the file is simple:
    one mesh, one primitive per surface kind, unlit materials."""
    import base64  # noqa: F401  (kept out of module scope; only used here)

    buf = bytearray()
    views, accessors, materials, primitives = [], [], [], []

    def pad4(b: bytearray):
        while len(b) % 4:
            b.append(0)

    for kind in GLB_KINDS:
        data = prims.get(kind)
        if not data or not data["idx"]:
            continue
        pos, col, idx = data["pos"], data["col"], data["idx"]

        pad4(buf)
        pos_off = len(buf)
        buf.extend(struct.pack(f"<{len(pos)}f", *pos))
        views.append({"buffer": 0, "byteOffset": pos_off, "byteLength": len(pos) * 4,
                      "target": 34962})
        pad4(buf)
        col_off = len(buf)
        buf.extend(bytes(col))
        views.append({"buffer": 0, "byteOffset": col_off, "byteLength": len(col),
                      "target": 34962})
        pad4(buf)
        idx_off = len(buf)
        buf.extend(struct.pack(f"<{len(idx)}I", *idx))
        views.append({"buffer": 0, "byteOffset": idx_off, "byteLength": len(idx) * 4,
                      "target": 34963})

        xs = pos[0::3]
        ys = pos[1::3]
        zs = pos[2::3]
        vpos, vcol, vidx = len(views) - 3, len(views) - 2, len(views) - 1
        accessors.append({"bufferView": vpos, "componentType": 5126, "count": len(pos) // 3,
                          "type": "VEC3",
                          "min": [min(xs), min(ys), min(zs)],
                          "max": [max(xs), max(ys), max(zs)]})
        accessors.append({"bufferView": vcol, "componentType": 5121, "normalized": True,
                          "count": len(col) // 4, "type": "VEC4"})
        accessors.append({"bufferView": vidx, "componentType": 5125,
                          "count": len(idx), "type": "SCALAR"})
        materials.append({
            "name": kind,
            # White base: the client sets baseColorFactor from its CSS tokens,
            # and COLOR_0's grey multiplies into it.
            "pbrMetallicRoughness": {"baseColorFactor": [1, 1, 1, 1],
                                     "metallicFactor": 0, "roughnessFactor": 1},
            "extensions": {"KHR_materials_unlit": {}},
        })
        primitives.append({
            "attributes": {"POSITION": len(accessors) - 3, "COLOR_0": len(accessors) - 2},
            "indices": len(accessors) - 1,
            "material": len(materials) - 1,
            "mode": 4,
        })

    gltf = {
        "asset": {"version": "2.0", "generator": "lod2.py"},
        "extensionsUsed": ["KHR_materials_unlit"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": "berlin"}],
        "meshes": [{"name": "berlin", "primitives": primitives}],
        "materials": materials,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(buf)}],
        "extras": {
            # Everything the client needs to place this on a map.
            "originLon": origin[0], "originLat": origin[1],
            "datum_m": datum, "units": "metres, ENU about origin, Z up",
            "kinds": [m["name"] for m in materials],
            "note": note,
        },
    }

    js = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(js) % 4:
        js += b" "
    pad4(buf)
    total = 12 + 8 + len(js) + 8 + len(buf)
    out = bytearray()
    out.extend(b"glTF")
    out.extend(struct.pack("<II", 2, total))
    out.extend(struct.pack("<I", len(js)))
    out.extend(b"JSON")
    out.extend(js)
    out.extend(struct.pack("<I", len(buf)))
    out.extend(b"BIN\x00")
    out.extend(buf)
    return bytes(out)


def cmd_glb(args):
    """Bake real LoD2 surfaces (plus OSM hero solids) into one GLB."""
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    if args.radius is not None:
        for m in marks:
            m["radius_m"] = args.radius

    def eff_source(m):
        return (args.source or m.get("source") or "lod2").lower()

    hero_marks = [m for m in marks if eff_source(m) == "osm"]
    # Collect LoD2 inside the hero discs too, even though OSM replaces it
    # there. The displaced buildings are discarded, but first they give the
    # exact ground level to sit the OSM parts on - and they are the only
    # source for it. Sampling from further out is worth several metres of
    # error: the Berliner Dom's own ground is 32.3 m, the nearest collected
    # neighbours say 36.6 m.
    for m in hero_marks:
        m["source"] = "both"
    found = collect(marks, args.min_height, args.source)
    for m in hero_marks:
        m["source"] = "osm"
    hero_discs = [(m["east"], m["north"], m["radius_m"]) for m in hero_marks]

    def centroid_xy(b):
        vs = list(b.vertices())
        return (sum(v[0] for v in vs) / len(vs), sum(v[1] for v in vs) / len(vs))

    # Collect LoD2 first, dropping anything a hero replaces.
    lod2_b, osm_b, seen = [], [], set()
    for m in marks:
        for b in found[m["id"]]:
            if b.gml_id in seen:
                continue
            seen.add(b.gml_id)
            (osm_b if b.function == "osm" else lod2_b).append((m["id"], b))

    kept_lod2 = []
    for lid, b in lod2_b:
        cx, cy = centroid_xy(b)
        if any((cx - e) ** 2 + (cy - n) ** 2 <= r * r for e, n, r in hero_discs):
            continue
        kept_lod2.append(b)

    # OSM heights are metres above ground; LoD2 is absolute DHHN2016. Rather
    # than hand-setting base_z_m per hero, take the ground level from the LoD2
    # buildings that the hero displaced - they are, by definition, right there.
    lod2_ground = [(centroid_xy(b), min(v[2] for v in b.vertices())) for _l, b in lod2_b]
    all_ground = sorted(z for _c, z in lod2_ground)

    def ground_near(e: float, n: float, r: float) -> float | None:
        """Median LoD2 ground level near a point, widening until something bites.

        A hero usually displaces LoD2 buildings that are right there, and those
        give the datum directly. The Berliner Dom is the exception: no trip stop
        reaches it, so no LoD2 was ever collected inside its disc. Without the
        widening step it would keep a zero datum and sit ~34 m below the city.
        """
        for mult in (1, 3, 8, 25):
            rr = (r * mult) ** 2
            zs = sorted(z for (cx, cy), z in lod2_ground
                        if (cx - e) ** 2 + (cy - n) ** 2 <= rr)
            if zs:
                return zs[len(zs) // 2]
        return all_ground[len(all_ground) // 2] if all_ground else None

    ground_by_hero: dict[str, float] = {}
    for m in hero_marks:
        g = ground_near(m["east"], m["north"], m["radius_m"])
        if g is not None:
            ground_by_hero[m["id"]] = g
    for lid, b in osm_b:
        dz = ground_by_hero.get(lid)
        if dz:
            for f in b.faces:
                f.ring = [(x, y, z + dz) for x, y, z in f.ring]

    buildings = kept_lod2 + [b for _l, b in osm_b]
    if not buildings:
        sys.exit("No buildings collected.")

    # ── project to lon/lat, then to local ENU metres ────────────
    back = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)
    all_xy = [(x, y) for b in buildings for f in b.faces
              if f.kind in ("roof", "wall") for x, y, _z in f.ring]
    lons, lats = back.transform([p[0] for p in all_xy], [p[1] for p in all_xy])
    lut = {}
    for (x, y), lo, la in zip(all_xy, lons, lats):
        lut[(x, y)] = (lo, la)

    lon0 = sum(lons) / len(lons)
    lat0 = sum(lats) / len(lats)
    mlon = 111320.0 * math.cos(math.radians(lat0))
    mlat = 110540.0
    datum = min(z for b in buildings for f in b.faces
                if f.kind in ("roof", "wall") for _x, _y, z in f.ring)

    def foot_area(b):
        a = 0.0
        for f in b.faces:
            if f.kind != "ground":
                continue
            r = f.ring
            a += abs(sum(r[i][0] * r[i + 1][1] - r[i + 1][0] * r[i][1]
                         for i in range(len(r) - 1))) / 2
        return a

    prims = {k: {"pos": [], "col": bytearray(), "idx": [], "map": {}} for k in GLB_KINDS}
    holes_bridged = 0

    def emit(kind, xyz, shade):
        p = prims[kind]
        key = (round(xyz[0], 3), round(xyz[1], 3), round(xyz[2], 3), shade)
        i = p["map"].get(key)
        if i is None:
            i = len(p["pos"]) // 3
            p["map"][key] = i
            p["pos"].extend(xyz)
            p["col"].extend((shade, shade, shade, 255))
        return i

    for b in buildings:
        monument = b.function != "osm" and foot_area(b) < MONUMENT_MAX_M2
        for f in b.faces:
            if f.kind not in ("roof", "wall"):
                continue
            nrm = f.normal()
            # Same north-west key light the SVG writer uses, so the two agree.
            lam = max(0.0, nrm[0] * -0.45 + nrm[1] * -0.35 + nrm[2] * 0.82)
            shade = max(0, min(255, round((0.60 + 0.40 * lam) * 255)))
            kind = "monument" if monument else f.kind

            def to_enu(r):
                out = []
                for x, y, z in r:
                    lo, la = lut[(x, y)]
                    out.append(((lo - lon0) * mlon, (la - lat0) * mlat, z - datum))
                return out

            enu = to_enu(f.ring)
            enu_holes = [to_enu(h) for h in f.holes] if f.holes else None
            if enu_holes:
                holes_bridged += 1
            poly, tris = _tri_fan_or_ear(enu, enu_holes)
            for a, c, d in tris:
                prims[kind]["idx"].extend(
                    (emit(kind, poly[a], shade), emit(kind, poly[c], shade), emit(kind, poly[d], shade))
                )

    note = (f"Berlin LoD2 (dl-de/zero-2-0) + OSM (ODbL) for {len(hero_marks)} hero landmarks. "
            f"{len(buildings)} buildings.")
    blob = _glb_bytes(prims, (round(lon0, 7), round(lat0, 7)), round(datum, 2), note)
    out = Path(args.out) if args.out else OUT / "berlin.glb"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(blob)

    tris = sum(len(p["idx"]) // 3 for p in prims.values())
    verts = sum(len(p["pos"]) // 3 for p in prims.values())
    print(f"\n{len(buildings)} buildings -> {out} ({out.stat().st_size/1e6:.2f} MB)")
    for k in GLB_KINDS:
        if prims[k]["idx"]:
            print(f"  {k:9s} {len(prims[k]['idx'])//3:7,d} tris  {len(prims[k]['pos'])//3:7,d} verts")
    print(f"  {'total':9s} {tris:7,d} tris  {verts:7,d} verts")
    print(f"  origin {lon0:.6f},{lat0:.6f}  datum {datum:.2f} m")
    if holes_bridged:
        print(f"  {holes_bridged} face(s) with interior rings, bridged and clipped")
    if ground_by_hero:
        pairs = ", ".join(f"{k.replace('hero-','')} {v:.1f}m" for k, v in ground_by_hero.items())
        print(f"  hero ground levels taken from displaced LoD2: {pairs}")


def cmd_geojson(args):
    """Emit map-anchored building footprints as GeoJSON (WGS84).

    The SVG and OBJ writers both throw the geo-reference away - SVG is already
    projected, OBJ is recentred local metres - so neither can be laid over a
    web map. This keeps lon/lat and hands the extrusion to the renderer:
    a footprint plus `height` is all a client needs to build a 3D massing.

    One feature per building. Ground surfaces become a MultiPolygon (buildings
    with courtyards or consolidated BuildingParts have several), so a renderer
    can treat one feature as one mesh.
    """
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    if args.radius is not None:
        for m in marks:
            m["radius_m"] = args.radius
    found = collect(marks, args.min_height, args.source)
    back = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True)

    def ring_to_lonlat(ring):
        # GeoJSON wants [lon, lat]; 6 dp is ~0.1 m, well past LoD2's accuracy.
        return [[round(v, 6) for v in back.transform(x, y)] for x, y, _z in ring]

    def ring_area(ring):
        """Shoelace on the UTM ring - already metres, so this is exact."""
        a = 0.0
        for i in range(len(ring) - 1):
            x1, y1, _ = ring[i]
            x2, y2, _ = ring[i + 1]
            a += x1 * y2 - x2 * y1
        return abs(a) / 2.0

    # Hero discs: landmarks pulled from OSM. Inside one, OSM *replaces* LoD2
    # rather than sitting on top of it - otherwise the Reichstag's flattened
    # LoD2 box would be drawn straight through the OSM dome.
    def eff_source(m: dict) -> str:
        return (args.source or m.get("source") or "lod2").lower()

    hero_discs = [
        (m["east"], m["north"], m["radius_m"]) for m in marks if eff_source(m) == "osm"
    ]

    def inside_hero(b: Building) -> bool:
        vs = list(b.vertices())
        cx = sum(v[0] for v in vs) / len(vs)
        cy = sum(v[1] for v in vs) / len(vs)
        return any((cx - e) ** 2 + (cy - n) ** 2 <= r * r for e, n, r in hero_discs)

    features, seen = [], set()
    dropped = 0
    for m in marks:
        for b in found[m["id"]]:
            if b.gml_id in seen:
                continue                       # a building can sit near two stops
            if b.function != "osm" and hero_discs and inside_hero(b):
                dropped += 1
                continue
            seen.add(b.gml_id)

            grounds = [f for f in b.faces if f.kind == "ground"]
            if not grounds:
                continue                       # nothing to extrude from
            polys = []
            for f in grounds:
                rings = [ring_to_lonlat(f.ring)]
                rings.extend(ring_to_lonlat(h) for h in f.holes)
                polys.append(rings)

            zs = [v[2] for v in b.vertices()]
            base = min(zs)
            gh = max(zs) - base
            # measuredHeight is the surveyed figure; geometry can disagree when
            # the model has been generalised. Prefer the survey, keep both.
            height = b.height if b.height else gh

            props = {
                "landmark": m["id"],
                "height": round(height, 2),
                "geom_height": round(gh, 2),
                "base_m": round(base, 2),          # DHHN2016 NH, for terrain
                # Lets a client separate real buildings from monument
                # objects: the Holocaust Memorial's stelae are ~2.4 m2
                # each, against a 196 m2 median for everything else.
                "footprint_m2": round(sum(ring_area(f.ring) for f in grounds), 1),
                # LoD2 stores an AdV numeric code; OSM stores the word already.
                "roof": (b.roof_type or "") if b.function == "osm"
                        else ROOF_TYPES.get(b.roof_type or "", ""),
            }
            if b.function == "osm":
                # Enough for a client to rebuild the solid: where the part
                # starts, and how much of its height is roof rather than wall.
                props["src"] = "osm"
                props["min_h_m"] = round(b.min_height or 0.0, 2)
                props["roof_h_m"] = round(b.roof_height or 0.0, 2)
                props["roof_shape"] = b.roof_type or "flat"

            features.append({
                "type": "Feature",
                "id": b.gml_id,
                "geometry": {"type": "MultiPolygon", "coordinates": polys},
                "properties": props,
            })

    fc = {
        "type": "FeatureCollection",
        "crs_note": "WGS84 lon/lat. height/base_m in metres; base_m is absolute (DHHN2016 NH).",
        "license": "Berlin LoD2, Datenlizenz Deutschland Zero 2.0",
        "features": features,
    }
    out = Path(args.out) if args.out else OUT / "buildings.geojson"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Minified: this ships as a static asset and gets fetched, not read.
    out.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    tall = sum(1 for f in features if f["properties"]["height"] >= 20)
    osm_n = sum(1 for f in features if f["properties"].get("src") == "osm")
    print(f"\n{len(features)} buildings -> {out} ({out.stat().st_size/1e6:.2f} MB)")
    if hero_discs:
        print(f"  {osm_n} from OSM across {len(hero_discs)} hero disc(s); "
              f"{dropped} LoD2 buildings replaced")
    print(f"  {tall} at 20 m or taller; "
          f"{sum(1 for f in features if not f['properties']['roof'])} with no roof code")


def cmd_build(args):
    marks = load_landmarks(ROOT / args.landmarks, args.only)
    if args.preset:
        args.azimuth, args.elevation = CAMERA_PRESETS[args.preset]
    cam = Camera(args.azimuth, args.elevation)
    OUT.mkdir(parents=True, exist_ok=True)
    found = collect(marks, args.min_height, args.source)

    manifest = []
    for m in marks:
        bl = found[m["id"]]
        if not bl:
            print(f"! {m['id']}: no buildings found - check coords / raise radius_m")
            continue
        svg, nfaces = build_svg(bl, cam, args, m["name"])
        if svg is None:
            print(f"! {m['id']}: everything culled")
            continue
        sp = OUT / f"{m['id']}.svg"
        sp.write_text(svg, encoding="utf-8")
        note = f"  {m['id']:26s} {len(bl):4d} buildings  {nfaces:6d} faces  -> {sp.name}"
        if args.obj:
            op = OUT / f"{m['id']}.obj"
            op.write_text(build_obj(bl, args.flatten), encoding="utf-8")
            note += f" + {op.name}"
        print(note)
        for b in bl:
            gh, fw, fd = b.extents()
            manifest.append({
                "landmark": m["id"], "gml_id": b.gml_id,
                "measured_height_m": f"{b.height:.2f}" if b.height else "",
                "geometric_height_m": f"{gh:.2f}",
                "footprint_w_m": f"{fw:.1f}", "footprint_d_m": f"{fd:.1f}",
                "roof_code": b.roof_type or "",
                "roof_form": ROOF_TYPES.get(b.roof_type or "", ""),
                "function_code": b.function or "", "faces": len(b.faces),
            })

    if manifest:
        mp = OUT / "buildings.csv"
        with open(mp, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
            w.writeheader()
            w.writerows(manifest)
        print(f"\nManifest: {mp}  ({len(manifest)} buildings)")


# --------------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--landmarks", default="landmarks.json")
    p.add_argument("--only", help="comma-separated landmark ids")
    p.add_argument("--source", choices=("lod2", "osm", "both"),
                   help="override every landmark's geometry source")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tiles").set_defaults(func=cmd_tiles)
    f = sub.add_parser("fetch"); f.add_argument("--force", action="store_true")
    f.set_defaults(func=cmd_fetch)
    sub.add_parser("probe").set_defaults(func=cmd_probe)

    fd = sub.add_parser("find", help="locate buildings by geometric signature")
    fd.add_argument("--near", required=True, metavar="LAT,LON")
    fd.add_argument("--radius", type=float, default=400.0)
    fd.add_argument("--min-height", type=float, default=15.0)
    fd.add_argument("--min-footprint", type=float, default=0.0,
                    help="longest footprint side, metres")
    fd.add_argument("--roof", help=f"filter by form: {', '.join(sorted(set(ROOF_TYPES.values())))}")
    fd.add_argument("--limit", type=int, default=25)
    fd.set_defaults(func=cmd_find)

    gb = sub.add_parser("glb", help="real LoD2 surfaces as a binary glTF, for web maps")
    gb.add_argument("--out", help="output path (default out/berlin.glb)")
    gb.add_argument("--radius", type=float, help="override every landmark's radius_m")
    gb.add_argument("--min-height", type=float, default=2.0,
                    help="skip buildings shorter than this (m); 0 keeps everything")
    gb.set_defaults(func=cmd_glb)

    gj = sub.add_parser("geojson", help="footprints + heights as GeoJSON, for web maps")
    gj.add_argument("--out", help="output path (default out/buildings.geojson)")
    gj.add_argument("--radius", type=float,
                    help="override every landmark's radius_m")
    gj.add_argument("--min-height", type=float, default=2.0,
                    help="skip buildings shorter than this (m); 0 keeps everything")
    gj.set_defaults(func=cmd_geojson)

    b = sub.add_parser("build")
    b.add_argument("--preset", choices=sorted(CAMERA_PRESETS),
                   help="camera preset (overrides --azimuth/--elevation)")
    b.add_argument("--azimuth", type=float, default=45.0,
                   help="compass rotation; 45 = classic, 0 = looking north")
    b.add_argument("--elevation", type=float, default=35.264,
                   help="camera tilt above horizon; 35.264 = true isometric")
    b.add_argument("--width-px", type=float, default=1600.0)
    b.add_argument("--height-px", type=float, default=0.0, help="0 = unconstrained")
    b.add_argument("--padding-px", type=float, default=24.0)
    b.add_argument("--layers", choices=("type", "depth"), default="type",
                   help="type = Roofs/Walls/Ground layers (tidy); "
                        "depth = per-building, correct occlusion")
    b.add_argument("--stroke", default="#1a1a1a", help='"" to disable strokes')
    b.add_argument("--stroke-width", type=float, default=0.5)
    b.add_argument("--outline", action="store_true", help="no fills, lines only")
    b.add_argument("--no-cull", action="store_true", help="keep hidden back faces")
    b.add_argument("--keep-ground", action="store_true")
    b.add_argument("--keep-closure", action="store_true")
    b.add_argument("--min-height", type=float, default=2.0,
                   help="skip buildings shorter than this (m); 0 keeps everything")
    b.add_argument("--flatten", action="store_true",
                   help="sit each building on y=0 instead of preserving terrain")
    b.add_argument("--obj", action="store_true", help="also write .obj")
    b.set_defaults(func=cmd_build)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
