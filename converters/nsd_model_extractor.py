#!/usr/bin/env python3
"""
NSD Model Reference Extractor for StarCraft: Ghost (Xbox)

Parses the NSD (Nihilistic Scene Definition) binary to extract per-entity
model references that the original JSON export missed. Outputs enhanced JSON
with model_ref fields for use by the Godot level loader.

NSD Binary Format (version 16):
  - Header: "NSD\x10" magic (4 bytes)
  - Entity count at offset 0x50 (u32 LE)
  - Entity records at offset 0x54+, each with:
    - u32 sub_record_count
    - u32 flags (always 0)
    - char[32] name (null-terminated)
    - Variable-length sub-record data
  - Sub-records contain type tags (u32 LE):
    - Type 4 (0x04): Model reference (.nod filename or model name)
    - Type 2 (0x02): Position (3 floats)
    - Type 1 (0x01): Rotation (1 float)
    - Type 3 (0x03): Additional transform data
"""

import struct
import json
import os
import sys

# Class-level model overrides (entity type name → model filename)
# Used when entity NAME directly maps to a model or when NSD binary
# doesn't contain an explicit model reference
CLASS_MODEL_OVERRIDES = {
    # Direct name matches
    "overlord": "overlord.gltf",
    "hackConsole_floor": "hackConsole_floor.gltf",
    "ge_ladder1mx_01": "GE_ladder1Mx_01.gltf",
    "ge_laddermain_01": "GE_ladderMain_01.gltf",

    # Door models (doorMetal01.nag → level-specific bunker door)
    "doorMetal01.nag": "1_2_1_BunkerDoor_01.gltf",

    # Pickup models (entity type → pickup model)
    "powhealth": "PU_Health.gltf",
    "powgrenade": "PU_Grenade.gltf",
    "powgauss_2x": "gaussRifle_1.gltf",
    "ge_footlocker_grn_gauss": "GE_footLocker_grn.gltf",
    "ge_footlocker_grn_health": "GE_footLocker_grn.gltf",
    "ge_footlocker_grn_spider": "GE_footLocker_grn.gltf",

    # Enemy models
    "marinesidekick": "marine1_elite.gltf",
    "marinesidekick_wounded": "marine1_elite.gltf",
    "cM121_OverlordUnload": "overlord.gltf",
}

# Entity types that should NEVER have models (game logic, invisible)
NO_MODEL_TYPES = {
    "ghost", "script_object", "script_target", "trigger", "trigger_region",
    "playerclip", "playerclip_prop_hull", "halo_white_dim",
    "cAIFightPath", "cActorWalk", "cBlastObj", "t_track",
    "firehuge", "firelarge", "ladder_dynamic",
    "hackconsole_floor_activate", "movetrack_hull_activate",
    "propTerrLift.nag",
}


def find_entity_headers(data, known_names):
    """Find all entity record headers in the NSD binary by searching for known names."""
    entities = []
    for name in known_names:
        name_bytes = name.encode('ascii') + b'\x00'
        idx = 0
        while True:
            idx = data.find(name_bytes, idx)
            if idx == -1:
                break
            # Entity header: name at offset+8, preceded by sub_count(u32) + flags(u32)
            entity_start = idx - 8
            if entity_start >= 0:
                sc = struct.unpack_from('<I', data, entity_start)[0]
                fl = struct.unpack_from('<I', data, entity_start + 4)[0]
                if 0 <= sc <= 15 and fl == 0:
                    entities.append((entity_start, sc, name))
            idx += 1
    entities.sort()
    return entities


def extract_model_ref_from_chunk(chunk, entity_name):
    """Extract model reference string from entity sub-record data.

    Looks for:
    1. .nod file references (type 4 sub-records)
    2. Known model name patterns (GE_, AB_, MS_, 121_, 1_2_1_, 222_)
    3. Other uppercase strings that match model naming conventions
    """
    # Strategy 1: Look for .nod references (highest confidence)
    nod_idx = chunk.find(b'.nod')
    if nod_idx != -1:
        start = nod_idx
        while start > 0 and chunk[start - 1] >= 0x20 and chunk[start - 1] < 0x7f:
            start -= 1
        ref = chunk[start:nod_idx + 4].decode('ascii', errors='replace')
        # Strip .nod extension for model matching
        return ref.replace('.nod', '')

    # Strategy 2: Find all printable strings and filter for model names
    strings = []
    s = b''
    for byte in chunk:
        if 0x20 <= byte < 0x7f:
            s += bytes([byte])
        else:
            if len(s) >= 4:
                strings.append(s.decode('ascii'))
            s = b''

    # Known model name prefixes (high confidence)
    model_prefixes = (
        'GE_', 'AB_', 'MS_', '121_', '1_2_1_', '222_',
        'Dropship', 'Tower', 'Cave', 'PU_',
    )

    for s in strings:
        if s == entity_name:
            continue
        if any(s.startswith(pfx) for pfx in model_prefixes):
            return s

    return None


