# Ghost Port Tools

[![BCOS Certified](https://img.shields.io/badge/BCOS-Certified-brightgreen?style=flat&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCIgZmlsbD0id2hpdGUiPjxwYXRoIGQ9Ik0xMiAxTDMgNXY2YzAgNS41NSAzLjg0IDEwLjc0IDkgMTIgNS4xNi0xLjI2IDktNi40NSA5LTEyVjVsLTktNHptLTIgMTZsLTQtNCA1LjQxLTUuNDEgMS40MSAxLjQxTDEwIDE0bDYtNiAxLjQxIDEuNDFMMTAgMTd6Ii8+PC9zdmc+)](BCOS.md)
Open-source conversion toolkit for **StarCraft: Ghost** (2004 Xbox) game assets.
Converts Nihilistic Software's proprietary NOD engine formats to modern standards
(glTF 2.0, PNG) for use in Godot 4.x or any 3D engine.

**You must supply your own game files.** This repository contains only tools and
documentation — no copyrighted game assets are included or will be accepted in
pull requests.

## What This Does

| Tool | Input | Output | Status |
|------|-------|--------|--------|
| `nil_parser.py` | `.nil` level geometry | `.obj` / `.json` / `.gltf` | Working (all 8 levels) |
| `nod_to_gltf.py` | `.nod` binary mesh | `.gltf` 2.0 | Working (1391/1401 models) |
| `nsd_model_extractor.py` | `.nsd` entity data | Entity JSON with model refs | Working (315 entities) |
| `convert_missing_textures.py` | `.dds` Xbox textures | `.png` | Working (480 DDS → 605 PNG) |
| `vertex_field_analysis.py` | `.nil` vertex data | Analysis report | Research tool |

## Quick Start

```bash
# 1. Clone this repo
git clone https://github.com/YourUser/ghost-port-tools.git
cd ghost-port-tools

# 2. Point it at your extracted SC:Ghost disc
python3 converters/nod_to_gltf.py \
    --source /path/to/your/starcraft_ghost/3D/Models/ \
    --output ./out/gltf/

# 3. Open the Godot project and drop models into assets/
```

## Requirements

- Python 3.8+
- No external dependencies (stdlib only)
- Godot 4.3+ (for the showcase project)

## Supported Formats

### NIL Level Geometry (fully decoded)

The `.nil` binary level format used by Nihilistic Software's NOD Engine across two games:
- **VTMR** (2000, PC): NIL version 27 — all little-endian, split vertex arrays
- **SC:Ghost** (2003, Xbox/GCN): NIL version 35 — mixed endian, merged vertex records

Both versions share the same magic (`NIL\x10`), header layout, and material table format.

**SC:Ghost NIL v35 Vertex Format (36 bytes):**
```
+0:   u16 LE  UV_U (normalized: value/65535)
+2:   u16 LE  UV_V (with V-flip: 1.0 - value/65535)
+4:   f32 BE  position X
+8:   f32 BE  position Y
+12:  f32 BE  position Z
+16:  f32 BE  unknown shader param A (range [-2, +2])
+20:  f32 BE  unknown shader param B (range [-2, +2])
+24:  f32 BE  texel density / mipmap LOD bias (geometric /4 series)
+28:  u8[4]   ARGB vertex color
+32:  f32 BE  unknown shader param D (range [-128, +128])
```

**VTMR cSectorVertex (24 bytes, decoded 2026-02-21):**
```
+0:   u32 LE  position_index (into separate aVertices[] array)
+4:   u8[4]   RGBA vertex color
+8:   f32 LE  texture U (world-space planar projection)
+12:  f32 LE  texture V (world-space planar projection)
+16:  f32 LE  lightmap U (optional)
+20:  f32 LE  lightmap V (optional)
```

**Key discovery**: Bytes [16-27] in SC:Ghost are **NOT normal vectors**. All 48 axis/sign
permutations tested against geometric face normals produce ~90° mean error (random).
Normals must be computed from triangle geometry.

### NOD Model Meshes

The `.nod` binary mesh format (version 0xA) used by Nihilistic Software's engine:

- **Header**: 0x5C bytes with shader count, bone count, vertex groups, mesh groups
- **Vertex types**: 4 types with 32/36/48-byte strides (static and skinned)
- **Index buffer**: u16 triangle strips AND triangle lists per mesh group
- **Mesh groups**: 0x38-byte descriptors with per-group LOD strip/list ranges

See [docs/NOD_FORMAT.md](docs/NOD_FORMAT.md) for the complete specification.

## Credits & Acknowledgments

This project builds on the work of several game preservation researchers:

- **[Ryan Sheffer](https://github.com/rfsheffer)** — Shared the official **Nihilistic Software NodSDK**
  containing `nil.htm` format documentation. This was the Rosetta Stone that enabled
  decoding the VTMR cSectorVertex structure and mapping the v27→v35 format evolution.
  His [nod_nad_to_fbx](https://github.com/rfsheffer/nod_nad_to_fbx) converter provided
  key insights into the NOD/NAD pipeline.

- **[RenolY2](https://github.com/RenolY2)** — [scg-modeldump](https://github.com/RenolY2/scg-modeldump) (MIT) —
  Original NOD format reverse engineering and OBJ converter for SC:Ghost. This project's
  NOD parser is based on their proven `read_nod.py` implementation.

- **[hypov8 (David)](https://github.com/hypov8)** — [Vampire.T.M.R_Noesis_plugin](https://github.com/hypov8/Vampire.T.M.R_Noesis_plugin) —
  VTMR Noesis plugin that established the critical connection between VTMR and SC:Ghost
  as products of the same Nihilistic NOD Engine lineage.

- **[hogsy](https://github.com/hogsy)** — [GhostTools](https://github.com/hogsy/GhostTools) —
  Early SC:Ghost format research. Referenced during initial reverse engineering.

- **Nihilistic Software** (now nStigate Games) — Original engine and game development.
  The NodSDK modding tools for VTMR provided the foundation for all format documentation.

- **Elyan Labs** — NIL level parser, glTF 2.0 converter, VTMR cSectorVertex decode,
  vertex field analysis, Godot 4.3 integration, NSD entity extraction.

- **xemu** project — Xbox emulation that made SC:Ghost research possible.

## Legal

This project distributes **tools only**, never game data. Reverse engineering for
interoperability is protected under DMCA Section 1201(f) and EU Directive 2009/24/EC.

StarCraft: Ghost is a trademark of Blizzard Entertainment. This project is not
affiliated with or endorsed by Blizzard Entertainment or Microsoft.
