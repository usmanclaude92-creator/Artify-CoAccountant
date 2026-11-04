import { createHmac } from "node:crypto";

const ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

function base32Decode(s: string): Buffer {
  let bits = "";
  for (const ch of s.replace(/=+$/, "").toUpperCase()) bits += ALPHABET.indexOf(ch).toString(2).padStart(5, "0");
  const bytes: number[] = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) bytes.push(parseInt(bits.slice(i, i + 8), 2));
  return Buffer.from(bytes);
}

/** RFC 6238 TOTP (SHA-1, 30 s, 6 digits). `stepOffset` shifts by whole 30-second steps. */
export function totp(secret: string, stepOffset = 0): string {
  const counter = Math.floor(Date.now() / 1000 / 30) + stepOffset;
  const buf = Buffer.alloc(8);
  buf.writeBigUInt64BE(BigInt(counter));
  const h = createHmac("sha1", base32Decode(secret)).update(buf).digest();
  const o = (h[h.length - 1] ?? 0) & 0xf;
  const code = (((h[o] ?? 0) & 0x7f) << 24) | (((h[o + 1] ?? 0) & 0xff) << 16) | (((h[o + 2] ?? 0) & 0xff) << 8) | ((h[o + 3] ?? 0) & 0xff);
  return String(code % 1_000_000).padStart(6, "0");
}
