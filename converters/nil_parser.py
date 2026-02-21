#!/usr/bin/env python3
"""
NIL Level Parser for StarCraft: Ghost Xbox assets.

Reverse-engineered from the leaked 2003 Xbox build of Nihilistic Software's
NOD engine. Parses .NIL binary level files and extracts geometry as
triangulated meshes with per-submesh material/bounding-box data.

== NIL Binary Format ==

Header (0x60 bytes):
  0x00: u8[4]   magic "NIL\x10"
  0x04: u32 LE  section_count (e.g. 35)
  0x08: u32 LE  flags
  0x0C: u32 LE  sub_count
  0x10: u32 LE  reserved
  0x14: float[3] bounding dimensions
  0x20: float[4] orientation
  0x30: u8[16]  padding
  0x50: float[3] unknown
  0x5C: u32 LE  material_name_count

Material Table:
  material_count x 0x20 bytes (null-padded ASCII shader names)

Level Geometry — Per-Section Layout:
  Section Descriptor (5 bytes):
    [0]   u8   0x01 (marker)
    [1]   u8   0x00
    [2-3] u16 LE vertex_count
    [4]   u8   0x00

  Vertex Data (vertex_count x 36 bytes, BIG ENDIAN positions):
    +0:   u16 LE UV U coordinate (compressed: value / 65535 -> [0,1])
    +2:   u16 LE UV V coordinate (compressed: 1.0 - value/65535, DX->GL V-flip)
    +4:   f32 BE position X
    +8:   f32 BE position Y
    +12:  f32 BE position Z
    +16:  f32 BE normal component NX
    +20:  u8[4]  packed tangent/normal data
    +24:  f32 BE normal component NZ
    +28:  u8[4]  ARGB color (byte 0 = alpha, always 0xFF)
    +32:  f32 BE additional vertex attribute

  Post-Vertex Submesh Data:
    +0:   u8   type_byte (0x3D-0xC0 range observed)
    +1:   u8   submesh_count (N)
    +2:   u8   pad (always 0x00)
    +3:   N x 47-byte submesh records (see below)
    +3+N*47: u32 LE total_index_count (= sum of all submesh strip+list)
    +3+N*47+4: u32 LE[total_index_count] index buffer

  47-Byte Submesh Record:
    [0-1]   u16 LE  vertex_base / material_id
    [2-3]   u16 LE  reserved (always 0)
    [4-7]   u32 LE  cumulative_index_offset
    [8-11]  4 bytes reserved (zeros)
    [12-13] u16 LE  strip_index_count
    [14-15] u16 LE  list_index_count
    [16-22] 7 bytes reserved (zeros)
    [23-46] 6 x f32 LE bounding box (min_x, min_y, min_z, max_x, max_y, max_z)

  After index buffer: portal/sector data, then next section descriptor.

== Mixed Endianness ==
  The NIL format uses MIXED endianness — a consequence of the NOD engine
  originating on PC (Vampire: The Masquerade - Redemption, 2000) and being
  ported to GameCube (PowerPC big-endian). Vertex position floats are
  BIG ENDIAN (GameCube origin), while all header fields, indices, submesh
  records, and bounding boxes are LITTLE ENDIAN (Xbox x86).

== Version History ==
  VTMR (2000 PC): NOD file version 7, NIL version ~33 (all LE)
  SC:Ghost (2003 Xbox): NOD file version 10 (0x0A), NIL version 35 (mixed)
  The version difference means sector loading code changed between versions.

Usage:
  python3 nil_parser.py --input path/to/level.nil --output level.obj
  python3 nil_parser.py --input path/to/level.nil --output level.json
  python3 nil_parser.py --input path/to/level.nil --output level.gltf
  python3 nil_parser.py --input path/to/level.nil --stats
"""
from __future__ import annotations

import argparse
import base64
import json
import math
import os
import struct
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VERTEX_STRIDE = 36          # bytes per vertex
SUBMESH_RECORD_SIZE = 47    # bytes per submesh record
SECTION_MARKER = 0x01       # first byte of section descriptor
MAX_COORD = 600.0           # max plausible coordinate value
MAX_VERTS_PER_SECTION = 50000  # raised from 10000 to include large sections
MAX_EDGE_LENGTH = 80.0      # degenerate triangle filter
MIN_TRIANGLE_AREA = 0.01    # degenerate triangle filter


# ---------------------------------------------------------------------------
# Binary helpers
# ---------------------------------------------------------------------------

