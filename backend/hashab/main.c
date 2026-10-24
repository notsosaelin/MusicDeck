/*
 * hashab: the iPod nano 6th/7th generation's database signature (HashAB), for musicdeck/ipod.py.
 *
 *   hashab <sha1 hex, 40> <FireWire GUID hex, 16>   prints the 57-byte signature as hex
 *
 * The signature itself is dstaley/hashab (src/, public domain, The Unlicense; the commit it's from is in
 * UPSTREAM_COMMIT), checked here against its test vectors (scripts/build_hashab.sh). Its 23 "random"
 * bytes are the fixed ones libgpod and iOpenPod use.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "calcHashAB.h"

static int from_hex(const char *text, uint8_t *out, size_t len) {
  if (strlen(text) != len * 2) return -1;
  for (size_t i = 0; i < len; i++) {
    unsigned int byte;
    if (sscanf(text + 2 * i, "%2x", &byte) != 1) return -1;
    out[i] = (uint8_t)byte;
  }
  return 0;
}

int main(int argc, char **argv) {
  uint8_t sha1[20], uuid[8], target[57];
  static const uint8_t random_bytes[23] = "ABCDEFGHIJKLMNOPQRSTUVW";
  if (argc != 3 || from_hex(argv[1], sha1, sizeof sha1) || from_hex(argv[2], uuid, sizeof uuid)) {
    fprintf(stderr, "usage: hashab <sha1: 40 hex digits> <FireWire GUID: 16 hex digits>\n");
    return 2;
  }
  calcHashAB(target, sha1, uuid, random_bytes);
  for (size_t i = 0; i < sizeof target; i++) printf("%02x", target[i]);
  printf("\n");
  return 0;
}
