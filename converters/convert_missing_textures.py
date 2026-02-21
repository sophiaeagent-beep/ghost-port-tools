#!/usr/bin/env python3
"""
Convert all missing StarCraft: Ghost level textures from DDS to PNG.

Collects material names from all MTL files, finds matching DDS sources
in the extracted game directories, converts them to PNG, and copies
to both data/textures/ and assets/textures/ for Godot.
"""

import os
import re
import sys
from pathlib import Path
from collections import defaultdict

try:
    from PIL import Image
except ImportError:
    print("ERROR: Pillow is required. Install with: pip install Pillow")
    sys.exit(1)


# ── Paths ──────────────────────────────────────────────────────────────────
BASE = Path("/home/scott/Games/xemu/ghost_port/godot_stage")
MTL_DIR = BASE / "data"
DATA_TEX_DIR = BASE / "data" / "textures"
ASSETS_TEX_DIR = BASE / "assets" / "textures"

DDS_SEARCH_DIRS = [
    Path("/home/scott/Games/xemu/ghost_port/godot_stage/assets/models_textured/textures"),
    Path("/home/scott/Games/xemu/starcraft_ghost/3D/Materials"),
    Path("/home/scott/Games/xemu/starcraft_ghost/Materials"),
]

# Materials that get solid-gray placeholders (no real texture)
PLACEHOLDER_MATERIALS = {"initialShadingGroup", "notex", "portal"}

# Materials that are special (video surfaces, sky, shadow) -- use tinted placeholders
SPECIAL_PLACEHOLDERS = {
    "dropship_bink2":   (32, 32, 40, 255),    # Dark blue-gray (video screen)
    "sky":              (100, 140, 180, 255),  # Light blue (sky)
    "ge_shadow":        (0, 0, 0, 128),        # Semi-transparent black (shadow)
}

# Hand-mapped fallbacks: material name -> DDS path for textures where
# automatic matching cannot find the right source
HAND_MAPPED_FALLBACKS = {
    # Borgo glass materials -> env map cubemap DDS (best available)
    "Borgo_131Glass":       "/home/scott/Games/xemu/starcraft_ghost/Materials/Effects/MS_env_Borgo.dds",
    "BorgoB_131Glass":      "/home/scott/Games/xemu/starcraft_ghost/Materials/Effects/MS_env_BorgoB.dds",
    # GE_clearA_gry_gls -> no exact match; use GE_roundA_whit as closest clear panel texture
    "GE_clearA_gry_gls":    "/home/scott/Games/xemu/starcraft_ghost/Materials/Lights/GE_roundA_whit.dds",
    # MS_Cave_Zerg -> use base MS_Cave texture (Zerged variant not separate DDS)
    "MS_Cave_Zerg":         "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/MS_Cave.dds",
    # MS_Ground121b_Distance -> use base MS_Ground121b
    "MS_Ground121b_Distance": "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/MS_Ground121b.dds",
    # MS_Ground_01 through _04 -> use MS_Ground_Plain (generic terrain)
    "MS_Ground_01":         "/home/scott/Games/xemu/starcraft_ghost/Materials/Terrain/MS_Ground_Plain.dds",
    "MS_Ground_02":         "/home/scott/Games/xemu/starcraft_ghost/Materials/Terrain/MS_Ground_Plain_Dark.dds",
    "MS_Ground_03":         "/home/scott/Games/xemu/starcraft_ghost/Materials/Terrain/MS_Ground_Plain_Dark_01.dds",
    "MS_Ground_04":         "/home/scott/Games/xemu/starcraft_ghost/Materials/Terrain/MS_Ground_Base_03.dds",
    # MS_Ground_Zerg_01 -> zerged creep terrain
    "MS_Ground_Zerg_01":    "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/MS_1_2_1_ZergCreep.dds",
    # MS_Mountain_Zerg_01 -> mountain with zerg overlay, use base mountain
    "MS_Mountain_Zerg_01":  "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/MS_Mountain_01.dds",
    # MS_Mountainshape_01 -> mountain terrain shape
    "MS_Mountainshape_01":  "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/MS_Mountain_01.dds",
    # MS_waterPipes -> use pipe fitting texture
    "MS_waterPipes":        "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/GE_pipeFitting_01.dds",
    "MS_waterPipes_02":     "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/GE_pipes_01.dds",
    # V5_grate_01_EtchedMask -> use base V5_Grate_01
    "V5_grate_01_EtchedMask": "/home/scott/Games/xemu/starcraft_ghost/3D/Materials/V5_Grate_01.dds",
}

