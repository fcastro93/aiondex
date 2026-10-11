"""AION2 wire format.

Frame:  varint L | 2-byte opcode | body       (frame size = varint_bytes + L - 4)
Bundle: opcode ffff, body = u32 LE uncompressed size | LZ4 block holding more frames
Server->client bodies are plaintext; client->server bodies are encrypted.
"""

BUNDLE_OPCODE = b"\xff\xff"
MAX_FRAME = 1 << 20


def read_varint(buf, pos):
    value = shift = 0
    while pos < len(buf):
        b = buf[pos]
        value |= (b & 0x7F) << shift
        pos += 1
        if not b & 0x80:
            return value, pos
        shift += 7
        if shift > 28:
            return None, pos
    return None, pos


def frame_size(buf, pos):
    """Size of the frame starting at pos, None if the header is incomplete, -1 if invalid."""
    length, body = read_varint(buf, pos)
    if length is None:
        return None if body >= len(buf) else -1
    size = (body - pos) + length - 4
    if length < 4 or size < (body - pos) + 2 or size > MAX_FRAME:
        return -1
    return size


def split_frames(data):
    """Yield complete frames from a buffer that starts on a frame boundary."""
    pos = 0
    while pos < len(data):
        size = frame_size(data, pos)
        if size is None or size < 0 or pos + size > len(data):
            return
        yield data[pos:pos + size]
        pos += size


def opcode_and_body(frame):
    _, p = read_varint(frame, 0)
    return frame[p:p + 2], frame[p + 2:]


def lz4_block_decompress(src, out_size):
    dst = bytearray()
    i = 0
    n = len(src)
    while i < n:
        token = src[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = src[i]
                i += 1
                lit += b
                if b != 255:
                    break
        dst += src[i:i + lit]
        i += lit
        if i >= n or len(dst) >= out_size:
            break
        offset = src[i] | (src[i + 1] << 8)
        i += 2
        mlen = token & 0x0F
        if mlen == 15:
            while True:
                b = src[i]
                i += 1
                mlen += b
                if b != 255:
                    break
        mlen += 4
        start = len(dst) - offset
        if offset >= mlen:
            dst += dst[start:start + mlen]
        else:
            for k in range(mlen):
                dst.append(dst[start + k])
    return bytes(dst)


def expand(frame, depth=0):
    """Yield (opcode bytes, body) for a frame, unpacking LZ4 bundles."""
    op, body = opcode_and_body(frame)
    if op == BUNDLE_OPCODE and depth < 3 and len(body) > 4:
        raw_size = int.from_bytes(body[:4], "little")
        try:
            inner = lz4_block_decompress(body[4:], raw_size)
        except IndexError:
            return
        for sub in split_frames(inner):
            yield from expand(sub, depth + 1)
    else:
        yield op, body
