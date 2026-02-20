# Ghost Port Tools

Open-source conversion toolkit for **StarCraft: Ghost** (2004 Xbox) game assets.
Converts Nihilistic Software's proprietary NOD engine formats to modern standards
(glTF 2.0, PNG) for use in Godot 4.x or any 3D engine.

**You must supply your own game files.** This repository contains only tools and
documentation — no copyrighted game assets are included or will be accepted in
pull requests.

## What This Does

| Tool | Input | Output | Status |
|------|-------|--------|--------|
| `nod_to_gltf.py` | `.nod` binary mesh | `.gltf` 2.0 | Working (1391/1401 models) |
| `xpr_to_png.py` | `.xpr` Xbox texture | `.png` | Planned |
| `nsa_to_mtl.py` | `.nsa` material def | Material JSON | Planned |
| `adpcm_decode.py` | Xbox ADPCM audio | `.wav` | Planned |

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

## NOD Format

The `.nod` binary mesh format (version 0xA) used by Nihilistic Software's engine:

- **Header**: 0x5C bytes with shader count, bone count, vertex groups, mesh groups
- **Vertex types**: 4 types with 32/36/48-byte strides (static and skinned)
- **Index buffer**: u16 triangle strips AND triangle lists per mesh group
- **Mesh groups**: 0x38-byte descriptors with per-group LOD strip/list ranges

See [docs/NOD_FORMAT.md](docs/NOD_FORMAT.md) for the complete specification.

## Credits

- **RenolY2** — [scg-modeldump](https://github.com/RenolY2/scg-modeldump) (MIT) —
  original NOD format reverse engineering and OBJ converter. This project's parser
  is based on their proven `read_nod.py` implementation.
- **Elyan Labs** — glTF 2.0 converter, Godot integration, format documentation
- **xemu** project — Xbox emulation that made this research possible

## Legal

This project distributes **tools only**, never game data. Reverse engineering for
interoperability is protected under DMCA Section 1201(f) and EU Directive 2009/24/EC.

StarCraft: Ghost is a trademark of Blizzard Entertainment. This project is not
affiliated with or endorsed by Blizzard Entertainment or Microsoft.
