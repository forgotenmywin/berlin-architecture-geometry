# Berlin LoD2 → isometric vectors

A pipeline that pulls Berlin's official 3D building model and turns any landmark
into layered axonometric SVG you can open straight in Illustrator.

---

## The data source

**3D-Gebäudemodelle im Level of Detail 2 (LoD2)**, Senatsverwaltung für
Stadtentwicklung, Bauen und Wohnen.

| | |
|---|---|
| Feed | `https://gdi.berlin.de/data/a_lod2/atom/` |
| Format | CityGML 1.0 (AdV profile), one `.xml` per tile inside a `.zip` |
| Tiling | 1 × 1 km, named `LoD2_{easting_km}_{northing_km}.zip` |
| CRS | EPSG:25833 — ETRS89 / UTM zone 33N, heights in DHHN2016 NH (metres, absolute) |
| Coverage | all of Berlin, ~550,000 buildings |
| Last refreshed | June 2026 |
| Licence | **Datenlizenz Deutschland – Zero – 2.0** |

That licence matters for illustration work: it's the German public-domain
equivalent. No attribution legally required, commercial use fine, no share-alike.
Credit it anyway if you feel like it, but you're not obliged.

Footprints come from the cadastre, so they're survey-accurate. Roofs are
"generalised standard forms" — real pitched/hipped/mansard/domed geometry, but
simplified. **Read the caveat at the bottom before you commit to a subject.**

---

## Setup

```bash
pip install -r requirements.txt      # just pyproj
```

## Use

```bash
python3 lod2.py tiles                          # which tiles do I need? (no download)
python3 lod2.py fetch                          # cache them into data/tiles/
python3 lod2.py build                          # SVGs for every landmark
python3 lod2.py build --only fernsehturm       # or just one
python3 lod2.py probe --only reichstag         # heights + roof forms, no output files
python3 lod2.py find --near 52.4986,13.3814   # locate a building by geometry
```

`build` implies `fetch`, so you can skip straight to it. Tiles are cached, so
re-running with different camera settings costs nothing.

### Camera

```bash
python3 lod2.py build --preset true-iso        # default: equal foreshortening, 45°/35.264°
python3 lod2.py build --preset illustrator     # 45°/30°, matches AI's isometric SSR
python3 lod2.py build --preset pixel-2to1      # 45°/26.565°, clean 2:1 slopes
python3 lod2.py build --preset planometric     # 45°/45°, architectural "military" view
python3 lod2.py build --preset low-oblique     # 45°/15°, dramatic and streetish

python3 lod2.py build --azimuth 200 --elevation 22   # or set it by hand
```

`--azimuth` is a compass rotation (0 = looking north, 45 = the classic
three-quarter). `--elevation` is how far above the horizon the camera sits.
It's a parallel projection throughout — no perspective, no vanishing points,
which is what makes the output tileable and rescalable.

### Look

```bash
--outline                  # line art only, no fills
--stroke "" --layers type  # flat fills, no strokes
--stroke-width 0.25
--width-px 2400            # output width; geometry is scaled to fit
--keep-ground              # include ground slabs (off by default — invisible from above)
--min-height 0             # keep sub-2 m clutter (kiosks, canopies) that's filtered by default
--flatten                  # sit every building on y=0 instead of preserving terrain
--obj                      # also write .obj for Blender / C4D / Rhino
```

### Layers — the one real trade-off

`--layers type` (default) gives you three Illustrator layers: `Roofs`, `Walls`,
`Ground`. Tidy, easy to restyle en masse. Occlusion is correct *within* each
building, but across buildings a tall wall can wrongly sit beneath a shorter
neighbour's roof.

`--layers depth` gives one group per building, painter's-algorithm ordered.
Occlusion is correct everywhere; you get a lot more layers to manage.

Start with `type`. Switch to `depth` if you see a building punching through
another one.

---

## What you get

```
out/
  fernsehturm.svg          layered axonometric, ready for Illustrator
  fernsehturm.obj          same geometry in 3D (with --obj)
  buildings.csv            manifest: heights, roof forms, footprints, face counts
data/tiles/
  LoD2_33_392_5820_1_BE.xml    the raw CityGML — yours to re-process however
```

Back-face culling and depth sorting happen before export, so you're not opening
a file with 3× the paths you can see. Walls are tonally separated by orientation
so planes read apart immediately; override all of it in Illustrator.

`buildings.csv` is worth a look before you draw. `roof_form` tells you what
you're getting (`dome`, `mansard`, `gable`, `barrel`, `spire`, `flat`…) and
`faces` tells you how much geometry there is — a 500-face building is richly
modelled, a 6-face one is a shoebox.

---

## Adding your own subjects

