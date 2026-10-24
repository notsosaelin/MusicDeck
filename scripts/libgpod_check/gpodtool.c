/* gpodtool: libgpod as the reference for MusicDeck's iPod code (scripts/libgpod_check/check.py).
     make <mountpoint> (<title> <artist> <album> <ipod path>)...  a new iTunesDB, written by libgpod
                                                                 (signed, and an iTunesSD, as SysInfo's
                                                                 ModelNumStr asks)
     dump <mountpoint>                                           libgpod reads it: tracks and playlists
     makeart <mountpoint> <image> (<title> <artist> <album> <ipod path>)...  as make, every track given
                                                                 <image> as its cover (an ArtworkDB too)
     art <mountpoint>                                            libgpod reads the covers: per track, its
                                                                 artwork id and each picture's size and
                                                                 middle pixel */
#include <gdk-pixbuf/gdk-pixbuf.h>
#include <gpod/itdb.h>
#include <locale.h>
#include <stdio.h>
#include <string.h>

int main(int argc, char **argv) {
  GError *err = NULL;
  /* libgpod sorts the iPod's lists by the locale's collation, as gtkpod does */
  setlocale(LC_ALL, "");
  int art = argc >= 4 && !strcmp(argv[1], "makeart");
  if (art || (argc >= 3 && !strcmp(argv[1], "make"))) {
    Itdb_iTunesDB *db = itdb_new();
    itdb_set_mountpoint(db, argv[2]);
    Itdb_Playlist *mpl = itdb_playlist_new("Test iPod", FALSE);
    itdb_playlist_set_mpl(mpl);
    itdb_playlist_add(db, mpl, -1);
    for (int i = 3 + art; i + 3 < argc; i += 4) {
      Itdb_Track *t = itdb_track_new();
      t->title = g_strdup(argv[i]);
      t->artist = g_strdup(argv[i + 1]);
      t->album = g_strdup(argv[i + 2]);
      t->ipod_path = g_strdup(argv[i + 3]);
      t->filetype = g_strdup("MPEG audio file");
      t->tracklen = 1000 * (i + 1);
      t->size = 1000;
      t->track_nr = i / 4 + 1;
      t->dbid = 0x1000 + i;
      t->dbid2 = t->dbid;
      t->time_added = 1700000000;
      t->time_modified = 1700000000;
      if (art && !itdb_track_set_thumbnails(t, argv[3])) {
        fprintf(stderr, "cover: %s\n", argv[3]);
        return 1;
      }
      itdb_track_add(db, t, -1);
      itdb_playlist_add_track(mpl, t, -1);
    }
    db->id = 0x0102030405060708ULL;
    if (!itdb_write(db, &err)) {
      fprintf(stderr, "write: %s\n", err ? err->message : "?");
      return 1;
    }
    printf("written\n");
    return 0;
  }
  if (argc == 3 && !strcmp(argv[1], "dump")) {
    Itdb_iTunesDB *db = itdb_parse(argv[2], &err);
    if (!db) {
      fprintf(stderr, "parse: %s\n", err ? err->message : "?");
      return 1;
    }
    printf("tracks %u\n", itdb_tracks_number(db));
    for (GList *l = db->tracks; l; l = l->next) {
      Itdb_Track *t = l->data;
      printf("track|%s|%s|%s|%s|%u|%u|%u|%u\n", t->title ? t->title : "", t->artist ? t->artist : "",
             t->album ? t->album : "", t->ipod_path ? t->ipod_path : "", t->tracklen, t->track_nr, t->size,
             t->playcount);
    }
    for (GList *l = db->playlists; l; l = l->next) {
      Itdb_Playlist *p = l->data;
      printf("playlist|%s|%d|%u", p->name, itdb_playlist_is_mpl(p), itdb_playlist_tracks_number(p));
      for (GList *m = p->members; m; m = m->next) printf("|%s", ((Itdb_Track *)m->data)->title);
      printf("\n");
    }
    return 0;
  }
  if (argc == 3 && !strcmp(argv[1], "art")) {
    Itdb_iTunesDB *db = itdb_parse(argv[2], &err);
    if (!db) {
      fprintf(stderr, "parse: %s\n", err ? err->message : "?");
      return 1;
    }
    for (GList *l = db->tracks; l; l = l->next) {
      Itdb_Track *t = l->data;
      printf("art|%s|%d|%u|%u", t->title ? t->title : "", itdb_track_has_thumbnails(t), t->mhii_link,
             t->artwork ? t->artwork->id : 0);
      if (itdb_track_has_thumbnails(t)) {
        GList *pixbufs = itdb_thumb_to_pixbufs(db->device, t->artwork->thumbnail);
        for (GList *p = pixbufs; p; p = p->next) {
          GdkPixbuf *pb = p->data;
          int w = gdk_pixbuf_get_width(pb), h = gdk_pixbuf_get_height(pb);
          guchar *px = gdk_pixbuf_get_pixels(pb) + (h / 2) * gdk_pixbuf_get_rowstride(pb)
                       + (w / 2) * gdk_pixbuf_get_n_channels(pb);
          printf("|%dx%d:%02x%02x%02x", w, h, px[0], px[1], px[2]);
        }
      }
      printf("\n");
    }
    return 0;
  }
  fprintf(stderr, "usage: gpodtool make|makeart|dump|art <mountpoint> ... (see the top of gpodtool.c)\n");
  return 2;
}