def read_u8(data: bytes, off: int) -> int:
    return data[off]

def read_u16(data: bytes, off: int) -> int:
    return struct.unpack_from('<H', data, off)[0]

def read_u32(data: bytes, off: int) -> int:
    return struct.unpack_from('<I', data, off)[0]

def read_f32_le(data: bytes, off: int) -> float:
    return struct.unpack_from('<f', data, off)[0]

def read_f32_be(data: bytes, off: int) -> float:
    return struct.unpack_from('>f', data, off)[0]


# ---------------------------------------------------------------------------
# Vertex validation and extraction
# ---------------------------------------------------------------------------

def _is_valid_vertex_be(data: bytes, off: int) -> bool:
    """Check if 36 bytes at off form a valid NIL vertex (BE floats)."""
    if off + VERTEX_STRIDE > len(data):
        return False
    if data[off + 28] != 0xFF:
        return False
    try:
        px = struct.unpack_from('>f', data, off + 4)[0]
        py = struct.unpack_from('>f', data, off + 8)[0]
        pz = struct.unpack_from('>f', data, off + 12)[0]
        if not (math.isfinite(px) and math.isfinite(py) and math.isfinite(pz)):
            return False
        return abs(px) < MAX_COORD and abs(py) < MAX_COORD and abs(pz) < MAX_COORD
    except struct.error:
        return False


def extract_vertices(data: bytes, offset: int, count: int) -> list[dict]:
    """Extract vertex data from a section using BIG ENDIAN floats.

    Vertex layout (36 bytes):
      +0:  u16 LE   UV U (compressed: value / 65535)
      +2:  u16 LE   UV V (compressed: value / 65535)
      +4:  f32 BE   position X
      +8:  f32 BE   position Y
      +12: f32 BE   position Z
      +16: f32 BE   normal X
      +24: f32 BE   normal Z
      +28: u8[4]    ARGB color (A=0xFF)
    """
    vertices = []
    for i in range(count):
        off = offset + i * VERTEX_STRIDE
        if off + VERTEX_STRIDE > len(data):
            break

        # UV coordinates: u16 LE compressed [0, 65535] -> [0.0, 1.0]
        # V-flip needed: DirectX (Xbox) has V=0 at top, OpenGL/OBJ/Godot has V=0 at bottom
        u = read_u16(data, off) / 65535.0
        v = 1.0 - read_u16(data, off + 2) / 65535.0

        px = read_f32_be(data, off + 4)
        py = read_f32_be(data, off + 8)
        pz = read_f32_be(data, off + 12)

        nx = read_f32_be(data, off + 16)
        nz = read_f32_be(data, off + 24)
        remainder = 1.0 - nx * nx - nz * nz
        ny = math.sqrt(max(0.0, remainder))

        a = data[off + 28]
        r = data[off + 29]
        g = data[off + 30]
        b = data[off + 31]

        vertices.append({
            'position': [px, py, pz],
            'normal': [nx, ny, nz],
            'color': [r, g, b, a],
            'uv': [u, v],
        })

    return vertices


# ---------------------------------------------------------------------------
# Section scanning — find all geometry sections in the NIL binary
# ---------------------------------------------------------------------------

def find_sections(data: bytes, start_offset: int) -> list[dict]:
    """Scan for section descriptors: 01 00 [u16 LE vertex_count] 00.

    Validates each candidate by checking that the vertex data at the
    expected positions contains valid BE float vertices (0xFF at +28).

    Returns list of section dicts with offset, vertex count, and raw
    post-vertex gap data for submesh parsing.
    """
    sections = []
    pos = start_offset
    data_len = len(data)

    while pos < data_len - 36:
        # Look for section marker pattern: 01 00 XX XX 00
        if data[pos] != SECTION_MARKER or data[pos + 1] != 0x00:
            pos += 1
            continue

        if pos + 5 > data_len:
            break

        if data[pos + 4] != 0x00:
            pos += 1
            continue

        vc = read_u16(data, pos + 2)
        if vc < 3 or vc > MAX_VERTS_PER_SECTION:
            pos += 1
            continue

        # Validate: first and second vertex should have 0xFF at +28
        vert_start = pos + 5
        vert_end = vert_start + vc * VERTEX_STRIDE
        if vert_end > data_len:
            pos += 1
            continue

        if not _is_valid_vertex_be(data, vert_start):
            pos += 1
            continue
        # Check second vertex too (at stride offset)
        if vc >= 2 and not _is_valid_vertex_be(data, vert_start + VERTEX_STRIDE):
            pos += 1
            continue

        # Valid section found — now parse post-vertex submesh data
        section = _parse_section_submeshes(data, pos, vert_start, vert_end, vc)
        if section is not None:
            sections.append(section)
            # Jump past this section's data
            pos = section['section_end']
        else:
            pos += 1

    return sections


