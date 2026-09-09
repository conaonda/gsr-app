(() => {
  "use strict";

  const API_BASE = "";
  const core = window.GSREditor || {};
  const $ = (id) => document.getElementById(id);
  const els = {};

  const DEFAULT_FIELD = () => ({
    length: null,
    width: null,
    dimension_source: "",
    dimensions_verified: false,
  });

  function deepClone(value) {
    if (value === undefined) return undefined;
    try {
      return JSON.parse(JSON.stringify(value));
    } catch (_) {
      return value;
    }
  }

  class MemoryDraftStore {
    constructor() {
      this.values = new Map();
    }
    load(key) {
      return this.values.has(key) ? deepClone(this.values.get(key)) : null;
    }
    save(key, snapshot) {
      this.values.set(key, deepClone(snapshot));
    }
    clear(key) {
      if (key === undefined) this.values.clear();
      else this.values.delete(key);
    }
  }

  class MemoryHistory {
    constructor(capacity = 100) {
      this.capacity = Math.max(1, Number(capacity) || 100);
      this.values = [];
      this.index = -1;
    }
    reset(snapshot) {
      this.values = [deepClone(snapshot)];
      this.index = 0;
    }
    commit(snapshot) {
      const copy = deepClone(snapshot);
      const current = this.index >= 0 ? this.values[this.index] : null;
      if (JSON.stringify(current) === JSON.stringify(copy)) return;
      this.values = this.values.slice(0, this.index + 1);
      this.values.push(copy);
      if (this.values.length > this.capacity) this.values.shift();
      this.index = this.values.length - 1;
    }
    undo() {
      if (this.index <= 0) return null;
      this.index -= 1;
      return deepClone(this.values[this.index]);
    }
    redo() {
      if (this.index < 0 || this.index >= this.values.length - 1) return null;
      this.index += 1;
      return deepClone(this.values[this.index]);
    }
    get canUndo() {
      return this.index > 0;
    }
    get canRedo() {
      return this.index >= 0 && this.index < this.values.length - 1;
    }
  }

  class FallbackViewport {
    constructor(
      imageWidth = 0,
      imageHeight = 0,
      viewWidth = 0,
      viewHeight = 0,
    ) {
      this.scale = 1;
      this.offsetX = 0;
      this.offsetY = 0;
      this.imageWidth = Number(imageWidth) || 0;
      this.imageHeight = Number(imageHeight) || 0;
      this.viewWidth = Number(viewWidth) || 0;
      this.viewHeight = Number(viewHeight) || 0;
    }
    fit(imageWidth, imageHeight, viewWidth, viewHeight) {
      this.imageWidth = Number(imageWidth) || 0;
      this.imageHeight = Number(imageHeight) || 0;
      this.viewWidth = Number(viewWidth) || 0;
      this.viewHeight = Number(viewHeight) || 0;
      this.scale =
        Math.min(
          this.viewWidth / Math.max(1, this.imageWidth),
          this.viewHeight / Math.max(1, this.imageHeight),
        ) || 1;
      this.offsetX = (this.viewWidth - this.imageWidth * this.scale) / 2;
      this.offsetY = (this.viewHeight - this.imageHeight * this.scale) / 2;
    }
    resize(viewWidth, viewHeight) {
      const oldScale = this.scale || 1;
      const cx = (this.viewWidth / 2 - this.offsetX) / oldScale;
      const cy = (this.viewHeight / 2 - this.offsetY) / oldScale;
      this.viewWidth = Number(viewWidth) || 0;
      this.viewHeight = Number(viewHeight) || 0;
      this.offsetX = this.viewWidth / 2 - cx * oldScale;
      this.offsetY = this.viewHeight / 2 - cy * oldScale;
    }
    toImage(point) {
      return {
        x: (point.x - this.offsetX) / this.scale,
        y: (point.y - this.offsetY) / this.scale,
      };
    }
    toView(point) {
      return {
        x: point.x * this.scale + this.offsetX,
        y: point.y * this.scale + this.offsetY,
      };
    }
    zoomAt(factor, point) {
      const image = this.toImage(point);
      this.scale = Math.max(0.02, Math.min(64, this.scale * Number(factor)));
      this.offsetX = point.x - image.x * this.scale;
      this.offsetY = point.y - image.y * this.scale;
    }
    pan(dx, dy) {
      this.offsetX += Number(dx) || 0;
      this.offsetY += Number(dy) || 0;
    }
    oneToOne() {
      const cx = (this.viewWidth / 2 - this.offsetX) / this.scale;
      const cy = (this.viewHeight / 2 - this.offsetY) / this.scale;
      this.scale = 1;
      this.offsetX = this.viewWidth / 2 - cx;
      this.offsetY = this.viewHeight / 2 - cy;
    }
  }

  const Viewport = core.Viewport || FallbackViewport;
  const DraftStore = core.DraftStore || MemoryDraftStore;
  const History = core.History || MemoryHistory;
  const projectFn = typeof core.project === "function" ? core.project : null;
  const invertFn = typeof core.invert === "function" ? core.invert : null;
  let draftStore;
  try {
    draftStore = new DraftStore();
  } catch (_) {
    draftStore = new MemoryDraftStore();
  }

  const state = {
    video: null,
    sourceRevision: "legacy",
    frames: [],
    currentFrameIndex: 0,
    sliderPreviewIndex: null,
    sourceImage: null,
    sourceObjectUrl: null,
    sourceSize: { width: 0, height: 0 },
    pairs: [],
    mode: "fit",
    tool: "select",
    pendingImage: null,
    pendingPitch: null,
    field: DEFAULT_FIELD(),
    registrations: [],
    selectedRegistrationId: null,
    savedResult: null,
    preview: null,
    requestSerial: 0,
    frameSerial: 0,
    imageSerial: 0,
    busy: new Set(),
    viewport: null,
    draftKey: null,
    draftVersion: 0,
    histories: new Map(),
    pendingViewSnapshot: null,
    selectedPointIndex: null,
    drag: null,
    selectionSerial: 0,
    overlays: { lines: true, support: true, residual: true },
    independentValidation: false,
    loupe: false,
  };

  const REASON_LABELS = {
    invalid_points: "대응점 형식이 올바르지 않습니다.",
    invalid_point: "대응점 좌표를 확인하세요.",
    invalid_point_role: "대응점 역할이 올바르지 않습니다.",
    nonfinite_input: "숫자가 아닌 좌표가 포함되어 있습니다.",
    pitch_out_of_range: "피치 좌표가 0–1 범위를 벗어났습니다.",
    invalid_fit_points: "FIT 대응점을 해석할 수 없습니다.",
    invalid_validation_points: "VALIDATION 대응점을 해석할 수 없습니다.",
    insufficient_fit_points: "FIT 대응점이 4개보다 적습니다.",
    missing_validation_points: "독립적인 VALIDATION 대응점이 없습니다.",
    degenerate_fit_geometry: "FIT 점이 한 선에 몰려 기하가 퇴화했습니다.",
    ill_conditioned_geometry: "기하 조건이 불안정해 신뢰할 수 없습니다.",
    fit_points_not_distributed:
      "FIT 점을 이미지와 피치에 더 넓게 분포시키세요.",
    homography_unavailable: "정합 행렬을 계산할 수 없습니다.",
    fit_point_outside_image: "FIT 점이 소스 이미지 밖에 있습니다.",
    invalid_fit_projection: "FIT 투영 결과를 계산할 수 없습니다.",
    fit_error_exceeds_threshold: "FIT 오차가 허용 기준을 초과했습니다.",
    validation_outside_valid_region: "VALIDATION 점이 유효 영역 밖에 있습니다.",
    validation_outside_image: "VALIDATION 점이 소스 이미지 밖에 있습니다.",
    validation_not_independent:
      "VALIDATION 점은 FIT에 사용하지 않은 점이어야 합니다.",
    invalid_validation_projection: "VALIDATION 투영 결과를 계산할 수 없습니다.",
    validation_error_exceeds_threshold:
      "VALIDATION 오차가 허용 기준을 초과했습니다.",
    sensitivity_unavailable: "민감도를 계산할 수 없습니다.",
    sensitivity_exceeds_threshold: "정합 민감도가 허용 기준을 초과했습니다.",
    propagated_results_require_independent_validation:
      "전파 결과는 독립적인 VALIDATION이 필요합니다.",
    invalid_image_size: "소스 이미지 크기가 올바르지 않습니다.",
  };

  function cacheElements() {
    [
      "apiState",
      "apiStateText",
      "globalAlert",
      "globalAlertText",
      "dismissAlert",
      "importForm",
      "videoPath",
      "importButton",
      "importHint",
      "refreshLibrary",
      "libraryList",
      "sessionPanel",
      "sessionName",
      "videoDimensions",
      "videoDuration",
      "videoFrameCount",
      "videoTimeBase",
      "framePanel",
      "frameLoading",
      "frameIndexReadout",
      "frameTotalReadout",
      "frameTimeReadout",
      "framePtsReadout",
      "frameSlider",
      "sliderEndLabel",
      "previousFrame",
      "nextFrame",
      "exportPanel",
      "exportLink",
      "emptyState",
      "analysisStage",
      "draftState",
      "clearPoints",
      "instructionStrip",
      "cancelPending",
      "sourceCanvas",
      "sourceWrap",
      "sourceCanvasMessage",
      "sourceCanvasSize",
      "pitchCanvas",
      "pointCount",
      "pointTableBody",
      "fieldPanel",
      "fieldLength",
      "fieldWidth",
      "dimensionsVerified",
      "dimensionSource",
      "dimensionHint",
      "estimatePanel",
      "qualityLamp",
      "estimateSummary",
      "estimateButton",
      "saveButton",
      "resultPanel",
      "resultSource",
      "resultBody",
      "propagationPanel",
      "propagationTarget",
      "propagateButton",
      "propagationHint",
      "historyPanel",
      "historyCount",
      "qualityTimeline",
      "historyList",
      "editorToolbar",
      "toolSelect",
      "toolAddFit",
      "toolAddValidation",
      "toolPan",
      "fitView",
      "oneToOneView",
      "zoomOutView",
      "zoomInView",
      "undoButton",
      "redoButton",
      "overlayLinesToggle",
      "overlaySupportToggle",
      "overlayResidualToggle",
      "overlayRegionToggle",
      "independentValidationToggle",
      "loupeToggle",
      "sourceLoupe",
      "loupeCanvas",
      "loupeReadout",
      "viewportReadout",
    ].forEach((id) => {
      els[id] = $(id);
    });
    els.modeButtons = [...document.querySelectorAll("[data-mode]")];
    els.toolButtons = [...document.querySelectorAll("[data-tool]")];
  }

  function nextSerial() {
    state.requestSerial += 1;
    // A newer edit/frame/selection makes any older mutation response
    // obsolete. Clear its UI lock immediately; the old finally block may be
    // skipped because its serial no longer matches.
    ["preview", "save", "propagate"].forEach((key) => {
      if (state.busy.has(key)) setBusy(key, false);
    });
    return state.requestSerial;
  }
  function setApiState(status, text) {
    if (els.apiState) els.apiState.dataset.status = status;
    if (els.apiStateText) els.apiStateText.textContent = text;
  }
  function showAlert(message, kind = "error") {
    if (!els.globalAlert) return;
    els.globalAlert.dataset.kind = kind;
    els.globalAlertText.textContent = message;
    els.globalAlert.hidden = false;
  }
  function hideAlert() {
    if (!els.globalAlert) return;
    els.globalAlert.hidden = true;
    els.globalAlertText.textContent = "";
  }

  function canSave() {
    return Boolean(
      state.video &&
        state.sourceImage &&
        !state.busy.has("frame") &&
        state.sliderPreviewIndex === null &&
        state.pairs.length > 0 &&
        state.pairs.every(isFinitePair),
    );
  }
  function setBusy(key, busy) {
    if (busy) state.busy.add(key);
    else state.busy.delete(key);
    document.body.classList.toggle("is-busy", state.busy.size > 0);
    if (els.importButton) els.importButton.disabled = state.busy.has("import");
    if (els.refreshLibrary)
      els.refreshLibrary.disabled = state.busy.has("library");
    if (els.frameSlider)
      els.frameSlider.disabled =
        state.busy.has("frames") || !state.frames.length;
    if (els.saveButton)
      els.saveButton.disabled = state.busy.has("save") || !canSave();
    if (els.estimateButton)
      els.estimateButton.disabled = state.busy.has("preview");
    if (els.propagateButton)
      els.propagateButton.disabled =
        state.busy.has("propagate") || !state.savedResult;
    if (els.frameLoading) els.frameLoading.hidden = !state.busy.has("frame");
  }

  async function apiFetch(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      let detail = `요청 실패 (${response.status})`;
      try {
        const payload = await response.json();
        if (payload?.detail) detail = String(payload.detail);
      } catch (_) {
        /* non-JSON error */
      }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    setApiState("online", "API 연결됨");
    return response;
  }
  async function getJson(path, options = {}) {
    return (await apiFetch(path, options)).json();
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(Number(seconds))) return "—";
    const value = Math.max(0, Number(seconds));
    return `${String(Math.floor(value / 3600)).padStart(2, "0")}:${String(Math.floor((value % 3600) / 60)).padStart(2, "0")}:${String(Math.floor(value % 60)).padStart(2, "0")}`;
  }
  function formatTime(seconds, withHours = true) {
    if (!Number.isFinite(Number(seconds))) return "00:00:00.000";
    const value = Math.max(0, Number(seconds));
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = Math.floor(value % 60);
    const millis = Math.floor((value - Math.floor(value)) * 1000 + 1e-6);
    if (!withHours && hours === 0)
      return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
    return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
  }
  function formatPts(pts) {
    if (pts === null || pts === undefined || pts === "") return "—";
    return Number.isFinite(Number(pts)) ? String(pts) : "—";
  }
  function statusLabel(status) {
    if (status === "usable") return "사용 가능";
    if (status === "review") return "검토 필요";
    if (status === "unavailable") return "사용 불가";
    return "미계산";
  }
  function statusClass(status) {
    return status === "usable" ||
      status === "review" ||
      status === "unavailable"
      ? status
      : "idle";
  }
  function coordinateLabel(level) {
    if (level === "metric") return "미터 좌표";
    if (level === "normalized") return "정규화 좌표";
    if (level === "image") return "이미지 좌표";
    return "—";
  }
  function reasonLabel(reason) {
    const text = String(reason);
    return REASON_LABELS[text] || text.replace(/_/g, " ");
  }
  function numericOrNull(value) {
    if (value === "" || value === null || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  function clonePairs(points = []) {
    return (Array.isArray(points) ? points : []).map((point, index) => ({
      id:
        point.id ||
        `pair-${Date.now()}-${index}-${Math.random().toString(16).slice(2)}`,
      image: [Number(point.image?.[0]), Number(point.image?.[1])],
      pitch: [Number(point.pitch?.[0]), Number(point.pitch?.[1])],
      role: point.role === "validation" ? "validation" : "fit",
    }));
  }
  function isFinitePair(pair) {
    return Boolean(
      pair &&
        Array.isArray(pair.image) &&
        Array.isArray(pair.pitch) &&
        pair.image.length === 2 &&
        pair.pitch.length === 2 &&
        pair.image.every(Number.isFinite) &&
        pair.pitch.every(Number.isFinite),
    );
  }
  function currentFrame() {
    return state.frames[state.currentFrameIndex] || null;
  }
  function currentFrameNumber() {
    return Number(currentFrame()?.index ?? state.currentFrameIndex);
  }
  function sourceRevision(video = state.video) {
    return String(video?.source_revision ?? video?.sourceRevision ?? "legacy");
  }
  function draftKeyFor(
    videoId = state.video?.id,
    revision = state.sourceRevision,
    frameIndex = currentFrameNumber(),
  ) {
    return `video:${String(videoId ?? "")}|source:${String(revision ?? "legacy")}|frame:${String(frameIndex)}`;
  }

  function viewCssSize() {
    const rect = els.sourceCanvas?.getBoundingClientRect?.();
    const wrapRect = els.sourceWrap?.getBoundingClientRect?.();
    return {
      width: Math.max(1, Number(rect?.width || wrapRect?.width || 960)),
      height: Math.max(1, Number(rect?.height || wrapRect?.height || 540)),
    };
  }
  function createViewport(width, height) {
    const view = viewCssSize();
    let viewport = null;
    try {
      viewport = new Viewport();
    } catch (_) {
      viewport = new FallbackViewport();
    }
    if (!viewport || typeof viewport.fit !== "function")
      viewport = new FallbackViewport();
    try {
      viewport.fit(width, height, view.width, view.height);
    } catch (_) {
      FallbackViewport.prototype.fit.call(
        viewport,
        width,
        height,
        view.width,
        view.height,
      );
    }
    return viewport;
  }
  function ensureViewport(reset = false) {
    if (!state.sourceSize.width || !state.sourceSize.height) return null;
    const view = viewCssSize();
    if (!state.viewport || reset)
      state.viewport = createViewport(
        state.sourceSize.width,
        state.sourceSize.height,
      );
    else {
      try {
        state.viewport.resize(view.width, view.height);
      } catch (_) {
        /* unavailable */
      }
    }
    if (reset && state.pendingViewSnapshot) {
      const restored = state.pendingViewSnapshot;
      if (
        Number.isFinite(Number(restored.scale)) &&
        Number.isFinite(Number(restored.offsetX)) &&
        Number.isFinite(Number(restored.offsetY))
      ) {
        state.viewport.scale = Number(restored.scale);
        state.viewport.offsetX = Number(restored.offsetX);
        state.viewport.offsetY = Number(restored.offsetY);
      }
      state.pendingViewSnapshot = null;
    }
    return state.viewport;
  }
  function viewportSnapshot() {
    if (!state.viewport) return null;
    return {
      scale: Number(state.viewport.scale) || 1,
      offsetX: Number(state.viewport.offsetX) || 0,
      offsetY: Number(state.viewport.offsetY) || 0,
    };
  }
  function snapshot() {
    return {
      points: clonePairs(state.pairs),
      field: deepClone(state.field),
      view: viewportSnapshot(),
      draftVersion: state.draftVersion,
    };
  }
  function ensureDraftKey() {
    if (!state.video || !state.frames.length) return null;
    const key = draftKeyFor(
      state.video.id,
      state.sourceRevision,
      currentFrameNumber(),
    );
    state.draftKey = key;
    return key;
  }
  function getHistory() {
    const key = ensureDraftKey();
    if (!key) return null;
    let history = state.histories.get(key);
    if (!history) {
      try {
        history = new History(100);
      } catch (_) {
        history = new MemoryHistory(100);
      }
      history.reset(snapshot());
      state.histories.set(key, history);
    }
    return history;
  }
  function persistDraft() {
    const key = ensureDraftKey();
    if (!key) return;
    try {
      draftStore.save(key, snapshot());
    } catch (_) {
      /* in-memory cache */
    }
  }
  function applySnapshot(value, { restoreView = true } = {}) {
    if (!value) return;
    state.pairs = clonePairs(value.points || value.pairs || []);
    state.field = {
      length: numericOrNull(value.field?.length),
      width: numericOrNull(value.field?.width),
      dimension_source: String(value.field?.dimension_source || ""),
      dimensions_verified: Boolean(value.field?.dimensions_verified),
    };
    state.pendingImage = null;
    state.pendingPitch = null;
    state.selectedPointIndex = null;
    if (
      Number.isInteger(Number(value.draftVersion)) &&
      Number(value.draftVersion) >= 0
    )
      state.draftVersion = Number(value.draftVersion);
    if (restoreView && value.view) {
      if (
        state.viewport &&
        Number.isFinite(Number(value.view.scale)) &&
        Number.isFinite(Number(value.view.offsetX)) &&
        Number.isFinite(Number(value.view.offsetY))
      ) {
        state.viewport.scale = Number(value.view.scale);
        state.viewport.offsetX = Number(value.view.offsetX);
        state.viewport.offsetY = Number(value.view.offsetY);
        state.pendingViewSnapshot = null;
      } else state.pendingViewSnapshot = deepClone(value.view);
    }
  }
  function restoreDraftForCurrentFrame() {
    const key = ensureDraftKey();
    const loaded = key ? draftStore.load(key) : null;
    if (loaded) applySnapshot(loaded);
    else {
      state.pairs = [];
      state.field = DEFAULT_FIELD();
      state.pendingImage = null;
      state.pendingPitch = null;
      state.selectedPointIndex = null;
      state.draftVersion = 0;
      state.pendingViewSnapshot = null;
    }
    state.preview = null;
    state.savedResult = null;
    state.selectedRegistrationId = null;
    if (!state.histories.has(key)) {
      let history;
      try {
        history = new History(100);
      } catch (_) {
        history = new MemoryHistory(100);
      }
      history.reset(snapshot());
      state.histories.set(key, history);
    }
  }
  function invalidateDisplayedResults() {
    state.preview = null;
    state.savedResult = null;
    state.selectedRegistrationId = null;
  }
  function markDraftDirty({ persist = true } = {}) {
    if (!state.video) return;
    state.draftVersion += 1;
    invalidateDisplayedResults();
    nextSerial();
    if (persist) persistDraft();
  }
  function commitCurrentHistory() {
    const history = getHistory();
    if (history) history.commit(snapshot());
    persistDraft();
    updateUndoRedo();
  }

  function localEstimate() {
    const fit = state.pairs.filter(
      (pair) => pair.role === "fit" && isFinitePair(pair),
    );
    const validation = state.pairs.filter(
      (pair) => pair.role === "validation" && isFinitePair(pair),
    );
    const reasons = [];
    if (fit.length < 4)
      reasons.push(`FIT 대응점 ${4 - fit.length}개가 더 필요합니다.`);
    if (validation.length < 1)
      reasons.push("독립적인 VALIDATION 대응점이 1개 이상 필요합니다.");
    const outside = state.pairs.some(
      (pair) =>
        !isFinitePair(pair) ||
        pair.image[0] < 0 ||
        pair.image[0] >= state.sourceSize.width ||
        pair.image[1] < 0 ||
        pair.image[1] >= state.sourceSize.height ||
        pair.pitch[0] < 0 ||
        pair.pitch[0] > 1 ||
        pair.pitch[1] < 0 ||
        pair.pitch[1] > 1,
    );
    if (outside && state.sourceSize.width && state.sourceSize.height)
      reasons.push("좌표가 허용 범위를 벗어났습니다.");
    if (fit.length >= 4) {
      const unique = new Set(
        fit.map(
          (pair) => `${pair.image[0].toFixed(3)}:${pair.image[1].toFixed(3)}`,
        ),
      );
      if (unique.size < 4) reasons.push("FIT 이미지 좌표가 중복됩니다.");
      const spanX =
        Math.max(...fit.map((pair) => pair.pitch[0])) -
        Math.min(...fit.map((pair) => pair.pitch[0]));
      const spanY =
        Math.max(...fit.map((pair) => pair.pitch[1])) -
        Math.min(...fit.map((pair) => pair.pitch[1]));
      if (spanX < 0.05 || spanY < 0.05)
        reasons.push("FIT 점을 더 넓게 분포시키세요.");
    }
    return {
      fitCount: fit.length,
      validationCount: validation.length,
      reasons,
      ready: reasons.length === 0,
    };
  }
  function renderEstimate() {
    if (!els.estimateSummary) return;
    const serverPreview = state.preview;
    const local = localEstimate();
    const points = serverPreview?.points || state.pairs;
    const estimate = serverPreview
      ? {
          fitCount: points.filter((point) => point.role === "fit").length,
          validationCount: points.filter((point) => point.role === "validation")
            .length,
          reasons: Array.isArray(serverPreview.reasons)
            ? serverPreview.reasons
            : [],
          ready: serverPreview.status === "usable",
        }
      : local;
    els.qualityLamp.dataset.status = estimate.ready ? "ready" : "review";
    els.estimateSummary.replaceChildren();
    const top = document.createElement("div");
    top.className = `estimate-top ${estimate.ready ? "ready" : "needs-review"}`;
    const icon = document.createElement("span");
    icon.className = "estimate-icon";
    icon.textContent = estimate.ready ? "✓" : "!";
    const heading = document.createElement("strong");
    heading.textContent = serverPreview
      ? `서버 미리보기 · ${statusLabel(serverPreview.status)}`
      : estimate.ready
        ? "서버 계산 준비됨"
        : "검토가 필요합니다";
    const counts = document.createElement("span");
    counts.className = "estimate-counts";
    counts.textContent = `FIT ${estimate.fitCount} · VALIDATION ${estimate.validationCount}`;
    top.append(icon, heading, counts);
    els.estimateSummary.append(top);
    const reasons = document.createElement("ul");
    reasons.className = "reason-list compact-reasons";
    if (estimate.reasons.length)
      estimate.reasons.forEach((reason) => {
        const li = document.createElement("li");
        li.textContent = reasonLabel(reason);
        reasons.append(li);
      });
    else {
      const li = document.createElement("li");
      li.className = "positive-reason";
      li.textContent = serverPreview
        ? "서버가 독립 검증을 통과했습니다."
        : "기본 입력 조건을 충족했습니다. 서버 미리보기를 실행하세요.";
      reasons.append(li);
    }
    els.estimateSummary.append(reasons);
    els.saveButton.disabled = state.busy.has("save") || !canSave();
  }
  function collectField() {
    return {
      length: numericOrNull(els.fieldLength.value),
      width: numericOrNull(els.fieldWidth.value),
      dimension_source: els.dimensionSource.value.trim(),
      dimensions_verified: Boolean(els.dimensionsVerified.checked),
    };
  }
  function renderField() {
    const field = state.field || DEFAULT_FIELD();
    els.fieldLength.value = field.length === null ? "" : String(field.length);
    els.fieldWidth.value = field.width === null ? "" : String(field.width);
    els.dimensionSource.value = field.dimension_source || "";
    els.dimensionsVerified.checked = Boolean(field.dimensions_verified);
    const hasNumbers =
      Number.isFinite(field.length) && Number.isFinite(field.width);
    if (field.dimensions_verified && (!hasNumbers || !field.dimension_source)) {
      els.dimensionHint.textContent =
        "확인됨으로 표시하려면 길이·너비와 출처를 모두 입력하세요.";
      els.dimensionHint.dataset.kind = "warning";
    } else if (hasNumbers && !field.dimensions_verified) {
      els.dimensionHint.textContent =
        "값은 입력됐지만 확인됨 체크 전에는 metric으로 사용되지 않습니다.";
      els.dimensionHint.dataset.kind = "warning";
    } else {
      els.dimensionHint.textContent =
        "숫자와 출처가 있어도 확인됨 체크 전에는 metric으로 사용되지 않습니다.";
      els.dimensionHint.dataset.kind = "";
    }
  }
  function renderVideoMeta() {
    if (!state.video) return;
    els.sessionName.textContent = state.video.name || `video-${state.video.id}`;
    els.videoDimensions.textContent =
      state.video.width && state.video.height
        ? `${state.video.width} × ${state.video.height}`
        : "—";
    els.videoDuration.textContent = formatDuration(state.video.duration);
    els.videoFrameCount.textContent = state.frames.length
      ? `${state.frames.length.toLocaleString("ko-KR")} frames`
      : "—";
    els.videoTimeBase.textContent = state.video.time_base || "—";
  }
  function renderFrameReadout(indexOverride = state.currentFrameIndex) {
    const frame = state.frames[indexOverride] || null;
    const index = frame ? (frame.index ?? indexOverride) : "—";
    els.frameIndexReadout.textContent = Number.isFinite(Number(index))
      ? String(index).padStart(4, "0")
      : "—";
    els.frameTotalReadout.textContent = state.frames.length
      ? String(state.frames.length).padStart(4, "0")
      : "—";
    els.frameTimeReadout.textContent = frame
      ? formatTime(frame.time_seconds)
      : "00:00:00.000";
    els.framePtsReadout.textContent = frame ? formatPts(frame.pts) : "—";
    const last = state.frames[state.frames.length - 1];
    els.sliderEndLabel.textContent = last
      ? formatTime(last.time_seconds, false)
      : "—";
  }

  function configureCanvas(canvas, fallbackWidth = 960, fallbackHeight = 540) {
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Number(rect.width) || fallbackWidth);
    const height = Math.max(1, Number(rect.height) || fallbackHeight);
    const dpr = Math.max(1, Number(window.devicePixelRatio) || 1);
    const physicalWidth = Math.max(1, Math.round(width * dpr));
    const physicalHeight = Math.max(1, Math.round(height * dpr));
    if (canvas.width !== physicalWidth || canvas.height !== physicalHeight) {
      canvas.width = physicalWidth;
      canvas.height = physicalHeight;
    }
    const context = canvas.getContext("2d");
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.imageSmoothingEnabled = true;
    return { context, width, height, dpr };
  }
  function canvasPointFromEvent(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }
  function sourceImagePointFromEvent(event) {
    const viewPoint = canvasPointFromEvent(els.sourceCanvas, event);
    if (!viewPoint || !state.viewport) return null;
    let imagePoint;
    try {
      imagePoint = state.viewport.toImage(viewPoint);
    } catch (_) {
      imagePoint = {
        x: (viewPoint.x - state.viewport.offsetX) / state.viewport.scale,
        y: (viewPoint.y - state.viewport.offsetY) / state.viewport.scale,
      };
    }
    return { view: viewPoint, image: imagePoint };
  }
  function pitchCanvasMetrics() {
    const rect = els.pitchCanvas.getBoundingClientRect();
    const width = Math.max(1, Number(rect.width) || 600);
    const height = Math.max(1, Number(rect.height) || 390);
    const dpr = Math.max(1, Number(window.devicePixelRatio) || 1);
    const padX = Math.max(36 / dpr, width * 0.075);
    const padY = Math.max(32 / dpr, height * 0.09);
    return {
      width,
      height,
      padX,
      padY,
      innerWidth: Math.max(1, width - padX * 2),
      innerHeight: Math.max(1, height - padY * 2),
    };
  }
  function pitchToCanvas(point) {
    const m = pitchCanvasMetrics();
    return {
      x: m.padX + point[0] * m.innerWidth,
      y: m.padY + point[1] * m.innerHeight,
    };
  }
  function pitchFromEvent(event) {
    const point = canvasPointFromEvent(els.pitchCanvas, event);
    if (!point) return null;
    const m = pitchCanvasMetrics();
    return {
      x: Math.max(0, Math.min(1, (point.x - m.padX) / m.innerWidth)),
      y: Math.max(0, Math.min(1, (point.y - m.padY) / m.innerHeight)),
    };
  }

  function projectPoint(matrix, point) {
    if (!matrix || !Array.isArray(point) || point.length < 2) return null;
    if (projectFn) {
      try {
        const result = projectFn(matrix, point);
        if (
          Array.isArray(result) &&
          result.length >= 2 &&
          result.every(Number.isFinite)
        )
          return [Number(result[0]), Number(result[1])];
      } catch (_) {
        /* fallback */
      }
    }
    const rows = Array.isArray(matrix[0])
      ? matrix
      : [matrix.slice(0, 3), matrix.slice(3, 6), matrix.slice(6, 9)];
    if (!rows || rows.length < 3) return null;
    const x = Number(point[0]);
    const y = Number(point[1]);
    const w = rows[2][0] * x + rows[2][1] * y + rows[2][2];
    if (!Number.isFinite(w) || Math.abs(w) < 1e-12) return null;
    const px = (rows[0][0] * x + rows[0][1] * y + rows[0][2]) / w;
    const py = (rows[1][0] * x + rows[1][1] * y + rows[1][2]) / w;
    return Number.isFinite(px) && Number.isFinite(py) ? [px, py] : null;
  }
  function invertMatrix(matrix) {
    if (!matrix) return null;
    if (invertFn) {
      try {
        return invertFn(matrix);
      } catch (_) {
        /* fallback */
      }
    }
    const m = Array.isArray(matrix[0]) ? matrix.flat() : matrix.slice();
    if (m.length !== 9) return null;
    const [a, b, c, d, e, f, g, h, i] = m;
    const A = e * i - f * h;
    const B = c * h - b * i;
    const C = b * f - c * e;
    const D = f * g - d * i;
    const E = a * i - c * g;
    const F = c * d - a * f;
    const G = d * h - e * g;
    const H = b * g - a * h;
    const I = a * e - b * d;
    const det = a * A + b * D + c * G;
    if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null;
    return [
      [A / det, B / det, C / det],
      [D / det, E / det, F / det],
      [G / det, H / det, I / det],
    ];
  }
  function viewPointFromImage(point) {
    if (!state.viewport) return null;
    try {
      return state.viewport.toView({ x: point[0], y: point[1] });
    } catch (_) {
      return {
        x: point[0] * state.viewport.scale + state.viewport.offsetX,
        y: point[1] * state.viewport.scale + state.viewport.offsetY,
      };
    }
  }

  function drawSourceMarker(
    context,
    point,
    role,
    number,
    selected = false,
    pending = false,
  ) {
    const color = role === "validation" ? "#f7b267" : "#55e3c1";
    const radius = selected ? 8 : 6;
    context.save();
    context.strokeStyle = color;
    context.lineWidth = selected ? 2.2 : 1.7;
    context.shadowColor = "rgba(0,0,0,.72)";
    context.shadowBlur = 6;
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.stroke();
    context.shadowBlur = 0;
    context.beginPath();
    context.moveTo(point.x - radius * 1.7, point.y);
    context.lineTo(point.x + radius * 1.7, point.y);
    context.moveTo(point.x, point.y - radius * 1.7);
    context.lineTo(point.x, point.y + radius * 1.7);
    context.stroke();
    if (number !== null && number !== undefined) {
      context.fillStyle = "rgba(4,12,23,.88)";
      context.fillRect(point.x + radius + 4, point.y - radius - 4, 23, 15);
      context.fillStyle = color;
      context.font = "600 10px ui-sans-serif, sans-serif";
      context.fillText(
        String(number).padStart(2, "0"),
        point.x + radius + 8,
        point.y + 7,
      );
    }
    if (pending) {
      context.setLineDash([4, 3]);
      context.globalAlpha = 0.8;
      context.beginPath();
      context.arc(point.x, point.y, radius * 1.7, 0, Math.PI * 2);
      context.stroke();
    }
    context.restore();
  }
  function drawImagePath(context, points, closed = false, style = {}) {
    if (!Array.isArray(points) || points.length < (closed ? 3 : 2)) return;
    context.save();
    context.strokeStyle = style.stroke || "#55e3c1";
    context.fillStyle = style.fill || "transparent";
    context.globalAlpha = style.alpha ?? 1;
    context.lineWidth = style.lineWidth || 1.5;
    if (style.dash) context.setLineDash(style.dash);
    context.beginPath();
    points.forEach((point, index) => {
      const p = viewPointFromImage(point);
      if (!p) return;
      if (index === 0) context.moveTo(p.x, p.y);
      else context.lineTo(p.x, p.y);
    });
    if (closed) context.closePath();
    if (style.fill && style.fill !== "transparent") context.fill();
    context.stroke();
    context.restore();
  }
  function sourceOverlayGeometry(result) {
    const inverse = invertMatrix(result?.matrix);
    if (!inverse) return null;
    const lines = (
      Array.isArray(result?.projected_lines) ? result.projected_lines : []
    )
      .map((line) =>
        Array.isArray(line) && line.length >= 2
          ? line.map((point) => projectPoint(inverse, point)).filter(Boolean)
          : [],
      )
      .filter((line) => line.length >= 2);
    const region = Array.isArray(result?.valid_region)
      ? result.valid_region
          .map((point) => projectPoint(inverse, point))
          .filter(Boolean)
      : [];
    return { inverse, lines, region };
  }

  function renderSourceCanvas() {
    const configured = configureCanvas(els.sourceCanvas);
    const context = configured.context;
    context.clearRect(0, 0, configured.width, configured.height);
    context.fillStyle = "#07121d";
    context.fillRect(0, 0, configured.width, configured.height);
    if (
      !state.sourceImage ||
      !state.sourceSize.width ||
      !state.sourceSize.height
    ) {
      els.sourceCanvasSize.textContent = "—";
      updateCanvasDiagnostics();
      return;
    }
    ensureViewport(false);
    const view = viewportSnapshot();
    context.save();
    context.beginPath();
    context.rect(0, 0, configured.width, configured.height);
    context.clip();
    context.translate(view.offsetX, view.offsetY);
    context.scale(view.scale, view.scale);
    context.drawImage(
      state.sourceImage,
      0,
      0,
      state.sourceSize.width,
      state.sourceSize.height,
    );
    context.restore();
    const display = state.preview || state.savedResult;
    const predictive = Boolean(display && !state.independentValidation);
    const geometry = predictive ? sourceOverlayGeometry(display) : null;
    if (geometry && state.overlays.support && geometry.region.length >= 3)
      drawImagePath(context, geometry.region, true, {
        stroke: "rgba(85,227,193,.62)",
        fill: "rgba(85,227,193,.08)",
        alpha: 0.95,
        lineWidth: 1.5,
        dash: [7, 6],
      });
    if (geometry && state.overlays.lines)
      geometry.lines.forEach((line) =>
        drawImagePath(context, line, false, {
          stroke: "#55e3c1",
          alpha: 0.78,
          lineWidth: 1.8,
        }),
      );
    if (geometry && state.overlays.residual)
      state.pairs
        .filter((pair) => pair.role === "validation" && isFinitePair(pair))
        .forEach((pair) => {
          const expected = projectPoint(geometry.inverse, pair.pitch);
          if (!expected) return;
          const observed = viewPointFromImage(pair.image);
          const projected = viewPointFromImage(expected);
          if (!observed || !projected) return;
          context.save();
          context.strokeStyle = "#f7b267";
          context.globalAlpha = 0.88;
          context.lineWidth = 1.4;
          context.setLineDash([4, 4]);
          context.beginPath();
          context.moveTo(observed.x, observed.y);
          context.lineTo(projected.x, projected.y);
          context.stroke();
          context.restore();
        });
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      const point = viewPointFromImage(pair.image);
      if (point)
        drawSourceMarker(
          context,
          point,
          pair.role,
          index + 1,
          state.selectedPointIndex === index,
        );
    });
    if (state.pendingImage) {
      const point = viewPointFromImage(state.pendingImage);
      if (point)
        drawSourceMarker(context, point, state.mode, null, false, true);
    }
    els.sourceCanvasSize.textContent = `${state.sourceSize.width} × ${state.sourceSize.height} px`;
    updateCanvasDiagnostics();
  }

  function drawPitchMarker(
    context,
    point,
    role,
    number,
    selected = false,
    pending = false,
  ) {
    const color = role === "validation" ? "#f7b267" : "#55e3c1";
    const radius = selected ? 8 : 6;
    context.save();
    context.fillStyle = color;
    context.strokeStyle = "#06131f";
    context.lineWidth = 2;
    context.beginPath();
    context.arc(point.x, point.y, radius, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    if (pending) {
      context.strokeStyle = color;
      context.setLineDash([4, 4]);
      context.beginPath();
      context.arc(point.x, point.y, radius * 1.7, 0, Math.PI * 2);
      context.stroke();
    }
    if (number !== null && number !== undefined) {
      context.fillStyle = "#05131f";
      context.font = "700 9px ui-sans-serif, sans-serif";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(String(number).padStart(2, "0"), point.x, point.y + 0.5);
    }
    context.restore();
  }
  function renderPitchCanvas() {
    const configured = configureCanvas(els.pitchCanvas, 600, 390);
    const context = configured.context;
    const m = pitchCanvasMetrics();
    context.clearRect(0, 0, m.width, m.height);
    context.fillStyle = "#0b2630";
    context.fillRect(0, 0, m.width, m.height);
    context.fillStyle = "rgba(30,184,159,.06)";
    context.fillRect(m.padX, m.padY, m.innerWidth, m.innerHeight);
    context.strokeStyle = "rgba(107,227,201,.48)";
    context.lineWidth = 1;
    context.strokeRect(m.padX, m.padY, m.innerWidth, m.innerHeight);
    context.strokeStyle = "rgba(107,227,201,.17)";
    context.lineWidth = 1;
    for (let i = 1; i < 10; i += 1) {
      const x = m.padX + (m.innerWidth * i) / 10;
      const y = m.padY + (m.innerHeight * i) / 10;
      context.beginPath();
      context.moveTo(x, m.padY);
      context.lineTo(x, m.padY + m.innerHeight);
      context.stroke();
      context.beginPath();
      context.moveTo(m.padX, y);
      context.lineTo(m.padX + m.innerWidth, y);
      context.stroke();
    }
    context.strokeStyle = "rgba(107,227,201,.30)";
    context.beginPath();
    context.moveTo(m.padX + m.innerWidth / 2, m.padY);
    context.lineTo(m.padX + m.innerWidth / 2, m.padY + m.innerHeight);
    context.stroke();
    const display = state.preview || state.savedResult;
    const predictive = Boolean(display && !state.independentValidation);
    if (
      predictive &&
      state.overlays.support &&
      Array.isArray(display.valid_region) &&
      display.valid_region.length >= 3
    ) {
      context.fillStyle = "rgba(85,227,193,.08)";
      context.strokeStyle = "rgba(85,227,193,.48)";
      context.setLineDash([7, 7]);
      context.beginPath();
      display.valid_region.forEach((point, index) => {
        const p = pitchToCanvas(point);
        if (index === 0) context.moveTo(p.x, p.y);
        else context.lineTo(p.x, p.y);
      });
      context.closePath();
      context.fill();
      context.stroke();
      context.setLineDash([]);
    }
    if (
      predictive &&
      state.overlays.lines &&
      Array.isArray(display.projected_lines)
    )
      display.projected_lines.forEach((line) => {
        if (!Array.isArray(line) || line.length < 2) return;
        const a = pitchToCanvas(line[0]);
        const b = pitchToCanvas(line[1]);
        context.save();
        context.strokeStyle = "#55e3c1";
        context.globalAlpha = 0.78;
        context.lineWidth = 1.7;
        context.beginPath();
        context.moveTo(a.x, a.y);
        context.lineTo(b.x, b.y);
        context.stroke();
        context.restore();
      });
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      drawPitchMarker(
        context,
        pitchToCanvas(pair.pitch),
        pair.role,
        index + 1,
        state.selectedPointIndex === index,
      );
    });
    if (state.pendingPitch)
      drawPitchMarker(
        context,
        pitchToCanvas([state.pendingPitch.x, state.pendingPitch.y]),
        state.mode,
        null,
        false,
        true,
      );
  }
  function updateCanvasDiagnostics() {
    if (!els.sourceCanvas) return;
    const view = viewportSnapshot() || { scale: 1, offsetX: 0, offsetY: 0 };
    els.sourceCanvas.dataset.viewportScale = String(view.scale);
    els.sourceCanvas.dataset.viewportOffsetX = String(view.offsetX);
    els.sourceCanvas.dataset.viewportOffsetY = String(view.offsetY);
    els.sourceCanvas.dataset.draftVersion = String(state.draftVersion);
    els.sourceCanvas.dataset.frameIndex = String(currentFrameNumber());
    els.sourceCanvas.dataset.sourceRevision = String(
      state.sourceRevision || "legacy",
    );
    els.sourceWrap.dataset.tool = state.tool;
    els.sourceWrap.dataset.validationMode = String(state.independentValidation);
    els.sourceWrap.dataset.overlayLines = String(
      state.overlays.lines && !state.independentValidation,
    );
    if (els.viewportReadout)
      els.viewportReadout.textContent = `${Math.round(view.scale * 100)}%`;
  }

  function renderPointTable() {
    els.pointCount.textContent = String(state.pairs.length);
    els.pointTableBody.replaceChildren();
    if (!state.pairs.length) {
      const row = document.createElement("tr");
      row.className = "table-empty";
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent =
        "캔버스에서 FIT 대응점을 4개 이상, 서로 떨어진 위치에 지정하세요.";
      row.append(cell);
      els.pointTableBody.append(row);
      return;
    }
    const fragment = document.createDocumentFragment();
    state.pairs.forEach((pair, index) => {
      const row = document.createElement("tr");
      row.className = pair.role === "validation" ? "validation-row" : "fit-row";
      row.classList.toggle(
        "selected-point",
        index === state.selectedPointIndex,
      );
      row.addEventListener("click", () => {
        state.selectedPointIndex = index;
        renderAll();
      });
      const number = document.createElement("td");
      number.className = "point-index";
      number.textContent = String(index + 1).padStart(2, "0");
      const role = document.createElement("td");
      const select = document.createElement("select");
      select.className = `role-select ${pair.role}`;
      select.setAttribute("aria-label", `점 ${index + 1} 역할`);
      [
        ["fit", "FIT"],
        ["validation", "VALIDATION"],
      ].forEach(([value, text]) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = text;
        option.selected = pair.role === value;
        select.append(option);
      });
      select.addEventListener("click", (event) => event.stopPropagation());
      select.addEventListener("change", () => {
        pair.role = select.value;
        markDraftDirty();
        commitCurrentHistory();
        renderAll();
      });
      role.append(select);
      const makeNumberInput = (
        value,
        { min, max, step, decimals, label, onValue },
      ) => {
        const input = document.createElement("input");
        input.type = "number";
        input.value = Number.isFinite(value)
          ? Number(value).toFixed(decimals ?? 3)
          : "";
        input.step = step || "0.001";
        if (min !== undefined) input.min = String(min);
        if (max !== undefined) input.max = String(max);
        input.className = "point-number";
        input.setAttribute("aria-label", label);
        input.addEventListener("click", (event) => event.stopPropagation());
        input.addEventListener("focus", () => {
          state.selectedPointIndex = index;
        });
        input.addEventListener("input", () => {
          onValue(numericOrNull(input.value));
          markDraftDirty();
          renderSourceCanvas();
          renderPitchCanvas();
          renderEstimate();
        });
        input.addEventListener("change", () => {
          onValue(numericOrNull(input.value));
          commitCurrentHistory();
          renderAll();
        });
        return input;
      };
      const imageX = document.createElement("td");
      imageX.append(
        makeNumberInput(pair.image[0], {
          min: 0,
          max: state.sourceSize.width || undefined,
          step: ".01",
          decimals: 2,
          label: `점 ${index + 1} 이미지 x`,
          onValue: (value) => {
            pair.image[0] = value;
          },
        }),
      );
      const imageY = document.createElement("td");
      imageY.append(
        makeNumberInput(pair.image[1], {
          min: 0,
          max: state.sourceSize.height || undefined,
          step: ".01",
          decimals: 2,
          label: `점 ${index + 1} 이미지 y`,
          onValue: (value) => {
            pair.image[1] = value;
          },
        }),
      );
      const pitchX = document.createElement("td");
      pitchX.append(
        makeNumberInput(pair.pitch[0], {
          min: 0,
          max: 1,
          step: ".0001",
          decimals: 4,
          label: `점 ${index + 1} 피치 x`,
          onValue: (value) => {
            pair.pitch[0] = value;
          },
        }),
      );
      const pitchY = document.createElement("td");
      pitchY.append(
        makeNumberInput(pair.pitch[1], {
          min: 0,
          max: 1,
          step: ".0001",
          decimals: 4,
          label: `점 ${index + 1} 피치 y`,
          onValue: (value) => {
            pair.pitch[1] = value;
          },
        }),
      );
      const remove = document.createElement("td");
      const removeButton = document.createElement("button");
      removeButton.type = "button";
      removeButton.className = "remove-point";
      removeButton.setAttribute("aria-label", `점 ${index + 1} 삭제`);
      removeButton.textContent = "×";
      removeButton.addEventListener("click", (event) => {
        event.stopPropagation();
        state.pairs.splice(index, 1);
        state.selectedPointIndex = null;
        markDraftDirty();
        commitCurrentHistory();
        renderAll();
      });
      remove.append(removeButton);
      row.append(number, role, imageX, imageY, pitchX, pitchY, remove);
      fragment.append(row);
    });
    els.pointTableBody.append(fragment);
  }

  function renderResult() {
    els.resultBody.replaceChildren();
    const result = state.preview || state.savedResult;
    const kind = state.preview
      ? "preview"
      : state.savedResult
        ? "saved"
        : "empty";
    els.resultBody.dataset.kind = kind;
    els.resultBody.dataset.draftVersion = String(state.draftVersion);
    if (!result) {
      const empty = document.createElement("div");
      empty.className = "subtle-empty";
      empty.textContent =
        "저장된 결과가 없습니다. 입력 조건 확인으로 서버 미리보기를 실행하세요.";
      els.resultBody.append(empty);
      els.resultSource.textContent = "—";
      return;
    }
    els.resultSource.textContent = state.preview
      ? `PREVIEW · v${state.draftVersion}`
      : result.source === "propagated"
        ? "PROPAGATED"
        : "MANUAL";
    const status = document.createElement("div");
    status.className = `result-status ${statusClass(result.status)}`;
    const statusMain = document.createElement("div");
    statusMain.className = "result-status-main";
    const dot = document.createElement("span");
    dot.className = "result-status-dot";
    const heading = document.createElement("strong");
    heading.textContent = statusLabel(result.status);
    statusMain.append(dot, heading);
    const level = document.createElement("span");
    level.className = "coordinate-level";
    level.textContent = coordinateLabel(result.coordinate_level);
    status.append(statusMain, level);
    els.resultBody.append(status);
    if (state.preview) {
      const note = document.createElement("p");
      note.className = "field-hint";
      note.textContent =
        "미리보기는 서버 계산 결과이며 저장되지 않았습니다. 저장하면 서버가 다시 계산합니다.";
      els.resultBody.append(note);
    }
    const metrics = document.createElement("div");
    metrics.className = "result-metrics";
    [
      ["FIT 오차", result.fit_error_px, "px"],
      ["검증 오차", result.validation_error_px, "px"],
      ["민감도", result.sensitivity, ""],
    ].forEach(([label, value, suffix]) => {
      const metric = document.createElement("div");
      metric.className = "result-metric";
      const labelNode = document.createElement("span");
      labelNode.textContent = label;
      const valueNode = document.createElement("strong");
      valueNode.textContent =
        value === null || value === undefined
          ? "—"
          : `${Number(value).toFixed(2)}${suffix}`;
      metric.append(labelNode, valueNode);
      metrics.append(metric);
    });
    els.resultBody.append(metrics);
    const reasons = document.createElement("div");
    reasons.className = "result-reasons";
    const reasonTitle = document.createElement("span");
    reasonTitle.className = "reason-title";
    reasonTitle.textContent = result.reasons?.length
      ? "실패·검토 사유"
      : "검토 사유";
    reasons.append(reasonTitle);
    const list = document.createElement("ul");
    list.className = "reason-list";
    if (Array.isArray(result.reasons) && result.reasons.length)
      result.reasons.forEach((reason) => {
        const li = document.createElement("li");
        li.textContent = reasonLabel(reason);
        list.append(li);
      });
    else {
      const li = document.createElement("li");
      li.className = "positive-reason";
      li.textContent = state.preview
        ? "서버가 보고한 차단 사유가 없습니다."
        : "서버 저장 결과입니다.";
      list.append(li);
    }
    reasons.append(list);
    els.resultBody.append(reasons);
  }

  function renderHistory() {
    const registrations = state.registrations || [];
    els.historyCount.textContent = String(registrations.length);
    els.qualityTimeline.replaceChildren();
    els.historyList.replaceChildren();
    if (!registrations.length) {
      const empty = document.createElement("div");
      empty.className = "subtle-empty";
      empty.textContent = "저장된 등록이 없습니다.";
      els.qualityTimeline.append(empty);
      return;
    }
    const timeline = document.createElement("div");
    timeline.className = "timeline-bars";
    const track = document.createElement("div");
    track.className = "timeline-track";
    const rail = document.createElement("div");
    rail.className = "timeline-rail";
    track.append(rail);
    const frameCount = Math.max(
      state.frames.length,
      ...registrations.map(
        (registration) => Number(registration.frame_index) + 1 || 0,
      ),
      1,
    );
    const denominator = Math.max(1, frameCount - 1);
    const start = Number(state.frames[0]?.time_seconds);
    const end = Number(
      state.frames[state.frames.length - 1]?.time_seconds ??
        state.video?.duration,
    );
    const span =
      Number.isFinite(start) && Number.isFinite(end) && end > start
        ? end - start
        : null;
    registrations.forEach((registration) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = `timeline-bar ${statusClass(registration.status)}`;
      button.title = `프레임 ${registration.frame_index} · ${statusLabel(registration.status)}`;
      const frameIndex = Number(registration.frame_index);
      const time = Number(registration.time_seconds);
      const position =
        span !== null && Number.isFinite(time)
          ? (time - start) / span
          : Number.isFinite(frameIndex)
            ? frameIndex / denominator
            : 0;
      button.style.left = `${Math.max(0, Math.min(100, position * 100))}%`;
      button.classList.toggle(
        "selected",
        String(registration.id) === String(state.selectedRegistrationId),
      );
      button.addEventListener("click", () => selectRegistration(registration));
      track.append(button);
    });
    timeline.append(track);
    const meta = document.createElement("div");
    meta.className = "timeline-meta";
    const coverage = document.createElement("span");
    coverage.textContent = `관측 ${new Set(registrations.map((registration) => String(registration.frame_index))).size} / ${frameCount} frames`;
    const range = document.createElement("span");
    range.textContent = `${formatTime(state.frames[0]?.time_seconds ?? 0, false)} → ${formatTime(state.frames[state.frames.length - 1]?.time_seconds ?? state.video?.duration ?? 0, false)}`;
    meta.append(coverage, range);
    timeline.append(meta);
    els.qualityTimeline.append(timeline);
    const fragment = document.createDocumentFragment();
    [...registrations].reverse().forEach((registration) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "history-item";
      item.classList.toggle(
        "selected",
        String(registration.id) === String(state.selectedRegistrationId),
      );
      item.addEventListener("click", () => selectRegistration(registration));
      const marker = document.createElement("span");
      marker.className = `history-marker ${statusClass(registration.status)}`;
      const body = document.createElement("span");
      body.className = "history-item-body";
      const line = document.createElement("span");
      line.className = "history-item-line";
      const title = document.createElement("strong");
      title.textContent = `F${String(registration.frame_index ?? "—").padStart(4, "0")}`;
      const time = document.createElement("small");
      time.textContent = formatTime(registration.time_seconds, false);
      line.append(title, time);
      const detail = document.createElement("span");
      detail.className = "history-item-detail";
      detail.textContent = `${statusLabel(registration.status)} · ${registration.source === "propagated" ? "전파" : "수동"}`;
      body.append(line, detail);
      const arrow = document.createElement("span");
      arrow.className = "history-arrow";
      arrow.textContent = "›";
      item.append(marker, body, arrow);
      fragment.append(item);
    });
    els.historyList.append(fragment);
  }

  function updateUndoRedo() {
    const history = getHistory();
    if (els.undoButton)
      els.undoButton.disabled = !history?.canUndo || state.busy.has("frame");
    if (els.redoButton)
      els.redoButton.disabled = !history?.canRedo || state.busy.has("frame");
  }
  function renderAll() {
    renderVideoMeta();
    renderFrameReadout();
    renderSourceCanvas();
    renderPitchCanvas();
    renderPointTable();
    renderField();
    renderEstimate();
    renderResult();
    renderHistory();
    updateUndoRedo();
    if (els.propagateButton)
      els.propagateButton.disabled =
        state.busy.has("propagate") || !state.savedResult;
    els.propagationHint.textContent = state.savedResult
      ? "전파 후 결과를 선택하고 새 VALIDATION 점으로 다시 확인하세요."
      : "저장된 등록 결과를 선택하면 사용할 수 있습니다.";
    updateCanvasDiagnostics();
  }

  function renderLibrary(videos) {
    els.libraryList.replaceChildren();
    if (!videos.length) {
      const empty = document.createElement("div");
      empty.className = "subtle-empty";
      empty.textContent = "등록된 영상이 없습니다.";
      els.libraryList.append(empty);
      return;
    }
    const fragment = document.createDocumentFragment();
    videos.forEach((video) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "library-item";
      item.dataset.videoId = String(video.id);
      item.classList.toggle(
        "selected",
        String(state.video?.id) === String(video.id),
      );
      const main = document.createElement("span");
      main.className = "library-item-main";
      const strong = document.createElement("strong");
      strong.textContent = video.name || `video-${video.id}`;
      const small = document.createElement("small");
      small.textContent = `${video.width && video.height ? `${video.width}×${video.height}` : "해상도 확인 전"} · ${formatDuration(video.duration)}`;
      main.append(strong, small);
      const arrow = document.createElement("span");
      arrow.className = "library-arrow";
      arrow.textContent = "›";
      item.append(main, arrow);
      item.addEventListener("click", () => loadVideo(video));
      fragment.append(item);
    });
    els.libraryList.append(fragment);
  }
  function renderLibraryError(message) {
    els.libraryList.replaceChildren();
    const error = document.createElement("div");
    error.className = "subtle-empty error-empty";
    error.textContent = `목록을 불러오지 못했습니다: ${message}`;
    els.libraryList.append(error);
  }
  async function refreshLibrary() {
    setBusy("library", true);
    try {
      const videos = await getJson("/api/videos");
      renderLibrary(Array.isArray(videos) ? videos : []);
    } catch (error) {
      setApiState("offline", "API 연결 실패");
      renderLibraryError(error.message);
    } finally {
      setBusy("library", false);
    }
  }

  function clearSessionState() {
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.video = null;
    state.sourceRevision = "legacy";
    state.frames = [];
    state.currentFrameIndex = 0;
    state.sliderPreviewIndex = null;
    state.sourceImage = null;
    state.sourceObjectUrl = null;
    state.sourceSize = { width: 0, height: 0 };
    state.viewport = null;
    state.draftKey = null;
    state.pairs = [];
    state.pendingImage = null;
    state.pendingPitch = null;
    state.field = DEFAULT_FIELD();
    state.registrations = [];
    invalidateDisplayedResults();
    state.draftVersion = 0;
    state.pendingViewSnapshot = null;
    state.histories.clear();
    els.sourceWrap.style.aspectRatio = "16 / 9";
    els.emptyState.hidden = false;
    els.analysisStage.hidden = true;
    [
      "sessionPanel",
      "framePanel",
      "exportPanel",
      "fieldPanel",
      "estimatePanel",
      "resultPanel",
      "propagationPanel",
      "historyPanel",
    ].forEach((id) => {
      els[id].hidden = true;
    });
    renderAll();
  }
  function populateVideo(video) {
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.video = video;
    state.sourceRevision = sourceRevision(video);
    state.frames = [];
    state.currentFrameIndex = 0;
    state.sliderPreviewIndex = null;
    state.sourceImage = null;
    state.sourceObjectUrl = null;
    state.sourceSize = { width: 0, height: 0 };
    state.viewport = null;
    state.draftKey = null;
    state.pairs = [];
    state.pendingImage = null;
    state.pendingPitch = null;
    state.field = DEFAULT_FIELD();
    state.registrations = [];
    invalidateDisplayedResults();
    state.draftVersion = 0;
    state.pendingViewSnapshot = null;
    state.selectedPointIndex = null;
    els.emptyState.hidden = true;
    els.analysisStage.hidden = false;
    [
      "sessionPanel",
      "framePanel",
      "exportPanel",
      "fieldPanel",
      "estimatePanel",
      "resultPanel",
      "propagationPanel",
      "historyPanel",
    ].forEach((id) => {
      els[id].hidden = false;
    });
    els.exportLink.href = `/api/videos/${encodeURIComponent(video.id)}/export`;
    els.sourceWrap.style.aspectRatio = "16 / 9";
    renderAll();
  }
  async function importVideo(event) {
    event.preventDefault();
    const path = els.videoPath.value.trim();
    if (!path) {
      els.videoPath.focus();
      showAlert("영상 경로를 입력하세요.");
      return;
    }
    persistDraft();
    const serial = nextSerial();
    setBusy("import", true);
    hideAlert();
    if (!state.video) {
      els.emptyState.hidden = true;
      els.analysisStage.hidden = false;
      els.sourceCanvasMessage.textContent =
        "영상 메타데이터와 실제 PTS 프레임을 확인하는 중입니다.";
      els.sourceCanvasMessage.hidden = false;
    }
    try {
      const response = await getJson("/api/videos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path }),
      });
      if (serial !== state.requestSerial) return;
      populateVideo(response);
      refreshLibrary();
      await loadFrames(response.id, serial);
      await loadRegistrations(response.id, serial);
    } catch (error) {
      if (serial === state.requestSerial) {
        if (!state.video) {
          els.emptyState.hidden = false;
          els.analysisStage.hidden = true;
        }
        showAlert(`영상을 등록하지 못했습니다: ${error.message}`);
      }
    } finally {
      setBusy("import", false);
    }
  }
  async function loadVideo(video) {
    persistDraft();
    const serial = nextSerial();
    populateVideo(video);
    setBusy("frames", true);
    hideAlert();
    try {
      await loadFrames(video.id, serial);
      await loadRegistrations(video.id, serial);
    } catch (error) {
      if (serial === state.requestSerial)
        showAlert(`영상을 불러오지 못했습니다: ${error.message}`);
    } finally {
      if (serial === state.requestSerial) setBusy("frames", false);
    }
  }
  async function loadFrames(videoId, serial = state.requestSerial) {
    setBusy("frames", true);
    try {
      const frames = await getJson(
        `/api/videos/${encodeURIComponent(videoId)}/frames`,
      );
      if (
        serial !== state.requestSerial ||
        !state.video ||
        String(state.video.id) !== String(videoId)
      )
        return;
      state.frames = Array.isArray(frames) ? frames : [];
      state.currentFrameIndex = 0;
      els.frameSlider.max = String(Math.max(0, state.frames.length - 1));
      els.frameSlider.value = "0";
      restoreDraftForCurrentFrame();
      renderFrameReadout();
      renderLibraryCurrent();
      if (!state.frames.length) {
        els.sourceCanvasMessage.textContent =
          "이 영상에서 프레임을 찾지 못했습니다.";
        els.sourceCanvasMessage.hidden = false;
        return;
      }
      await loadFrameImage(0, serial);
    } finally {
      if (serial === state.requestSerial) setBusy("frames", false);
    }
  }
  async function loadRegistrations(videoId, serial = state.requestSerial) {
    const registrations = await getJson(
      `/api/videos/${encodeURIComponent(videoId)}/registrations`,
    );
    if (
      serial !== state.requestSerial ||
      !state.video ||
      String(state.video.id) !== String(videoId)
    )
      return;
    state.registrations = Array.isArray(registrations) ? registrations : [];
    renderHistory();
  }
  function invalidateSourceImage() {
    state.imageSerial += 1;
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.sourceObjectUrl = null;
    state.sourceImage = null;
    state.sourceSize = { width: 0, height: 0 };
    state.viewport = null;
    els.sourceCanvasMessage.textContent =
      "실제 프레임 PNG를 불러오는 중입니다.";
    els.sourceCanvasMessage.hidden = false;
  }
  async function loadFrameImage(index, serial = state.requestSerial) {
    if (!state.video || !state.frames[index]) return;
    const videoId = String(state.video.id);
    const frameSerial = ++state.frameSerial;
    const imageSerial = ++state.imageSerial;
    setBusy("frame", true);
    els.sourceCanvasMessage.textContent =
      "실제 프레임 PNG를 불러오는 중입니다.";
    els.sourceCanvasMessage.hidden = false;
    const current = () =>
      String(state.video?.id) === videoId &&
      frameSerial === state.frameSerial &&
      imageSerial === state.imageSerial;
    try {
      const response = await apiFetch(
        `/api/videos/${encodeURIComponent(videoId)}/frame/${encodeURIComponent(index)}`,
      );
      const blob = await response.blob();
      if (!current()) return;
      const objectUrl = URL.createObjectURL(blob);
      const image = new Image();
      image.decoding = "async";
      image.onload = () => {
        if (!current()) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
        state.sourceObjectUrl = objectUrl;
        state.sourceImage = image;
        state.sourceSize = {
          width: image.naturalWidth,
          height: image.naturalHeight,
        };
        els.sourceWrap.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
        state.currentFrameIndex = index;
        state.sliderPreviewIndex = null;
        els.frameSlider.value = String(index);
        ensureViewport(true);
        els.sourceCanvasMessage.hidden = true;
        renderAll();
        setBusy("frame", false);
      };
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        if (current()) {
          showAlert("프레임 이미지를 해석하지 못했습니다.");
          setBusy("frame", false);
        }
      };
      image.src = objectUrl;
    } catch (error) {
      if (current()) {
        showAlert(`프레임을 불러오지 못했습니다: ${error.message}`);
        setBusy("frame", false);
      }
    }
  }
  async function selectFrame(
    index,
    { fromHistory = false, skipDraftRestore = false, force = false } = {},
  ) {
    if (!state.frames.length) return;
    const parsed = Math.max(
      0,
      Math.min(Number(index) || 0, state.frames.length - 1),
    );
    if (!force && parsed === state.currentFrameIndex && state.sourceImage)
      return;
    persistDraft();
    const serial = nextSerial();
    state.currentFrameIndex = parsed;
    state.sliderPreviewIndex = null;
    els.frameSlider.value = String(parsed);
    state.pendingImage = null;
    state.pendingPitch = null;
    state.selectedPointIndex = null;
    invalidateDisplayedResults();
    if (!skipDraftRestore) restoreDraftForCurrentFrame();
    invalidateSourceImage();
    renderAll();
    await loadFrameImage(parsed, serial);
    if (!fromHistory)
      setInstruction("소스 프레임에서 FIT 대응점을 클릭하세요.");
  }
  function renderLibraryCurrent() {
    [...els.libraryList.querySelectorAll(".library-item")].forEach((item) =>
      item.classList.toggle(
        "selected",
        item.dataset.videoId === String(state.video?.id),
      ),
    );
  }

  function setInstruction(message) {
    const target = els.instructionStrip?.querySelector("div:nth-child(2) span");
    if (target) target.textContent = message;
  }
  function updateMode(mode) {
    state.mode = mode === "validation" ? "validation" : "fit";
    state.tool = state.mode === "validation" ? "add-validation" : "add-fit";
    state.independentValidation = state.mode === "validation";
    els.modeButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.mode === state.mode),
    );
    updateToolButtons();
    updateCanvasDiagnostics();
    renderSourceCanvas();
    renderPitchCanvas();
    setInstruction(
      state.mode === "fit"
        ? "소스 프레임에서 FIT 대응점을 클릭하세요."
        : "소스 프레임에서 VALIDATION 대응점을 클릭하세요. 예측 오버레이는 숨겨집니다.",
    );
  }
  function updateToolButtons() {
    els.toolButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.tool === state.tool),
    );
    els.sourceWrap.dataset.tool = state.tool;
  }
  function updateTool(tool) {
    if (!["select", "add-fit", "add-validation", "pan"].includes(tool)) return;
    state.tool = tool;
    state.mode = tool === "add-validation" ? "validation" : "fit";
    if (tool === "add-validation") state.independentValidation = true;
    if (tool === "add-fit" || tool === "select" || tool === "pan")
      state.independentValidation = Boolean(
        els.independentValidationToggle?.checked,
      );
    state.pendingImage = null;
    state.pendingPitch = null;
    els.modeButtons.forEach((button) =>
      button.classList.toggle("active", button.dataset.mode === state.mode),
    );
    updateToolButtons();
    updateCanvasDiagnostics();
    renderSourceCanvas();
    renderPitchCanvas();
    setInstruction(
      tool === "pan"
        ? "소스 프레임을 드래그해 뷰포트를 이동하세요."
        : state.mode === "validation"
          ? "독립 VALIDATION 대응점을 지정하세요. 예측 오버레이는 숨겨집니다."
          : tool === "select"
            ? "기존 점을 선택·이동하거나 빈 곳을 클릭해 대응점을 추가하세요."
            : "소스 프레임에서 FIT 대응점을 클릭하세요.",
    );
  }
  function beginPendingImage(point) {
    state.pendingImage = [
      Math.max(0, Math.min(state.sourceSize.width, point.image.x)),
      Math.max(0, Math.min(state.sourceSize.height, point.image.y)),
    ];
    state.pendingPitch = null;
    renderSourceCanvas();
    renderPitchCanvas();
    setInstruction("피치 좌표 평면에서 대응점을 클릭하세요.");
  }
  function completePendingPair(point) {
    if (!state.pendingImage || !point) return;
    state.pairs.push({
      id: `pair-${Date.now()}-${state.pairs.length}`,
      image: state.pendingImage.slice(),
      pitch: [point.x, point.y],
      role: state.mode,
    });
    state.pendingImage = null;
    state.pendingPitch = null;
    markDraftDirty();
    commitCurrentHistory();
    renderAll();
    setInstruction(
      state.mode === "fit"
        ? "FIT 점이 추가되었습니다. 다음 대응점을 지정하세요."
        : "VALIDATION 점이 추가되었습니다. 다음 대응점을 지정하세요.",
    );
  }
  function nearestSourcePoint(viewPoint) {
    let best = null;
    let distance = Infinity;
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      const view = viewPointFromImage(pair.image);
      if (!view) return;
      const d = Math.hypot(view.x - viewPoint.x, view.y - viewPoint.y);
      if (d < distance) {
        distance = d;
        best = index;
      }
    });
    return distance <= 18 ? best : null;
  }
  function nearestPitchPoint(point) {
    let best = null;
    let distance = Infinity;
    const target = pitchToCanvas([point.x, point.y]);
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      const view = pitchToCanvas(pair.pitch);
      const d = Math.hypot(view.x - target.x, view.y - target.y);
      if (d < distance) {
        distance = d;
        best = index;
      }
    });
    return distance <= 20 ? best : null;
  }
  function startPointDrag(index, axis, event) {
    state.selectedPointIndex = index;
    state.drag = {
      kind: "point",
      axis,
      index,
      last: canvasPointFromEvent(
        axis === "image" ? els.sourceCanvas : els.pitchCanvas,
        event,
      ),
      moved: false,
      invalidated: false,
    };
    try {
      (axis === "image" ? els.sourceCanvas : els.pitchCanvas).setPointerCapture(
        event.pointerId,
      );
    } catch (_) {
      /* unsupported */
    }
  }
  function startPan(event) {
    state.drag = {
      kind: "pan",
      last: canvasPointFromEvent(els.sourceCanvas, event),
      moved: false,
    };
    els.sourceWrap.classList.add("is-panning");
    try {
      els.sourceCanvas.setPointerCapture(event.pointerId);
    } catch (_) {
      /* unsupported */
    }
  }
  function handleSourcePointerDown(event) {
    if (!state.sourceImage || state.busy.has("frame")) return;
    const point = sourceImagePointFromEvent(event);
    if (!point) return;
    els.sourceCanvas.focus();
    if (state.tool === "pan" || event.button === 1 || event.altKey) {
      event.preventDefault();
      startPan(event);
      return;
    }
    const hit = nearestSourcePoint(point.view);
    if (state.tool === "select" && hit !== null) {
      event.preventDefault();
      startPointDrag(hit, "image", event);
      return;
    }
    beginPendingImage(point);
  }
  function handlePitchPointerDown(event) {
    const point = pitchFromEvent(event);
    if (!point) return;
    els.pitchCanvas.focus();
    if (state.pendingImage) {
      completePendingPair(point);
      return;
    }
    if (state.tool === "select") {
      const hit = nearestPitchPoint(point);
      if (hit !== null) {
        event.preventDefault();
        startPointDrag(hit, "pitch", event);
      }
    }
  }
  function moveDraggedPoint(event) {
    if (!state.drag) return;
    if (state.drag.kind === "pan") {
      const point = canvasPointFromEvent(els.sourceCanvas, event);
      if (!point || !state.drag.last) return;
      const dx = point.x - state.drag.last.x;
      const dy = point.y - state.drag.last.y;
      if (dx || dy) {
        state.viewport.pan(dx, dy);
        state.drag.moved = true;
        state.drag.last = point;
        persistDraft();
        renderSourceCanvas();
        updateCanvasDiagnostics();
        updateLoupe(event);
      }
      return;
    }
    const index = state.drag.index;
    const pair = state.pairs[index];
    if (!pair) return;
    const point = canvasPointFromEvent(
      state.drag.axis === "image" ? els.sourceCanvas : els.pitchCanvas,
      event,
    );
    if (!point) return;
    if (state.drag.axis === "image") {
      const converted = sourceImagePointFromEvent(event);
      if (!converted) return;
      pair.image[0] = Math.max(
        0,
        Math.min(state.sourceSize.width, converted.image.x),
      );
      pair.image[1] = Math.max(
        0,
        Math.min(state.sourceSize.height, converted.image.y),
      );
    } else {
      const pitch = pitchFromEvent(event);
      if (!pitch) return;
      pair.pitch[0] = pitch.x;
      pair.pitch[1] = pitch.y;
    }
    state.drag.moved = true;
    if (!state.drag.invalidated) {
      state.drag.invalidated = true;
      markDraftDirty({ persist: false });
    }
    renderSourceCanvas();
    renderPitchCanvas();
    renderPointTable();
    renderEstimate();
    updateLoupe(event);
  }
  function finishDrag(event) {
    const drag = state.drag;
    if (!drag) return;
    state.drag = null;
    els.sourceWrap.classList.remove("is-panning");
    try {
      els.sourceCanvas.releasePointerCapture(event.pointerId);
      els.pitchCanvas.releasePointerCapture(event.pointerId);
    } catch (_) {
      /* unsupported */
    }
    if (drag.kind === "pan") {
      if (drag.moved) persistDraft();
      return;
    }
    if (drag.moved) {
      if (!drag.invalidated) markDraftDirty({ persist: false });
      commitCurrentHistory();
      renderAll();
    }
  }
  function nudgeSelected(canvasKind, key, event) {
    if (
      state.selectedPointIndex === null ||
      !state.pairs[state.selectedPointIndex]
    )
      return false;
    const pair = state.pairs[state.selectedPointIndex];
    const amount = event.shiftKey ? 10 : 1;
    if (canvasKind === "image") {
      if (key === "ArrowLeft") pair.image[0] -= amount;
      else if (key === "ArrowRight") pair.image[0] += amount;
      else if (key === "ArrowUp") pair.image[1] -= amount;
      else if (key === "ArrowDown") pair.image[1] += amount;
      pair.image[0] = Math.max(
        0,
        Math.min(state.sourceSize.width, pair.image[0]),
      );
      pair.image[1] = Math.max(
        0,
        Math.min(state.sourceSize.height, pair.image[1]),
      );
    } else {
      const delta = amount / 1000;
      if (key === "ArrowLeft") pair.pitch[0] -= delta;
      else if (key === "ArrowRight") pair.pitch[0] += delta;
      else if (key === "ArrowUp") pair.pitch[1] -= delta;
      else if (key === "ArrowDown") pair.pitch[1] += delta;
      pair.pitch[0] = Math.max(0, Math.min(1, pair.pitch[0]));
      pair.pitch[1] = Math.max(0, Math.min(1, pair.pitch[1]));
    }
    markDraftDirty();
    commitCurrentHistory();
    renderAll();
    return true;
  }

  function updateLoupe(event) {
    if (!state.loupe || !state.sourceImage || !els.sourceLoupe) return;
    const sourcePoint = sourceImagePointFromEvent(event);
    if (!sourcePoint) return;
    const rect = els.sourceWrap.getBoundingClientRect();
    const left = Math.max(
      4,
      Math.min(rect.width - 196, sourcePoint.view.x + 8),
    );
    const top = Math.max(
      4,
      Math.min(rect.height - 216, sourcePoint.view.y + 8),
    );
    els.sourceLoupe.hidden = false;
    els.sourceLoupe.style.left = `${left}px`;
    els.sourceLoupe.style.top = `${top}px`;
    const canvas = els.loupeCanvas;
    const context = canvas.getContext("2d");
    const size = Math.min(canvas.width, canvas.height);
    const radius = Math.max(
      8,
      Math.min(state.sourceSize.width, state.sourceSize.height) / 24,
    );
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.imageSmoothingEnabled = false;
    context.drawImage(
      state.sourceImage,
      sourcePoint.image.x - radius,
      sourcePoint.image.y - radius,
      radius * 2,
      radius * 2,
      0,
      0,
      size,
      size,
    );
    context.strokeStyle = "#55e3c1";
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(size / 2 - 12, size / 2);
    context.lineTo(size / 2 + 12, size / 2);
    context.moveTo(size / 2, size / 2 - 12);
    context.lineTo(size / 2, size / 2 + 12);
    context.stroke();
    els.loupeReadout.textContent = `x ${sourcePoint.image.x.toFixed(1)} · y ${sourcePoint.image.y.toFixed(1)}`;
  }
  function toggleOverlay(name, value) {
    state.overlays[name] = Boolean(value);
    if (name === "support" && els.overlayRegionToggle)
      els.overlayRegionToggle.setAttribute(
        "aria-pressed",
        String(state.overlays.support),
      );
    renderSourceCanvas();
    renderPitchCanvas();
  }
  function changeView(action) {
    if (!state.sourceImage || !state.viewport) return;
    const view = viewCssSize();
    if (action === "fit")
      state.viewport.fit(
        state.sourceSize.width,
        state.sourceSize.height,
        view.width,
        view.height,
      );
    else if (action === "one") state.viewport.oneToOne();
    else if (action === "in")
      state.viewport.zoomAt(1.25, { x: view.width / 2, y: view.height / 2 });
    else if (action === "out")
      state.viewport.zoomAt(0.8, { x: view.width / 2, y: view.height / 2 });
    persistDraft();
    renderSourceCanvas();
    updateCanvasDiagnostics();
  }
  function handleSourceWheel(event) {
    if (!state.sourceImage || !state.viewport) return;
    event.preventDefault();
    const point = canvasPointFromEvent(els.sourceCanvas, event);
    if (!point) return;
    state.viewport.zoomAt(event.deltaY < 0 ? 1.15 : 1 / 1.15, point);
    persistDraft();
    renderSourceCanvas();
    updateCanvasDiagnostics();
    updateLoupe(event);
  }

  function payloadForRegistration() {
    const frame = currentFrame();
    state.field = collectField();
    return {
      frame_index: frame?.index ?? state.currentFrameIndex,
      field: deepClone(state.field),
      points: state.pairs.map((pair) => ({
        image: [pair.image[0], pair.image[1]],
        pitch: [pair.pitch[0], pair.pitch[1]],
        role: pair.role,
      })),
      note: "",
    };
  }
  function previewRequestKey(version) {
    return {
      videoId: String(state.video?.id ?? ""),
      sourceRevision: String(state.sourceRevision),
      frameIndex: currentFrameNumber(),
      draftVersion: Number(version),
    };
  }
  function previewKeyMatches(key, response) {
    return (
      key.videoId === String(state.video?.id ?? "") &&
      key.sourceRevision === String(state.sourceRevision) &&
      key.frameIndex === currentFrameNumber() &&
      key.draftVersion === Number(state.draftVersion) &&
      Number(response?.draft_version) === key.draftVersion
    );
  }
  async function previewEstimate() {
    if (!state.video || !state.sourceImage || !state.frames.length) return;
    const version = state.draftVersion;
    const key = previewRequestKey(version);
    const serial = nextSerial();
    setBusy("preview", true);
    hideAlert();
    state.preview = null;
    renderResult();
    renderSourceCanvas();
    renderPitchCanvas();
    try {
      const response = await getJson(
        `/api/videos/${encodeURIComponent(state.video.id)}/registrations/preview`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            ...payloadForRegistration(),
            draft_version: version,
          }),
        },
      );
      if (serial !== state.requestSerial || !previewKeyMatches(key, response))
        return;
      state.preview = {
        ...response,
        source: "preview",
        persisted: false,
        draft_version: version,
      };
      renderAll();
      setInstruction(
        `서버 미리보기 결과: ${statusLabel(response.status)}. 저장 시 서버가 다시 계산합니다.`,
      );
    } catch (error) {
      if (
        serial === state.requestSerial &&
        key.draftVersion === state.draftVersion
      )
        showAlert(`미리보기를 계산하지 못했습니다: ${error.message}`);
    } finally {
      if (serial === state.requestSerial) setBusy("preview", false);
    }
  }
  async function saveRegistration() {
    if (!canSave()) {
      renderAll();
      showAlert(
        "비어 있거나 숫자가 아닌 대응점은 저장할 수 없습니다.",
        "warning",
      );
      return;
    }
    if (!state.video) return;
    const serial = nextSerial();
    const key = previewRequestKey(state.draftVersion);
    setBusy("save", true);
    hideAlert();
    try {
      const response = await getJson(
        `/api/videos/${encodeURIComponent(state.video.id)}/registrations`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payloadForRegistration()),
        },
      );
      if (
        serial !== state.requestSerial ||
        key.videoId !== String(state.video?.id) ||
        key.frameIndex !== currentFrameNumber()
      )
        return;
      state.savedResult = response;
      state.selectedRegistrationId = response.id;
      state.pairs = clonePairs(response.points || state.pairs);
      state.preview = null;
      state.registrations = [...state.registrations, response];
      persistDraft();
      renderAll();
      showAlert(
        response.status === "usable"
          ? "등록 결과를 저장했습니다."
          : "결과를 저장했지만 검토가 필요합니다.",
        response.status === "usable" ? "success" : "warning",
      );
    } catch (error) {
      if (serial === state.requestSerial)
        showAlert(`등록 결과를 저장하지 못했습니다: ${error.message}`);
    } finally {
      if (serial === state.requestSerial) setBusy("save", false);
    }
  }
  async function selectRegistration(registration) {
    if (!registration) return;
    const selectionToken = ++state.selectionSerial;
    const selectedVideoId = String(state.video?.id ?? "");
    nextSerial();
    const target = state.frames.findIndex(
      (frame) => Number(frame.index) === Number(registration.frame_index),
    );
    const targetIndex = target >= 0 ? target : Number(registration.frame_index);
    if (targetIndex !== state.currentFrameIndex || !state.sourceImage)
      await selectFrame(targetIndex, { fromHistory: true });
    if (
      selectionToken !== state.selectionSerial ||
      selectedVideoId !== String(state.video?.id ?? "") ||
      Number(registration.frame_index) !== currentFrameNumber()
    )
      return;
    state.selectedRegistrationId = registration.id;
    state.savedResult = registration;
    state.preview = null;
    state.pairs = clonePairs(registration.points || []);
    state.field = {
      length: numericOrNull(registration.field?.length),
      width: numericOrNull(registration.field?.width),
      dimension_source: registration.field?.dimension_source || "",
      dimensions_verified: Boolean(registration.field?.dimensions_verified),
    };
    state.pendingImage = null;
    state.pendingPitch = null;
    state.selectedPointIndex = null;
    const history = getHistory();
    if (history) history.reset(snapshot());
    persistDraft();
    renderAll();
    els.propagationTarget.value = String(
      Math.min(targetIndex + 1, Math.max(0, state.frames.length - 1)),
    );
  }
  async function propagateRegistration() {
    if (!state.video || !state.savedResult) return;
    const target = Number(els.propagationTarget.value);
    if (
      !Number.isInteger(target) ||
      target < 0 ||
      target >= state.frames.length
    ) {
      showAlert("유효한 대상 프레임 인덱스를 입력하세요.");
      return;
    }
    const serial = nextSerial();
    setBusy("propagate", true);
    hideAlert();
    try {
      const response = await getJson(
        `/api/videos/${encodeURIComponent(state.video.id)}/propagate`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            registration_id: state.savedResult.id,
            target_frame_index: target,
          }),
        },
      );
      if (serial !== state.requestSerial) return;
      state.registrations = [...state.registrations, response];
      await selectRegistration(response);
      showAlert(
        response.status === "usable"
          ? "전파 결과를 저장했습니다. VALIDATION을 다시 확인하세요."
          : "전파 결과가 저장됐지만 검토가 필요합니다.",
        "warning",
      );
    } catch (error) {
      if (serial === state.requestSerial)
        showAlert(`전파하지 못했습니다: ${error.message}`);
    } finally {
      if (serial === state.requestSerial) setBusy("propagate", false);
    }
  }
  function undo() {
    const history = getHistory();
    const value = history?.undo();
    if (!value) return;
    applySnapshot(value);
    markDraftDirty();
    persistDraft();
    renderAll();
    setInstruction("실행을 취소했습니다.");
  }
  function redo() {
    const history = getHistory();
    const value = history?.redo();
    if (!value) return;
    applySnapshot(value);
    markDraftDirty();
    persistDraft();
    renderAll();
    setInstruction("다시 실행했습니다.");
  }

  function bindEvents() {
    els.importForm.addEventListener("submit", importVideo);
    els.dismissAlert.addEventListener("click", hideAlert);
    els.refreshLibrary.addEventListener("click", refreshLibrary);
    els.sourceCanvas.addEventListener("pointerdown", handleSourcePointerDown);
    els.sourceCanvas.addEventListener("pointermove", (event) => {
      if (state.drag) moveDraggedPoint(event);
      else updateLoupe(event);
    });
    els.sourceCanvas.addEventListener("pointerup", finishDrag);
    els.sourceCanvas.addEventListener("pointercancel", finishDrag);
    els.sourceCanvas.addEventListener("wheel", handleSourceWheel, {
      passive: false,
    });
    els.sourceCanvas.addEventListener("mouseleave", () => {
      if (state.loupe) els.sourceLoupe.hidden = true;
    });
    els.pitchCanvas.addEventListener("pointerdown", handlePitchPointerDown);
    els.pitchCanvas.addEventListener("pointermove", moveDraggedPoint);
    els.pitchCanvas.addEventListener("pointerup", finishDrag);
    els.pitchCanvas.addEventListener("pointercancel", finishDrag);
    els.pitchCanvas.addEventListener("mousemove", (event) => {
      if (state.pendingImage) {
        const point = pitchFromEvent(event);
        if (point) {
          state.pendingPitch = point;
          renderPitchCanvas();
        }
      }
    });
    els.pitchCanvas.addEventListener("mouseleave", () => {
      if (state.pendingPitch) {
        state.pendingPitch = null;
        renderPitchCanvas();
      }
    });
    els.modeButtons.forEach((button) =>
      button.addEventListener("click", () => updateMode(button.dataset.mode)),
    );
    els.toolButtons.forEach((button) =>
      button.addEventListener("click", () => updateTool(button.dataset.tool)),
    );
    els.cancelPending.addEventListener("click", () => {
      state.pendingImage = null;
      state.pendingPitch = null;
      renderAll();
      setInstruction("선택이 취소되었습니다. 소스 프레임에서 다시 시작하세요.");
    });
    els.clearPoints.addEventListener("click", () => {
      if (!state.pairs.length) return;
      state.pairs = [];
      state.pendingImage = null;
      state.pendingPitch = null;
      markDraftDirty();
      commitCurrentHistory();
      renderAll();
      setInstruction(
        "대응점이 초기화되었습니다. 소스 프레임에서 FIT 대응점을 클릭하세요.",
      );
    });
    els.frameSlider.addEventListener("input", () => {
      const index = Number(els.frameSlider.value);
      state.sliderPreviewIndex = index;
      renderFrameReadout(index);
      renderEstimate();
    });
    els.frameSlider.addEventListener("change", () =>
      selectFrame(Number(els.frameSlider.value)),
    );
    els.previousFrame.addEventListener("click", () =>
      selectFrame(state.currentFrameIndex - 1),
    );
    els.nextFrame.addEventListener("click", () =>
      selectFrame(state.currentFrameIndex + 1),
    );
    [
      els.fieldLength,
      els.fieldWidth,
      els.dimensionSource,
      els.dimensionsVerified,
    ].forEach((input) => {
      input.addEventListener("input", () => {
        state.field = collectField();
        markDraftDirty();
        renderField();
        renderEstimate();
        renderResult();
        renderSourceCanvas();
      });
      input.addEventListener("change", () => {
        state.field = collectField();
        commitCurrentHistory();
        renderAll();
      });
    });
    els.estimateButton.addEventListener("click", previewEstimate);
    els.saveButton.addEventListener("click", saveRegistration);
    els.propagateButton.addEventListener("click", propagateRegistration);
    els.undoButton.addEventListener("click", undo);
    els.redoButton.addEventListener("click", redo);
    els.fitView.addEventListener("click", () => changeView("fit"));
    els.oneToOneView.addEventListener("click", () => changeView("one"));
    els.zoomInView.addEventListener("click", () => changeView("in"));
    els.zoomOutView.addEventListener("click", () => changeView("out"));
    els.overlayLinesToggle.addEventListener("change", () =>
      toggleOverlay("lines", els.overlayLinesToggle.checked),
    );
    els.overlaySupportToggle.addEventListener("change", () => {
      toggleOverlay("support", els.overlaySupportToggle.checked);
      els.overlayRegionToggle.setAttribute(
        "aria-pressed",
        String(els.overlaySupportToggle.checked),
      );
    });
    els.overlayResidualToggle.addEventListener("change", () =>
      toggleOverlay("residual", els.overlayResidualToggle.checked),
    );
    els.overlayRegionToggle.addEventListener("click", () => {
      els.overlaySupportToggle.checked = !els.overlaySupportToggle.checked;
      toggleOverlay("support", els.overlaySupportToggle.checked);
    });
    els.independentValidationToggle.addEventListener("change", () => {
      state.independentValidation =
        els.independentValidationToggle.checked || state.mode === "validation";
      renderSourceCanvas();
      renderPitchCanvas();
      updateCanvasDiagnostics();
    });
    els.loupeToggle.addEventListener("change", () => {
      state.loupe = els.loupeToggle.checked;
      if (!state.loupe) els.sourceLoupe.hidden = true;
    });
    els.sourceCanvas.addEventListener("keydown", (event) => {
      if (
        ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
          event.key,
        ) &&
        nudgeSelected("image", event.key, event)
      )
        event.preventDefault();
    });
    els.pitchCanvas.addEventListener("keydown", (event) => {
      if (
        ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(
          event.key,
        ) &&
        nudgeSelected("pitch", event.key, event)
      )
        event.preventDefault();
    });
    document.addEventListener("keydown", (event) => {
      if (event.target.matches("input, select, textarea")) return;
      if (event.key === "v") updateTool("select");
      else if (event.key === "f") updateTool("add-fit");
      else if (event.key === "g") updateTool("add-validation");
      else if (event.key === "h") updateTool("pan");
      else if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "z"
      ) {
        event.preventDefault();
        event.shiftKey ? redo() : undo();
      } else if (
        (event.ctrlKey || event.metaKey) &&
        event.key.toLowerCase() === "y"
      ) {
        event.preventDefault();
        redo();
      }
    });
    window.addEventListener("resize", () => {
      if (state.viewport && state.sourceImage) {
        const view = viewCssSize();
        try {
          state.viewport.resize(view.width, view.height);
        } catch (_) {}
      }
      renderSourceCanvas();
      renderPitchCanvas();
    });
  }

  async function init() {
    cacheElements();
    bindEvents();
    clearSessionState();
    updateMode("fit");
    setApiState("loading", "연결 확인 중");
    await refreshLibrary();
  }
  window.addEventListener("DOMContentLoaded", init);
})();
