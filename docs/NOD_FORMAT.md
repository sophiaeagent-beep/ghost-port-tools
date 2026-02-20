# NOD Binary Mesh Format Specification

Version 0xA — Nihilistic Software Engine (used in StarCraft: Ghost Xbox, 2004)

This format was also used in Vampire: The Masquerade - Redemption (2000).
Documentation based on reverse engineering by RenolY2 (scg-modeldump) and Elyan Labs.

## File Structure Overview

```
┌─────────────────────────────────────────┐
│ Header (0x5C bytes)                     │
├─────────────────────────────────────────┤
│ Shader Names (shaderCount × 0x20 bytes) │
├─────────────────────────────────────────┤
│ Bone Data (boneCount × 0x40 bytes)      │
├─────────────────────────────────────────┤
│ Vertex Data (per vertex group)          │
├─────────────────────────────────────────┤
│ Index Buffer (indexCount × u16)         │
├─────────────────────────────────────────┤
│ Mesh Groups (meshGroupCount × 0x38)     │
└─────────────────────────────────────────┘
```

## Header (0x5C bytes)

| Offset | Size | Type | Field | Description |
|--------|------|------|-------|-------------|
| 0x00 | 4 | u32 | version | Must be `0x0A` (10) |
| 0x04 | 1 | u8 | shaderCount | Number of shader/material names |
| 0x05 | 1 | u8 | boneCount | Number of bones (skeleton) |
| 0x06 | 1 | u8 | vertGroupCount | Number of vertex groups (1-4) |
| 0x07 | 1 | u8 | meshGroupCount | Number of mesh group descriptors |
| 0x08 | 4 | u32 | flags | Model flags |
| 0x0C | 12 | float[3] | bboxMin | Bounding box minimum (x, y, z) |
| 0x18 | 12 | float[3] | bboxMax | Bounding box maximum (x, y, z) |
| 0x24 | 32 | vtxGroup[4] | vtxGroups | 4 vertex group slots (see below) |
| 0x44 | 4 | u32 | indexCount | Total number of u16 indices |
| 0x48 | 16 | u32[4] | lodStarts | LOD start offsets |
| 0x58 | 1 | u8 | lodCount | Number of LOD levels |
| 0x59 | 3 | — | padding | Padding to 0x5C |

### Vertex Group Slot (8 bytes each, 4 slots at 0x24)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| +0x00 | 1 | u8 | vtxType | Vertex format type (0-3) |
| +0x01 | 3 | — | padding | Always zero |
| +0x04 | 4 | u32 | vtxCount | Number of vertices in this group |

Only the first `vertGroupCount` slots contain valid data.

## Vertex Types

| Type | Stride | Layout | Use Case |
|------|--------|--------|----------|
| 0 | 0x20 (32 bytes) | pos(3f) + normal(3f) + uv(2f) | Static geometry |
| 1 | 0x24 (36 bytes) | pos(3f) + normal(3f) + uv(2f) + 4 unknown | Light skinning |
| 2 | 0x30 (48 bytes) | pos(3f) + normal(3f) + uv(2f) + 16 unknown | Full skinning |
| 3 | 0x20 (32 bytes) | pos(3f) + normal(3f) + uv(2f) | Same as type 0 |

### Vertex Layout (common prefix, all types)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| +0x00 | 12 | float[3] | position (x, y, z) |
| +0x0C | 12 | float[3] | normal (x, y, z) |
| +0x18 | 8 | float[2] | texcoord (u, v) |
| +0x20 | varies | — | Type-specific extra data |

**UV Convention**: V is flipped compared to OpenGL/glTF. Apply `v = 1.0 - v` when
converting to glTF/OBJ.

## Shader Names

Located immediately after the header at offset 0x5C.

Each shader name is a 32-byte (0x20) null-padded ASCII string. These correspond to
material definitions in `.nsa` files and map to texture filenames.

## Bone Data

Each bone is 0x40 (64) bytes:

