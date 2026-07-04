#!/usr/bin/env python3
"""Classify M3 table exports as tenant-global vs company-specific.

Usage:
  python3 classify_export.py <export.zip | directory> [output.csv]

Reads Infor M3 grid data-management table export files (the binary
length-prefixed format) and classifies each table by where its rows live:

  NO_CONO   - table has no xxCONO column at all (tenant-wide by schema)
  GLOBAL    - all rows at CONO 0/blank (company copy will MISS these)
  COMPANY   - all rows at CONO > 0 (moves with a company copy)
  MIXED     - rows at both CONO 0 and CONO > 0
  EMPTY     - no data rows in export

A CONO field absent from a row's null bitmap is treated as CONO 0
(that is how blank/global rows are actually stored, e.g. COSRVI).
"""
import struct, sys, os, csv, zipfile, io
from collections import Counter

def iter_sources(path):
    if os.path.isdir(path):
        for name in sorted(os.listdir(path)):
            full = os.path.join(path, name)
            if os.path.isfile(full):
                yield name, open(full, 'rb')
    elif zipfile.is_zipfile(path):
        zf = zipfile.ZipFile(path)
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if not info.is_dir():
                yield os.path.basename(info.filename), zf.open(info)
    else:
        yield os.path.basename(path), open(path, 'rb')

def read_exact(f, n):
    buf = f.read(n)
    if len(buf) != n:
        raise EOFError(f"wanted {n} bytes, got {len(buf)}")
    return buf

def classify_stream(f):
    """Parse one table export stream. Returns dict of stats."""
    hlen = struct.unpack('>I', read_exact(f, 4))[0]
    header = read_exact(f, hlen).decode('utf-8')
    fields = [fd.split(';')[1] for fd in header.split('\x01')]
    nfields = len(fields)
    bitmap_bytes = (nfields + 7) // 8

    # locate the company column: 6-char name ending in 'cono'
    cono_idx = next((i for i, n in enumerate(fields)
                     if len(n) == 6 and n.lower().endswith('cono')), None)

    rows = 0
    cono_counts = Counter()
    while True:
        lenbuf = f.read(4)
        if not lenbuf:
            break
        rowlen = struct.unpack('>I', lenbuf)[0]
        row = read_exact(f, rowlen)
        rows += 1
        if cono_idx is None:
            continue
        bm = row[:bitmap_bytes]
        pos = bitmap_bytes
        cono_val = '0'  # absent from bitmap => null/blank => global
        for i in range(nfields):
            if not (bm[i // 8] & (0x80 >> (i % 8))):
                continue
            vlen = struct.unpack('>I', row[pos:pos+4])[0]
            pos += 4
            if i == cono_idx:
                v = row[pos:pos+vlen].decode('utf-8').strip()
                cono_val = v if v else '0'
                break  # cono found; skip rest of row
            pos += vlen
            if i > cono_idx:
                break
        cono_counts[cono_val] += 1

    if rows == 0:
        cls = 'EMPTY'
    elif cono_idx is None:
        cls = 'NO_CONO'
    else:
        zero = cono_counts.get('0', 0)
        nonzero = rows - zero
        cls = 'MIXED' if (zero and nonzero) else ('GLOBAL' if zero else 'COMPANY')
    return {
        'fields': nfields,
        'cono_field': fields[cono_idx] if cono_idx is not None else '',
        'rows': rows,
        'rows_cono0': cono_counts.get('0', 0),
        'conos': ' '.join(sorted((k for k in cono_counts if k != '0'), key=int)),
        'class': cls,
    }

def main():
    src = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else 'table_classification.csv'
    results = []
    for name, f in iter_sources(src):
        if name.upper() == 'TABLE_INFO':
            continue  # java-serialized catalog, not a table dump
        try:
            with f:
                stats = classify_stream(f)
            results.append({'table': name, **stats, 'error': ''})
        except Exception as e:
            results.append({'table': name, 'fields': '', 'cono_field': '',
                            'rows': '', 'rows_cono0': '', 'conos': '',
                            'class': 'PARSE_ERROR', 'error': str(e)})
    cols = ['table', 'class', 'rows', 'rows_cono0', 'conos',
            'cono_field', 'fields', 'error']
    with open(out, 'w', newline='') as fo:
        w = csv.DictWriter(fo, fieldnames=cols)
        w.writeheader()
        for r in sorted(results, key=lambda r: (r['class'], r['table'])):
            w.writerow(r)
    summary = Counter(r['class'] for r in results)
    print(f"{len(results)} tables -> {out}")
    for cls, n in summary.most_common():
        print(f"  {cls:<12} {n}")
    flagged = [r for r in results if r['class'] in ('GLOBAL', 'MIXED', 'NO_CONO')]
    if flagged:
        print("\nTables a company copy will miss (fully or partly):")
        for r in flagged:
            print(f"  {r['table']:<12} {r['class']:<8} rows={r['rows']} cono0={r['rows_cono0']}")

if __name__ == '__main__':
    main()
