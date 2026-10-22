export const EQ_BANDS = [32, 64, 125, 250, 500, 1000, 2000, 4000, 8000, 16000];
export const EQ_RANGE = 12;

export const EQ_PRESETS: Record<string, number[]> = {
  Flat: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
  "Bass boost": [6, 5, 4, 2, 0, 0, 0, 0, 0, 0],
  "Bass cut": [-6, -5, -3, -1, 0, 0, 0, 0, 0, 0],
  "Treble boost": [0, 0, 0, 0, 0, 1, 2, 4, 5, 6],
  Vocal: [-2, -2, -1, 1, 3, 4, 3, 1, 0, -1],
  Rock: [4, 3, 2, 0, -1, -1, 1, 2, 3, 4],
  Electronic: [5, 4, 1, 0, -2, 1, 0, 1, 4, 5],
  Acoustic: [3, 3, 2, 1, 1, 1, 2, 2, 2, 1],
  Loudness: [5, 4, 2, 0, -1, -1, 0, 2, 4, 5],
  // Small speakers like the Deck's roll off hard below ~150 Hz; boosting there just distorts.
  "Deck speakers": [-4, -2, 1, 2, 1, 0, 1, 2, 2, 1],
};

export const formatBand = (hz: number) => (hz >= 1000 ? `${hz / 1000}k` : `${hz}`);
