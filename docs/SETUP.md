# Setup Guide

## Prerequisites

- Python 3.8+ (no pip packages needed)
- Godot 4.3+ (for the showcase)
- Your own extracted StarCraft: Ghost Xbox disc image

## Step 1: Extract Game Files

Use your preferred Xbox disc extraction tool to get the game files. You need:

```
starcraft_ghost/
├── 3D/
│   └── Models/          ← .nod mesh files (1400+)
├── Materials/           ← .nsa material definitions
└── Textures/            ← .dds texture files
```

**Do not commit these files to any public repository.**

## Step 2: Convert Models

```bash
cd ghost-port-tools

# Convert all models to glTF
python3 converters/nod_to_gltf.py \
    --source /path/to/starcraft_ghost/3D/Models/ \
    --output ./out/gltf/

# Convert specific models only
python3 converters/nod_to_gltf.py \
    --source /path/to/starcraft_ghost/3D/Models/ \
    --output ./out/gltf/ \
    --filter nova

# Verbose mode (shows skipped files)
python3 converters/nod_to_gltf.py \
    --source /path/to/starcraft_ghost/3D/Models/ \
    --output ./out/gltf/ \
    --verbose
```

Expected output: ~1391/1401 files converted successfully.

## Step 3: Set Up Godot Project

1. Open Godot 4.3+
2. Open the project at `godot_project/`
3. Copy your converted `.gltf` files into `godot_project/assets/models/`
4. Godot will auto-import them
5. Run the GhostShowcase scene

## Step 4: Verify

The showcase has 3 scenes (press Space/Right arrow to cycle):

1. **Nova vs Zerg** — Nova with rifle facing zerglings and hydralisk
2. **Terran Arsenal** — Marines, firebats, siege tanks, goliath, wraith
3. **Full Lineup** — All loaded models in a circle

Press R to toggle camera rotation, Up/Down to adjust camera height.

## Troubleshooting

**Models show as gray cubes**: The .gltf file failed to import. Check Godot's
Output panel for error messages. Most common cause: file was corrupted during copy.

**Models look distorted**: Make sure you're using the v2 converter from this repo,
not an older version. The v2 converter correctly handles per-mesh-group strip and
list index ranges.

**Only some models convert**: ~10 files in the SC:Ghost disc have non-standard
headers (version != 0xA) and are intentionally skipped.
