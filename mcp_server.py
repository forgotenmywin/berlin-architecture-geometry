from __future__ import annotations

import json
import math
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from pyproj import Transformer
from mcp.server.fastmcp import FastMCP
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent
LOD2_SCRIPT = ROOT / "lod2.py"
DATA_DIR = ROOT / "data"
ZIP_DIR = DATA_DIR / "zips"
TILE_DIR = DATA_DIR / "tiles"
REQUEST_DIR = DATA_DIR / "requests"
OUT_DIR = ROOT / "out"
PUBLIC_URL_FILE = DATA_DIR / "public_url.txt"
ATOM_BASE = "https://gdi.berlin.de/data/a_lod2/atom"

# Approximate outer bounds of Berlin, used to reject accidental requests
# for locations that this Berlin-only dataset cannot cover.
BERLIN_BOUNDS = (13.00, 52.25, 13.85, 52.75)  # west, south, east, north
MAX_SIDE_METRES = 2000.0
MAX_TILES = 25

TO_UTM = Transformer.from_crs("EPSG:4326", "EPSG:25833", always_xy=True)
JOB_LOCK = threading.Lock()

for folder in (ZIP_DIR, TILE_DIR, REQUEST_DIR, OUT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

if not LOD2_SCRIPT.is_file():
    raise RuntimeError("lod2.py was not found. Keep the repository's original lod2.py file.")

# Pin mcp to the 1.x SDK line in requirements.txt; this uses its FastMCP API.
mcp = FastMCP(
    "Berlin LoD2 CityGML",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/mcp/",
    host="0.0.0.0",
)


def _slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "-", (value or "berlin-area").strip()).strip("-_")
    return (value[:40] or "berlin-area").lower()


