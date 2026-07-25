# Berlin Isometric Plate — Sourcing & Pipeline Plan

Isometric vector illustration of six Berlin subjects, drawn in Adobe Illustrator over geometry derived from Berlin open geodata.

**Status:** planning complete, geometry downloaded, projection not yet fixed.

---

## 0\. Subjects

| \#  | Subject                                 | Location                                     | Type                            | State to draw        |
| :-- | :-------------------------------------- | :------------------------------------------- | :------------------------------ | :------------------- |
| 1   | Gendarmenmarkt ensemble                 | Gendarmenmarkt, Mitte                        | Urban fabric \+ domed monuments | Post-2025 renovation |
| 2   | Konzerthaus (Schinkel's Schauspielhaus) | Gendarmenmarkt                               | Domed monument                  | _undecided_          |
| 3   | Humboldt-Universität, main building     | Unter den Linden 6                           | Urban fabric                    | Current              |
| 4   | Neues Museum                            | Museumsinsel                                 | Domed monument                  | _undecided — see §5_ |
| 5   | Berliner Dom                            | Am Lustgarten                                | Domed monument                  | _undecided — see §5_ |
| 6   | Hegel-Denkmal                           | Hegelplatz / Dorotheenstraße 24              | Small object                    | _undecided_          |
| 7   | Hegel & Fichte graves                   | Dorotheenstädt. Friedhof, Chausseestraße 126 | Small object \+ landscape       | Current              |

Subject 7 sits \~1.5 km north of the others; subjects 1–6 fall within \~1.2 km along Unter den Linden and the Museumsinsel.

### Type definitions

- **Urban fabric** — LoD2 geometry is adequate as-is.
- **Domed monument** — LoD2 fails (see §4). Requires mesh \+ archival elevations.
- **Small object** — no geodata exists at any level. Hand-drawn from photographs.
- **Landscape** — no building geometry. Orthophoto \+ OSM paths \+ own photographs.

---

## 1\. Blocking decisions

### 1.1 Composition — single plate or vignette set

Subjects 1–6 are close enough for one continuous isometric plate with cartographic compression. Subject 7 wants either an inset or its own vignette.

Determines whether a shared ground plane is needed. **Decide before Blender assembly.**

### 1.2 Projection family — BLOCKING

Not a free parameter set at render time. Choose the family; the camera angle follows.

| Projection               | Camera X rotation   | On-screen axis angle | Character                                              |
| :----------------------- | :------------------ | :------------------- | :----------------------------------------------------- |
| True isometric           | 54.736° (arctan √2) | 30°                  | Equal foreshortening all axes; roofs read strongly     |
| 2:1 dimetric             | 26.565° (arctan ½)  | 26.565°              | Pixel-perfect stroke alignment; facade-dominant        |
| Planometric ("military") | 45°                 | 45°                  | Plan undistorted; best for showing the square's paving |

**This does not block the geometry download** — 3D data re-renders at any angle for free. It blocks two things:

1. The first hand-drawn path. Re-projecting a 3D scene is free; re-projecting forty buildings of manual line work is not possible.
2. The detail sourcing budget. At 2:1 dimetric the drawing is facade-dominant, so spend on elevations and rectified facade photography. At true isometric or planometric, roofs become half the drawing — and roofs are exactly where LoD2 is weakest.

### 1.3 Light direction and Z exaggeration

Single global light direction, and a single vertical exaggeration factor if the domes need help reading. Fix once, record here, never deviate.

---

## 2\. Geometry sources

### 2.1 Primary — Berlin 3D Download Portal

`https://www.businesslocationcenter.de/berlin3d-downloadportal`

Two distinct products; the choice matters:

- **Photogrammetric mesh** — tiles as ZIP with geometry and textures in OBJ, current as of June 2025\. Post-renovation vintage. _Originally noted here as "the product that gets the domes right" — see §4.1: LoD2 already gets four of the five domes right, and OSM fixes the fifth with cleaner geometry. Keep the mesh for verifying the post-2025 Gendarmenmarkt paving (§4.2) and for ornament reference, not for dome massing — noisy triangles are poor input for Freestyle edge extraction._
- **LoD2 CityGML** — city-wide textured LoD2 building stock, derived from the ALK cadastral map. Clean, low-poly, editable. Whole-district archives also available at `http://download-berlin3d.virtualcitymap.de/citygml/` — take the Mitte archive.

Three extents needed: Gendarmenmarkt · Unter den Linden/Museumsinsel · Chausseestraße.

### 2.2 Convenience wrapper — 3DCityLoader

`https://3dcityloader.com/en/city/berlin`

Interactive map selection → DXF, STL, or OBJ, including terrain model and OSM street geometry. Free tier caps at 10,000 m². The square's paved surface alone is 14,000 m² and the full ensemble runs \~3.5 ha, so expect three or four adjoining selections.

### 2.3 Footprints — ALKIS via WFS

`https://gdi.berlin.de/services/wfs/alkis_gebaeude`

Cadastral building outlines — the most precise footprint source available, better than OSM. Datenlizenz Deutschland Zero 2.0, no access restrictions, no attribution required. Serves GeoJSON, GeoPackage, or GML.

**EPSG:25833 (UTM 33N).** Reproject before any planar operation.

---

## 3\. Reference sources

### 3.1 Hubs

| Source                             | URL                                                         | Notes                                                                                                                                                                         |
| :--------------------------------- | :---------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Architekturmuseum der TU Berlin    | `architekturmuseum.ub.tu-berlin.de`                         | Primary resource for the monuments. PD scans download unrestricted. Core strength is Schinkelschule \+ Berlin historicism. Also mirrored via Europeana and Deutsche Fotothek. |
| Berliner Denkmaldatenbank          | `denkmaldatenbank.berlin.de`                                | \~12,000 records, full-text and structured search, enriched from the Denkmaltopographie volumes. Data current 05.06.2026. All seven subjects are listed.                      |
| Deutsche Digitale Bibliothek       | `deutsche-digitale-bibliothek.de`                           | Aggregator. Discovery layer only — see §3.5.                                                                                                                                  |
| Deutsche Fotothek, Berlin drawings | `deutschefotothek.de/cms/architekturzeichnungen-berlin.xml` | 62,000+ sheets incl. the 1885 Architekturmuseum collection and the Architekten- und Ingenieurverein holdings.                                                                 |
| Wikimedia Commons                  | `commons.wikimedia.org`                                     | Small objects, current-state photography. German _Panoramafreiheit_ (§59 UrhG) covers street-level exteriors.                                                                 |

### 3.2 Konzerthaus — Schinkel

**Karl Friedrich Schinkel, _Sammlung architektonischer Entwürfe_, Neue vollständige Ausgabe in CLXXIV Tafeln, Ernst & Korn, 1858\.** Full volume digitised by Smithsonian Libraries: `https://library.si.edu/digital-library/book/sammlungarchite00schi`

- 12 plates cover the Schauspielhaus.
- Heft 2 (1821) plate 1 is the perspective view — redrawn by Otto from Schinkel's 1818/19 original, with the rubble and clouds removed. Presentation graphic, not measured.
- A supplementary delivery of six further Schauspielhaus plates appeared in 1826, numbered 13–18. **The measured elevations are here.**

### 3.3 Neues Museum — Stüler

**Friedrich August Stüler, _Das neue Museum in Berlin_, 24 Tafeln, Ernst & Korn, Berlin 1862\.**

- Full volume as one PDF, TU München mediaTUM: `https://mediatum.ub.tum.de/1445776` (`0015_RD_17_Stüler_Friedrich_Das_neue_Museum_in_Berlin.pdf`)
- DDB record via Architekturmuseum TU Berlin, Inv. Nr. 5388,03, with DOI. Rights: work and digitisation both public domain, picture credit requested. Record is dated 1862 despite a title page reading Potsdam 1853\.
- Includes the east facade with the connecting gallery to the Altes Museum and the colonnades.

### 3.4 Berliner Dom — Raschdorff

Search the Architekturmuseum for **Julius Raschdorff** — he founded the museum and incorporated his own drawings into it personally. Curated overview with inventory numbers: `https://architekturmuseum.ub.tu-berlin.de/CBO/index.php?I=35`

Known inventory numbers from that page:

- Inv. 14913 — east elevation of the Festsaalbau, ink and watercolour on card
- Inv. 14915 — overall plan (Lichtdruck)
- Inv. 14917 — overall view from the east (Lichtdruck)

Caution: those three belong to the 1884–88 Schloss-extension-plus-Dom scheme, which was never built. See §5.

### 3.5 Hegel-Denkmal and small objects

| Source                                                            | URL                                                            |
| :---------------------------------------------------------------- | :------------------------------------------------------------- |
| Wikimedia Commons category (19 files, incl. Bundesarchiv)         | `commons.wikimedia.org/wiki/Category:Hegel_memorial_(Berlin)`  |
| Bildhauerei in Berlin — fullest scholarly description             | `bildhauerei-in-berlin.de/bildwerk/hegel-denkmal-7860/`        |
| vanderkrogt statues database — multi-angle \+ inscription details | `statues.vanderkrogt.net/object.php?webpage=ST&record=debe002` |

Object facts: over-life-size portrait bust with herm section on a multi-tiered Muschelkalk pedestal, by Gustav Blaeser, cast at Lauchhammer, unveiled 3 June 1871 when the former Platz am Schlossbauhof was renamed. **Not a full-figure statue.** The original configuration included a two-step base plate and granite pedestal by Paul Wimmel to a design by Georg Gustav Erbkam, plus a cast-iron railing on four sides — decide whether to draw the original or present configuration.

Graves and cemetery: Commons category search. No single verified URL. Hegel lies beside Fichte; Brecht and Marcuse are nearby.

### 3.6 Searching the DDB

Filters that do most of the work: **Medientyp: Bilder**, then **Datengeber: Architekturmuseum der Technischen Universität Berlin**.

Object-type vocabulary:

| Term                          | Meaning                | Useful?                                 |
| :---------------------------- | :--------------------- | :-------------------------------------- |
| **Aufriss**                   | Orthographic elevation | Primary target                          |
| **Grundriss**                 | Plan                   | Yes                                     |
| **Schnitt** (Längs-/Quer-)    | Section                | Yes — dome interiors, drum heights      |
| **Lageplan / Situationsplan** | Site plan              | Yes, footprint alignment                |
| **Detail / Werkzeichnung**    | Detail or shop drawing | Yes, ornament and cornice profiles      |
| **Perspektivische Ansicht**   | Perspective view       | No — has the convergence we're escaping |
| **Ansicht**                   | Ambiguous              | Verify before trusting                  |

The distinction that matters most:

- **Entwurfszeichnung** — a design proposal. May document something never built or altered in execution.
- **Bauaufnahme / Aufmaß** — measured survey of what physically stands. What we want. Rarer.
- **Messbildaufnahme** — photogrammetric survey photography. Rectified and dimensionally reliable; effectively a free facade orthophoto. Take it whenever it appears.

**Best single filter: look for _Maßstab_ in the metadata.** A stated scale (1:100, 1:50) means measurable. No scale means presentation graphics.

Medium terms and what they imply: **Handzeichnung** (original, ink or watercolour), **Lichtpause** (diazo copy, usually a working drawing, often dimensionally trustworthy), **Kupferstich / Radierung / Lichtdruck** (print from a plate volume), **Tafel** (numbered plate).

DDB usually serves a low-res derivative. Always follow _"Objekt beim Datenpartner anzeigen"_ through to the Architekturmuseum viewer for downloadable high-res.

---

## 4\. Known data problems

### 4.1 LoD2 and domes — measured, not assumed

_Revised after probing and rendering every subject. The original claim here — that LoD2 returns "a hip roof or a flat slab" for all five domed monuments — is wrong, and acting on it would have wasted most of the modelling budget._

LoD2 is generated automatically from ALKIS footprints, the terrain model and orthophotos, using generalised standard roof forms, with height accuracy around one metre. It fails on **one** of our subjects, not five:

| Subject              | LoD2 reality                                                      | Verdict     |
| :------------------- | :---------------------------------------------------------------- | :---------- |
| Deutscher Dom        | 63 m, **539 faces** — drum, colonnade, lantern, apse half-dome    | Good as-is  |
| Französischer Dom    | 61 m geometry, **494 faces** — drum, colonnade, tower             | Good as-is  |
| Konzerthaus          | 23.5 m, 143 faces — massing fine, **portico and tympanum absent** | Partial     |
| Neues Museum         | 30.5 m, 191 faces, courtyards resolved                            | Adequate    |
| Humboldt-Universität | 26.3 m, 96 faces                                                  | Adequate    |
| **Berliner Dom**     | **flat slab, 43 m** against a real 98 m                           | **Broken**  |
| Hegel-Denkmal        | absent — no cadastral record                                      | Photos only |

What the good models _do_ lack is ornament and smooth curvature: dome caps are faceted, and there are no cornices, pilasters or sculpture. That is a detail-sourcing problem for §3, not a geometry problem — the massing is a sound armature.

**Diagnostic trap: `measuredHeight` lies.** The Französischer Dom reports 23.92 m while its geometry is 61.36 m — the attribute describes the building body, not the tower. Reading a low `measuredHeight` as "generalised" would condemn two perfectly good models. The reliable test is geometric height plus face count. `geom` far _below_ `height` means generalisation; `geom` far _above_ it means a tower that is modelled.

Remedy for the Berliner Dom: **OSM Simple 3D Buildings**, not hand-modelling. See §4.3.

### 4.3 OSM covers exactly the gap

Checked across all subjects, OSM 3D coverage is the inverse of LoD2's — excellent on the famous failures, absent elsewhere:

- **Berliner Dom** — comprehensively mapped. 40 `building:part` solids; main cupola `min_height=50, height=75, roof:shape=dome, roof:height=25`, two side cupolas at 45→52, body parts 28→45 m, plus `roof:material=copper` and colours.
- **Gendarmenmarkt** — 6 parts, and the two Dome carry **no** height or roof tags at all. LoD2 is strictly better here.
- **Neues Museum** — partial; the building itself is only `building:levels=4`.

`lod2.py` now reads this directly: `--source osm` or `--source both`, or `"source"` per landmark. Extruding OSM's parametric solids yields **clean-edged** geometry, which matters for Phase B — Freestyle recovers edges from a photogrammetric mesh badly, and cleanly from parametric solids.

`--source both` is the best result for the Dom: LoD2's survey-accurate body fills the gaps between OSM's floating parts, with the cupolas rising from it. Requires `"base_z_m": 32.3` to put OSM's ground-relative heights onto the LoD2 DHHN2016 datum.

Known limits of the importer: `roof:shape` values outside dome / onion / pyramidal / hipped / cone fall back to flat (it reports which), and parts with `min_height` float free where OSM has not mapped what sits beneath — which is why `both` beats `osm` alone.

### 4.2 Aerial and Street View imagery is largely unusable

Gendarmenmarkt renovation began October 2022 and the square only reopened after roughly two and a half years of construction, with full release in early March 2025\. Anything captured in between shows a sand pit. The paving is entirely new — 14,000 m² of natural stone.

Check capture dates before trusting any imagery for the ground plane. The June 2025 mesh is post-reopening and safe.

---

## 5\. Reconciliation problems

Both monuments require an explicit decision about _which building_ is being drawn, recorded in the manifest before any path is committed.

### 5.1 Berliner Dom

Built 1893–1905 by Julius Raschdorff and his son Otto. Badly destroyed in the war, rebuilt 1975–82 in somewhat simplified form. The Denkmalskirche formerly on the north side was dropped entirely.

Raschdorff's drawings therefore document a building that partly no longer exists — and the 1884–88 drawings document one that never existed at all. Every detail taken from the archive must be checked against the 2025 mesh.

### 5.2 Neues Museum

Stüler 1843–55; Chipperfield restoration 1999–2009. The same problem inverted: Chipperfield deliberately left war damage legible, so Stüler's 1862 plates show a surface condition that has been intentionally _not_ restored.

---

## 6\. Pipeline

### Phase A — Geometry acquisition ✅

Mesh and/or LoD2 tiles for three extents; ALKIS footprints as GeoJSON.

### Phase B — Blender assembly

1. Import tiles, align on shared ground plane, delete everything outside frame.
2. Replace failed dome geometry per §4.1 — in practice the Berliner Dom only, via `lod2.py --only berliner-dom --source both build --obj`.
3. Set orthographic camera to the §1.2 angles.
4. Render two passes:
   - **Freestyle \+ SVG exporter addon** → vector edge lines, silhouette and crease only
   - **Flat AO / clay pass** → shading guide for Phase C hatching

Freestyle SVG output imports into Illustrator as editable paths. This is what saves the tracing time on the Blockrand buildings, where only honest massing is needed.

### Phase C — Illustrator

1. Import SVG.
2. Establish line hierarchy immediately — silhouette / major edge / detail / texture as four stroke weights, saved as graphic styles.
3. One layer per subject so each landmark stays independently movable during composition.
4. Small objects built by hand on separate artboards at 3–4× final size, then placed.
5. Cross-hatching last, on dome curvature and under cornices, driven by the AO pass. Set up as brushes so density stays consistent across all seven subjects.

---

## 7\. Repository layout

*The numbered scheme below was planned before the pipeline existed. What got built is flatter, because `lod2.py` fetches and clips geometry on demand rather than storing it per subject — there is no reason to keep three geometry extents on disk when a landmark id and a radius regenerate any of them.*

Actual:

```
berlin-architecture-geometry/
  PLAN.md                    # this file
  README.md                  # the pipeline, sources, and what LoD2 gets wrong
  lod2.py                    # fetch · probe · find · build · geojson
  requirements.txt
  landmarks.json             # 33 verified subjects across the city
  plan-subjects.json         # the seven subjects in §0, for fidelity probing
  trip-places.json           # the Berlin trip's stops
  map-buildings.json         # trip stops + hero landmarks sourced from OSM
  out/                       # renders: SVG, OBJ, buildings.csv, GeoJSON
  data/tiles/                # CityGML cache      (gitignored, ~886 MB)
  data/osm/                  # OSM extracts       (gitignored, ~17 MB)
```

Still to create when the drawing starts: `30_blender/`, `40_illustrator/`, and `90_credits.md` per §8. A `00_manifest.csv` is partly redundant now — `out/buildings.csv` already records every building's height, footprint, roof form and face count — but the *state to draw* column from §5 has no home yet and needs one.

`.gitignore` covers the tile cache, the OSM extracts, the source zips and the reference photography: ~1.6 GB, all re-fetchable. `out/` is tracked despite being generated, because rebuilding it means re-downloading ~900 MB of tiles.

---

## 8\. Attribution policy

Maintain `90_credits.md` from the start rather than reconstructing later. Credit lines are **per object**, not per institution.

| Source                      | Requirement                                                               |
| :-------------------------- | :------------------------------------------------------------------------ |
| ALKIS via WFS               | Datenlizenz Deutschland Zero 2.0 — none required                          |
| Geoportal Berlin datasets   | _"Geoportal Berlin / \[dataset title\]"_ — verify per dataset page        |
| Architekturmuseum TU Berlin | PD, picture credit requested: institution \+ Inv. Nr. Record the DOI too. |
| Smithsonian Libraries (SAE) | Check the item page                                                       |
| Wikimedia Commons           | Per-file licence; record author and licence per image                     |

On the buildings themselves: all long out of architectural copyright. German _Panoramafreiheit_ (§59 UrhG) covers exterior photography from public space, including the Chipperfield work and the permanently installed sculpture. The residual risk is tracing a single photographer's specific composition — synthesising massing from geodata and detail from multiple references avoids it.

---

## 9\. Open items

- [ ] **Fix projection family (§1.2) — blocks all hand-drawn work**
- [ ] Decide single plate vs. vignette set (§1.1)
- [ ] Fix light direction and Z exaggeration factor (§1.3)
- [ ] Decide building state per subject, record in `00_manifest.csv` (§5)
- [ ] Verify Geoportal licence terms per dataset page (§8)
- [ ] Locate a verified Commons category URL for the Dorotheenstädtischer Friedhof
- [ ] Locate Stüler / Neues Museum _Bauaufnahme_ material, if any exists post-1990
- [ ] Confirm whether measured elevations exist for the Gontard dome towers

## Verification note

Links in §2, §3.1–3.5 were confirmed live during planning. Items flagged as "search entry" have no verified deep URL and require a catalogue search.
