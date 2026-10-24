/** The space between a grid's cards, across and down */
export const GRID_GAP = 16;
/** A grid's covers by the Cover size choice: about this share of the grid's width each */
export const GRID_SHARE = { small: 0.12, medium: 0.16, large: 0.22 } as const;
/** The smallest a grid's cover gets, however short the screen */
export const GRID_MIN = 64;

/**
 * A grid's shape, the same everywhere a grid is: the covers about the chosen share of its width (so as
 * big as the covers on the rows elsewhere), but never so big that a whole row (cover and the name under
 * it) wouldn't fit under its title; as many columns as that takes, the covers then sized so the columns
 * fill the width edge to edge; and as many whole rows on screen as fit the height. `caption` is a card's
 * height past its cover (0 for tiles), `room` the height under the title. A card is its cover with 2px
 * round it.
 */
export function gridShape(
  width: number,
  room: number,
  caption: number,
  gap: number,
  share: number,
): { cols: number; size: number; rows: number; pitch: number } {
  const wanted = Math.max(GRID_MIN, Math.min(width * share, room - caption - 4));
  const cols = Math.max(2, Math.ceil((width + gap) / (wanted + 4 + gap)));
  const size = Math.max(GRID_MIN, Math.floor((width - (cols - 1) * gap) / cols - 4));
  const pitch = size + 4 + caption + gap;
  const rows = Math.max(1, Math.floor((room + gap) / pitch));
  return { cols, size, rows, pitch };
}
