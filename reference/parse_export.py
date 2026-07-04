import struct, sys, json

def parse(path):
    data = open(path, 'rb').read()
    hlen = struct.unpack('>I', data[:4])[0]
    header = data[4:4+hlen].decode('utf-8')
    fields = []
    for fd in header.split('\x01'):
        typ, name, maxlen, flag = fd.split(';')
        fields.append({'type': typ, 'name': name, 'maxlen': maxlen, 'flag': flag})
    nfields = len(fields)
    bitmap_bytes = (nfields + 7) // 8

    pos = 4 + hlen
    rows = []
    while pos < len(data):
        rowlen = struct.unpack('>I', data[pos:pos+4])[0]
        pos += 4
        rowend = pos + rowlen
        bm = data[pos:pos+bitmap_bytes]
        pos += bitmap_bytes
        present = []
        for i in range(nfields):
            if bm[i//8] & (0x80 >> (i % 8)):
                present.append(i)
        row = {}
        for idx in present:
            vlen = struct.unpack('>I', data[pos:pos+4])[0]
            pos += 4
            row[fields[idx]['name']] = data[pos:pos+vlen].decode('utf-8')
            pos += vlen
        assert pos == rowend, f"row parse mismatch at {pos} vs {rowend}"
        rows.append(row)
    return fields, rows

for path in sys.argv[1:]:
    print(f"\n===== {path} =====")
    fields, rows = parse(path)
    print(f"Fields ({len(fields)}): " + ", ".join(f['name'] for f in fields))
    print(f"Rows: {len(rows)}")
    for r in rows:
        print(json.dumps(r))