def _parse_section_submeshes(data: bytes, desc_offset: int,
                              vert_start: int, vert_end: int,
                              vc: int) -> dict | None:
    """Parse the post-vertex submesh records and index buffer for one section.

    Layout after vertex data:
      +0: u8 type_byte
      +1: u8 submesh_count (N)
      +2: u8 pad (0)
      +3: N x 47-byte records
      +3+N*47: u32 total_index_count (u32 for large sections, backwards-compatible)
      +3+N*47+4: index buffer (total_index_count x u16 LE)
    """
    gap_off = vert_end
    if gap_off + 3 > len(data):
        return None

    stype = data[gap_off]
    scount = data[gap_off + 1]
    pad = data[gap_off + 2]

    # Sanity checks
    if scount < 1 or scount > 64:
        return None
    if pad != 0:
        return None

    rec_end = gap_off + 3 + scount * SUBMESH_RECORD_SIZE
    if rec_end + 4 > len(data):
        return None

    # Read total index count — u32 for large sections (>65535 indices),
    # backwards-compatible since small sections have 0x0000 in high word
    total_idx = read_u32(data, rec_end)

    # Validate: sum of all submesh strip+list counts should equal total_idx
    submeshes = []
    calc_sum = 0
    for r in range(scount):
        roff = gap_off + 3 + r * SUBMESH_RECORD_SIZE
        if roff + SUBMESH_RECORD_SIZE > len(data):
            return None

        vertex_base = read_u16(data, roff)
        reserved1 = read_u16(data, roff + 2)
        # Cumulative offset is u32 at [4:8] — high word at [6:7] is nonzero
        # in large sections (>65535 indices). Backwards-compatible.
        cum_offset = read_u32(data, roff + 4)
        strip_count = read_u16(data, roff + 12)
        list_count = read_u16(data, roff + 14)

        # Read bounding box (6 x f32 LE at byte 23)
        bb_off = roff + 23
        if bb_off + 24 > len(data):
            return None
        bb_min = [read_f32_le(data, bb_off + i * 4) for i in range(3)]
        bb_max = [read_f32_le(data, bb_off + 12 + i * 4) for i in range(3)]

        # Validate bounding box values are plausible
        bb_valid = all(
            math.isfinite(v) and abs(v) < MAX_COORD
            for v in bb_min + bb_max
        )

        submeshes.append({
            'vertex_base': vertex_base,
            'cum_offset': cum_offset,
            'strip_count': strip_count,
            'list_count': list_count,
            'bbox_min': bb_min if bb_valid else None,
            'bbox_max': bb_max if bb_valid else None,
        })

        calc_sum += strip_count + list_count

    # Validation: calculated sum must match stored total
    if calc_sum != total_idx:
        return None

    # Validate cumulative offsets are consistent
    running = 0
    for sm in submeshes:
        if sm['cum_offset'] != running:
            return None
        running += sm['strip_count'] + sm['list_count']

    # Read index buffer (starts after u32 total_index_count)
    idx_start = rec_end + 4
    idx_end = idx_start + total_idx * 2
    if idx_end > len(data):
        return None

    indices = []
    for i in range(total_idx):
        idx = read_u16(data, idx_start + i * 2)
        if idx >= vc:
            return None  # Index out of range
        indices.append(idx)

    return {
        'desc_offset': desc_offset,
        'vert_start': vert_start,
        'vert_count': vc,
        'type_byte': stype,
        'submesh_count': scount,
        'submeshes': submeshes,
        'total_index_count': total_idx,
        'indices': indices,
        'section_end': idx_end,
    }


# ---------------------------------------------------------------------------
# Triangle extraction from strips and lists
# ---------------------------------------------------------------------------