def build_model_index(models_dir):
    """Build case-insensitive index of available glTF/GLB model files."""
    index = {}
    if not os.path.isdir(models_dir):
        return index
    for fn in os.listdir(models_dir):
        if fn.endswith(('.gltf', '.glb')):
            base = fn.rsplit('.', 1)[0]
            index[base.lower()] = fn
    return index


def resolve_model_path(ref, model_index):
    """Resolve a model reference to an actual glTF file path."""
    if not ref:
        return None
    clean = ref.replace('.nod', '')
    if clean.lower() in model_index:
        return model_index[clean.lower()]
    return None


def main():
    nsd_path = sys.argv[1] if len(sys.argv) > 1 else \
        '/home/scott/Games/xemu/starcraft_ghost/Levels/1_2_1_Miners_Bunker.nsd'
    json_path = sys.argv[2] if len(sys.argv) > 2 else \
        '/home/scott/Games/xemu/ghost_port/godot_stage/data/nsd_entities_1_2_1.json'
    models_dir = sys.argv[3] if len(sys.argv) > 3 else \
        '/home/scott/Games/xemu/ghost_port/godot_stage/assets/models_all'
    output_path = json_path.replace('.json', '_enhanced.json')

    # Read NSD binary
    with open(nsd_path, 'rb') as f:
        data = f.read()

    # Read existing JSON
    with open(json_path) as f:
        j = json.load(f)
    json_entities = j['entities']

    # Build model index
    model_index = build_model_index(models_dir)
    print(f"Model index: {len(model_index)} glTF/GLB files")

    # Get unique entity class names
    known_names = set(e['name'] for e in json_entities)

    # Find binary entity headers
    bin_entities = find_entity_headers(data, known_names)
    print(f"Binary entities found: {len(bin_entities)}")

    # Calculate data ranges
    entities_with_range = []
    for i in range(len(bin_entities)):
        pos, sc, name = bin_entities[i]
        next_pos = bin_entities[i + 1][0] if i + 1 < len(bin_entities) else len(data)
        entities_with_range.append((pos, sc, name, next_pos))

    # Group by name for order matching (Nth JSON entity = Nth binary entity of same name)
    bin_by_name = {}
    for pos, sc, name, next_pos in entities_with_range:
        if name not in bin_by_name:
            bin_by_name[name] = []
        bin_by_name[name].append((pos, sc, next_pos))

    json_by_name = {}
    for i, e in enumerate(json_entities):
        name = e['name']
        if name not in json_by_name:
            json_by_name[name] = []
        json_by_name[name].append(i)

    # Extract model references
    stats = {"nsd_ref": 0, "class_override": 0, "no_model": 0, "unresolved": 0, "total_with_model": 0}

    for name in known_names:
        json_indices = json_by_name.get(name, [])
        bin_instances = bin_by_name.get(name, [])

        for idx, ji in enumerate(json_indices):
            entity = json_entities[ji]

            # Skip types that should never have models
            if name in NO_MODEL_TYPES:
                entity['model_ref'] = None
                stats["no_model"] += 1
                continue

            # Try NSD binary extraction first
            model_ref = None
            if idx < len(bin_instances):
                pos, sc, next_pos = bin_instances[idx]
                chunk = data[pos + 40:next_pos]
                model_ref = extract_model_ref_from_chunk(chunk, name)

            if model_ref:
                resolved = resolve_model_path(model_ref, model_index)
                if resolved:
                    entity['model_ref'] = resolved
                    stats["nsd_ref"] += 1
                    stats["total_with_model"] += 1
                    continue
                # NSD ref found but no glTF match — try class override
                entity['model_ref_raw'] = model_ref

            # Try class-level override
            if name in CLASS_MODEL_OVERRIDES:
                override = CLASS_MODEL_OVERRIDES[name]
                base = override.rsplit('.', 1)[0]
                if base.lower() in model_index:
                    entity['model_ref'] = model_index[base.lower()]
                    stats["class_override"] += 1
                    stats["total_with_model"] += 1
                    continue

            # No model found
            entity['model_ref'] = None
            stats["unresolved"] += 1

    # Write enhanced JSON
    j['entities'] = json_entities
    j['model_stats'] = stats
    with open(output_path, 'w') as f:
        json.dump(j, f, indent=2)

    print(f"\nResults:")
    print(f"  NSD binary refs matched: {stats['nsd_ref']}")
    print(f"  Class overrides matched: {stats['class_override']}")
    print(f"  No model needed:         {stats['no_model']}")
    print(f"  Unresolved:              {stats['unresolved']}")
    print(f"  TOTAL WITH MODEL:        {stats['total_with_model']}")
    print(f"\nEnhanced JSON written to: {output_path}")


if __name__ == '__main__':
    main()
