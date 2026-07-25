# Credits

Per PLAN.md §8: credit lines are **per object**, not per institution. Maintained
as material is acquired rather than reconstructed at the end.

---

## Geometry actually shipped

### Berlin LoD2 building model

3D-Gebäudemodelle im Level of Detail 2 (LoD2), Senatsverwaltung für
Stadtentwicklung, Bauen und Wohnen.
<https://gdi.berlin.de/data/a_lod2/atom/>

**Datenlizenz Deutschland – Zero – 2.0.** Public-domain equivalent: no
attribution legally required, commercial use fine, no share-alike. Credited
here anyway.

Used for: everything `lod2.py` emits by default — `out/*.svg`, `out/*.obj`,
`out/buildings.csv`, and the bulk of `out/map-buildings.geojson`.

### OpenStreetMap

<https://www.openstreetmap.org/copyright>

**© OpenStreetMap contributors, ODbL 1.0.** This one *does* carry obligations:
attribution, and share-alike on derived databases.

Used for:

- the hero landmark geometry in `out/map-buildings.geojson` — 166
  `building:part` solids across 8 discs, covering the Berliner Dom,
  Brandenburger Tor, Reichstag, Neues Museum, DHM, Bebelplatz,
  Staatsbibliothek and the resistance memorial
- the basemap in `berlin-trip.spa/public/berlin/` — districts, water, roads,
  transit and the Wall route. The property names give it away: `c: "primary"`,
  `ref: "S1"`, `net: "sbahn"`, `k: "water"`
- coordinates for two map places — the Hegel-Denkmal (node 1608998742) and
  Nofretete's room in the Neues Museum (way 576083618)

---

## Reference material held locally, not shipped

Kept under `reference/` (gitignored — see the sizes below).

### Bust of Nefertiti — 3D scan

Bust of Queen Nefertiti, Ägyptisches Museum und Papyrussammlung, Ident. Nr.
ÄM 21300. Amarna, Amenhotep IV / Akhenaten, 1351–1334 BC. Limestone, painted;
gypsum plaster, rock crystal, wax. 49.0 × 24.5 × 35.0 cm. Donation James Simon,
1920.

3D model by **Trigon Art, Berlin 2008**, structured-light 3D scan, 2008.
© 3D Model: Ägyptisches Museum und Papyrussammlung, Staatliche Museen zu
Berlin – Preußischer Kulturbesitz. ID `aem_aem21300_3dsl01_mo08-03_p.obj`.

SMB state: **CC BY-NC-SA 4.0**, and "Copyright restrictions apply". Commercial
permissions via `contact@bpk-bildagentur.de`.

<https://www.smb.museum/en/museums-institutions/aegyptisches-museum-und-papyrussammlung/collection-research/bust-of-nefertiti/the-bust/>

Note on that claim: **§68 UrhG** ("Vervielfältigungen gemeinfreier visueller
Werke werden nicht durch verwandte Schutzrechte nach den Teilen 2 und 3
geschützt", in force since 2021) removes neighbouring-rights protection from
faithful reproductions of public-domain visual works. The bust is ~1345 BC and
this is a structured-light scan, so the asserted licence rests on shaky ground —
which is the argument that got the file released in the first place. Of the
three terms, NC and BY are satisfied trivially by this project; only ShareAlike
would matter, and only if the claim holds.

234 MB zipped / 845 MB unpacked OBJ. **6,421,238 faces, 3,210,620 vertices.**

Not used in the web map: the bust is 49 cm tall, which is 0.9 px at the map's
maximum zoom. Held for the Illustrator plate, where it can be drawn at any size.

### Hegel Memorial — 3D scan (considered, not acquired)

"Hegel Memorial" by **VIMUNE**, Sketchfab, published 6 November 2021.
Photogrammetry. **CC Attribution (CC BY 4.0).** 4M triangles / 2M vertices.
<https://sketchfab.com/3d-models/hegel-memorial-4ffc023bc5854cb0b0748ac68628d8c2>

Download is account-gated. Not fetched. Recorded here so the licence is on
file if it is ever used.

Subject: Hegel-Denkmal by Gustav Blaeser, cast at Lauchhammer, unveiled
3 June 1871 — a bust with herm section on a tiered Muschelkalk pedestal, not a
full figure. Absent from LoD2: no cadastral record.

---

## Still to record

Archival plates and photography per PLAN.md §3 as they are used — the
Architekturmuseum TU Berlin material wants institution plus Inv. Nr. plus DOI,
and Wikimedia Commons files want author and licence per file.