# Suffixes to strip for fallback matching
STRIP_SUFFIXES = [
    "_fullbright", "_full", "_gloss", "_glossmap", "_glo", "_mask",
    "_ZergedClose", "_Zerged",
]

# Variant suffixes: try replacing _01a/_01b with _01
VARIANT_RE = re.compile(r'_(\d+)[a-z]$', re.IGNORECASE)


def collect_material_names():
    """Parse all MTL files and return set of material names."""
    materials = set()
    for mtl_file in sorted(MTL_DIR.glob("*.mtl")):
        with open(mtl_file, "r") as f:
            for line in f:
                line = line.strip()
                if line.startswith("newmtl "):
                    mat_name = line[7:].strip()
                    if mat_name:
                        materials.add(mat_name)
    return materials


def get_existing_pngs(directory):
    """Return set of base names (without extension) for existing PNGs."""
    names = set()
    if directory.exists():
        for png in directory.glob("*.png"):
            names.add(png.stem)
    return names


def build_dds_index():
    """
    Build a case-insensitive index: lowercase_stem -> list of full paths.
    Scans all DDS search directories recursively.
    """
    index = defaultdict(list)
    for search_dir in DDS_SEARCH_DIRS:
        if not search_dir.exists():
            print(f"  WARNING: Search dir not found: {search_dir}")
            continue
        for dds_file in search_dir.rglob("*.dds"):
            stem_lower = dds_file.stem.lower()
            index[stem_lower].append(dds_file)
        # Also check for .DDS extension
        for dds_file in search_dir.rglob("*.DDS"):
            stem_lower = dds_file.stem.lower()
            if dds_file not in index[stem_lower]:
                index[stem_lower].append(dds_file)
    return index


def find_dds_for_material(mat_name, dds_index):
    """
    Try to find a DDS file matching the material name.
    Returns the Path to the DDS file, or None if not found.

    Search strategy:
    1. Exact case-insensitive match
    2. Strip known suffixes (_full, _fullbright, _gloss, etc.)
    3. Replace variant suffixes (_01a -> _01)
    """
    # Strategy 1: exact match (case-insensitive)
    key = mat_name.lower()
    if key in dds_index:
        return dds_index[key][0], "exact"

    # Strategy 2: strip known suffixes
    for suffix in STRIP_SUFFIXES:
        if key.endswith(suffix.lower()):
            base = key[:-len(suffix)]
            if base in dds_index:
                return dds_index[base][0], f"strip_{suffix}"

    # Strategy 3: variant replacement (_01a -> _01, etc.)
    m = VARIANT_RE.search(key)
    if m:
        base_variant = key[:m.start()] + "_" + m.group(1)
        if base_variant in dds_index:
            return dds_index[base_variant][0], "variant"

    # Strategy 4: try adding common prefixes/lowering further
    # Some materials like "ms_cave" might match "MS_Cave" in the index
    # Already handled by case-insensitive index, but try without underscores

    return None, "not_found"


def convert_dds_to_png(dds_path, png_path):
    """Convert a DDS file to PNG using Pillow."""
    try:
        img = Image.open(dds_path)
        img.save(png_path, "PNG")
        return True
    except Exception as e:
        print(f"    ERROR converting {dds_path}: {e}")
        return False


def create_placeholder_png(png_path, size=64, color=(128, 128, 128, 255)):
    """Create a solid gray placeholder PNG."""
    try:
        img = Image.new("RGBA", (size, size), color)
        img.save(png_path, "PNG")
        return True
    except Exception as e:
        print(f"    ERROR creating placeholder {png_path}: {e}")
        return False


