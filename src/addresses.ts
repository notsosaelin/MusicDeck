/** Home-network addresses: plain http:// is normal there and stays inside the house. */
const PRIVATE_HOST =
  /^(localhost|[^.:[\]]+|.+\.(local|lan|home|internal)|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d+\.\d+|\[(::1|f[cd][0-9a-f]*:[^\]]*|fe80:[^\]]*)\])$/i;

/**
 * A warning for a server address that would send the password, and everything else, unencrypted
 * across the internet: http:// to anything outside the home network (Tailscale addresses count as
 * home, being encrypted already). Undefined when the address is fine.
 */
export function unencryptedWarning(url: string | undefined): string | undefined {
  const match = /^http:\/\/(\[[^\]]+\]|[^/:?#]+)/i.exec((url ?? "").trim());
  if (!match || PRIVATE_HOST.test(match[1])) return undefined;
  return "Not encrypted: your password and music would cross the internet readable by anyone on the way. Use https://.";
}
