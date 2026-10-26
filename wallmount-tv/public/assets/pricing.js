// Single source of truth for prices and form options.
// Imported by the browser estimator (assets/app.js) and by the server (server/quotes.js),
// so a submitted quote is always re-priced server-side from these numbers.
// Edit the amounts here to change prices everywhere.

export const TV_SIZES = {
  small: { label: 'Up to 42"', price: 129 },
  medium: { label: '43" – 65"', price: 159 },
  large: { label: '66" – 85"', price: 199 },
  xl: { label: '86" and larger', price: 259 },
};

export const WALL_TYPES = {
  drywall: { label: 'Drywall (wood or metal studs)', price: 0 },
  masonry: { label: 'Brick, stone or concrete', price: 60 },
  fireplace: { label: 'Above a fireplace', price: 80 },
  unsure: { label: 'Not sure', price: 0 },
};

export const MOUNTS = {
  own: { label: 'I have a mount', price: 0 },
  fixed: { label: 'Fixed (slim)', price: 39 },
  tilting: { label: 'Tilting', price: 59 },
  full_motion: { label: 'Full-motion', price: 99 },
};

export const CONCEALMENT = {
  none: { label: 'No concealment', price: 0 },
  cover: { label: 'Paintable cord cover', price: 39 },
  in_wall: { label: 'In-wall (behind drywall)', price: 129 },
};

export const EXTRAS = {
  soundbar: { label: 'Soundbar mounting', price: 49 },
  setup: { label: 'Device setup & Wi-Fi', price: 29 },
  removal: { label: 'Remove old mount & patch', price: 39 },
};

// Each additional TV in the same visit gets this discount on its line.
export const MULTI_TV_DISCOUNT = 0.1;
export const MAX_TVS = 10;

/**
 * Price a job. Unknown keys throw, so callers must validate first.
 * @returns {{ perTv: number, tvCount: number, extras: number, discount: number, total: number }}
 */
export function estimate({ tvSize, wallType, mount, concealment, extras = [], tvCount = 1 }) {
  const pick = (table, key) => {
    if (!Object.hasOwn(table, key)) throw new RangeError(`Unknown option: ${key}`);
    return table[key].price;
  };
  const perTv =
    pick(TV_SIZES, tvSize) + pick(WALL_TYPES, wallType) + pick(MOUNTS, mount) + pick(CONCEALMENT, concealment);
  const extrasTotal = [...new Set(extras)].reduce((sum, key) => sum + pick(EXTRAS, key), 0);
  const discount = Math.round(perTv * MULTI_TV_DISCOUNT * (tvCount - 1));
  return {
    perTv,
    tvCount,
    extras: extrasTotal,
    discount,
    total: perTv * tvCount - discount + extrasTotal,
  };
}