def main():
    print("=" * 70)
    print("StarCraft: Ghost Missing Texture Converter")
    print("=" * 70)

    # Ensure output directories exist
    DATA_TEX_DIR.mkdir(parents=True, exist_ok=True)
    ASSETS_TEX_DIR.mkdir(parents=True, exist_ok=True)

    # Step 1: Collect all material names from MTL files
    print("\n[1/5] Collecting material names from MTL files...")
    materials = collect_material_names()
    print(f"  Found {len(materials)} unique materials across all MTL files")

    # Step 2: Check which PNGs already exist
    print("\n[2/5] Checking existing PNGs...")
    existing_data = get_existing_pngs(DATA_TEX_DIR)
    existing_assets = get_existing_pngs(ASSETS_TEX_DIR)
    print(f"  data/textures/: {len(existing_data)} PNGs")
    print(f"  assets/textures/: {len(existing_assets)} PNGs")

    # Determine what's missing (case-insensitive check)
    existing_lower = {n.lower() for n in existing_data}
    missing = []
    already_have = []
    for mat in sorted(materials):
        if mat.lower() in existing_lower:
            already_have.append(mat)
        else:
            missing.append(mat)

    print(f"\n  Already have PNG: {len(already_have)}")
    print(f"  Missing PNG:     {len(missing)}")

    if not missing:
        print("\nAll textures already converted! Nothing to do.")
        return

    # Step 3: Build DDS index
    print("\n[3/5] Building DDS file index from search directories...")
    dds_index = build_dds_index()
    print(f"  Indexed {sum(len(v) for v in dds_index.values())} DDS files ({len(dds_index)} unique names)")

    # Step 4: Find and convert
    print(f"\n[4/5] Finding and converting {len(missing)} missing textures...")
    converted = 0
    placeholders_created = 0
    still_missing = []

    for mat_name in sorted(missing):
        png_data = DATA_TEX_DIR / f"{mat_name}.png"
        png_assets = ASSETS_TEX_DIR / f"{mat_name}.png"

        # Check if it's a placeholder material (initialShadingGroup, notex, portal)
        if mat_name in PLACEHOLDER_MATERIALS:
            if create_placeholder_png(png_data):
                placeholders_created += 1
                print(f"  [PLACEHOLDER] {mat_name} -> 64x64 gray")
                if not png_assets.exists():
                    create_placeholder_png(png_assets)
            continue

        # Check if it's a special placeholder (video, sky, shadow)
        if mat_name in SPECIAL_PLACEHOLDERS:
            color = SPECIAL_PLACEHOLDERS[mat_name]
            if create_placeholder_png(png_data, size=64, color=color):
                placeholders_created += 1
                print(f"  [SPECIAL] {mat_name} -> 64x64 tinted placeholder")
                if not png_assets.exists():
                    create_placeholder_png(png_assets, size=64, color=color)
            continue

        # Check hand-mapped fallbacks first
        if mat_name in HAND_MAPPED_FALLBACKS:
            fallback_path = Path(HAND_MAPPED_FALLBACKS[mat_name])
            if fallback_path.exists():
                if convert_dds_to_png(fallback_path, png_data):
                    converted += 1
                    size_kb = png_data.stat().st_size / 1024
                    print(f"  [MAPPED] {mat_name} <- {fallback_path.name} (hand-mapped) [{size_kb:.0f}KB]")
                    if not png_assets.exists():
                        convert_dds_to_png(fallback_path, png_assets)
                    continue
                else:
                    print(f"  [WARN] Hand-mapped fallback failed for {mat_name}")

        # Try automatic DDS matching
        dds_path, strategy = find_dds_for_material(mat_name, dds_index)

        if dds_path is None:
            still_missing.append(mat_name)
            print(f"  [MISSING] {mat_name} -- no DDS source found")
            continue

        # Convert DDS -> PNG
        if convert_dds_to_png(dds_path, png_data):
            converted += 1
            size_kb = png_data.stat().st_size / 1024
            print(f"  [OK] {mat_name} <- {dds_path.name} ({strategy}) [{size_kb:.0f}KB]")

            # Also copy/convert to assets/textures/
            if not png_assets.exists():
                convert_dds_to_png(dds_path, png_assets)
        else:
            still_missing.append(mat_name)

    # Step 5: Summary
    print("\n" + "=" * 70)
    print("[5/5] SUMMARY")
    print("=" * 70)
    print(f"  Total materials in MTL files:  {len(materials)}")
    print(f"  Already had PNG:               {len(already_have)}")
    print(f"  Newly converted from DDS:      {converted}")
    print(f"  Placeholders created:          {placeholders_created}")
    print(f"  Still missing (no source):     {len(still_missing)}")
    print(f"  Total coverage:                {len(already_have) + converted + placeholders_created}/{len(materials)}")

    if still_missing:
        print(f"\n  Still missing textures ({len(still_missing)}):")
        for m in still_missing:
            print(f"    - {m}")

    # Final counts
    final_data = len(list(DATA_TEX_DIR.glob("*.png")))
    final_assets = len(list(ASSETS_TEX_DIR.glob("*.png")))
    print(f"\n  Final PNG counts:")
    print(f"    data/textures/:   {final_data}")
    print(f"    assets/textures/: {final_assets}")


if __name__ == "__main__":
    main()
