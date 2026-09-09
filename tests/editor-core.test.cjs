'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const {
  Viewport,
  project,
  invert,
  History,
  DraftStore,
} = require('../web/editor-core.js');

function approx(actual, expected, epsilon = 1e-9) {
  assert.ok(Math.abs(actual - expected) <= epsilon,
    `expected ${actual} to be within ${epsilon} of ${expected}`);
}

function approxPoint(actual, expected, epsilon = 1e-9) {
  approx(actual[0], expected[0], epsilon);
  approx(actual[1], expected[1], epsilon);
}

test('exports the same API globally and through CommonJS', () => {
  assert.equal(globalThis.GSREditor.Viewport, Viewport);
  assert.equal(globalThis.GSREditor.project, project);
  assert.equal(globalThis.GSREditor.invert, invert);
});

test('fit uses CSS pixels, records dimensions, and resets the camera', () => {
  const viewport = new Viewport();
  viewport.fit(1920, 1080, 960, 720);
  assert.equal(viewport.imageWidth, 1920);
  assert.equal(viewport.imageHeight, 1080);
  assert.equal(viewport.viewWidth, 960);
  assert.equal(viewport.viewHeight, 720);
  approx(viewport.scale, 0.5);
  approx(viewport.offsetX, 0);
  approx(viewport.offsetY, 90);
  viewport.pan(40, -12).zoomAt(2, { x: 300, y: 250 });
  viewport.fit(1920, 1080, 960, 720);
  approx(viewport.scale, 0.5);
  approx(viewport.offsetX, 0);
  approx(viewport.offsetY, 90);
});

test('image/view conversions are inverse and independent of device pixel ratio', () => {
  const viewport = new Viewport();
  viewport.fit(1000, 500, 500, 500);
  const source = { x: 225, y: 175 };
  const view = viewport.toView(source);
  assert.deepEqual(view, { x: 112.5, y: 212.5 });
  assert.deepEqual(viewport.toImage(view), source);
  // A caller passes CSS pixels; DPR is intentionally not part of the model.
  assert.deepEqual(viewport.toImage({ x: 250, y: 250 }), { x: 500, y: 250 });
});

test('resize preserves the image point at the viewport center', () => {
  const viewport = new Viewport();
  viewport.fit(1200, 800, 600, 600).pan(-80, 35).zoomAt(1.75, { x: 110, y: 220 });
  const oldCenter = viewport.toImage({ x: 300, y: 300 });
  viewport.resize(900, 500);
  const newCenter = viewport.toImage({ x: 450, y: 250 });
  approx(newCenter.x, oldCenter.x);
  approx(newCenter.y, oldCenter.y);
});

test('zoomAt anchors the cursor and clamps interactive zoom', () => {
  const viewport = new Viewport({ minScale: 0.25, maxScale: 4 });
  viewport.fit(1000, 500, 500, 300);
  const cursor = { x: 321, y: 178 };
  const imageAtCursor = viewport.toImage(cursor);
  viewport.zoomAt(2, cursor);
  const anchored = viewport.toImage(cursor);
  approx(anchored.x, imageAtCursor.x);
  approx(anchored.y, imageAtCursor.y);
  viewport.zoomAt(1000, cursor);
  assert.equal(viewport.scale, 4);
  viewport.zoomAt(0.000001, cursor);
  assert.equal(viewport.scale, 0.25);
});

test('oneToOne uses one source pixel per CSS pixel and centers the image', () => {
  const viewport = new Viewport();
  viewport.fit(320, 180, 800, 600).oneToOne();
  assert.equal(viewport.scale, 1);
  approx(viewport.offsetX, 240);
  approx(viewport.offsetY, 210);
  assert.deepEqual(viewport.toView({ x: 0, y: 0 }), { x: 240, y: 210 });
});

test('projects non-affine homographies and inverts them', () => {
  const matrix = [
    [2, 0.25, 5],
    [0.1, 3, -4],
    [0.002, -0.001, 1],
  ];
  const point = [120, 80];
  const mapped = project(matrix, point);
  assert.ok(mapped);
  const inverse = invert(matrix);
  assert.ok(inverse);
  approxPoint(project(inverse, mapped), point, 1e-7);
  // Flat input remains flat so callers can use either common representation.
  const flatInverse = invert(matrix.flat());
  assert.equal(flatInverse.length, 9);
  approxPoint(project(flatInverse, mapped), point, 1e-7);
});

test('projection and inversion reject malformed, singular, and unsafe input', () => {
  assert.equal(project([[1, 0, 0], [0, 1, 0], [0, 0, 0]], [2, 3]), null);
  assert.equal(project([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [Infinity, 3]), null);
  assert.equal(project([[1, 0, 0], [0, 1, 0], [1, 0, -1]], [1, 0]), null);
  assert.equal(invert([[1, 2, 3], [2, 4, 6], [0, 0, 1]]), null);
  assert.equal(invert([[1, 0, 0], [0, 1, 0], [0, 0, 1e-14]]), null);
  assert.equal(invert([[1, 0], [0, 1]]), null);
});

test('history deep-copies snapshots, deduplicates, and clears redo branches', () => {
  const history = new History(10);
  const initial = { points: [{ x: 1, y: 2 }], field: { length: 100 } };
  history.reset(initial);
  initial.points[0].x = 999;
  assert.equal(history.current.points[0].x, 1);
  history.commit({ points: [{ x: 2, y: 2 }], field: { length: 100 } });
  history.commit({ points: [{ x: 2, y: 2 }], field: { length: 100 } });
  assert.equal(history.canUndo, true);
  const undone = history.undo();
  undone.points[0].x = -10;
  assert.equal(history.current.points[0].x, 1);
  assert.equal(history.canRedo, true);
  assert.equal(history.redo().points[0].x, 2);
  history.undo();
  history.commit({ points: [{ x: 3, y: 3 }], field: { length: 100 } });
  assert.equal(history.canRedo, false);
});

test('history retains at most its configured undo capacity', () => {
  const history = new History({ capacity: 2 });
  history.reset({ value: 0 });
  history.commit({ value: 1 });
  history.commit({ value: 2 });
  history.commit({ value: 3 });
  assert.deepEqual(history.undo(), { value: 2 });
  assert.deepEqual(history.undo(), { value: 1 });
  assert.equal(history.undo(), null);
});

test('draft store isolates saved and loaded snapshots by key', () => {
  const store = new DraftStore();
  const source = { pairs: [{ image: [10, 20] }], nested: { value: 1 } };
  store.save('video:rev:0', source);
  source.pairs[0].image[0] = 999;
  const loaded = store.load('video:rev:0');
  assert.equal(loaded.pairs[0].image[0], 10);
  loaded.nested.value = 55;
  assert.equal(store.load('video:rev:0').nested.value, 1);
  assert.equal(store.load('missing'), null);
  store.save('other', { value: 2 });
  store.clear('video:rev:0');
  assert.equal(store.load('video:rev:0'), null);
  assert.deepEqual(store.load('other'), { value: 2 });
  store.clear();
  assert.equal(store.size, 0);
});