def _area_from_bbox(west: float, south: float, east: float, north: float, name: str = "berlin-area") -> dict[str, Any]:
    vals = (west, south, east, north)
    if not all(math.isfinite(float(v)) for v in vals):
        raise ValueError("BBox coordinates must be finite numbers.")
    if west >= east or south >= north:
        raise ValueError("BBox must be ordered west, south, east, north.")

    bw, bs, be, bn = BERLIN_BOUNDS
    if west < bw or south < bs or east > be or north > bn:
        raise ValueError(
            "This MCP currently serves Berlin only. The requested bbox is outside its supported Berlin bounds."
        )

    corners = [
        TO_UTM.transform(west, south),
        TO_UTM.transform(west, north),
        TO_UTM.transform(east, south),
        TO_UTM.transform(east, north),
    ]
    xs = [p[0] for p in corners]
    ys = [p[1] for p in corners]
    min_e, max_e = min(xs), max(xs)
    min_n, max_n = min(ys), max(ys)
    width = max_e - min_e
    height = max_n - min_n

    if width > MAX_SIDE_METRES or height > MAX_SIDE_METRES:
        raise ValueError(
            f"Area is too large ({width:.0f} m × {height:.0f} m). Maximum side is {MAX_SIDE_METRES:.0f} m."
        )

    center_e = (min_e + max_e) / 2.0
    center_n = (min_n + max_n) / 2.0
    lon, lat = Transformer.from_crs("EPSG:25833", "EPSG:4326", always_xy=True).transform(center_e, center_n)
    radius = math.hypot(width / 2.0, height / 2.0) + 2.0

    # This reproduces the tile-grid calculation used by the repository's lod2.py.
    tile_names: set[str] = set()
    ei = int((center_e - radius) // 1000)
    ni = int((center_n - radius) // 1000)
    e1 = center_e + radius
    n1 = center_n + radius
    while ei * 1000 <= e1:
        nj = ni
        while nj * 1000 <= n1:
            tile_names.add(f"LoD2_{ei}_{nj}")
            nj += 1
        ei += 1

    if len(tile_names) > MAX_TILES:
        raise ValueError(f"This bbox touches {len(tile_names)} LoD2 tiles; maximum allowed is {MAX_TILES}.")

    area_id = _slug(name)
    mark = {
        "id": area_id,
        "name": name[:100],
        "lat": lat,
        "lon": lon,
        "radius_m": radius,
    }
    request_path = REQUEST_DIR / f"{area_id}.json"
    request_path.write_text(json.dumps({"landmarks": [mark]}, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "id": area_id,
        "name": mark["name"],
        "bbox": {"west": west, "south": south, "east": east, "north": north},
        "width_m": round(width, 1),
        "height_m": round(height, 1),
        "center": {"lat": lat, "lon": lon},
        "radius_m": round(radius, 1),
        "tiles": sorted(tile_names),
        "request_file": request_path,
    }


def _download_and_extract_tile(tile_name: str, force: bool = False) -> dict[str, Any]:
    ZIP_DIR.mkdir(parents=True, exist_ok=True)
    TILE_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = ZIP_DIR / f"{tile_name}.zip"

    if force or not zip_path.is_file() or zip_path.stat().st_size == 0:
        temp_path = zip_path.with_suffix(".zip.part")
        url = f"{ATOM_BASE}/{tile_name}.zip"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "berlin-lod2-mcp/1.0 (GitHub Actions)"},
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response, temp_path.open("wb") as output:
                shutil.copyfileobj(response, output)
            temp_path.replace(zip_path)
        except urllib.error.HTTPError as exc:
            temp_path.unlink(missing_ok=True)
            if exc.code == 404:
                return {"tile": tile_name, "status": "not_published", "note": "No LoD2 tile is published for this grid cell."}
            raise RuntimeError(f"Download failed for {tile_name}: HTTP {exc.code}") from exc
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            raise RuntimeError(f"Download failed for {tile_name}: {exc}") from exc

    if not zipfile.is_zipfile(zip_path):
        zip_path.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not a valid ZIP: {tile_name}")

    extracted_files: list[str] = []
    with zipfile.ZipFile(zip_path, "r") as archive:
        members = [m for m in archive.infolist() if m.filename.lower().endswith((".xml", ".gml"))]
        if not members:
            raise RuntimeError(f"ZIP has no CityGML XML/GML file: {tile_name}")
        for member in members:
            # Use only the basename, never trust paths stored inside a ZIP.
            safe_name = Path(member.filename).name
            if not safe_name:
                continue
            destination = TILE_DIR / safe_name
            if force or not destination.exists() or destination.stat().st_size == 0:
                with archive.open(member, "r") as source, destination.open("wb") as output:
                    shutil.copyfileobj(source, output)
            extracted_files.append(destination.name)

    return {
        "tile": tile_name,
        "status": "downloaded" if extracted_files else "empty",
        "zip": zip_path.name,
        "zip_bytes": zip_path.stat().st_size,
        "citygml_files": extracted_files,
    }


def _ensure_area_tiles(area: dict[str, Any], force: bool = False) -> list[dict[str, Any]]:
    results = []
    available_for_area = 0
    for tile in area["tiles"]:
        result = _download_and_extract_tile(tile, force=force)
        results.append(result)
        expected_prefix = tile.replace("LoD2_", "LoD2_33_")
        cached_xml = list(TILE_DIR.glob(f"*{expected_prefix}*.xml")) + list(TILE_DIR.glob(f"*{expected_prefix}*.gml"))
        if result.get("status") == "downloaded" or cached_xml:
            available_for_area += 1
    if available_for_area == 0:
        raise RuntimeError("No CityGML tile was available for this bbox.")
    return results


def _run_lod2(area: dict[str, Any], command: str, extra_args: list[str], timeout: int = 900) -> subprocess.CompletedProcess[str]:
    request_file = str(area["request_file"])
    cmd = [sys.executable, str(LOD2_SCRIPT), "--landmarks", request_file, command, *extra_args]
    result = subprocess.run(
        cmd,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "No output from lod2.py")[-6000:]
        raise RuntimeError(f"lod2.py exited with code {result.returncode}:\n{detail}")
    return result


def _public_base_url() -> str:
    try:
        value = PUBLIC_URL_FILE.read_text(encoding="utf-8").strip().rstrip("/")
    except OSError:
        value = ""
    if not value.startswith("https://") or ".trycloudflare.com" not in value:
        raise RuntimeError("Quick Tunnel URL is not ready yet. Check that the GitHub Actions workflow reached the 'MCP URL' log line.")
    return value


def _file_link(folder: str, filename: str) -> str:
    return f"{_public_base_url()}/download/{folder}/{filename}"


@mcp.tool()
def list_lod2_tiles(west: float, south: float, east: float, north: float, name: str = "berlin-area") -> dict[str, Any]:
    """List the official 1 km LoD2 tiles touched by a Berlin bbox. Coordinates are WGS84 longitude/latitude."""
    area = _area_from_bbox(west, south, east, north, name)
    return {k: v for k, v in area.items() if k != "request_file"}


@mcp.tool()
def download_lod2_zip_bundle(west: float, south: float, east: float, north: float, name: str = "berlin-area", force: bool = False) -> dict[str, Any]:
    """Download and retain the original official LoD2 ZIP tiles for a Berlin bbox, then package them into one downloadable ZIP."""
    with JOB_LOCK:
        area = _area_from_bbox(west, south, east, north, name)
        tile_results = _ensure_area_tiles(area, force=force)
        available = [ZIP_DIR / f"{tile}.zip" for tile in area["tiles"] if (ZIP_DIR / f"{tile}.zip").is_file()]
        if not available:
            raise RuntimeError("The official feed did not return any ZIP tiles for this bbox.")

        bundle = OUT_DIR / f"lod2-{area['id']}-original-tiles.zip"
        manifest = {
            "dataset": "Berlin LoD2 CityGML",
            "source": ATOM_BASE,
            "crs": "EPSG:25833",
            "height_datum": "DHHN2016 NH",
            "bbox_wgs84": area["bbox"],
            "included_tiles": [p.name for p in available],
            "not_published_tiles": [r["tile"] for r in tile_results if r.get("status") == "not_published"],
        }
        with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_STORED) as output:
            for path in available:
                output.write(path, arcname=f"original_tiles/{path.name}")
            output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))

        return {
            "status": "ready",
            "bbox": area["bbox"],
            "tile_results": tile_results,
            "bundle": bundle.name,
            "bundle_bytes": bundle.stat().st_size,
            "download_url": _file_link("out", bundle.name),
            "note": "The bundle contains the original official tile ZIPs; the CityGML files are not converted or resampled.",
        }


