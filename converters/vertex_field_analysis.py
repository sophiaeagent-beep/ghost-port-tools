#!/usr/bin/env python3
"""
SC:Ghost NIL v35 Vertex Field Analysis

Decodes ALL 36 bytes of the merged vertex format and attempts to identify
the unknown fields at bytes [16-19], [20-23], [24-27], [32-35] by trying
multiple interpretations and computing statistics.

Mapping from VTMR v27 (split arrays, 36 bytes total):
  aVertices[]:      12 bytes = 3x f32 LE (X, Y, Z position)
  cSectorVertex[]:  24 bytes:
    [0-3]   u32 LE position_index
    [4-7]   RGBA vertex color
    [8-11]  f32 LE texture U (world-space planar)
    [12-15] f32 LE texture V (world-space planar)
    [16-23] 2x f32 LE lightmap UVs

SC:Ghost v35 (merged, 36 bytes per vertex):
    [0-1]   u16 LE UV_U (normalized: value/65535)         -- CONFIRMED
    [2-3]   u16 LE UV_V (V-flip: 1.0 - value/65535)      -- CONFIRMED
    [4-7]   f32 BE position X                             -- CONFIRMED
    [8-11]  f32 BE position Y                             -- CONFIRMED
    [12-15] f32 BE position Z                             -- CONFIRMED
    [16-19] f32 BE ???
    [20-23] 4x u8 ???
    [24-27] f32 BE ???
    [28-31] 4x u8 ARGB vertex color                      -- CONFIRMED
    [32-35] f32 BE ???

This script exhaustively decodes all possible interpretations.
"""

import struct
import math
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
NIL_FILE = "/home/scott/Games/xemu/starcraft_ghost/Levels/1_2_1_Miners_Bunker.nil"
VERTEX_STRIDE = 36
MAX_COORD = 600.0
NUM_VERTICES_TO_DUMP = 50       # detailed per-vertex dump
NUM_VERTICES_FOR_STATS = 500    # broader statistical sample

# ---------------------------------------------------------------------------
# Binary helpers
# ---------------------------------------------------------------------------
def ru16(d, o):   return struct.unpack_from('<H', d, o)[0]
def ru16be(d, o): return struct.unpack_from('>H', d, o)[0]
def ru32(d, o):   return struct.unpack_from('<I', d, o)[0]
def rf32le(d, o): return struct.unpack_from('<f', d, o)[0]
def rf32be(d, o): return struct.unpack_from('>f', d, o)[0]
def ri8(d, o):    return struct.unpack_from('b', d, o)[0]   # signed byte
def ri16(d, o):   return struct.unpack_from('<h', d, o)[0]   # signed i16 LE
def ri16be(d, o): return struct.unpack_from('>h', d, o)[0]   # signed i16 BE

def is_valid_vertex(data, off):
    """Check if 36 bytes at offset form a valid SC:Ghost vertex."""
    if off + VERTEX_STRIDE > len(data):
        return False
    if data[off + 28] != 0xFF:
        return False
    try:
        px = rf32be(data, off + 4)
        py = rf32be(data, off + 8)
        pz = rf32be(data, off + 12)
        if not all(math.isfinite(v) and abs(v) < MAX_COORD for v in (px, py, pz)):
            return False
        return True
    except struct.error:
        return False


def find_sections(data, start_offset):
    """Find geometry sections. Returns list of (desc_offset, vert_start, vert_count)."""
    sections = []
    pos = start_offset
    dlen = len(data)

    while pos < dlen - 36:
        if data[pos] != 0x01 or data[pos + 1] != 0x00:
            pos += 1
            continue
        if pos + 5 > dlen:
            break
        if data[pos + 4] != 0x00:
            pos += 1
            continue

        vc = ru16(data, pos + 2)
        if vc < 3 or vc > 50000:
            pos += 1
            continue

        vert_start = pos + 5
        vert_end = vert_start + vc * VERTEX_STRIDE
        if vert_end > dlen:
            pos += 1
            continue

        # Validate first 2 vertices
        if not is_valid_vertex(data, vert_start):
            pos += 1
            continue
        if vc >= 2 and not is_valid_vertex(data, vert_start + VERTEX_STRIDE):
            pos += 1
            continue

        sections.append((pos, vert_start, vc))
        pos = vert_end  # skip past vertex data
    return sections


def parse_header(data):
    """Parse NIL header, return data_start offset."""
    if len(data) < 0x60:
        return None
    magic = data[0:4]
    if magic != b'NIL\x10':
        return None
    mat_count = ru32(data, 0x5C)
    if mat_count > 200:
        return None
    return 0x60 + mat_count * 0x20


