import struct, re, sys

data = open('/mnt/user-data/uploads/TABLE_INFO','rb').read()

# ArrayList size: 4-byte int right after 'sizexp' marker
m = data.find(b'sizexp')
size = struct.unpack('>I', data[m+6:m+10])[0]
print(f"Declared list size: {size}")

tables = []
# First element: full classdesc ends with 'xp', then 8-byte long, then TC_STRING (0x74) 2-byte len + name
# Subsequent: 'sq \x00\x7e\x00\x02' (TC_OBJECT + back-reference) + 8-byte long + 0x74 + len + name
pos = data.find(b'Ljava/lang/String;xp') + len(b'Ljava/lang/String;xp')
noRec = struct.unpack('>q', data[pos:pos+8])[0]; pos += 8
assert data[pos] == 0x74
slen = struct.unpack('>H', data[pos+1:pos+3])[0]
name = data[pos+3:pos+3+slen].decode(); pos += 3+slen
tables.append((name, noRec))

pat = re.compile(rb'sq\x00\x7e\x00\x02(.{8})\x74(..)', re.DOTALL)
while True:
    m = pat.match(data, pos)
    if not m: break
    noRec = struct.unpack('>q', m.group(1))[0]
    slen = struct.unpack('>H', m.group(2))[0]
    name = data[m.end():m.end()+slen].decode()
    tables.append((name, noRec))
    pos = m.end() + slen

print(f"Parsed: {len(tables)} tables, leftover bytes after last: {len(data)-pos}")
total = sum(n for _,n in tables)
nonempty = [(t,n) for t,n in tables if n > 0]
print(f"Total records across all tables: {total:,}")
print(f"Non-empty tables: {len(nonempty)}")
print("\nTop 30 by record count:")
for t,n in sorted(tables, key=lambda x:-x[1])[:30]:
    print(f"  {t:<10} {n:>12,}")

import csv
with open('/mnt/user-data/outputs/table_info.csv','w',newline='') as f:
    w = csv.writer(f); w.writerow(['table','records'])
    for t,n in sorted(tables): w.writerow([t,n])
