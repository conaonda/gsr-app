(function attachEditorCore(root, factory) {
  'use strict';

  const api = factory();

  // The browser build is loaded as a plain script, while the test suite loads
  // the same file through require(). Keep both entry points pointing at the
  // exact same API object.
  if (typeof module === 'object' && module && module.exports) {
    module.exports = api;
  }
  if (typeof window !== 'undefined') {
    window.GSREditor = api;
  }
  if (root && (typeof root === 'object' || typeof root === 'function')) {
    root.GSREditor = api;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this, function createEditorCore() {
  'use strict';

  const DEFAULT_MIN_SCALE = 0.05;
  const DEFAULT_MAX_SCALE = 32;
  const DEFAULT_HISTORY_CAPACITY = 100;
  const PROJECTIVE_EPSILON = 1e-12;
  const DETERMINANT_EPSILON = 1e-12;

  function isFiniteNumber(value) {
    return typeof value === 'number' && Number.isFinite(value);
  }

  function positiveDimension(value, name) {
    if (!isFiniteNumber(value) || value <= 0) {
      throw new RangeError(`${name} must be a finite number greater than zero`);
    }
    return value;
  }

  function nonNegativeInteger(value, fallback, name) {
    const numeric = value === undefined ? fallback : Number(value);
    if (!Number.isFinite(numeric) || numeric < 0) {
      throw new RangeError(`${name} must be a finite non-negative number`);
    }
    return Math.floor(numeric);
  }

  function pointComponents(point) {
    if (Array.isArray(point) || ArrayBuffer.isView(point)) {
      return { x: point[0], y: point[1], array: true };
    }
    if (point && typeof point === 'object') {
      return { x: point.x, y: point.y, array: false };
    }
    return null;
  }

  function finitePoint(point) {
    const components = pointComponents(point);
    if (!components || !isFiniteNumber(components.x) || !isFiniteNumber(components.y)) {
      return null;
    }
    return components;
  }

  function pointResult(input, x, y) {
    const components = pointComponents(input);
    return components && components.array ? [x, y] : { x, y };
  }

  function clamp(value, low, high) {
    return Math.min(high, Math.max(low, value));
  }

  class Viewport {
    constructor(options = {}) {
      if (typeof options === 'number') options = { maxScale: options };
      if (!options || typeof options !== 'object') options = {};

      const configuredMin = options.minScale ?? options.minZoom ?? options.zoomMin;
      const configuredMax = options.maxScale ?? options.maxZoom ?? options.zoomMax;
      const minScale = configuredMin === undefined ? DEFAULT_MIN_SCALE : Number(configuredMin);
      const maxScale = configuredMax === undefined ? DEFAULT_MAX_SCALE : Number(configuredMax);
      if (!isFiniteNumber(minScale) || minScale <= 0) {
        throw new RangeError('minScale must be a finite number greater than zero');
      }
      if (!isFiniteNumber(maxScale) || maxScale <= 0 || maxScale < minScale) {
        throw new RangeError('maxScale must be finite, greater than zero, and at least minScale');
      }

      this.minScale = minScale;
      this.maxScale = maxScale;
      this.scale = 1;
      this.offsetX = 0;
      this.offsetY = 0;
      this.imageWidth = 0;
      this.imageHeight = 0;
      this.viewWidth = 0;
      this.viewHeight = 0;
    }

    get sourceWidth() {
      return this.imageWidth;
    }

    get sourceHeight() {
      return this.imageHeight;
    }

    fit(imageWidth, imageHeight, viewWidth, viewHeight) {
      positiveDimension(imageWidth, 'imageWidth');
      positiveDimension(imageHeight, 'imageHeight');
      positiveDimension(viewWidth, 'viewWidth');
      positiveDimension(viewHeight, 'viewHeight');

      this.imageWidth = imageWidth;
      this.imageHeight = imageHeight;
      this.viewWidth = viewWidth;
      this.viewHeight = viewHeight;
      // Fit is deliberately allowed to use the exact contain scale even if a
      // caller supplied zoom bounds that would otherwise clamp interactive
      // zooming. A fit must show the complete source image.
      this.scale = Math.min(viewWidth / imageWidth, viewHeight / imageHeight);
      this.offsetX = (viewWidth - imageWidth * this.scale) / 2;
      this.offsetY = (viewHeight - imageHeight * this.scale) / 2;
      return this;
    }

    resize(viewWidth, viewHeight) {
      positiveDimension(viewWidth, 'viewWidth');
      positiveDimension(viewHeight, 'viewHeight');

      const hadViewport = this.viewWidth > 0 && this.viewHeight > 0 &&
        this.imageWidth > 0 && this.imageHeight > 0 && isFiniteNumber(this.scale) && this.scale > 0;
      let centerImageX = 0;
      let centerImageY = 0;
      if (hadViewport) {
        centerImageX = (this.viewWidth / 2 - this.offsetX) / this.scale;
        centerImageY = (this.viewHeight / 2 - this.offsetY) / this.scale;
      }

      this.viewWidth = viewWidth;
      this.viewHeight = viewHeight;
      if (hadViewport) {
        this.offsetX = viewWidth / 2 - centerImageX * this.scale;
        this.offsetY = viewHeight / 2 - centerImageY * this.scale;
      }
      return this;
    }

    toImage(point) {
      const components = finitePoint(point);
      if (!components || !isFiniteNumber(this.scale) || this.scale <= 0) return null;
      return pointResult(point,
        (components.x - this.offsetX) / this.scale,
        (components.y - this.offsetY) / this.scale);
    }

    toView(point) {
      const components = finitePoint(point);
      if (!components || !isFiniteNumber(this.scale) || this.scale <= 0) return null;
      return pointResult(point,
        components.x * this.scale + this.offsetX,
        components.y * this.scale + this.offsetY);
    }

    zoomAt(factor, point) {
      const cursor = finitePoint(point);
      const numericFactor = Number(factor);
      if (!cursor || !Number.isFinite(numericFactor) || numericFactor <= 0 ||
        !isFiniteNumber(this.scale) || this.scale <= 0) {
        return this;
      }

      const anchorX = (cursor.x - this.offsetX) / this.scale;
      const anchorY = (cursor.y - this.offsetY) / this.scale;
      let nextScale;
      if (numericFactor >= 1 && this.scale >= this.maxScale / numericFactor) {
        nextScale = this.maxScale;
      } else if (numericFactor < 1 && this.scale <= this.minScale / numericFactor) {
        nextScale = this.minScale;
      } else {
        nextScale = this.scale * numericFactor;
      }
      if (!Number.isFinite(nextScale)) nextScale = numericFactor > 1 ? this.maxScale : this.minScale;
      nextScale = clamp(nextScale, this.minScale, this.maxScale);

      this.scale = nextScale;
      this.offsetX = cursor.x - anchorX * nextScale;
      this.offsetY = cursor.y - anchorY * nextScale;
      return this;
    }

    pan(dx, dy) {
      if (dx && typeof dx === 'object' && dy === undefined) {
        dy = dx.y;
        dx = dx.x;
      }
      if (!isFiniteNumber(dx) || !isFiniteNumber(dy)) return this;
      this.offsetX += dx;
      this.offsetY += dy;
      return this;
    }

    oneToOne() {
      this.scale = 1;
      if (this.viewWidth > 0 && this.viewHeight > 0) {
        this.offsetX = (this.viewWidth - this.imageWidth) / 2;
        this.offsetY = (this.viewHeight - this.imageHeight) / 2;
      }
      return this;
    }
  }

  function readMatrix(matrix) {
    if (Array.isArray(matrix) && matrix.length === 3 &&
      matrix.every((row) => (Array.isArray(row) || ArrayBuffer.isView(row)) && row.length === 3)) {
      const values = matrix.reduce((flat, row) => flat.concat([row[0], row[1], row[2]]), []);
      return { values, shape: 'nested' };
    }
    if ((Array.isArray(matrix) || ArrayBuffer.isView(matrix)) && matrix.length === 9) {
      return { values: Array.from(matrix), shape: 'flat' };
    }
    return null;
  }

  function finiteMatrix(values) {
    return values.every((value) => isFiniteNumber(value));
  }

  function formatMatrix(values, shape) {
    if (shape === 'nested') {
      return [values.slice(0, 3), values.slice(3, 6), values.slice(6, 9)];
    }
    return values.slice();
  }

  function project(matrix, point) {
    const parsed = readMatrix(matrix);
    const components = finitePoint(point);
    if (!parsed || !components || !finiteMatrix(parsed.values)) return null;

    const values = parsed.values;
    const x = components.x;
    const y = components.y;
    const numeratorX = values[0] * x + values[1] * y + values[2];
    const numeratorY = values[3] * x + values[4] * y + values[5];
    const denominator = values[6] * x + values[7] * y + values[8];
    const denominatorMagnitude = Math.abs(values[6] * x) + Math.abs(values[7] * y) + Math.abs(values[8]);
    if (!Number.isFinite(numeratorX) || !Number.isFinite(numeratorY) || !Number.isFinite(denominator) ||
      denominatorMagnitude === 0 || Math.abs(denominator) <= PROJECTIVE_EPSILON * denominatorMagnitude) {
      return null;
    }

    const projectedX = numeratorX / denominator;
    const projectedY = numeratorY / denominator;
    if (!Number.isFinite(projectedX) || !Number.isFinite(projectedY)) return null;
    return [projectedX, projectedY];
  }

  function invert(matrix) {
    const parsed = readMatrix(matrix);
    if (!parsed || !finiteMatrix(parsed.values)) return null;

    const values = parsed.values;
    const magnitude = Math.max(...values.map((value) => Math.abs(value)));
    if (!Number.isFinite(magnitude) || magnitude === 0) return null;

    // Normalizing first keeps determinant and cofactors in a useful numeric
    // range. It also makes the singularity check independent of arbitrary
    // homography scale (H and cH describe the same projection).
    const normalized = values.map((value) => value / magnitude);
    const a = normalized[0];
    const b = normalized[1];
    const c = normalized[2];
    const d = normalized[3];
    const e = normalized[4];
    const f = normalized[5];
    const g = normalized[6];
    const h = normalized[7];
    const i = normalized[8];

    const cofactor = [
      e * i - f * h,
      c * h - b * i,
      b * f - c * e,
      f * g - d * i,
      a * i - c * g,
      c * d - a * f,
      d * h - e * g,
      b * g - a * h,
      a * e - b * d,
    ];
    const determinant = a * cofactor[0] + b * cofactor[3] + c * cofactor[6];
    if (!Number.isFinite(determinant) || Math.abs(determinant) <= DETERMINANT_EPSILON) return null;

    const inverseScale = 1 / magnitude;
    const inverse = cofactor.map((value) => value / determinant * inverseScale);
    if (!finiteMatrix(inverse)) return null;
    return formatMatrix(inverse, parsed.shape);
  }

  function deepClone(value, seen = new WeakMap()) {
    if (value === null || typeof value !== 'object') return value;
    if (seen.has(value)) return seen.get(value);

    if (value instanceof Date) return new Date(value.getTime());
    if (value instanceof RegExp) return new RegExp(value.source, value.flags);
    if (typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer) return value.slice(0);
    if (typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(value)) {
      if (value instanceof DataView) {
        return new DataView(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
      }
      return new value.constructor(value);
    }

    if (value instanceof Map) {
      const copy = new Map();
      seen.set(value, copy);
      value.forEach((entryValue, entryKey) => copy.set(deepClone(entryKey, seen), deepClone(entryValue, seen)));
      return copy;
    }
    if (value instanceof Set) {
      const copy = new Set();
      seen.set(value, copy);
      value.forEach((entry) => copy.add(deepClone(entry, seen)));
      return copy;
    }

    const copy = Array.isArray(value) ? [] : Object.create(Object.getPrototypeOf(value));
    seen.set(value, copy);
    Reflect.ownKeys(value).forEach((key) => {
      const descriptor = Object.getOwnPropertyDescriptor(value, key);
      if (descriptor && Object.prototype.hasOwnProperty.call(descriptor, 'value')) {
        descriptor.value = deepClone(descriptor.value, seen);
      }
      try {
        Object.defineProperty(copy, key, descriptor);
      } catch (_) {
        copy[key] = deepClone(value[key], seen);
      }
    });
    return copy;
  }

  function deepEqual(left, right, seen = new Map()) {
    if (Object.is(left, right)) return true;
    if (left === null || right === null || typeof left !== 'object' || typeof right !== 'object') return false;

    const previous = seen.get(left);
    if (previous) return previous === right;
    seen.set(left, right);

    if (left instanceof Date || right instanceof Date) {
      return left instanceof Date && right instanceof Date && left.getTime() === right.getTime();
    }
    if (left instanceof RegExp || right instanceof RegExp) {
      return left instanceof RegExp && right instanceof RegExp && left.source === right.source && left.flags === right.flags;
    }
    if (typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(left) || typeof ArrayBuffer !== 'undefined' && ArrayBuffer.isView(right)) {
      if (!ArrayBuffer.isView(left) || !ArrayBuffer.isView(right) || left.constructor !== right.constructor || left.length !== right.length) return false;
      for (let index = 0; index < left.length; index += 1) if (!Object.is(left[index], right[index])) return false;
      return true;
    }
    if (typeof ArrayBuffer !== 'undefined' && left instanceof ArrayBuffer || typeof ArrayBuffer !== 'undefined' && right instanceof ArrayBuffer) {
      if (!(left instanceof ArrayBuffer) || !(right instanceof ArrayBuffer) || left.byteLength !== right.byteLength) return false;
      const a = new Uint8Array(left);
      const b = new Uint8Array(right);
      for (let index = 0; index < a.length; index += 1) if (a[index] !== b[index]) return false;
      return true;
    }
    if (left instanceof Map || right instanceof Map) {
      if (!(left instanceof Map) || !(right instanceof Map) || left.size !== right.size) return false;
      const leftEntries = [...left.entries()];
      const rightEntries = [...right.entries()];
      return leftEntries.every((entry, index) => deepEqual(entry[0], rightEntries[index][0], seen) && deepEqual(entry[1], rightEntries[index][1], seen));
    }
    if (left instanceof Set || right instanceof Set) {
      if (!(left instanceof Set) || !(right instanceof Set) || left.size !== right.size) return false;
      const leftValues = [...left.values()];
      const rightValues = [...right.values()];
      return leftValues.every((entry, index) => deepEqual(entry, rightValues[index], seen));
    }

    if (Array.isArray(left) || Array.isArray(right)) {
      if (!Array.isArray(left) || !Array.isArray(right) || left.length !== right.length) return false;
      for (let index = 0; index < left.length; index += 1) {
        if (!deepEqual(left[index], right[index], seen)) return false;
      }
      return true;
    }

    const leftKeys = Reflect.ownKeys(left);
    const rightKeys = Reflect.ownKeys(right);
    if (leftKeys.length !== rightKeys.length) return false;
    for (const key of leftKeys) {
      if (!Object.prototype.hasOwnProperty.call(right, key) || !deepEqual(left[key], right[key], seen)) return false;
    }
    return true;
  }

  function historyCapacity(options) {
    if (typeof options === 'number') return nonNegativeInteger(options, DEFAULT_HISTORY_CAPACITY, 'capacity');
    if (!options || typeof options !== 'object') return DEFAULT_HISTORY_CAPACITY;
    return nonNegativeInteger(options.capacity ?? options.maxEntries ?? options.limit,
      DEFAULT_HISTORY_CAPACITY, 'capacity');
  }

  class History {
    constructor(options = DEFAULT_HISTORY_CAPACITY) {
      this.capacity = historyCapacity(options);
      this._past = [];
      this._future = [];
      this._present = undefined;
      this._initialized = false;
    }

    get canUndo() {
      return this._past.length > 0;
    }

    get canRedo() {
      return this._future.length > 0;
    }

    get current() {
      return this._initialized ? deepClone(this._present) : null;
    }

    get size() {
      return (this._initialized ? 1 : 0) + this._past.length;
    }

    reset(snapshot) {
      this._present = deepClone(snapshot);
      this._past = [];
      this._future = [];
      this._initialized = true;
      return this;
    }

    commit(snapshot) {
      if (!this._initialized) return this.reset(snapshot);
      if (deepEqual(this._present, snapshot)) return this;

      if (this.capacity > 0) {
        this._past.push(deepClone(this._present));
        if (this._past.length > this.capacity) this._past.splice(0, this._past.length - this.capacity);
      }
      this._present = deepClone(snapshot);
      this._future = [];
      return this;
    }

    undo() {
      if (!this.canUndo) return null;
      this._future.push(deepClone(this._present));
      this._present = this._past.pop();
      return deepClone(this._present);
    }

    redo() {
      if (!this.canRedo) return null;
      if (this.capacity > 0) {
        this._past.push(deepClone(this._present));
        if (this._past.length > this.capacity) this._past.splice(0, this._past.length - this.capacity);
      }
      this._present = this._future.pop();
      return deepClone(this._present);
    }
  }

  class DraftStore {
    constructor() {
      this._snapshots = new Map();
    }

    load(key) {
      return this._snapshots.has(key) ? deepClone(this._snapshots.get(key)) : null;
    }

    save(key, snapshot) {
      this._snapshots.set(key, deepClone(snapshot));
      return this;
    }

    clear(key) {
      if (arguments.length > 0) this._snapshots.delete(key);
      else this._snapshots.clear();
      return this;
    }

    get size() {
      return this._snapshots.size;
    }
  }

  return { Viewport, project, invert, History, DraftStore };
}));