# ---------------------------------------------------------------------------
# Main analysis
# ---------------------------------------------------------------------------
def main():
    path = Path(NIL_FILE)
    if not path.exists():
        print(f"ERROR: {NIL_FILE} not found")
        sys.exit(1)

    data = path.read_bytes()
    print(f"Loaded {path.name}: {len(data):,} bytes ({len(data)/1024:.0f} KB)")

    data_start = parse_header(data)
    if data_start is None:
        print("ERROR: Invalid NIL header")
        sys.exit(1)

    # Parse header info for display
    section_count = ru32(data, 0x04)
    mat_count = ru32(data, 0x5C)
    print(f"Header: section_count={section_count}, materials={mat_count}")
    print(f"Geometry data starts at offset 0x{data_start:X}")

    # Find all sections
    sections = find_sections(data, data_start)
    print(f"\nFound {len(sections)} geometry sections")

    if not sections:
        print("ERROR: No valid sections found")
        sys.exit(1)

    # Print section summary
    print("\nSection summary:")
    for i, (desc_off, vstart, vc) in enumerate(sections):
        print(f"  Section {i:2d}: offset=0x{desc_off:06X}  verts={vc:5d}  "
              f"vert_data=0x{vstart:06X}-0x{vstart + vc*36:06X}")

    # Pick the largest section with >= 50 vertices for analysis
    best = max(sections, key=lambda s: s[2])
    desc_off, vert_start, vert_count = best
    print(f"\n{'='*90}")
    print(f"ANALYZING: Section at 0x{desc_off:06X} with {vert_count} vertices")
    print(f"{'='*90}")

    n_dump = min(NUM_VERTICES_TO_DUMP, vert_count)
    n_stats = min(NUM_VERTICES_FOR_STATS, vert_count)

    # -----------------------------------------------------------------------
    # Detailed per-vertex dump (first 50)
    # -----------------------------------------------------------------------
    print(f"\n--- DETAILED VERTEX DUMP (first {n_dump} of {vert_count}) ---\n")

    for vi in range(n_dump):
        off = vert_start + vi * VERTEX_STRIDE
        raw = data[off:off + VERTEX_STRIDE]

        # Known fields
        uv_u_raw = ru16(data, off)
        uv_v_raw = ru16(data, off + 2)
        uv_u = uv_u_raw / 65535.0
        uv_v = 1.0 - uv_v_raw / 65535.0

        px = rf32be(data, off + 4)
        py = rf32be(data, off + 8)
        pz = rf32be(data, off + 12)

        # ARGB color at [28-31]
        a_col = data[off + 28]
        r_col = data[off + 29]
        g_col = data[off + 30]
        b_col = data[off + 31]

        # --- Unknown field [16-19] ---
        b16 = data[off+16:off+20]
        f16_be = rf32be(data, off + 16)
        f16_le = rf32le(data, off + 16)
        u8_16 = (data[off+16], data[off+17], data[off+18], data[off+19])
        u16le_16 = (ru16(data, off+16), ru16(data, off+18))
        u16be_16 = (ru16be(data, off+16), ru16be(data, off+18))
        i8_16 = (ri8(data, off+16), ri8(data, off+17), ri8(data, off+18), ri8(data, off+19))

        # --- Unknown field [20-23] ---
        b20 = data[off+20:off+24]
        f20_be = rf32be(data, off + 20)
        f20_le = rf32le(data, off + 20)
        u8_20 = (data[off+20], data[off+21], data[off+22], data[off+23])
        i8_20 = (ri8(data, off+20), ri8(data, off+21), ri8(data, off+22), ri8(data, off+23))
        u16le_20 = (ru16(data, off+20), ru16(data, off+22))
        u16be_20 = (ru16be(data, off+20), ru16be(data, off+22))

        # --- Unknown field [24-27] ---
        b24 = data[off+24:off+28]
        f24_be = rf32be(data, off + 24)
        f24_le = rf32le(data, off + 24)
        u8_24 = (data[off+24], data[off+25], data[off+26], data[off+27])
        i8_24 = (ri8(data, off+24), ri8(data, off+25), ri8(data, off+26), ri8(data, off+27))
        u16le_24 = (ru16(data, off+24), ru16(data, off+26))
        u16be_24 = (ru16be(data, off+24), ru16be(data, off+26))

        # --- Unknown field [32-35] ---
        b32 = data[off+32:off+36]
        f32_be = rf32be(data, off + 32)
        f32_le = rf32le(data, off + 32)
        u8_32 = (data[off+32], data[off+33], data[off+34], data[off+35])
        i8_32 = (ri8(data, off+32), ri8(data, off+33), ri8(data, off+34), ri8(data, off+35))
        u16le_32 = (ru16(data, off+32), ru16(data, off+34))
        u16be_32 = (ru16be(data, off+32), ru16be(data, off+34))

        print(f"Vertex {vi:3d}  (offset 0x{off:06X})")
        print(f"  Raw hex: {raw.hex(' ')}")
        print(f"  [0-3]   UV:   U={uv_u:.5f} V={uv_v:.5f}  (raw: {uv_u_raw}, {uv_v_raw})")
        print(f"  [4-15]  Pos:  X={px:10.4f} Y={py:10.4f} Z={pz:10.4f}")
        print(f"  [28-31] ARGB: A={a_col} R={r_col} G={g_col} B={b_col}  ({a_col:02X}{r_col:02X}{g_col:02X}{b_col:02X})")
        print(f"  --- UNKNOWN FIELDS ---")
        print(f"  [16-19] hex={b16.hex(' ')}")
        print(f"          f32 BE={f16_be:12.6f}  f32 LE={f16_le:12.6f}")
        print(f"          u8=({u8_16[0]:3d},{u8_16[1]:3d},{u8_16[2]:3d},{u8_16[3]:3d})")
        print(f"          i8=({i8_16[0]:4d},{i8_16[1]:4d},{i8_16[2]:4d},{i8_16[3]:4d})")
        print(f"          u16 LE=({u16le_16[0]:5d},{u16le_16[1]:5d})  u16 BE=({u16be_16[0]:5d},{u16be_16[1]:5d})")
        print(f"  [20-23] hex={b20.hex(' ')}")
        print(f"          f32 BE={f20_be:12.6f}  f32 LE={f20_le:12.6f}")
        print(f"          u8=({u8_20[0]:3d},{u8_20[1]:3d},{u8_20[2]:3d},{u8_20[3]:3d})")
        print(f"          i8=({i8_20[0]:4d},{i8_20[1]:4d},{i8_20[2]:4d},{i8_20[3]:4d})")
        print(f"          u16 LE=({u16le_20[0]:5d},{u16le_20[1]:5d})  u16 BE=({u16be_20[0]:5d},{u16be_20[1]:5d})")
        print(f"  [24-27] hex={b24.hex(' ')}")
        print(f"          f32 BE={f24_be:12.6f}  f32 LE={f24_le:12.6f}")
        print(f"          u8=({u8_24[0]:3d},{u8_24[1]:3d},{u8_24[2]:3d},{u8_24[3]:3d})")
        print(f"          i8=({i8_24[0]:4d},{i8_24[1]:4d},{i8_24[2]:4d},{i8_24[3]:4d})")
        print(f"          u16 LE=({u16le_24[0]:5d},{u16le_24[1]:5d})  u16 BE=({u16be_24[0]:5d},{u16be_24[1]:5d})")
        print(f"  [32-35] hex={b32.hex(' ')}")
        print(f"          f32 BE={f32_be:12.6f}  f32 LE={f32_le:12.6f}")
        print(f"          u8=({u8_32[0]:3d},{u8_32[1]:3d},{u8_32[2]:3d},{u8_32[3]:3d})")
        print(f"          i8=({i8_32[0]:4d},{i8_32[1]:4d},{i8_32[2]:4d},{i8_32[3]:4d})")
        print(f"          u16 LE=({u16le_32[0]:5d},{u16le_32[1]:5d})  u16 BE=({u16be_32[0]:5d},{u16be_32[1]:5d})")
        print()

    # -----------------------------------------------------------------------
    # Statistical analysis over larger sample
    # -----------------------------------------------------------------------
    print(f"\n{'='*90}")
    print(f"STATISTICAL ANALYSIS ({n_stats} vertices)")
    print(f"{'='*90}")

    # Collectors
    f16_be_vals = []
    f16_le_vals = []
    f20_be_vals = []
    f20_le_vals = []
    f24_be_vals = []
    f24_le_vals = []
    f32_be_vals = []
    f32_le_vals = []

    u8_20_all = []   # all 4-byte tuples at [20-23]
    i8_20_all = []
    u8_32_all = []
    i8_32_all = []

    # Normal hypothesis: NX=[16-19] BE, NZ=[24-27] BE, compute NY from [20-23]
    normal_mag_sq = []  # NX^2 + NZ^2
    normal_3d_mag = []  # full normal magnitude if NY derivable

    # Packed normal hypothesis for [20-23]: 4 signed bytes as /127 components
    packed_snorm_20 = []
    packed_unorm_20 = []

    # Lightmap UV hypothesis: [16-19] and [24-27] as f32 BE in [0,1] range
    lightmap_in_range = 0

    # Color stats
    argb_unique = set()

    # Also collect [16-19] as possible 4xu8 packed normal
    packed_snorm_16 = []
    packed_snorm_24 = []

    for vi in range(n_stats):
        off = vert_start + vi * VERTEX_STRIDE

        # [16-19]
        f16b = rf32be(data, off + 16)
        f16l = rf32le(data, off + 16)
        if math.isfinite(f16b): f16_be_vals.append(f16b)
        if math.isfinite(f16l): f16_le_vals.append(f16l)

        # [20-23]
        f20b = rf32be(data, off + 20)
        f20l = rf32le(data, off + 20)
        if math.isfinite(f20b): f20_be_vals.append(f20b)
        if math.isfinite(f20l): f20_le_vals.append(f20l)
        u8_20_all.append((data[off+20], data[off+21], data[off+22], data[off+23]))
        i8_20_all.append((ri8(data,off+20), ri8(data,off+21), ri8(data,off+22), ri8(data,off+23)))

        # [24-27]
        f24b = rf32be(data, off + 24)
        f24l = rf32le(data, off + 24)
        if math.isfinite(f24b): f24_be_vals.append(f24b)
        if math.isfinite(f24l): f24_le_vals.append(f24l)

        # [32-35]
        f32b = rf32be(data, off + 32)
        f32l = rf32le(data, off + 32)
        if math.isfinite(f32b): f32_be_vals.append(f32b)
        if math.isfinite(f32l): f32_le_vals.append(f32l)
        u8_32_all.append((data[off+32], data[off+33], data[off+34], data[off+35]))
        i8_32_all.append((ri8(data,off+32), ri8(data,off+33), ri8(data,off+34), ri8(data,off+35)))

        # --- Hypothesis tests ---

        # Normal hypothesis: NX=[16-19] BE, NZ=[24-27] BE
        if math.isfinite(f16b) and math.isfinite(f24b):
            sq = f16b * f16b + f24b * f24b
            normal_mag_sq.append(sq)
            remainder = 1.0 - sq
            if remainder >= 0:
                ny = math.sqrt(remainder)
                mag = math.sqrt(f16b**2 + ny**2 + f24b**2)
                normal_3d_mag.append(mag)

        # Lightmap UV hypothesis
        if math.isfinite(f16b) and math.isfinite(f24b):
            if 0.0 <= f16b <= 1.0 and 0.0 <= f24b <= 1.0:
                lightmap_in_range += 1

        # Packed signed normal at [20-23]: each byte / 127.0 -> [-1, +1]
        sn = tuple(ri8(data, off+20+j) / 127.0 for j in range(4))
        packed_snorm_20.append(sn)
        un = tuple(data[off+20+j] / 255.0 for j in range(4))
        packed_unorm_20.append(un)

        # Packed signed normal at [16-19]
        sn16 = tuple(ri8(data, off+16+j) / 127.0 for j in range(4))
        packed_snorm_16.append(sn16)
        sn24 = tuple(ri8(data, off+24+j) / 127.0 for j in range(4))
        packed_snorm_24.append(sn24)

        # Color
        argb_unique.add((data[off+28], data[off+29], data[off+30], data[off+31]))

    # -----------------------------------------------------------------------
    # Print statistics
    # -----------------------------------------------------------------------
    def print_float_stats(name, vals):
        if not vals:
            print(f"  {name}: NO VALID VALUES")
            return
        mn, mx = min(vals), max(vals)
        avg = sum(vals) / len(vals)
        in01 = sum(1 for v in vals if 0.0 <= v <= 1.0)
        inneg1to1 = sum(1 for v in vals if -1.0 <= v <= 1.0)
        in_neg2to2 = sum(1 for v in vals if -2.0 <= v <= 2.0)
        print(f"  {name}: min={mn:12.6f}  max={mx:12.6f}  avg={avg:12.6f}")
        print(f"    [0,1]: {in01}/{len(vals)} ({100*in01/len(vals):.1f}%)  "
              f"[-1,1]: {inneg1to1}/{len(vals)} ({100*inneg1to1/len(vals):.1f}%)  "
              f"[-2,2]: {in_neg2to2}/{len(vals)} ({100*in_neg2to2/len(vals):.1f}%)")

    print(f"\n--- Field [16-19] interpretations ---")
    print_float_stats("[16-19] f32 BE", f16_be_vals)
    print_float_stats("[16-19] f32 LE", f16_le_vals)

    print(f"\n--- Field [20-23] interpretations ---")
    print_float_stats("[20-23] f32 BE", f20_be_vals)
    print_float_stats("[20-23] f32 LE", f20_le_vals)
    # Byte range analysis
    if u8_20_all:
        for bi in range(4):
            vals = [t[bi] for t in u8_20_all]
            print(f"  [20+{bi}] u8: min={min(vals):3d}  max={max(vals):3d}  avg={sum(vals)/len(vals):.1f}")
        for bi in range(4):
            vals = [t[bi] for t in i8_20_all]
            print(f"  [20+{bi}] i8: min={min(vals):4d}  max={max(vals):4d}  avg={sum(vals)/len(vals):.1f}")

    print(f"\n--- Field [24-27] interpretations ---")
    print_float_stats("[24-27] f32 BE", f24_be_vals)
    print_float_stats("[24-27] f32 LE", f24_le_vals)

    print(f"\n--- Field [32-35] interpretations ---")
    print_float_stats("[32-35] f32 BE", f32_be_vals)
    print_float_stats("[32-35] f32 LE", f32_le_vals)
    if u8_32_all:
        for bi in range(4):
            vals = [t[bi] for t in u8_32_all]
            print(f"  [32+{bi}] u8: min={min(vals):3d}  max={max(vals):3d}  avg={sum(vals)/len(vals):.1f}")

    # -----------------------------------------------------------------------
    # Hypothesis results
    # -----------------------------------------------------------------------
    print(f"\n{'='*90}")
    print(f"HYPOTHESIS TESTING")
    print(f"{'='*90}")

    # H1: [16-19] and [24-27] as normal X,Z (f32 BE), derive NY
    print(f"\n--- H1: [16-19]=NX (f32 BE), [24-27]=NZ (f32 BE), derive NY ---")
    if normal_mag_sq:
        le1 = sum(1 for s in normal_mag_sq if s <= 1.0)
        le1_1 = sum(1 for s in normal_mag_sq if s <= 1.1)
        print(f"  NX^2 + NZ^2 <= 1.0: {le1}/{len(normal_mag_sq)} ({100*le1/len(normal_mag_sq):.1f}%)")
        print(f"  NX^2 + NZ^2 <= 1.1: {le1_1}/{len(normal_mag_sq)} ({100*le1_1/len(normal_mag_sq):.1f}%)")
        print(f"  NX^2+NZ^2 min={min(normal_mag_sq):.6f}  max={max(normal_mag_sq):.6f}  avg={sum(normal_mag_sq)/len(normal_mag_sq):.6f}")
    if normal_3d_mag:
        near1 = sum(1 for m in normal_3d_mag if 0.99 <= m <= 1.01)
        print(f"  |N| in [0.99,1.01]: {near1}/{len(normal_3d_mag)} ({100*near1/len(normal_3d_mag):.1f}%)")
        print(f"  |N| min={min(normal_3d_mag):.6f}  max={max(normal_3d_mag):.6f}")
        verdict = "LIKELY NORMALS" if le1/len(normal_mag_sq) > 0.9 else "UNLIKELY NORMALS"
        print(f"  ** VERDICT: {verdict} **")

    # H2: [16-19] and [24-27] as lightmap UVs (f32 BE in [0,1])
    print(f"\n--- H2: [16-19]=lm_U (f32 BE), [24-27]=lm_V (f32 BE) in [0,1] ---")
    print(f"  Both in [0,1]: {lightmap_in_range}/{n_stats} ({100*lightmap_in_range/n_stats:.1f}%)")
    verdict = "LIKELY LIGHTMAP UVs" if lightmap_in_range/n_stats > 0.8 else "UNLIKELY LIGHTMAP UVs"
    print(f"  ** VERDICT: {verdict} **")

    # H3: [20-23] as packed normals (4 signed bytes / 127)
    print(f"\n--- H3: [20-23] as packed signed normal (i8/127, 4 components) ---")
    if packed_snorm_20:
        for ci in range(4):
            vals = [t[ci] for t in packed_snorm_20]
            in_range = sum(1 for v in vals if -1.0 <= v <= 1.0)
            print(f"  comp[{ci}]: min={min(vals):7.4f}  max={max(vals):7.4f}  avg={sum(vals)/len(vals):7.4f}  "
                  f"[-1,1]: {in_range}/{len(vals)}")
        # Check if first 3 form unit normal
        unit_count = 0
        for sn in packed_snorm_20:
            mag = math.sqrt(sn[0]**2 + sn[1]**2 + sn[2]**2)
            if 0.9 <= mag <= 1.1:
                unit_count += 1
        print(f"  |xyz| in [0.9,1.1]: {unit_count}/{len(packed_snorm_20)} ({100*unit_count/len(packed_snorm_20):.1f}%)")
        verdict = "LIKELY PACKED NORMAL" if unit_count/len(packed_snorm_20) > 0.7 else "UNLIKELY PACKED NORMAL"
        print(f"  ** VERDICT: {verdict} **")

    # H3b: [20-23] as packed unsigned normal (u8/255, 4 components, remapped 0-1)
    print(f"\n--- H3b: [20-23] as packed unsigned normal (u8/255, remap to [-1,1]) ---")
    if packed_unorm_20:
        unit_count = 0
        for un in packed_unorm_20:
            # Remap: 0->-1, 0.5->0, 1->+1
            rn = tuple(v * 2.0 - 1.0 for v in un)
            mag = math.sqrt(rn[0]**2 + rn[1]**2 + rn[2]**2)
            if 0.9 <= mag <= 1.1:
                unit_count += 1
        print(f"  |remap(xyz)| in [0.9,1.1]: {unit_count}/{len(packed_unorm_20)} ({100*unit_count/len(packed_unorm_20):.1f}%)")
        verdict = "LIKELY PACKED NORMAL (UNORM)" if unit_count/len(packed_unorm_20) > 0.7 else "UNLIKELY"
        print(f"  ** VERDICT: {verdict} **")

    # H4: [32-35] as tangent W or additional normal component
    print(f"\n--- H4: [32-35] as tangent handedness/W (f32 BE) ---")
    if f32_be_vals:
        # Tangent W is typically +1 or -1
        near_pos1 = sum(1 for v in f32_be_vals if 0.9 <= v <= 1.1)
        near_neg1 = sum(1 for v in f32_be_vals if -1.1 <= v <= -0.9)
        print(f"  Near +1: {near_pos1}/{len(f32_be_vals)}")
        print(f"  Near -1: {near_neg1}/{len(f32_be_vals)}")
        print(f"  Combined: {near_pos1+near_neg1}/{len(f32_be_vals)} ({100*(near_pos1+near_neg1)/len(f32_be_vals):.1f}%)")

    # H5: Combined normal check: NX(f32 BE @16) + packed NY/NZ(@20-23) + NZ(f32 BE @24)
    # Maybe [16-19]=NX, [20-23]=packed(NY, tangent_x, tangent_y, tangent_z), [24-27]=NZ
    print(f"\n--- H5: [16-19]=NX, [24-27]=NZ, [20-23] byte[0]=packed NY ---")
    if packed_snorm_20 and f16_be_vals and f24_be_vals:
        unit3_count = 0
        for vi in range(min(len(f16_be_vals), len(f24_be_vals), len(packed_snorm_20))):
            nx = f16_be_vals[vi]
            nz = f24_be_vals[vi]
            ny_packed = packed_snorm_20[vi][0]  # first byte of [20-23]
            mag = math.sqrt(nx**2 + ny_packed**2 + nz**2)
            if 0.9 <= mag <= 1.1:
                unit3_count += 1
        print(f"  |NX,byte20/127,NZ| in [0.9,1.1]: {unit3_count}/{min(len(f16_be_vals),len(packed_snorm_20))} "
              f"({100*unit3_count/min(len(f16_be_vals),len(packed_snorm_20)):.1f}%)")

    # H5b: same but NY from byte[1]
    print(f"\n--- H5b: [16-19]=NX, [24-27]=NZ, [20-23] byte[1]=packed NY ---")
    if packed_snorm_20 and f16_be_vals and f24_be_vals:
        unit3_count = 0
        for vi in range(min(len(f16_be_vals), len(f24_be_vals), len(packed_snorm_20))):
            nx = f16_be_vals[vi]
            nz = f24_be_vals[vi]
            ny_packed = packed_snorm_20[vi][1]
            mag = math.sqrt(nx**2 + ny_packed**2 + nz**2)
            if 0.9 <= mag <= 1.1:
                unit3_count += 1
        print(f"  |NX,byte21/127,NZ| in [0.9,1.1]: {unit3_count}/{min(len(f16_be_vals),len(packed_snorm_20))}")

    # H6: All 4 packed bytes at [16-19] as a compressed normal
    print(f"\n--- H6: [16-19] as 4x i8/127 packed normal ---")
    if packed_snorm_16:
        unit_count = 0
        for sn in packed_snorm_16:
            mag = math.sqrt(sn[0]**2 + sn[1]**2 + sn[2]**2)
            if 0.9 <= mag <= 1.1:
                unit_count += 1
        print(f"  |xyz| in [0.9,1.1]: {unit_count}/{len(packed_snorm_16)} ({100*unit_count/len(packed_snorm_16):.1f}%)")

    # H7: [24-27] as 4x i8/127 packed normal
    print(f"\n--- H7: [24-27] as 4x i8/127 packed normal ---")
    if packed_snorm_24:
        unit_count = 0
        for sn in packed_snorm_24:
            mag = math.sqrt(sn[0]**2 + sn[1]**2 + sn[2]**2)
            if 0.9 <= mag <= 1.1:
                unit_count += 1
        print(f"  |xyz| in [0.9,1.1]: {unit_count}/{len(packed_snorm_24)} ({100*unit_count/len(packed_snorm_24):.1f}%)")

    # H8: [16-19] as f32 BE = NY (normal Y component)
    print(f"\n--- H8: What if [16-19]=NY? Then {16} is a single normal component ---")
    if f16_be_vals:
        in_range = sum(1 for v in f16_be_vals if -1.0 <= v <= 1.0)
        print(f"  [-1,1]: {in_range}/{len(f16_be_vals)} ({100*in_range/len(f16_be_vals):.1f}%)")

    # H9: What if the ENTIRE normal is at [20-23] as 4 packed bytes?
    # And [16-19] and [24-27] are something else entirely?
    print(f"\n--- H9: [20-23] as FULL 4-byte packed normal (XYZW) ---")
    print(f"  (i8/127 interpretation, first 3 as normal XYZ):")
    if packed_snorm_20:
        mags = []
        for sn in packed_snorm_20:
            mag = math.sqrt(sn[0]**2 + sn[1]**2 + sn[2]**2)
            mags.append(mag)
        near1 = sum(1 for m in mags if 0.9 <= m <= 1.1)
        print(f"  |xyz| in [0.9,1.1]: {near1}/{len(mags)} ({100*near1/len(mags):.1f}%)")
        print(f"  |xyz| min={min(mags):.4f}  max={max(mags):.4f}  avg={sum(mags)/len(mags):.4f}")
        # 4th component analysis
        w_vals = [sn[3] for sn in packed_snorm_20]
        near_pm1 = sum(1 for w in w_vals if abs(abs(w) - 1.0) < 0.1)
        print(f"  4th byte (tangent W?): near +/-1: {near_pm1}/{len(w_vals)}")

    # H10: [16-19] and [24-27] as SECOND UV set (lightmap/detail UV)
    # Check if they look like uv coordinates (repeating patterns, clustering)
    print(f"\n--- H10: [16-19] and [24-27] value distribution ---")
    if f16_be_vals and f24_be_vals:
        # Check unique value count (UVs tend to have more variation than normals)
        unique_16 = len(set(round(v, 4) for v in f16_be_vals))
        unique_24 = len(set(round(v, 4) for v in f24_be_vals))
        print(f"  [16-19] unique values: {unique_16}/{len(f16_be_vals)}")
        print(f"  [24-27] unique values: {unique_24}/{len(f24_be_vals)}")

    # H11: [32-35] value distribution
    print(f"\n--- H11: [32-35] value distribution ---")
    if f32_be_vals:
        unique_32 = len(set(round(v, 4) for v in f32_be_vals))
        print(f"  Unique values: {unique_32}/{len(f32_be_vals)}")
        # Check if it correlates with Y position
        # (could be height-based ambient occlusion or similar)

    # -----------------------------------------------------------------------
    # ARGB Color analysis
    # -----------------------------------------------------------------------
    print(f"\n{'='*90}")
    print(f"VERTEX COLOR ANALYSIS")
    print(f"{'='*90}")
    print(f"\nUnique ARGB colors: {len(argb_unique)}")
    for argb in sorted(argb_unique):
        a, r, g, b = argb
        print(f"  A={a:3d} R={r:3d} G={g:3d} B={b:3d}  (#{a:02X}{r:02X}{g:02X}{b:02X})")

    # -----------------------------------------------------------------------
    # VTMR comparison (show format evolution)
    # -----------------------------------------------------------------------
    print(f"\n{'='*90}")
    print(f"VTMR v27 vs SC:Ghost v35 FORMAT COMPARISON")
    print(f"{'='*90}")
    print(f"""
VTMR v27 (split arrays, 36 bytes total per vertex):
  aVertices[]:      [0-3] f32 LE X  [4-7] f32 LE Y  [8-11] f32 LE Z
  cSectorVertex[]:
    [0-3]   u32 LE position_index (into aVertices[])
    [4-7]   R G B A  vertex color (byte 7 = alpha = 0xFF)
    [8-11]  f32 LE texture U (world-space, range -6 to +15)
    [12-15] f32 LE texture V (world-space, range -6 to +12)
    [16-19] f32 LE lightmap U (range ~0 to 0.64)
    [20-23] f32 LE lightmap V (range ~0 to 0.64)

SC:Ghost v35 (merged, 36 bytes per vertex):
    [0-1]   u16 LE UV_U (normalized 0-65535)    <-- compressed from f32 world-space
    [2-3]   u16 LE UV_V (V-flipped)             <-- compressed from f32 world-space
    [4-7]   f32 BE position X                   <-- was separate array, now merged; LE->BE
    [8-11]  f32 BE position Y
    [12-15] f32 BE position Z
    [16-19] f32 BE ???                           <-- NEW: not in VTMR
    [20-23] 4x u8 ???                            <-- NEW: not in VTMR
    [24-27] f32 BE ???                           <-- NEW: not in VTMR
    [28-31] A R G B  vertex color                <-- RGBA->ARGB reorder!
    [32-35] f32 BE ???                           <-- NEW: replaces lightmap UVs?

Format Evolution (VTMR v27 -> SC:Ghost v35):
  - Position index removed (merged format)
  - UV compressed: f32 world-space -> u16 normalized (saves 4 bytes)
  - Position merged inline: saves 4 bytes (no index)
  - Endianness: LE -> BE (GameCube/PPC origin)
  - Color byte order: RGBA -> ARGB
  - Lightmap UVs (8 bytes) -> replaced by new data
  - NET NEW: 16 extra bytes at [16-19], [20-23], [24-27], [32-35]
    These 16 bytes are likely: normal vector + tangent data
    (VTMR had no per-vertex normals — used face normals instead)
""")

    # -----------------------------------------------------------------------
    # Multi-section cross-validation
    # -----------------------------------------------------------------------
    print(f"{'='*90}")
    print(f"CROSS-SECTION VALIDATION (checking consistency across all {len(sections)} sections)")
    print(f"{'='*90}")

    for si, (desc_off, vstart, vc) in enumerate(sections[:8]):  # First 8 sections
        n = min(100, vc)
        f16_in_neg1_1 = 0
        f24_in_neg1_1 = 0
        mag_sq_le1 = 0
        packed20_unit = 0
        lm_in_01 = 0
        f32_in_neg1_1 = 0

        for vi in range(n):
            off = vstart + vi * VERTEX_STRIDE
            f16b = rf32be(data, off + 16)
            f24b = rf32be(data, off + 24)
            f32b = rf32be(data, off + 32)

            if math.isfinite(f16b) and -1.0 <= f16b <= 1.0:
                f16_in_neg1_1 += 1
            if math.isfinite(f24b) and -1.0 <= f24b <= 1.0:
                f24_in_neg1_1 += 1
            if math.isfinite(f16b) and math.isfinite(f24b):
                if f16b**2 + f24b**2 <= 1.05:
                    mag_sq_le1 += 1
                if 0 <= f16b <= 1 and 0 <= f24b <= 1:
                    lm_in_01 += 1
            if math.isfinite(f32b) and -1.0 <= f32b <= 1.0:
                f32_in_neg1_1 += 1

            # Packed normal at [20-23]
            sn = tuple(ri8(data, off+20+j) / 127.0 for j in range(4))
            mag = math.sqrt(sn[0]**2 + sn[1]**2 + sn[2]**2)
            if 0.9 <= mag <= 1.1:
                packed20_unit += 1

        print(f"\n  Section {si} ({vc} verts, sample {n}):")
        print(f"    [16-19] f32 BE in [-1,1]: {f16_in_neg1_1}/{n} ({100*f16_in_neg1_1/n:.0f}%)")
        print(f"    [24-27] f32 BE in [-1,1]: {f24_in_neg1_1}/{n} ({100*f24_in_neg1_1/n:.0f}%)")
        print(f"    NX^2+NZ^2 <= 1.05:       {mag_sq_le1}/{n} ({100*mag_sq_le1/n:.0f}%)")
        print(f"    [20-23] packed unit norm:  {packed20_unit}/{n} ({100*packed20_unit/n:.0f}%)")
        print(f"    [16,24] both in [0,1]:    {lm_in_01}/{n} ({100*lm_in_01/n:.0f}%)")
        print(f"    [32-35] f32 BE in [-1,1]: {f32_in_neg1_1}/{n} ({100*f32_in_neg1_1/n:.0f}%)")

    # -----------------------------------------------------------------------
    # Final verdict
    # -----------------------------------------------------------------------
    print(f"\n{'='*90}")
    print(f"FINAL ANALYSIS SUMMARY")
    print(f"{'='*90}")
    print("""
Based on the statistical analysis, the most likely interpretations are:

Byte [16-19]: f32 BE
  - If values are in [-1,1] range with NX^2+NZ^2<=1 -> NORMAL X COMPONENT
  - If values are in [0,1] range -> LIGHTMAP U or SECOND UV U
  - If values exceed 1.0 -> SOME OTHER ATTRIBUTE (tangent? binormal?)

Byte [20-23]: 4x u8
  - If first 3 bytes as i8/127 form unit vector -> PACKED NORMAL (XYZ + W handedness)
  - If not unit vector -> PACKED TANGENT or OTHER DATA
  - 4th byte often +127 or -128 -> TANGENT HANDEDNESS (W)

Byte [24-27]: f32 BE
  - If correlates with [16-19] as NX^2+NZ^2<=1 -> NORMAL Z COMPONENT
  - If values in [0,1] -> LIGHTMAP V or SECOND UV V

Byte [28-31]: ARGB vertex color (CONFIRMED)
  - Byte 28 = alpha (always 0xFF)
  - Bytes 29-31 = RGB

Byte [32-35]: f32 BE
  - If near +/-1 -> TANGENT HANDEDNESS
  - If in [0,1] -> ADDITIONAL UV or AMBIENT OCCLUSION
  - If other range -> UNKNOWN
""")


if __name__ == '__main__':
    main()
