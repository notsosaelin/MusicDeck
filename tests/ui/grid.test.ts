/** The media center's grids: their shape for the screen they're on (components/media/grid.ts). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { GRID_GAP, GRID_MIN, GRID_SHARE, gridShape } from "../../src/components/media/grid";

// width of the grid and its room under the title, in CSS pixels, as measured on devices
const SCREENS = {
  "a handheld at 1080p with Steam's UI scaled up, the mini player showing": { width: 735, room: 200 },
  "the same, nothing playing": { width: 735, room: 270 },
  "Steam Deck (1280x800)": { width: 1180, room: 520 },
  "a 1080p TV": { width: 1800, room: 820 },
};
const CAPTION = 46; // the name and artist under a cover

for (const [name, { width, room }] of Object.entries(SCREENS))
  for (const [choice, share] of Object.entries(GRID_SHARE))
    test(`${name}, ${choice} covers: fills the width, whole rows only`, () => {
      const { cols, size, rows, pitch } = gridShape(width, room, CAPTION, GRID_GAP, share);
      const across = cols * (size + 4) + (cols - 1) * GRID_GAP;
      // edge to edge: what's left over is the rounding of each column, a few pixels, not a column's gap
      assert.ok(across <= width && width - across < cols + 1, `${cols} × ${size}px is ${across} of ${width}`);
      // the rows on screen are whole: they fit under the title, and one more wouldn't
      assert.ok(rows * pitch - GRID_GAP <= room, `${rows} rows of ${pitch} in ${room}`);
      assert.ok((rows + 1) * pitch - GRID_GAP > room, `room for another row of ${pitch} in ${room}`);
      // at least one whole row (cover and name), and covers never tiny
      assert.ok(size + 4 + CAPTION <= room && size >= GRID_MIN, `a ${size}px cover in ${room}`);
      assert.ok(cols >= 2);
    });

test("bigger cover choices give fewer, bigger covers", () => {
  const { width, room } = SCREENS["Steam Deck (1280x800)"];
  const [small, medium, large] = [GRID_SHARE.small, GRID_SHARE.medium, GRID_SHARE.large].map((share) =>
    gridShape(width, room, CAPTION, GRID_GAP, share),
  );
  assert.ok(small.size < medium.size && medium.size < large.size);
  assert.ok(small.cols > medium.cols && medium.cols > large.cols);
});

test("the shape doesn't depend on where the rows sit, only on the room (no feedback between the two)", () => {
  // the same inputs always give the same shape: measuring the title, not where the rows start
  const a = gridShape(735, 200, CAPTION, GRID_GAP, GRID_SHARE.medium);
  const b = gridShape(735, 200, CAPTION, GRID_GAP, GRID_SHARE.medium);
  assert.deepEqual(a, b);
});

test("tiles (names inside) fit more rows than covers with names under", () => {
  const covers = gridShape(1180, 520, CAPTION, GRID_GAP, GRID_SHARE.medium);
  const tiles = gridShape(1180, 520, 0, GRID_GAP, GRID_SHARE.medium);
  assert.ok(tiles.rows >= covers.rows && tiles.pitch < covers.pitch);
});
