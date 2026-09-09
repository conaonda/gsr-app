(function (root) {
  "use strict";
  function buildPitchReference(field = {}) {
    const length = Number(field.length) > 0 ? Number(field.length) : 40;
    const width = Number(field.width) > 0 ? Number(field.width) : 20;
    const landmarks = [
      ["top-left", "왼쪽 위 코너", [0, 0]],
      ["top-right", "오른쪽 위 코너", [1, 0]],
      ["bottom-right", "오른쪽 아래 코너", [1, 1]],
      ["bottom-left", "왼쪽 아래 코너", [0, 1]],
      ["half-top", "중앙선 · 위 터치라인 교차점", [0.5, 0]],
      ["half-bottom", "중앙선 · 아래 터치라인 교차점", [0.5, 1]],
      ["center", "경기장 중심점", [0.5, 0.5]],
    ].map(([id, name, point]) => ({ id, name, point }));
    const paths = [
      {
        points: [
          [0, 0],
          [1, 0],
          [1, 1],
          [0, 1],
          [0, 0],
        ],
        illustrative: false,
      },
      {
        points: [
          [0.5, 0],
          [0.5, 1],
        ],
        illustrative: false,
      },
    ];
    // These dimensions are drawing examples, not surveyed field facts.
    // Only invariant landmarks above can supply an exact input coordinate.
    const circle = [];
    for (let i = 0; i <= 64; i++) {
      const angle = (i * Math.PI) / 32;
      circle.push([
        0.5 + (3 / length) * Math.cos(angle),
        0.5 + (3 / width) * Math.sin(angle),
      ]);
    }
    paths.push({ points: circle, illustrative: true });
    for (const side of [0, 1]) {
      const points = [];
      for (let i = 0; i <= 48; i++) {
        const angle = -Math.PI / 2 + (i * Math.PI) / 48;
        const x = Math.cos(angle) * Math.min(0.22, 6 / length);
        points.push([
          side === 0 ? x : 1 - x,
          0.5 + Math.sin(angle) * Math.min(0.4, 7.5 / width),
        ]);
      }
      paths.push({ points, illustrative: true });
      paths.push({
        points: [
          [side, 0.425],
          [side === 0 ? -0.035 : 1.035, 0.425],
          [side === 0 ? -0.035 : 1.035, 0.575],
          [side, 0.575],
        ],
        illustrative: true,
      });
    }
    return { landmarks, paths };
  }
  const api = { buildPitchReference };
  root.GSRPitchReference = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