def _triangle_area(v0, v1, v2):
    """Compute triangle area from 3 position arrays."""
    ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def _edge_length(v0, v1):
    """Distance between two position arrays."""
    dx = v1[0] - v0[0]
    dy = v1[1] - v0[1]
    dz = v1[2] - v0[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def triangulate_strip(indices: list[int], vertices: list[dict],
                      filter_degenerate: bool = True) -> list[tuple[int, int, int]]:
    """Convert triangle strip indices to triangle list.

    Strip winding alternates: even triangles use (i0, i1, i2),
    odd triangles use (i0, i2, i1) for consistent face normals.
    Degenerate triangles (repeated indices) are strip restarts.
    """
    triangles = []
    for i in range(len(indices) - 2):
        i0, i1, i2 = indices[i], indices[i + 1], indices[i + 2]

        # Skip degenerate (strip restart)
        if i0 == i1 or i1 == i2 or i0 == i2:
            continue

        # Alternate winding
        if i % 2 == 0:
            tri = (i0, i1, i2)
        else:
            tri = (i0, i2, i1)

        if filter_degenerate and vertices:
            p0 = vertices[tri[0]]['position']
            p1 = vertices[tri[1]]['position']
            p2 = vertices[tri[2]]['position']
            # Filter long-edge or zero-area triangles
            if (_edge_length(p0, p1) > MAX_EDGE_LENGTH or
                _edge_length(p1, p2) > MAX_EDGE_LENGTH or
                _edge_length(p0, p2) > MAX_EDGE_LENGTH):
                continue
            if _triangle_area(p0, p1, p2) < MIN_TRIANGLE_AREA:
                continue

        triangles.append(tri)

    return triangles


def triangulate_list(indices: list[int], vertices: list[dict],
                     filter_degenerate: bool = True) -> list[tuple[int, int, int]]:
    """Convert triangle list indices to triangle tuples.

    Every 3 consecutive indices form one triangle.
    """
    triangles = []
    for i in range(len(indices) // 3):
        i0 = indices[i * 3]
        i1 = indices[i * 3 + 1]
        i2 = indices[i * 3 + 2]

        if i0 == i1 or i1 == i2 or i0 == i2:
            continue

        if filter_degenerate and vertices:
            p0 = vertices[i0]['position']
            p1 = vertices[i1]['position']
            p2 = vertices[i2]['position']
            if (_edge_length(p0, p1) > MAX_EDGE_LENGTH or
                _edge_length(p1, p2) > MAX_EDGE_LENGTH or
                _edge_length(p0, p2) > MAX_EDGE_LENGTH):
                continue
            if _triangle_area(p0, p1, p2) < MIN_TRIANGLE_AREA:
                continue

        triangles.append((i0, i1, i2))

    return triangles


# ---------------------------------------------------------------------------
# Coordinate transform: DirectX (LH, Y-up) -> Godot (RH, Y-up)
# ---------------------------------------------------------------------------

def dx_to_godot_position(pos: list[float]) -> list[float]:
    """Negate Z axis for right-handed conversion."""
    return [pos[0], pos[1], -pos[2]]


def dx_to_godot_normal(n: list[float]) -> list[float]:
    """Negate Z axis for normal vectors."""
    return [n[0], n[1], -n[2]]


# ---------------------------------------------------------------------------
# NIL header parsing
# ---------------------------------------------------------------------------

def parse_nil_header(data: bytes) -> dict | None:
    """Parse the NIL file header and material names."""
    if len(data) < 0x60:
        return None

    magic = data[0:4]
    if magic != b'NIL\x10':
        return None

    section_count = read_u32(data, 0x04)
    flags = read_u32(data, 0x08)
    sub_count = read_u32(data, 0x0C)

    mat_count = read_u32(data, 0x5C)
    if mat_count > 200:
        return None

    materials = []
    off = 0x60
    for i in range(mat_count):
        if off + 0x20 > len(data):
            break
        raw = data[off:off + 0x20]
        name = raw.split(b'\x00')[0].decode('ascii', errors='ignore')
        materials.append(name)
        off += 0x20

    return {
        'magic': magic.decode('ascii', errors='ignore'),
        'section_count': section_count,
        'flags': flags,
        'sub_count': sub_count,
        'material_count': mat_count,
        'materials': materials,
        'data_start': off,
    }


# ---------------------------------------------------------------------------
# Full NIL parse
# ---------------------------------------------------------------------------

def parse_nil(data: bytes, godot_coords: bool = True,
              filter_degenerate: bool = True) -> dict | None:
    """Parse a complete NIL file into structured mesh data.

    Uses deterministic section-based scanning:
    1. Parse header and material table
    2. Scan for section descriptors (01 00 [u16 vc] 00)
    3. For each section, parse submesh records and index buffer
    4. Triangulate strips and lists per submesh
    5. Apply coordinate transform and degenerate filtering
    """
    header = parse_nil_header(data)
    if header is None:
        return None

    materials = header['materials']

    # Find all valid sections
    sections = find_sections(data, header['data_start'])
    if not sections:
        return None

    mesh_groups = []
    total_verts = 0
    total_tris = 0

    for si, section in enumerate(sections):
        vertices = extract_vertices(data, section['vert_start'],
                                    section['vert_count'])

        if godot_coords:
            for v in vertices:
                v['position'] = dx_to_godot_position(v['position'])
                v['normal'] = dx_to_godot_normal(v['normal'])

        total_verts += len(vertices)  # Count once per section, not per submesh

        # Process each submesh within this section
        all_indices = section['indices']

        for smi, sm in enumerate(section['submeshes']):
            strip_start = sm['cum_offset']
            strip_end = strip_start + sm['strip_count']
            list_start = strip_end
            list_end = list_start + sm['list_count']

            strip_indices = all_indices[strip_start:strip_end]
            list_indices = all_indices[list_start:list_end]

            # Triangulate both strips and lists
            triangles = []
            if strip_indices:
                triangles.extend(triangulate_strip(
                    strip_indices, vertices, filter_degenerate))
            if list_indices:
                triangles.extend(triangulate_list(
                    list_indices, vertices, filter_degenerate))

            if not triangles:
                continue

            # Flatten triangle tuples to index list
            flat_indices = []
            for t in triangles:
                flat_indices.extend(t)

            # Build mesh group
            mg = _build_mesh_group(
                group_id=len(mesh_groups),
                section_id=si,
                submesh_id=smi,
                vertices=vertices,
                indices=flat_indices,
                section=section,
                submesh=sm,
                materials=materials,
            )
            mesh_groups.append(mg)
            total_tris += len(triangles)

    if not mesh_groups:
        return None

    # Compute level bounding box
    all_min = [
        min(mg['bbox_min'][i] for mg in mesh_groups)
        for i in range(3)
    ]
    all_max = [
        max(mg['bbox_max'][i] for mg in mesh_groups)
        for i in range(3)
    ]

    return {
        'header': {
            'magic': header['magic'],
            'section_count': header['section_count'],
            'material_count': header['material_count'],
            'materials': header['materials'],
        },
        'stats': {
            'sections_found': len(sections),
            'mesh_groups': len(mesh_groups),
            'total_vertices': total_verts,
            'total_triangles': total_tris,
            'bbox_min': all_min,
            'bbox_max': all_max,
        },
        'mesh_groups': mesh_groups,
    }


def _build_mesh_group(group_id: int, section_id: int, submesh_id: int,
                      vertices: list[dict], indices: list[int],
                      section: dict, submesh: dict,
                      materials: list[str]) -> dict:
    """Build a mesh group dict from vertices and indices."""
    used = set(indices)
    xs = [vertices[i]['position'][0] for i in used if i < len(vertices)]
    ys = [vertices[i]['position'][1] for i in used if i < len(vertices)]
    zs = [vertices[i]['position'][2] for i in used if i < len(vertices)]

    if not xs:
        xs = ys = zs = [0]

    # Use submesh bounding box if available, else compute from vertices
    if submesh.get('bbox_min') and submesh.get('bbox_max'):
        bbox_min = submesh['bbox_min']
        bbox_max = submesh['bbox_max']
    else:
        bbox_min = [min(xs), min(ys), min(zs)]
        bbox_max = [max(xs), max(ys), max(zs)]

    # Try to assign material name from vertex_base
    mat_idx = submesh.get('vertex_base', 0)
    material_name = None
    if mat_idx < len(materials):
        material_name = materials[mat_idx]

    # Dominant vertex color
    color_counts = {}
    for v in vertices:
        c = tuple(v['color'][:3])
        color_counts[c] = color_counts.get(c, 0) + 1
    dominant_color = max(color_counts, key=color_counts.get) \
        if color_counts else (128, 128, 128)

    return {
        'id': group_id,
        'section_id': section_id,
        'submesh_id': submesh_id,
        'vertex_count': len(vertices),
        'triangle_count': len(indices) // 3,
        'vertices': vertices,
        'indices': indices,
        'bbox_min': bbox_min,
        'bbox_max': bbox_max,
        'dominant_color': list(dominant_color),
        'material_name': material_name,
        'material_idx': mat_idx,
        'strip_count': submesh['strip_count'],
        'list_count': submesh['list_count'],
        'offset': f'0x{section["desc_offset"]:X}',
        'type_byte': f'0x{section["type_byte"]:02X}',
    }


# ---------------------------------------------------------------------------
# OBJ output
# ---------------------------------------------------------------------------

def export_obj(parsed: dict, output_path: Path):
    """Export parsed NIL as Wavefront OBJ."""
    lines = [
        f"# StarCraft Ghost NIL level - {output_path.stem}",
        f"# {parsed['stats']['total_vertices']:,} vertices, "
        f"{parsed['stats']['total_triangles']:,} triangles",
        f"# {parsed['stats']['sections_found']} sections, "
        f"{parsed['stats']['mesh_groups']} submeshes",
        "",
    ]

    # Write MTL reference if we have materials
    mtl_path = output_path.with_suffix('.mtl')
    if parsed['header']['materials']:
        lines.append(f"mtllib {mtl_path.name}")
        lines.append("")

    vtx_offset = 0
    for mg in parsed['mesh_groups']:
        mat = mg.get('material_name')
        name = mat or f"Section{mg['section_id']}_Sub{mg['submesh_id']}"
        lines.append(f"o {name}")
        if mat:
            lines.append(f"usemtl {mat}")
        lines.append(f"# Section {mg['section_id']}, submesh {mg['submesh_id']}"
                      f" | {mg['vertex_count']} verts, {mg['triangle_count']} tris"
                      f" | strip={mg['strip_count']} list={mg['list_count']}"
                      f" | type={mg['type_byte']}")

        for v in mg['vertices']:
            p = v['position']
            lines.append(f"v {p[0]:.6f} {p[1]:.6f} {p[2]:.6f}")

        for v in mg['vertices']:
            n = v['normal']
            lines.append(f"vn {n[0]:.6f} {n[1]:.6f} {n[2]:.6f}")

        for v in mg['vertices']:
            uv = v['uv']
            lines.append(f"vt {uv[0]:.6f} {uv[1]:.6f}")

        indices = mg['indices']
        for i in range(0, len(indices), 3):
            i0 = indices[i] + vtx_offset + 1
            i1 = indices[i + 1] + vtx_offset + 1
            i2 = indices[i + 2] + vtx_offset + 1
            lines.append(f"f {i0}/{i0}/{i0} {i1}/{i1}/{i1} {i2}/{i2}/{i2}")

        vtx_offset += mg['vertex_count']
        lines.append("")

    # Write MTL file
    if parsed['header']['materials']:
        mtl_lines = [f"# StarCraft Ghost materials for {output_path.stem}"]
        for mat_name in parsed['header']['materials']:
            mtl_lines.append(f"\nnewmtl {mat_name}")
            mtl_lines.append("Ka 0.2 0.2 0.2")
            mtl_lines.append("Kd 0.8 0.8 0.8")
            mtl_lines.append("Ks 0.1 0.1 0.1")
            mtl_lines.append(f"map_Kd textures/{mat_name}.dds")
        mtl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(mtl_path, 'w') as f:
            f.write('\n'.join(mtl_lines))
        print(f"Wrote {mtl_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        f.write('\n'.join(lines))

    size = output_path.stat().st_size
    print(f"Wrote {output_path} ({size / 1024:.1f} KB)")


# ---------------------------------------------------------------------------
# JSON output (for Godot level loader)
# ---------------------------------------------------------------------------

def export_json(parsed: dict, output_path: Path, compact: bool = False):
    """Export parsed NIL data as JSON for the Godot level loader.

    When compact=True, flattens vertex data to parallel arrays and
    remaps indices to only include referenced vertices (huge size savings
    for large sections with many submeshes sharing a vertex buffer).
    """
    if compact:
        for mg in parsed['mesh_groups']:
            verts = mg['vertices']
            indices = mg['indices']

            # Find unique referenced vertex indices and build remap
            used = sorted(set(indices))
            remap = {old: new for new, old in enumerate(used)}

            # Build compact vertex arrays with only referenced verts
            positions = []
            normals = []
            colors = []
            uvs = []
            for vi in used:
                v = verts[vi]
                positions.extend(round(c, 4) for c in v['position'])
                normals.extend(round(c, 4) for c in v['normal'])
                colors.extend(v['color'])
                uvs.extend(round(c, 5) for c in v['uv'])

            # Remap indices to new vertex array
            mg['positions'] = positions
            mg['normals'] = normals
            mg['colors'] = colors
            mg['uvs'] = uvs
            mg['indices'] = [remap[i] for i in indices]
            mg['vertex_count'] = len(used)
            del mg['vertices']

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(parsed, f, indent=None if compact else 2)

    size = output_path.stat().st_size
    print(f"Wrote {output_path} ({size / 1024:.1f} KB)")


# ---------------------------------------------------------------------------
# glTF output (for direct Godot import)
# ---------------------------------------------------------------------------

def export_gltf(parsed: dict, output_path: Path):
    """Export parsed NIL level geometry as a single glTF 2.0 file."""
    all_positions = []
    all_normals = []
    all_uvs = []
    all_colors = []
    all_indices = []

    vtx_offset = 0

    for mg in parsed['mesh_groups']:
        for v in mg['vertices']:
            all_positions.append(v['position'])
            all_normals.append(v['normal'])
            all_uvs.append(v['uv'])
            c = v['color']
            all_colors.append([c[0] / 255.0, c[1] / 255.0,
                               c[2] / 255.0, c[3] / 255.0])

        for idx in mg['indices']:
            all_indices.append(idx + vtx_offset)

        vtx_offset += len(mg['vertices'])

    if not all_positions:
        print("ERROR: No geometry to export")
        return

    vcount = len(all_positions)
    icount = len(all_indices)

    pos_bytes = b''.join(struct.pack('<3f', *p) for p in all_positions)
    normal_bytes = b''.join(struct.pack('<3f', *n) for n in all_normals)
    uv_bytes = b''.join(struct.pack('<2f', *uv) for uv in all_uvs)
    color_bytes = b''.join(struct.pack('<4f', *c) for c in all_colors)

    if vcount > 65535:
        idx_bytes = b''.join(struct.pack('<I', i) for i in all_indices)
        idx_component = 5125  # UNSIGNED_INT
    else:
        idx_bytes = b''.join(struct.pack('<H', i) for i in all_indices)
        idx_component = 5123  # UNSIGNED_SHORT

    def pad4(b):
        r = len(b) % 4
        return b + b'\x00' * (4 - r) if r else b

    pos_bytes = pad4(pos_bytes)
    normal_bytes = pad4(normal_bytes)
    uv_bytes = pad4(uv_bytes)
    color_bytes = pad4(color_bytes)
    idx_bytes = pad4(idx_bytes)

    blob = pos_bytes + normal_bytes + uv_bytes + color_bytes + idx_bytes
    b64 = base64.b64encode(blob).decode('ascii')

    xs = [p[0] for p in all_positions]
    ys = [p[1] for p in all_positions]
    zs = [p[2] for p in all_positions]

    off_pos = 0
    off_norm = len(pos_bytes)
    off_uv = off_norm + len(normal_bytes)
    off_col = off_uv + len(uv_bytes)
    off_idx = off_col + len(color_bytes)

    name = output_path.stem

    gltf = {
        "asset": {
            "version": "2.0",
            "generator": "ghost_port nil_parser.py",
            "extras": {
                "source": "StarCraft: Ghost NIL level",
                "engine": "Nihilistic NOD Engine (2000-2005)",
                "materials": parsed['header']['materials'],
                "sections_found": parsed['stats']['sections_found'],
                "mesh_groups": parsed['stats']['mesh_groups'],
                "total_vertices": vcount,
                "total_triangles": icount // 3,
            }
        },
        "scene": 0,
        "scenes": [{"name": name, "nodes": [0]}],
        "nodes": [{"name": name, "mesh": 0}],
        "meshes": [{
            "name": f"{name}_mesh",
            "primitives": [{
                "attributes": {
                    "POSITION": 0,
                    "NORMAL": 1,
                    "TEXCOORD_0": 2,
                    "COLOR_0": 3,
                },
                "indices": 4,
                "mode": 4,  # TRIANGLES
            }]
        }],
        "buffers": [{
            "uri": f"data:application/octet-stream;base64,{b64}",
            "byteLength": len(blob),
        }],
        "bufferViews": [
            {"buffer": 0, "byteOffset": off_pos,
             "byteLength": len(pos_bytes),
             "target": 34962, "byteStride": 12},
            {"buffer": 0, "byteOffset": off_norm,
             "byteLength": len(normal_bytes),
             "target": 34962, "byteStride": 12},
            {"buffer": 0, "byteOffset": off_uv,
             "byteLength": len(uv_bytes),
             "target": 34962, "byteStride": 8},
            {"buffer": 0, "byteOffset": off_col,
             "byteLength": len(color_bytes),
             "target": 34962, "byteStride": 16},
            {"buffer": 0, "byteOffset": off_idx,
             "byteLength": len(idx_bytes),
             "target": 34963},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": vcount,
             "type": "VEC3",
             "min": [min(xs), min(ys), min(zs)],
             "max": [max(xs), max(ys), max(zs)]},
            {"bufferView": 1, "componentType": 5126, "count": vcount,
             "type": "VEC3"},
            {"bufferView": 2, "componentType": 5126, "count": vcount,
             "type": "VEC2"},
            {"bufferView": 3, "componentType": 5126, "count": vcount,
             "type": "VEC4"},
            {"bufferView": 4, "componentType": idx_component, "count": icount,
             "type": "SCALAR"},
        ],
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(gltf, f)

    size = output_path.stat().st_size
    print(f"Wrote {output_path} ({size / 1024 / 1024:.1f} MB)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Parse StarCraft Ghost NIL level files '
                    '(Nihilistic NOD Engine)')
    parser.add_argument('--input', '-i', required=True,
                        help='Input .nil file')
    parser.add_argument('--output', '-o',
                        help='Output file (.obj, .json, .gltf)')
    parser.add_argument('--format',
                        choices=['json', 'json-compact', 'gltf', 'obj'],
                        default=None,
                        help='Output format (auto-detected from extension)')
    parser.add_argument('--no-transform', action='store_true',
                        help='Keep DirectX coordinates (skip Godot transform)')
    parser.add_argument('--no-filter', action='store_true',
                        help='Disable degenerate triangle filtering')
    parser.add_argument('--stats', action='store_true',
                        help='Print statistics only, no output file')
    args = parser.parse_args()

    input_path = Path(args.input)

    if not input_path.exists():
        print(f"ERROR: {input_path} not found")
        sys.exit(1)

    print(f"Parsing {input_path.name} "
          f"({input_path.stat().st_size / 1024:.0f} KB)...")

    data = input_path.read_bytes()
    parsed = parse_nil(data,
                       godot_coords=not args.no_transform,
                       filter_degenerate=not args.no_filter)

    if parsed is None:
        print("ERROR: Failed to parse NIL file (no valid sections found)")
        sys.exit(1)

    # Print stats
    stats = parsed['stats']
    header = parsed['header']
    print(f"  Materials: {header['material_count']}")
    print(f"  Sections found: {stats['sections_found']}")
    print(f"  Mesh groups (submeshes): {stats['mesh_groups']}")
    print(f"  Total vertices: {stats['total_vertices']:,}")
    print(f"  Total triangles: {stats['total_triangles']:,}")
    print(f"  Bounding box: "
          f"({stats['bbox_min'][0]:.0f},"
          f"{stats['bbox_min'][1]:.0f},"
          f"{stats['bbox_min'][2]:.0f}) to "
          f"({stats['bbox_max'][0]:.0f},"
          f"{stats['bbox_max'][1]:.0f},"
          f"{stats['bbox_max'][2]:.0f})")

    for mg in parsed['mesh_groups']:
        mat = mg.get('material_name', '')
        mat_str = f" [{mat}]" if mat else ""
        print(f"    Section {mg['section_id']:2d} sub {mg['submesh_id']}: "
              f"{mg['triangle_count']:4d} tris "
              f"(strip={mg['strip_count']:4d} list={mg['list_count']:3d})"
              f"{mat_str} type={mg['type_byte']}")

    if args.stats:
        print(f"\n  Materials ({len(header['materials'])}):")
        for i, m in enumerate(header['materials']):
            print(f"    {i:2d}: {m}")
        return

    if not args.output:
        print("\nNo output file specified (use --output or --stats)")
        return

    output_path = Path(args.output)

    # Determine format
    fmt = args.format
    if fmt is None:
        ext = output_path.suffix.lower()
        fmt = {'.gltf': 'gltf', '.obj': 'obj',
               '.json': 'json'}.get(ext, 'json')

    # Export
    if fmt == 'gltf':
        export_gltf(parsed, output_path)
    elif fmt == 'obj':
        export_obj(parsed, output_path)
    elif fmt == 'json-compact':
        export_json(parsed, output_path, compact=True)
    else:
        export_json(parsed, output_path, compact=False)

    print("Done!")


if __name__ == '__main__':
    main()