| Offset | Size | Type | Field |
|--------|------|------|-------|
| +0x00 | 12 | float[3] | restTranslate |
| +0x0C | 36 | — | Unknown data |
| +0x30 | 12 | float[3] | invTranslate |
| +0x3C | 4 | u32 | packed IDs (parentID at bits 16-23, tagID at bits 24-31) |

## Index Buffer

A flat array of `indexCount` unsigned 16-bit integers. These are shared across all
mesh groups — each mesh group specifies its own range within this buffer.

**Important**: Indices are NOT one continuous strip. Each mesh group descriptor
defines which indices belong to it and whether they form strips or lists.

## Mesh Group Descriptor (0x38 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| +0x00 | 4 | u32 | materialId | Index into shader names array |
| +0x04 | 24 | LOD[4] | lods | 4 level-of-detail entries (6 bytes each) |
| +0x1C | 2 | u16 | vertexCount | Number of vertices this mesh uses |
| +0x1E | 1 | u8 | groupFlags | Mesh group flags |
| +0x1F | 1 | u8 | blendShapeCount | Number of blend shapes |
| +0x20 | 1 | u8 | blendGroup | Blend group index |
| +0x21 | 20 | u8[20] | bones | Bone indices for this mesh |
| +0x35 | 1 | u8 | boneCount | Bones used by this mesh |
| +0x36 | 1 | u8 | vtxGroup | Which vertex group (0-3) this mesh reads from |
| +0x37 | 1 | — | padding | |

### LOD Entry (6 bytes)

| Offset | Size | Type | Field |
|--------|------|------|-------|
| +0x00 | 2 | u16 | stripCount | Number of strip indices |
| +0x02 | 2 | u16 | listCount | Number of list indices |
| +0x04 | 2 | u16 | vtxCount | Number of vertices at this LOD |

### Index Offset Accumulation

Index offsets accumulate across mesh groups. For each mesh group:

```
stripStart = accumulated_offset
listStart  = accumulated_offset + stripCount
accumulated_offset += stripCount + listCount
```

This repeats for all 4 LODs within each mesh group, and across all mesh groups.

## Triangle Strip Format

Strip indices use the **Xbox D3DPT_TRIANGLESTRIP** format with a sliding window:

```
For indices [i0, i1, i2, i3, i4, i5, ...]:
  Triangle 0: (i0, i1, i2)    — even: normal winding
  Triangle 1: (i1, i3, i2)    — odd: swapped winding
  Triangle 2: (i2, i3, i4)    — even: normal winding
  ...
```

**Degenerate triangles** (where 2+ indices are identical) act as strip restart
markers and should be skipped. They do NOT reset the winding counter.

## Triangle List Format

List indices are simple groups of 3:

```
For indices [i0, i1, i2, i3, i4, i5, ...]:
  Triangle 0: (i0, i1, i2)
  Triangle 1: (i3, i4, i5)
  ...
```

## Coordinate System

NOD uses a **left-handed Y-up** coordinate system (matching DirectX):
- +X = right
- +Y = up
- +Z = forward (into screen)

For OpenGL/Godot (right-handed Y-up), swap: `out_y = nod_z`, `out_z = -nod_y`

## Material Files (.nsa)

Materials are defined in `.nsa` text files with this format:

```
MaterialName
{
    texture TextureFilename.dds
    param value
    ...
}
```

Comments start with `;` or `//`. Nested braces are allowed but inner content
is currently not parsed by most tools.

## File Statistics (StarCraft: Ghost Xbox)

| Metric | Value |
|--------|-------|
| Total .nod files | ~1,401 |
| Successfully parseable | ~1,391 (99.3%) |
| Total vertices | ~1.98M |
| Total triangles | ~1.6M |
| Vertex types used | 0, 1, 2 (type 3 rare) |
| Typical character model | 3,000-6,000 verts |
| Typical environment piece | 100-2,000 verts |
