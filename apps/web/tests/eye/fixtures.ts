/**
 * Synthetic pixel fixtures for the Active Eye's derivation tests.
 *
 * Never real imagery — per the M18 task brief, every frame here is a plain
 * generated RGBA array, either a flat colour (a static scene) or simple
 * per-cell blocks (to make one region "move" without any picture existing).
 */

/** A `width`x`height` RGBA buffer, one flat colour throughout. */
export function flatFrame(width: number, height: number, gray: number): Uint8ClampedArray {
  const data = new Uint8ClampedArray(width * height * 4);
  for (let i = 0; i < data.length; i += 4) {
    data[i] = gray;
    data[i + 1] = gray;
    data[i + 2] = gray;
    data[i + 3] = 255;
  }
  return data;
}

/**
 * A frame that is `base` gray everywhere except one rectangular block, which
 * is `blockGray`. Used to simulate "something in the room changed" between
 * two synthetic frames without any real image ever existing.
 */
export function blockFrame(
  width: number,
  height: number,
  base: number,
  block: { x0: number; y0: number; x1: number; y1: number; gray: number },
): Uint8ClampedArray {
  const data = flatFrame(width, height, base);
  for (let y = block.y0; y < block.y1; y++) {
    for (let x = block.x0; x < block.x1; x++) {
      const idx = (y * width + x) * 4;
      data[idx] = block.gray;
      data[idx + 1] = block.gray;
      data[idx + 2] = block.gray;
      data[idx + 3] = 255;
    }
  }
  return data;
}