Edit `landmarks.json`:

```json
{ "id": "my-building", "name": "Something", "lat": 52.5, "lon": 13.4, "radius_m": 80 }
```

`radius_m` is how much context to pull in. 0–40 gets you the building nearly
alone; 80–150 gets it with neighbours; 200+ gets a streetscape. Any building
with a single vertex inside the disc is included whole — nothing gets sliced.

Coordinates: right-click in Google Maps or OpenStreetMap and copy the lat/lon.
The script handles WGS84 → UTM33N.

If a new coordinate returns nothing, or returns the wrong thing, use `find` to
hunt by geometric signature instead — it works entirely off the cached tiles,
no geocoder involved:

```bash
python3 lod2.py find --near 52.4817,13.3556 --radius 300 --min-height 25
python3 lod2.py find --near 52.5137,13.3928 --radius 200 --roof dome
python3 lod2.py find --near 52.5,13.4 --radius 500 --min-footprint 80
```

It prints each match's height, footprint, roof form, distance and its own
lat/lon — so you paste the corrected coordinate straight back into
`landmarks.json`. This is how the entries in there were checked.

**Most freestanding monuments aren't in this dataset.** It's derived from the
cadastre, so anything without a building record simply isn't there. Confirmed
absent: Brandenburger Tor, Siegessäule. Expect the same for Molecule Man and
the Treptow memorial. Towers built *as buildings* are present and often
excellent — Fernsehturm 229 m, Funkturm 130 m.

**The Holocaust Memorial is the exception — and it's spectacular.** Every
stele is modelled individually: 1,928 of them within 90 m of the centre, each
a ~2.4 m² footprint between 2.0 and 7.5 m tall, tracking the real undulation
of Eisenman's field. Nothing else in the extract comes close to that footprint
size, so a `footprint_m2` threshold cleanly separates the stelae from actual
buildings — everything else has a median footprint of 196 m².

---

## The caveat: LoD2 fidelity is wildly uneven

This is the thing to know before you pick subjects. Some buildings are modelled
beautifully; some monuments are boxes.

**Came out excellent** — Fernsehturm (229 m: shaft, sphere and antenna stub, 837
faces), both Gendarmenmarkt domes with drums and lanterns, the Philharmonie's
folded tent roof, the Jüdisches Museum's Libeskind zigzag, Hauptbahnhof's curved
train shed, the Shell-Haus's serpentine stepped facade, Rotes Rathaus with
courtyards and spire, the Gasometer Schöneberg frame, the Karl-Marx-Allee blocks.

**Came out as a box** — Berliner Dom: correct 96 × 100 m articulated footprint,
flat roof at 43 m, dome entirely absent (real dome tops out at 98 m). Reichstag:
exact 101 × 140 m footprint, flat at 28 m, Foster dome absent. Haus der Kulturen
der Welt: the catenary roof flattened to a slab.

There's no pattern to predict it from, so check before you invest drawing time.

Diagnose before you draw:

```bash
python3 lod2.py probe --only berliner-dom
```

If `geom` is far below `height`, the model has been generalised. If both are
suspiciously low for a building you know is tall, the survey itself truncated
it — that's the Reichstag case, and no amount of parsing will recover it.

