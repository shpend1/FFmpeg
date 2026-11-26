"""Test only: writes the real duration into a fragmented MP4's mvhd (FFmpeg's muxer leaves 0 with empty_moov),
as files from other muxers have it. setdur.py <file> <seconds>"""
import struct, sys
p, secs = sys.argv[1], float(sys.argv[2])
b = bytearray(open(p, 'rb').read(1 << 20))
i = b.index(b'mvhd') - 4
v = b[i + 8]
if v == 1:
    ts = struct.unpack('>I', b[i + 28:i + 32])[0]; b[i + 32:i + 40] = struct.pack('>Q', int(secs * ts))
else:
    ts = struct.unpack('>I', b[i + 20:i + 24])[0]; b[i + 24:i + 28] = struct.pack('>I', int(secs * ts))
with open(p, 'r+b') as f:
    f.write(b)
print('mvhd duration set:', secs, 's at timescale', ts)