@mcp.tool()
def inspect_lod2_area(west: float, south: float, east: float, north: float, name: str = "berlin-area") -> dict[str, Any]:
    """Download/cache required LoD2 tiles and run the repository's building probe to report measured heights and roof geometry."""
    with JOB_LOCK:
        area = _area_from_bbox(west, south, east, north, name)
        tile_results = _ensure_area_tiles(area)
        result = _run_lod2(area, "probe", [], timeout=900)
        return {
            "status": "inspected",
            "bbox": area["bbox"],
            "tiles": tile_results,
            "probe_report": (result.stdout or result.stderr)[-12000:],
            "note": "Heights and roof forms come from the official LoD2 CityGML parser. This report is not a promise of perfect real-world accuracy.",
        }


@mcp.tool()
def export_lod2_glb(west: float, south: float, east: float, north: float, name: str = "berlin-area") -> dict[str, Any]:
    """Export official LoD2 wall and roof surfaces for a Berlin bbox as a GLB file and return a public download URL."""
    with JOB_LOCK:
        area = _area_from_bbox(west, south, east, north, name)
        tile_results = _ensure_area_tiles(area)
        output_file = OUT_DIR / f"berlin-{area['id']}.glb"
        output_file.unlink(missing_ok=True)
        result = _run_lod2(area, "glb", ["--out", str(output_file)], timeout=1200)
        if not output_file.is_file() or output_file.stat().st_size == 0:
            detail = (result.stdout or result.stderr or "The exporter returned no GLB file.")[-6000:]
            raise RuntimeError(f"GLB export did not create the expected file.\n{detail}")
        return {
            "status": "exported",
            "bbox": area["bbox"],
            "tiles": tile_results,
            "file": output_file.name,
            "bytes": output_file.stat().st_size,
            "download_url": _file_link("out", output_file.name),
            "log": (result.stdout or "")[-3000:],
        }


async def health(_request):
    return JSONResponse({"status": "ok", "service": "Berlin LoD2 MCP", "mcp_endpoint": "/mcp/"})


async def download_file(request):
    kind = request.path_params["kind"]
    filename = request.path_params["filename"]
    if kind == "out":
        base = OUT_DIR
    elif kind == "zips":
        base = ZIP_DIR
    else:
        return JSONResponse({"error": "Unknown file category."}, status_code=404)

    # Prevent path traversal; only files directly inside the allowed directory are served.
    if Path(filename).name != filename or filename in ("", ".", ".."):
        return JSONResponse({"error": "Invalid filename."}, status_code=400)
    target = (base / filename).resolve()
    if target.parent != base.resolve() or not target.is_file():
        return JSONResponse({"error": "File not found."}, status_code=404)
    return FileResponse(target, filename=target.name, media_type="application/octet-stream")


# The SDK app includes its own lifespan/session-manager startup. Add the
# health and download routes before the MCP route so that the MCP catch-all
# cannot shadow them.
app = mcp.streamable_http_app()
app.router.routes.insert(0, Route("/healthz", health, methods=["GET"]))
app.router.routes.insert(1, Route("/download/{kind:str}/{filename:str}", download_file, methods=["GET"]))