For those, the built-in fallback is OSM — see below. Failing that, the
**Berlin 3D Downloadportal** (<https://download-berlin3d.virtualcitymap.de/>)
serves genuine LoD3 models and a textured photogrammetric mesh for selected
landmarks. Interactive picker, no bulk API, six export formats.

---

## `glb` — real surfaces for a web map

`geojson` emits a footprint and one height, so a client can only extrude a
prism. That is fine for a plan view and useless for anything else: it discards
38% of the geometry and **100% of the roof shape**. Every building in Berlin
comes out a flat-topped box.

`glb` ships the surfaces themselves.

```bash
python3 lod2.py --landmarks map-buildings.json glb --out out/berlin.glb
npx @gltf-transform/cli meshopt out/berlin.glb out/berlin.mo.glb
```

For the trip area that is 87,750 triangles — 3.05 MB raw, **616 kB after
meshopt, ~290 kB gzipped**. Against the prism GeoJSON's 1.30 MB / 160 kB
gzipped, roughly double the transfer for actual architecture.

Three primitives, one per surface kind (`roof`, `wall`, `monument`), each
unlit with a white base colour. `COLOR_0` carries **only** a shading factor as
grey, so a client sets `baseColorFactor` from its own palette and glTF
multiplies the two — retinting for light/dark needs no rebuild.

**Coordinates are local ENU metres** (X east, Y north, Z up) about a lon/lat
origin in the file's `extras`, deliberately *not* UTM. UTM grid north is up to
a degree off true north in Berlin, and a web-Mercator basemap is aligned to
true north — baking UTM would skew the scene by ~50 m at the edges. A client
projects the one origin, works out pixels per metre, and everything follows.

Mixing sources is handled automatically. Inside a hero disc OSM replaces LoD2,
and the displaced LoD2 buildings are used first to read off the exact ground
level, so the OSM parts (whose heights are metres above ground) land on the
LoD2 datum without any hand-set `base_z_m`. Berliner Dom: 32.3 m, Reichstag:
35.1 m, Bebelplatz: 31.7 m.

Faces past a triangle are ear-clipped rather than fanned — 4,538 of the trip
area's faces have more than four sides and a fan would fold the concave ones.
Interior rings are bridged into the exterior first, so light wells stay open;
24 faces out of 32,322 have any.

Roof forms come in two families. **Loft** shapes taper toward the centroid as
they rise — dome, onion, cone, pyramidal, hipped, mansard — and are built by
scaling the ring inward step by step. **Ridge** shapes are height fields over
the footprint — gabled, gambrel, barrel/round, skillion — built by lifting a
triangulated cap and dropping a skirt to the eaves, which is what puts vertical
ends on a gable instead of quietly hipping it. Ridge direction comes from
`roof:direction` if present, else `roof:orientation` against the footprint's
minimum-area long axis; 29 of the 35 ridge-family parts carry one of the two.

---

## `mesh2paper.py` — monuments the building datasets don't have

Some subjects have no cadastral record at all. The Hegel-Denkmal is a 3.3 m
bust on a pedestal, so LoD2 has nothing and OSM maps it as a bare point;
Nofretete is a 49 cm object indoors. For those the only geometry available is
a photogrammetry or structured-light scan, and scans are the wrong shape and
size for a map — the Nefertiti one is 6.4 M triangles and 845 MB.

```bash
# GLB in, GLB + hover sprite out
python3 mesh2paper.py scan.glb out/hegel-denkmal.glb --target-tris 4000 \
  --crop-radius 1.15 --name hegel-denkmal --credit "$CREDIT" \
  --svg out/hegel-denkmal.svg --svg-width 200 --svg-tris 700

# OBJ in — museum scans usually ship OBJ, and rarely in metres
python3 mesh2paper.py scan.obj out/nofretete.glb --target-tris 4000 \
  --height 0.49 --name nofretete --credit "$CREDIT" \
  --svg out/nofretete.svg --svg-azimuth 180 --svg-elevation 0 \
  --svg-width 200 --svg-tris 3000
```

Output matches what `glb` writes — local metres, Z up, one unlit primitive,
`COLOR_0` carrying only a shading factor — so the client needs no second code
path.

Decimation is **vertex clustering**, not quadric simplification: meshoptimizer
refuses to collapse across the disconnected components scans are full of and
stalls around 45,000 triangles however hard you push `--ratio`. Clustering
snaps to a grid, welds, and drops what collapsed. The one thing that matters
is where the surviving vertex goes: it must be the **mean of the vertices that
fell in the cell**, not the cell centre. Snapping to centres is invisible on a
building, whose faces are axis-aligned planes already, and ruinous on anything
organic — the first pass at Nefertiti came out as a heap of voxels.

An OBJ is read in two passes and snapped to a 512³ grid as the faces stream in,
which is what keeps 6.4 M triangles from becoming several GB of Python tuples.
OBJ carries no up-axis convention, so the tallest extent is assumed vertical;
`--up` overrides it. Worth checking against the object's real dimensions — for
Nefertiti the three extents divided by the catalogue's 49.0 × 24.5 × 35.0 cm
all landed on the same ~10.3 units/cm, which confirms the axis, confirms the
scan is undistorted, and gives the scale. Sketchfab models are usually in
metres; museum scans are not, so `--height` normalises them — without it the
Nefertiti GLB claims a 489 m bust.

`--svg` also emits a flat axonometric sprite. The map's default view loads no
WebGL at all, so a monument that exists only as a mesh cannot appear there; a
sprite can, at whatever size reads, at any zoom.

Three controls decide whether that sprite is legible, and all three mattered
for Nefertiti:

- **`--svg-elevation`** defaults to the map's 35.264°, which looks *down* at
  the subject. That is right for a monument seen among buildings and wrong for
  a portrait: every profile came out as a leaning L because the camera was
  sitting on top of her crown. `0` puts it at eye level.
- **`--svg-azimuth`** defaults to 45°. `180` is her left profile, the view she
  is conventionally photographed in.
- **`--svg-tris`** at the old default of 600–900 loses the flat-topped crown
  that makes her recognisable. 3,000 costs 32 kB gzipped, which is nothing for
  an asset fetched on hover.

Two further fixes to the renderer itself, both visible at any angle. Polygons
are stroked in **their own fill colour** — not an outline, but a way to close
the anti-aliasing seams between adjacent faces, which otherwise read as a pale
wireframe laid over the object. And faces are drawn **two-sided** by default:
back-face culling assumes consistent winding, a scan does not have it, and the
inward-wound faces were being culled into holes that showed the dark interior
through as specks. `--svg-cull` restores culling and halves the file.

The key light is defined in the image basis rather than in world space, so it
follows the camera. A fixed world light only models the faces it happens to
point at — swing round to a profile and the lit side is behind the object,
leaving a flat grey cut-out.

---

## Sources: `--source lod2 | osm | both`

`lod2.py` reads two geometry sources. Set `"source"` per landmark, or
`--source` for a whole run.

| | Strength | Weakness |
|---|---|---|
| `lod2` (default) | Survey-accurate footprints, real roof forms, all ~550,000 buildings | Generalises a handful of landmarks into slabs |
| `osm` | Exact where the community cared — full cupolas, `min_height`, `roof:shape` | Almost nothing outside the famous buildings |
| `both` | LoD2 body + OSM detail | Overlapping geometry; needs `base_z_m` |

Coverage is the inverse of what you'd guess. **Measured across the
Gendarmenmarkt and Museumsinsel subjects:**

- Deutscher Dom — LoD2 gives 63 m and **539 faces**: drum, colonnade, lantern.
  OSM has no heights at all. Use LoD2.
- Französischer Dom — LoD2 gives 61 m and **494 faces**. OSM has no heights.
  Use LoD2.
- Berliner Dom — LoD2 gives a **flat 43 m slab**. OSM has 40 `building:part`
  solids with real cupolas reaching 82 m. Use `both`.

```bash
python3 lod2.py --only berliner-dom --source both build --obj
```

**`base_z_m` matters when mixing.** OSM heights are metres above ground; LoD2
is absolute DHHN2016. Without it the OSM parts sit ~32 m underground. Probe the
LoD2 base and put it in the landmark:

```json
{ "id": "berliner-dom", "source": "both", "base_z_m": 32.3, ... }
```

Roof shapes understood: `dome`, `onion`, `pyramidal`, `hipped`, `cone`.
Anything else is drawn flat, and the run prints what it approximated. OSM
extracts cache in `data/osm/`.

### Reading `probe` output

`measuredHeight` is not a reliable fidelity signal. The Französischer Dom
reports 23.92 m while its geometry is 61.36 m — the attribute describes the
building body, not the tower.

- `geom` far **below** `height` → generalised, detail lost. (Berliner Dom.)
- `geom` far **above** `height` → a tower or spire that *is* modelled. Good.
- Face count is the better tell: 500 faces is richly modelled, 67 is a box.

---

## What's already built

`out/` contains 18 landmarks rendered at `--preset true-iso`, plus `.obj`
versions and a 423-building manifest. `data/tiles/` has 26 tiles cached (769 MB),
covering central Mitte, Tiergarten, Charlottenburg, Schöneberg and Friedrichshain.

The remaining 15 landmarks in `landmarks.json` — Olympiastadion, Tempelhof,
Schloss Charlottenburg, ICC, Velodrom, Treptowers, Corbusierhaus, Mäusebunker,
Bierpinsel and the rest — need another ~30 tiles, roughly 1.5 GB. Run
`python3 lod2.py build --obj` and leave it going; it picks up where the cache
leaves off.

## Notes

- Parsing is fast: ~0.4 s per tile, streamed, so memory stays flat.
- Tiles are 3–57 MB unzipped. The cache adds up — 26 tiles is ~770 MB.
- Geometry lives in the `boundedBy` surfaces (`WallSurface` / `RoofSurface` /
  `GroundSurface`); `lod2Solid` only xlinks to them. Handy if you write your own
  parser.
- `BuildingPart` geometry is folded into its parent building, so consolidated
  complexes come through whole.
- The feed only publishes tiles containing buildings. Water, forest and grid
  edges legitimately 404 — the script notes them and moves on.

## Sources

- [3D-Gebäudemodelle im Level of Detail 2 (LoD 2) — Berlin Open Data](https://daten.berlin.de/datensaetze/3d-gebaudemodelle-im-level-of-detail-2-lod-2-3c7c49af)
- [ATOM download service](https://gdi.berlin.de/data/a_lod2/atom/)
- [Datenlizenz Deutschland Zero 2.0](https://www.govdata.de/dl-de/zero-2-0)
- [Berlin 3D Downloadportal (LoD3 / textured)](https://download-berlin3d.virtualcitymap.de/)
