(() => {
  'use strict';

  const API_BASE = '';
  const els = {};
  const state = {
    video: null,
    frames: [],
    currentFrameIndex: 0,
    sliderPreviewIndex: null,
    sourceImage: null,
    sourceObjectUrl: null,
    sourceSize: { width: 0, height: 0 },
    pairs: [],
    mode: 'fit',
    pendingImage: null,
    pendingPitch: null,
    field: { length: null, width: null, dimension_source: '', dimensions_verified: false },
    registrations: [],
    selectedRegistrationId: null,
    selectedResult: null,
    preview: null,
    requestSerial: 0,
    frameSerial: 0,
    imageSerial: 0,
    busy: new Set(),
  };

  const $ = (id) => document.getElementById(id);

  function cacheElements() {
    [
      'apiState', 'apiStateText', 'globalAlert', 'globalAlertText', 'dismissAlert', 'importForm', 'videoPath', 'importButton',
      'importHint', 'refreshLibrary', 'libraryList', 'sessionPanel', 'sessionName', 'videoDimensions', 'videoDuration',
      'videoFrameCount', 'videoTimeBase', 'framePanel', 'frameLoading', 'frameIndexReadout', 'frameTotalReadout',
      'frameTimeReadout', 'framePtsReadout', 'frameSlider', 'sliderEndLabel', 'previousFrame', 'nextFrame', 'exportPanel',
      'exportLink', 'emptyState', 'analysisStage', 'draftState', 'clearPoints', 'instructionStrip', 'cancelPending',
      'sourceCanvas', 'sourceWrap', 'sourceCanvasMessage', 'sourceCanvasSize', 'pitchCanvas', 'pointCount', 'pointTableBody',
      'fieldPanel', 'fieldLength', 'fieldWidth', 'dimensionsVerified', 'dimensionSource', 'dimensionHint', 'estimatePanel',
      'qualityLamp', 'estimateSummary', 'estimateButton', 'saveButton', 'resultPanel', 'resultSource', 'resultBody',
      'propagationPanel', 'propagationTarget', 'propagateButton', 'propagationHint', 'historyPanel', 'historyCount',
      'qualityTimeline', 'historyList',
    ].forEach((id) => { els[id] = $(id); });
    els.modeButtons = [...document.querySelectorAll('[data-mode]')];
  }

  function nextSerial() {
    state.requestSerial += 1;
    return state.requestSerial;
  }

  function setApiState(status, text) {
    els.apiState.dataset.status = status;
    els.apiStateText.textContent = text;
  }

  function showAlert(message, kind = 'error') {
    els.globalAlert.dataset.kind = kind;
    els.globalAlertText.textContent = message;
    els.globalAlert.hidden = false;
  }

  function hideAlert() {
    els.globalAlert.hidden = true;
    els.globalAlertText.textContent = '';
  }

  function setBusy(key, busy) {
    if (busy) state.busy.add(key); else state.busy.delete(key);
    const anyBusy = state.busy.size > 0;
    document.body.classList.toggle('is-busy', anyBusy);
    els.importButton.disabled = state.busy.has('import');
    els.refreshLibrary.disabled = state.busy.has('library');
    els.frameSlider.disabled = state.busy.has('frames') || !state.frames.length;
    els.saveButton.disabled = state.busy.has('save') || !canSave();
    els.propagateButton.disabled = state.busy.has('propagate') || !state.selectedResult;
    if (state.busy.has('frame')) els.frameLoading.hidden = false;
    else els.frameLoading.hidden = true;
  }

  async function apiFetch(path, options = {}) {
    const response = await fetch(`${API_BASE}${path}`, {
      ...options,
      headers: { Accept: 'application/json', ...(options.headers || {}) },
    });
    if (!response.ok) {
      let detail = `요청 실패 (${response.status})`;
      try {
        const payload = await response.json();
        if (payload && payload.detail) detail = String(payload.detail);
      } catch (_) { /* response may not be JSON */ }
      const error = new Error(detail);
      error.status = response.status;
      throw error;
    }
    setApiState('online', 'API 연결됨');
    return response;
  }

  async function getJson(path, options = {}) {
    const response = await apiFetch(path, options);
    return response.json();
  }

  function formatDuration(seconds) {
    if (!Number.isFinite(Number(seconds))) return '—';
    const value = Math.max(0, Number(seconds));
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = Math.floor(value % 60);
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  function formatTime(seconds, withHours = true) {
    if (!Number.isFinite(Number(seconds))) return '00:00:00.000';
    const value = Math.max(0, Number(seconds));
    const hours = Math.floor(value / 3600);
    const minutes = Math.floor((value % 3600) / 60);
    const secs = Math.floor(value % 60);
    const millis = Math.floor((value - Math.floor(value)) * 1000 + 1e-6);
    if (!withHours && hours === 0) return `${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${String(millis).padStart(3, '0')}`;
  }

  function formatPts(pts) {
    if (pts === null || pts === undefined || pts === '') return '—';
    const numeric = Number(pts);
    return Number.isFinite(numeric) ? String(pts) : '—';
  }

  function statusLabel(status) {
    if (status === 'usable') return '사용 가능';
    if (status === 'review') return '검토 필요';
    if (status === 'unavailable') return '사용 불가';
    return '미계산';
  }

  function statusClass(status) {
    if (status === 'usable') return 'usable';
    if (status === 'review') return 'review';
    if (status === 'unavailable') return 'unavailable';
    return 'idle';
  }

  function coordinateLabel(level) {
    if (level === 'metric') return '미터 좌표';
    if (level === 'normalized') return '정규화 좌표';
    if (level === 'image') return '이미지 좌표';
    return '—';
  }

  const REASON_LABELS = {
    invalid_points: '대응점 형식이 올바르지 않습니다.',
    invalid_point: '대응점 좌표를 확인하세요.',
    invalid_point_role: '대응점 역할이 올바르지 않습니다.',
    nonfinite_input: '숫자가 아닌 좌표가 포함되어 있습니다.',
    pitch_out_of_range: '피치 좌표가 0–1 범위를 벗어났습니다.',
    invalid_fit_points: 'FIT 대응점을 해석할 수 없습니다.',
    invalid_validation_points: 'VALIDATION 대응점을 해석할 수 없습니다.',
    insufficient_fit_points: 'FIT 대응점이 4개보다 적습니다.',
    missing_validation_points: '독립적인 VALIDATION 대응점이 없습니다.',
    degenerate_fit_geometry: 'FIT 점이 한 선에 몰려 기하가 퇴화했습니다.',
    ill_conditioned_geometry: '기하 조건이 불안정해 신뢰할 수 없습니다.',
    fit_points_not_distributed: 'FIT 점을 이미지와 피치에 더 넓게 분포시키세요.',
    homography_unavailable: '정합 행렬을 계산할 수 없습니다.',
    fit_point_outside_image: 'FIT 점이 소스 이미지 밖에 있습니다.',
    invalid_fit_projection: 'FIT 투영 결과를 계산할 수 없습니다.',
    fit_error_exceeds_threshold: 'FIT 오차가 허용 기준을 초과했습니다.',
    validation_outside_valid_region: 'VALIDATION 점이 유효 영역 밖에 있습니다.',
    validation_outside_image: 'VALIDATION 점이 소스 이미지 밖에 있습니다.',
    validation_not_independent: 'VALIDATION 점은 FIT에 사용하지 않은 점이어야 합니다.',
    invalid_validation_projection: 'VALIDATION 투영 결과를 계산할 수 없습니다.',
    validation_error_exceeds_threshold: 'VALIDATION 오차가 허용 기준을 초과했습니다.',
    sensitivity_unavailable: '민감도를 계산할 수 없습니다.',
    sensitivity_exceeds_threshold: '정합 민감도가 허용 기준을 초과했습니다.',
    propagated_results_require_independent_validation: '전파 결과는 독립적인 VALIDATION이 필요합니다.',
    invalid_image_size: '소스 이미지 크기가 올바르지 않습니다.',
  };

  function reasonLabel(reason) {
    const text = String(reason);
    return REASON_LABELS[text] || text.replace(/_/g, ' ');
  }

  function numericOrNull(value) {
    if (value === '' || value === null || value === undefined) return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }

  function clonePairs(points = []) {
    return points.map((point, index) => ({
      id: `pair-${Date.now()}-${index}-${Math.random().toString(16).slice(2)}`,
      image: [Number(point.image?.[0]), Number(point.image?.[1])],
      pitch: [Number(point.pitch?.[0]), Number(point.pitch?.[1])],
      role: point.role === 'validation' ? 'validation' : 'fit',
    }));
  }

  function clearSessionState() {
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.video = null;
    state.frames = [];
    state.currentFrameIndex = 0;
    state.sliderPreviewIndex = null;
    state.sourceImage = null;
    state.sourceObjectUrl = null;
    state.sourceSize = { width: 0, height: 0 };
    els.sourceWrap.style.aspectRatio = '16 / 9';
    state.pairs = [];
    state.pendingImage = null;
    state.pendingPitch = null;
    state.registrations = [];
    state.selectedRegistrationId = null;
    state.selectedResult = null;
    state.preview = null;
    state.field = { length: null, width: null, dimension_source: '', dimensions_verified: false };
    els.emptyState.hidden = false;
    els.analysisStage.hidden = true;
    ['sessionPanel', 'framePanel', 'exportPanel', 'fieldPanel', 'estimatePanel', 'resultPanel', 'propagationPanel', 'historyPanel'].forEach((id) => { els[id].hidden = true; });
    els.libraryList.replaceChildren();
    const empty = document.createElement('div');
    empty.className = 'subtle-empty';
    empty.textContent = '등록된 영상이 없습니다.';
    els.libraryList.append(empty);
    renderAll();
  }

  function resetRegistrationForFrame() {
    state.pairs = [];
    state.pendingImage = null;
    state.pendingPitch = null;
    state.selectedRegistrationId = null;
    state.selectedResult = null;
    state.preview = null;
    renderAll();
  }

  function markDraftDirty() {
    // A selected result is a saved snapshot. Once a point or field changes,
    // hide that snapshot so its quality values cannot describe this draft.
    state.selectedRegistrationId = null;
    state.selectedResult = null;
    state.preview = null;
  }

  function invalidateSourceImage() {
    state.imageSerial += 1;
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.sourceObjectUrl = null;
    state.sourceImage = null;
    state.sourceSize = { width: 0, height: 0 };
    els.sourceCanvasMessage.textContent = '실제 프레임 PNG를 불러오는 중입니다.';
    els.sourceCanvasMessage.hidden = false;
  }

  function populateVideo(video) {
    state.video = video;
    state.frames = [];
    state.currentFrameIndex = 0;
    state.sliderPreviewIndex = null;
    state.sourceImage = null;
    if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
    state.sourceObjectUrl = null;
    state.sourceSize = { width: 0, height: 0 };
    els.sourceWrap.style.aspectRatio = '16 / 9';
    state.pairs = [];
    state.pendingImage = null;
    state.pendingPitch = null;
    state.selectedRegistrationId = null;
    state.selectedResult = null;
    state.preview = null;
    state.registrations = [];
    state.field = { length: null, width: null, dimension_source: '', dimensions_verified: false };
    els.emptyState.hidden = true;
    els.analysisStage.hidden = false;
    ['sessionPanel', 'framePanel', 'exportPanel', 'fieldPanel', 'estimatePanel', 'resultPanel', 'propagationPanel', 'historyPanel'].forEach((id) => { els[id].hidden = false; });
    els.exportLink.href = `/api/videos/${encodeURIComponent(video.id)}/export`;
    renderAll();
  }

  async function refreshLibrary() {
    setBusy('library', true);
    try {
      const videos = await getJson('/api/videos');
      renderLibrary(Array.isArray(videos) ? videos : []);
      if (!state.video && (!videos || videos.length === 0)) {
        els.libraryList.replaceChildren();
        const empty = document.createElement('div');
        empty.className = 'subtle-empty';
        empty.textContent = '등록된 영상이 없습니다.';
        els.libraryList.append(empty);
      }
    } catch (error) {
      setApiState('offline', 'API 연결 실패');
      renderLibraryError(error.message);
    } finally {
      setBusy('library', false);
    }
  }

  function renderLibrary(videos) {
    els.libraryList.replaceChildren();
    if (!videos.length) {
      const empty = document.createElement('div');
      empty.className = 'subtle-empty';
      empty.textContent = '등록된 영상이 없습니다.';
      els.libraryList.append(empty);
      return;
    }
    const fragment = document.createDocumentFragment();
    videos.forEach((video) => {
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'library-item';
      if (state.video && String(state.video.id) === String(video.id)) item.classList.add('selected');
      const main = document.createElement('span');
      main.className = 'library-item-main';
      const strong = document.createElement('strong');
      strong.textContent = video.name || `video-${video.id}`;
      const small = document.createElement('small');
      const dimensions = video.width && video.height ? `${video.width}×${video.height}` : '해상도 확인 전';
      small.textContent = `${dimensions} · ${formatDuration(video.duration)}`;
      main.append(strong, small);
      const arrow = document.createElement('span');
      arrow.className = 'library-arrow';
      arrow.textContent = '›';
      item.append(main, arrow);
      item.addEventListener('click', () => loadVideo(video));
      fragment.append(item);
    });
    els.libraryList.append(fragment);
  }

  function renderLibraryCurrent() {
    if (!state.video) return;
    [...els.libraryList.querySelectorAll('.library-item')].forEach((item) => {
      const name = item.querySelector('strong')?.textContent;
      item.classList.toggle('selected', name === (state.video.name || `video-${state.video.id}`));
    });
  }

  function renderLibraryError(message) {
    els.libraryList.replaceChildren();
    const error = document.createElement('div');
    error.className = 'subtle-empty error-empty';
    error.textContent = `목록을 불러오지 못했습니다: ${message}`;
    els.libraryList.append(error);
  }

  async function importVideo(event) {
    event.preventDefault();
    const path = els.videoPath.value.trim();
    if (!path) {
      els.videoPath.focus();
      showAlert('영상 경로를 입력하세요.');
      return;
    }
    const serial = nextSerial();
    setBusy('import', true);
    hideAlert();
    // Reveal a real loading shell before the first await. This keeps the
    // loading overlay observable for slow ffprobe calls and prevents a race
    // where a hidden empty-state descendant is mistaken for a loaded frame.
    if (!state.video) {
      els.emptyState.hidden = true;
      els.analysisStage.hidden = false;
      els.sourceCanvasMessage.textContent = '영상 메타데이터와 실제 PTS 프레임을 확인하는 중입니다.';
      els.sourceCanvasMessage.hidden = false;
    }
    try {
      const response = await getJson('/api/videos', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
      if (serial !== state.requestSerial) return;
      populateVideo(response);
      renderLibraryFromCurrentAfterImport(response);
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
      setBusy('import', false);
    }
  }

  function renderLibraryFromCurrentAfterImport(video) {
    // Refreshing from the API is authoritative; this keeps the UI responsive if that call is slow.
    refreshLibrary();
    if (video) {
      const existing = [...els.libraryList.querySelectorAll('.library-item')].find((node) => node.querySelector('strong')?.textContent === video.name);
      if (existing) existing.classList.add('selected');
    }
  }

  async function loadVideo(video) {
    const serial = nextSerial();
    populateVideo(video);
    setBusy('frames', true);
    hideAlert();
    try {
      await loadFrames(video.id, serial);
      await loadRegistrations(video.id, serial);
    } catch (error) {
      if (serial === state.requestSerial) showAlert(`영상을 불러오지 못했습니다: ${error.message}`);
    } finally {
      if (serial === state.requestSerial) setBusy('frames', false);
    }
  }

  async function loadFrames(videoId, serial = state.requestSerial) {
    setBusy('frames', true);
    try {
      const frames = await getJson(`/api/videos/${encodeURIComponent(videoId)}/frames`);
      if (serial !== state.requestSerial || !state.video || String(state.video.id) !== String(videoId)) return;
      state.frames = Array.isArray(frames) ? frames : [];
      state.currentFrameIndex = 0;
      els.frameSlider.max = String(Math.max(0, state.frames.length - 1));
      els.frameSlider.value = '0';
      renderFrameReadout();
      renderLibraryCurrent();
      if (!state.frames.length) {
        els.sourceCanvasMessage.textContent = '이 영상에서 프레임을 찾지 못했습니다.';
        els.sourceCanvasMessage.hidden = false;
        return;
      }
      await loadFrameImage(0, serial);
    } finally {
      if (serial === state.requestSerial) setBusy('frames', false);
    }
  }

  async function loadRegistrations(videoId, serial = state.requestSerial) {
    const registrations = await getJson(`/api/videos/${encodeURIComponent(videoId)}/registrations`);
    if (serial !== state.requestSerial || !state.video || String(state.video.id) !== String(videoId)) return;
    state.registrations = Array.isArray(registrations) ? registrations : [];
    renderHistory();
  }

  async function loadFrameImage(index, serial = state.requestSerial, fromHistory = false) {
    if (!state.video || !state.frames[index]) return;
    const frameSerial = ++state.frameSerial;
    const imageSerial = ++state.imageSerial;
    setBusy('frame', true);
    els.sourceCanvasMessage.textContent = '실제 프레임 PNG를 불러오는 중입니다.';
    els.sourceCanvasMessage.hidden = false;
    try {
      const response = await apiFetch(`/api/videos/${encodeURIComponent(state.video.id)}/frame/${encodeURIComponent(index)}`);
      const blob = await response.blob();
      if (serial !== state.requestSerial || frameSerial !== state.frameSerial || imageSerial !== state.imageSerial) return;
      const objectUrl = URL.createObjectURL(blob);
      const image = new Image();
      image.decoding = 'async';
      image.onload = () => {
        if (serial !== state.requestSerial || frameSerial !== state.frameSerial || imageSerial !== state.imageSerial) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        if (state.sourceObjectUrl) URL.revokeObjectURL(state.sourceObjectUrl);
        state.sourceObjectUrl = objectUrl;
        state.sourceImage = image;
        state.sourceSize = { width: image.naturalWidth, height: image.naturalHeight };
        els.sourceWrap.style.aspectRatio = `${image.naturalWidth} / ${image.naturalHeight}`;
        state.currentFrameIndex = index;
        state.sliderPreviewIndex = null;
        els.frameSlider.value = String(index);
        els.sourceCanvasMessage.hidden = true;
        renderAll();
        setBusy('frame', false);
      };
      image.onerror = () => {
        URL.revokeObjectURL(objectUrl);
        if (serial === state.requestSerial && frameSerial === state.frameSerial && imageSerial === state.imageSerial) {
          showAlert('프레임 이미지를 해석하지 못했습니다.');
          setBusy('frame', false);
        }
      };
      image.src = objectUrl;
    } catch (error) {
      if (serial === state.requestSerial && frameSerial === state.frameSerial && imageSerial === state.imageSerial) {
        showAlert(`프레임을 불러오지 못했습니다: ${error.message}`);
        setBusy('frame', false);
      }
    }
  }

  async function selectFrame(index, { fromHistory = false } = {}) {
    const parsed = Math.max(0, Math.min(Number(index) || 0, state.frames.length - 1));
    if (!state.frames[parsed]) return;
    state.currentFrameIndex = parsed;
    state.sliderPreviewIndex = null;
    els.frameSlider.value = String(parsed);
    if (!fromHistory) resetRegistrationForFrame();
    invalidateSourceImage();
    renderAll();
    const serial = state.requestSerial;
    await loadFrameImage(parsed, serial, fromHistory);
  }

  function frameRecord() {
    return state.frames[state.currentFrameIndex] || null;
  }

  function renderFrameReadout(indexOverride = state.currentFrameIndex) {
    const frame = state.frames[indexOverride] || null;
    const index = frame ? (frame.index ?? state.currentFrameIndex) : '—';
    els.frameIndexReadout.textContent = Number.isFinite(Number(index)) ? String(index).padStart(4, '0') : '—';
    els.frameTotalReadout.textContent = state.frames.length ? String(state.frames.length).padStart(4, '0') : '—';
    els.frameTimeReadout.textContent = frame ? formatTime(frame.time_seconds) : '00:00:00.000';
    els.framePtsReadout.textContent = frame ? formatPts(frame.pts) : '—';
    const last = state.frames[state.frames.length - 1];
    els.sliderEndLabel.textContent = last ? formatTime(last.time_seconds, false) : '—';
  }

  function canvasPointFromEvent(canvas, event) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return null;
    return {
      x: (event.clientX - rect.left) * (canvas.width / rect.width),
      y: (event.clientY - rect.top) * (canvas.height / rect.height),
    };
  }

  function pitchCanvasMetrics() {
    const width = els.pitchCanvas.width;
    const height = els.pitchCanvas.height;
    const padX = Math.max(36, width * 0.075);
    const padY = Math.max(32, height * 0.09);
    return { width, height, padX, padY, innerWidth: width - padX * 2, innerHeight: height - padY * 2 };
  }

  function pitchToCanvas(point) {
    const m = pitchCanvasMetrics();
    return { x: m.padX + point[0] * m.innerWidth, y: m.padY + point[1] * m.innerHeight };
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

  function handleSourceClick(event) {
    if (!state.sourceImage || state.busy.has('frame')) return;
    const point = canvasPointFromEvent(els.sourceCanvas, event);
    if (!point) return;
    markDraftDirty();
    state.pendingImage = [point.x, point.y];
    state.pendingPitch = null;
    renderAll();
    setInstruction('피치 좌표 평면에서 대응점을 클릭하세요.');
  }

  function handlePitchClick(event) {
    const point = pitchFromEvent(event);
    if (!point) return;
    if (!state.pendingImage) {
      setInstruction('먼저 소스 프레임에서 이미지 지점을 클릭하세요.');
      return;
    }
    markDraftDirty();
    state.pairs.push({ id: `pair-${Date.now()}-${state.pairs.length}`, image: state.pendingImage, pitch: [point.x, point.y], role: state.mode });
    state.pendingImage = null;
    state.pendingPitch = null;
    state.preview = null;
    renderAll();
    setInstruction(state.mode === 'fit' ? 'FIT 점이 추가되었습니다. 다음 대응점을 지정하세요.' : 'VALIDATION 점이 추가되었습니다. 다음 대응점을 지정하세요.');
  }

  function setInstruction(message) {
    const target = els.instructionStrip.querySelector('div:nth-child(2) span');
    if (target) target.textContent = message;
  }

  function updateMode(mode) {
    state.mode = mode === 'validation' ? 'validation' : 'fit';
    els.modeButtons.forEach((button) => button.classList.toggle('active', button.dataset.mode === state.mode));
    setInstruction(state.mode === 'fit' ? '소스 프레임에서 FIT 대응점을 클릭하세요.' : '소스 프레임에서 VALIDATION 대응점을 클릭하세요.');
  }

  function makeNumberInput(value, { min, max, step, decimals, label, onChange }) {
    const input = document.createElement('input');
    input.type = 'number';
    input.value = Number.isFinite(value) ? Number(value).toFixed(decimals ?? 3) : '';
    input.step = step || '0.001';
    if (min !== undefined) input.min = String(min);
    if (max !== undefined) input.max = String(max);
    input.className = 'point-number';
    input.setAttribute('aria-label', label);
    input.addEventListener('input', () => { onChange(numericOrNull(input.value)); markDraftDirty(); });
    input.addEventListener('change', () => { onChange(numericOrNull(input.value)); markDraftDirty(); renderAll(); });
    return input;
  }

  function renderPointTable() {
    els.pointCount.textContent = String(state.pairs.length);
    els.pointTableBody.replaceChildren();
    if (!state.pairs.length) {
      const row = document.createElement('tr');
      row.className = 'table-empty';
      const cell = document.createElement('td');
      cell.colSpan = 7;
      cell.textContent = '캔버스에서 FIT 대응점을 4개 이상, 서로 떨어진 위치에 지정하세요.';
      row.append(cell);
      els.pointTableBody.append(row);
      return;
    }
    const fragment = document.createDocumentFragment();
    state.pairs.forEach((pair, index) => {
      const row = document.createElement('tr');
      row.className = pair.role === 'validation' ? 'validation-row' : 'fit-row';
      const number = document.createElement('td');
      number.className = 'point-index';
      number.textContent = String(index + 1).padStart(2, '0');
      const role = document.createElement('td');
      const select = document.createElement('select');
      select.className = `role-select ${pair.role}`;
      select.setAttribute('aria-label', `점 ${index + 1} 역할`);
      [['fit', 'FIT'], ['validation', 'VALIDATION']].forEach(([value, text]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = text;
        option.selected = pair.role === value;
        select.append(option);
      });
      select.addEventListener('change', () => { pair.role = select.value; markDraftDirty(); renderAll(); });
      role.append(select);
      const imageX = document.createElement('td');
      imageX.append(makeNumberInput(pair.image[0], { min: 0, max: state.sourceSize.width || undefined, step: '0.01', decimals: 2, label: `점 ${index + 1} 이미지 x`, onChange: (value) => { pair.image[0] = value; state.preview = null; } }));
      const imageY = document.createElement('td');
      imageY.append(makeNumberInput(pair.image[1], { min: 0, max: state.sourceSize.height || undefined, step: '0.01', decimals: 2, label: `점 ${index + 1} 이미지 y`, onChange: (value) => { pair.image[1] = value; state.preview = null; } }));
      const pitchX = document.createElement('td');
      pitchX.append(makeNumberInput(pair.pitch[0], { min: 0, max: 1, step: '0.0001', decimals: 4, label: `점 ${index + 1} 피치 x`, onChange: (value) => { pair.pitch[0] = value; state.preview = null; } }));
      const pitchY = document.createElement('td');
      pitchY.append(makeNumberInput(pair.pitch[1], { min: 0, max: 1, step: '0.0001', decimals: 4, label: `점 ${index + 1} 피치 y`, onChange: (value) => { pair.pitch[1] = value; state.preview = null; } }));
      const remove = document.createElement('td');
      const removeButton = document.createElement('button');
      removeButton.type = 'button';
      removeButton.className = 'remove-point';
      removeButton.setAttribute('aria-label', `점 ${index + 1} 삭제`);
      removeButton.textContent = '×';
      removeButton.addEventListener('click', () => { state.pairs.splice(index, 1); markDraftDirty(); renderAll(); });
      remove.append(removeButton);
      row.append(number, role, imageX, imageY, pitchX, pitchY, remove);
      fragment.append(row);
    });
    els.pointTableBody.append(fragment);
  }

  function isFinitePair(pair) {
    return pair && pair.image && pair.pitch && pair.image.length === 2 && pair.pitch.length === 2 && pair.image.every(Number.isFinite) && pair.pitch.every(Number.isFinite);
  }

  function localEstimate() {
    const fit = state.pairs.filter((pair) => pair.role === 'fit' && isFinitePair(pair));
    const validation = state.pairs.filter((pair) => pair.role === 'validation' && isFinitePair(pair));
    const reasons = [];
    if (fit.length < 4) reasons.push(`FIT 대응점 ${4 - fit.length}개가 더 필요합니다.`);
    if (validation.length < 1) reasons.push('독립적인 VALIDATION 대응점이 1개 이상 필요합니다.');
    if (state.sourceSize.width && state.sourceSize.height) {
      const outside = state.pairs.some((pair) => !isFinitePair(pair) || pair.image[0] < 0 || pair.image[0] > state.sourceSize.width || pair.image[1] < 0 || pair.image[1] > state.sourceSize.height || pair.pitch[0] < 0 || pair.pitch[0] > 1 || pair.pitch[1] < 0 || pair.pitch[1] > 1);
      if (outside) reasons.push('좌표가 허용 범위를 벗어났습니다.');
    }
    if (fit.length >= 4) {
      const unique = new Set(fit.map((pair) => `${pair.image[0].toFixed(3)}:${pair.image[1].toFixed(3)}`));
      if (unique.size < 4) reasons.push('FIT 이미지 좌표가 중복됩니다.');
      const spanX = Math.max(...fit.map((pair) => pair.pitch[0])) - Math.min(...fit.map((pair) => pair.pitch[0]));
      const spanY = Math.max(...fit.map((pair) => pair.pitch[1])) - Math.min(...fit.map((pair) => pair.pitch[1]));
      if (spanX < 0.05 || spanY < 0.05) reasons.push('FIT 점을 더 넓게 분포시키세요.');
    }
    return { fitCount: fit.length, validationCount: validation.length, reasons, ready: reasons.length === 0 };
  }

  function canSave() {
    // The server is the authority for geometry quality. Allow a partially
    // specified registration to be saved so review/unavailable diagnostics
    // remain visible and append-only history is useful during point cleanup.
    return Boolean(state.video && state.sourceImage && state.sliderPreviewIndex === null && !state.busy.has('frame') && state.pairs.length > 0 && state.pairs.every(isFinitePair));
  }

  function renderEstimate() {
    const estimate = state.preview || localEstimate();
    els.qualityLamp.dataset.status = estimate.ready ? 'ready' : 'review';
    els.estimateSummary.replaceChildren();
    const top = document.createElement('div');
    top.className = `estimate-top ${estimate.ready ? 'ready' : 'needs-review'}`;
    const icon = document.createElement('span');
    icon.className = 'estimate-icon';
    icon.textContent = estimate.ready ? '✓' : '!';
    const heading = document.createElement('strong');
    heading.textContent = estimate.ready ? '서버 계산 준비됨' : '검토가 필요합니다';
    top.append(icon, heading);
    const counts = document.createElement('span');
    counts.className = 'estimate-counts';
    counts.textContent = `FIT ${estimate.fitCount} · VALIDATION ${estimate.validationCount}`;
    top.append(counts);
    els.estimateSummary.append(top);
    const reasons = document.createElement('ul');
    reasons.className = 'reason-list compact-reasons';
    if (estimate.reasons.length) {
      estimate.reasons.forEach((reason) => { const li = document.createElement('li'); li.textContent = reasonLabel(reason); reasons.append(li); });
    } else {
      const li = document.createElement('li');
      li.className = 'positive-reason';
      li.textContent = '기본 입력 조건을 충족했습니다. 서버 geometry 결과를 저장하세요.';
      reasons.append(li);
    }
    els.estimateSummary.append(reasons);
    els.saveButton.disabled = state.busy.has('save') || !canSave();
  }

  function collectField() {
    const length = numericOrNull(els.fieldLength.value);
    const width = numericOrNull(els.fieldWidth.value);
    const source = els.dimensionSource.value.trim();
    return { length, width, dimension_source: source, dimensions_verified: Boolean(els.dimensionsVerified.checked) };
  }

  function renderField() {
    const field = state.field;
    els.fieldLength.value = field.length === null ? '' : String(field.length);
    els.fieldWidth.value = field.width === null ? '' : String(field.width);
    els.dimensionSource.value = field.dimension_source || '';
    els.dimensionsVerified.checked = Boolean(field.dimensions_verified);
    const hasNumbers = Number.isFinite(field.length) && Number.isFinite(field.width);
    if (field.dimensions_verified && (!hasNumbers || !field.dimension_source)) {
      els.dimensionHint.textContent = '확인됨으로 표시하려면 길이·너비와 출처를 모두 입력하세요.';
      els.dimensionHint.dataset.kind = 'warning';
    } else if (hasNumbers && !field.dimensions_verified) {
      els.dimensionHint.textContent = '값은 입력됐지만 확인됨 체크 전에는 metric으로 사용되지 않습니다.';
      els.dimensionHint.dataset.kind = 'warning';
    } else {
      els.dimensionHint.textContent = '숫자와 출처가 있어도 확인됨 체크 전에는 metric으로 사용되지 않습니다.';
      els.dimensionHint.dataset.kind = '';
    }
  }

  function renderSourceCanvas() {
    const canvas = els.sourceCanvas;
    const context = canvas.getContext('2d');
    if (!state.sourceImage || !state.sourceSize.width || !state.sourceSize.height) {
      canvas.width = 960;
      canvas.height = 540;
      context.clearRect(0, 0, canvas.width, canvas.height);
      els.sourceCanvasSize.textContent = '—';
      return;
    }
    canvas.width = state.sourceSize.width;
    canvas.height = state.sourceSize.height;
    context.clearRect(0, 0, canvas.width, canvas.height);
    context.drawImage(state.sourceImage, 0, 0, canvas.width, canvas.height);
    context.fillStyle = 'rgba(4, 12, 23, 0.08)';
    context.fillRect(0, 0, canvas.width, canvas.height);
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      drawSourcePoint(context, pair.image, pair.role, index + 1);
    });
    if (state.pendingImage) drawSourcePoint(context, state.pendingImage, state.mode, null, true);
    els.sourceCanvasSize.textContent = `${state.sourceSize.width} × ${state.sourceSize.height} px`;
  }

  function drawSourcePoint(context, point, role, number, pending = false) {
    const radius = Math.max(8, Math.min(context.canvas.width, context.canvas.height) * 0.012);
    const color = role === 'validation' ? '#f7b267' : '#55e3c1';
    context.save();
    context.strokeStyle = color;
    context.lineWidth = Math.max(2, radius * 0.2);
    context.shadowColor = 'rgba(0, 0, 0, 0.65)';
    context.shadowBlur = radius;
    context.beginPath();
    context.arc(point[0], point[1], radius, 0, Math.PI * 2);
    context.stroke();
    context.shadowBlur = 0;
    context.beginPath();
    context.moveTo(point[0] - radius * 1.8, point[1]);
    context.lineTo(point[0] + radius * 1.8, point[1]);
    context.moveTo(point[0], point[1] - radius * 1.8);
    context.lineTo(point[0], point[1] + radius * 1.8);
    context.stroke();
    if (number !== null && number !== undefined) {
      context.fillStyle = 'rgba(4, 12, 23, 0.86)';
      context.fillRect(point[0] + radius + 4, point[1] - radius - 4, Math.max(23, radius * 2.2), radius * 1.7);
      context.fillStyle = color;
      context.font = `600 ${Math.max(11, radius * 1.1)}px ui-sans-serif, sans-serif`;
      context.fillText(String(number).padStart(2, '0'), point[0] + radius + 8, point[1] + radius * 0.25);
    }
    if (pending) {
      context.setLineDash([radius * 0.55, radius * 0.4]);
      context.globalAlpha = 0.8;
      context.beginPath();
      context.arc(point[0], point[1], radius * 1.7, 0, Math.PI * 2);
      context.stroke();
    }
    context.restore();
  }

  function renderPitchCanvas() {
    const canvas = els.pitchCanvas;
    const context = canvas.getContext('2d');
    const width = Math.max(600, Math.floor(canvas.clientWidth * window.devicePixelRatio || 1));
    const height = Math.max(390, Math.floor(canvas.clientHeight * window.devicePixelRatio || 1));
    if (canvas.width !== width || canvas.height !== height) { canvas.width = width; canvas.height = height; }
    context.clearRect(0, 0, canvas.width, canvas.height);
    const m = pitchCanvasMetrics();
    context.fillStyle = '#0b2630';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.fillStyle = 'rgba(30, 184, 159, 0.06)';
    context.fillRect(m.padX, m.padY, m.innerWidth, m.innerHeight);
    context.strokeStyle = 'rgba(107, 227, 201, 0.48)';
    context.lineWidth = Math.max(1, canvas.width / 720);
    context.strokeRect(m.padX, m.padY, m.innerWidth, m.innerHeight);
    context.strokeStyle = 'rgba(107, 227, 201, 0.17)';
    context.lineWidth = Math.max(1, canvas.width / 1300);
    for (let i = 1; i < 10; i += 1) {
      const x = m.padX + m.innerWidth * i / 10;
      const y = m.padY + m.innerHeight * i / 10;
      context.beginPath(); context.moveTo(x, m.padY); context.lineTo(x, m.padY + m.innerHeight); context.stroke();
      context.beginPath(); context.moveTo(m.padX, y); context.lineTo(m.padX + m.innerWidth, y); context.stroke();
    }
    // Only the outer boundary and halfway line are assumed. Regulation
    // markings vary by sport, age group, and venue, so they stay absent.
    context.strokeStyle = 'rgba(107, 227, 201, 0.30)';
    context.beginPath(); context.moveTo(m.padX + m.innerWidth / 2, m.padY); context.lineTo(m.padX + m.innerWidth / 2, m.padY + m.innerHeight); context.stroke();
    const result = state.selectedResult;
    if (result && Array.isArray(result.valid_region) && result.valid_region.length >= 3) {
      context.fillStyle = 'rgba(85, 227, 193, 0.08)';
      context.strokeStyle = 'rgba(85, 227, 193, 0.48)';
      context.setLineDash([7, 7]);
      context.beginPath();
      result.valid_region.forEach((point, index) => { const p = pitchToCanvas(point); if (index === 0) context.moveTo(p.x, p.y); else context.lineTo(p.x, p.y); });
      context.closePath(); context.fill(); context.stroke(); context.setLineDash([]);
    }
    if (result && Array.isArray(result.projected_lines)) {
      context.strokeStyle = '#55e3c1';
      context.globalAlpha = 0.78;
      context.lineWidth = Math.max(2, canvas.width / 420);
      result.projected_lines.forEach((line) => {
        if (!Array.isArray(line) || line.length < 2) return;
        const a = pitchToCanvas(line[0]); const b = pitchToCanvas(line[1]);
        context.beginPath(); context.moveTo(a.x, a.y); context.lineTo(b.x, b.y); context.stroke();
      });
      context.globalAlpha = 1;
    }
    state.pairs.forEach((pair, index) => {
      if (!isFinitePair(pair)) return;
      const point = pitchToCanvas(pair.pitch);
      drawPitchPoint(context, point, pair.role, index + 1);
    });
    if (state.pendingPitch) drawPitchPoint(context, pitchToCanvas([state.pendingPitch.x, state.pendingPitch.y]), state.mode, null, true);
  }

  function drawPitchPoint(context, point, role, number, pending = false) {
    const radius = Math.max(7, context.canvas.width / 110);
    const color = role === 'validation' ? '#f7b267' : '#55e3c1';
    context.save();
    context.fillStyle = color;
    context.strokeStyle = '#06131f';
    context.lineWidth = Math.max(2, radius * 0.28);
    context.beginPath(); context.arc(point.x, point.y, radius, 0, Math.PI * 2); context.fill(); context.stroke();
    if (pending) { context.strokeStyle = color; context.setLineDash([4, 4]); context.beginPath(); context.arc(point.x, point.y, radius * 1.75, 0, Math.PI * 2); context.stroke(); }
    if (number !== null && number !== undefined) {
      context.fillStyle = '#05131f'; context.font = `700 ${Math.max(11, radius * 0.95)}px ui-sans-serif, sans-serif`; context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillText(String(number).padStart(2, '0'), point.x, point.y + 0.5);
    }
    context.restore();
  }

  function renderResult() {
    els.resultBody.replaceChildren();
    if (!state.selectedResult) {
      const empty = document.createElement('div'); empty.className = 'subtle-empty'; empty.textContent = '저장된 결과가 없습니다.'; els.resultBody.append(empty); els.resultSource.textContent = '—'; return;
    }
    const result = state.selectedResult;
    els.resultSource.textContent = result.source === 'propagated' ? 'PROPAGATED' : 'MANUAL';
    const status = document.createElement('div'); status.className = `result-status ${statusClass(result.status)}`;
    const statusMain = document.createElement('div'); statusMain.className = 'result-status-main';
    const dot = document.createElement('span'); dot.className = 'result-status-dot';
    const heading = document.createElement('strong'); heading.textContent = statusLabel(result.status);
    statusMain.append(dot, heading);
    const level = document.createElement('span'); level.className = 'coordinate-level'; level.textContent = coordinateLabel(result.coordinate_level);
    status.append(statusMain, level); els.resultBody.append(status);
    const metrics = document.createElement('div'); metrics.className = 'result-metrics';
    [['FIT 오차', result.fit_error_px, 'px'], ['검증 오차', result.validation_error_px, 'px'], ['민감도', result.sensitivity, '']].forEach(([label, value, suffix]) => {
      const metric = document.createElement('div'); metric.className = 'result-metric';
      const labelNode = document.createElement('span'); labelNode.textContent = label;
      const valueNode = document.createElement('strong'); valueNode.textContent = value === null || value === undefined ? '—' : `${Number(value).toFixed(2)}${suffix}`;
      metric.append(labelNode, valueNode); metrics.append(metric);
    });
    els.resultBody.append(metrics);
    const reasons = document.createElement('div'); reasons.className = 'result-reasons';
    const reasonTitle = document.createElement('span'); reasonTitle.className = 'reason-title'; reasonTitle.textContent = result.reasons?.length ? '실패·검토 사유' : '검토 사유'; reasons.append(reasonTitle);
    const list = document.createElement('ul'); list.className = 'reason-list';
    if (Array.isArray(result.reasons) && result.reasons.length) result.reasons.forEach((reason) => { const li = document.createElement('li'); li.textContent = reasonLabel(reason); list.append(li); });
    else { const li = document.createElement('li'); li.className = 'positive-reason'; li.textContent = '서버가 보고한 차단 사유가 없습니다.'; list.append(li); }
    reasons.append(list); els.resultBody.append(reasons);
  }

  function renderHistory() {
    const registrations = state.registrations || [];
    els.historyCount.textContent = String(registrations.length);
    els.qualityTimeline.replaceChildren();
    els.historyList.replaceChildren();
    if (!registrations.length) {
      const empty = document.createElement('div'); empty.className = 'subtle-empty'; empty.textContent = '저장된 등록이 없습니다.'; els.qualityTimeline.append(empty); return;
    }
    const timeline = document.createElement('div'); timeline.className = 'timeline-bars';
    const track = document.createElement('div'); track.className = 'timeline-track';
    const rail = document.createElement('div'); rail.className = 'timeline-rail'; track.append(rail);
    const frameCount = Math.max(state.frames.length, ...registrations.map((registration) => Number(registration.frame_index) + 1 || 0), 1);
    const denominator = Math.max(1, frameCount - 1);
    const timelineStart = Number(state.frames[0]?.time_seconds);
    const timelineEnd = Number(state.frames[state.frames.length - 1]?.time_seconds ?? state.video?.duration);
    const timelineSpan = Number.isFinite(timelineStart) && Number.isFinite(timelineEnd) && timelineEnd > timelineStart ? timelineEnd - timelineStart : null;
    const evaluatedFrames = new Set(registrations.map((registration) => String(registration.frame_index))).size;
    registrations.forEach((registration) => {
      const button = document.createElement('button'); button.type = 'button'; button.className = `timeline-bar ${statusClass(registration.status)}`;
      button.title = `프레임 ${registration.frame_index} · ${statusLabel(registration.status)}`;
      const frameIndex = Number(registration.frame_index);
      const registrationTime = Number(registration.time_seconds);
      const position = timelineSpan !== null && Number.isFinite(registrationTime)
        ? (registrationTime - timelineStart) / timelineSpan
        : (Number.isFinite(frameIndex) ? frameIndex / denominator : 0);
      button.style.left = `${Math.max(0, Math.min(100, position * 100))}%`;
      button.classList.toggle('selected', String(registration.id) === String(state.selectedRegistrationId));
      button.addEventListener('click', () => selectRegistration(registration));
      track.append(button);
    });
    timeline.append(track);
    const timelineMeta = document.createElement('div'); timelineMeta.className = 'timeline-meta';
    const coverage = document.createElement('span'); coverage.textContent = `관측 ${evaluatedFrames} / ${frameCount} frames`;
    const firstFrame = state.frames[0];
    const lastFrame = state.frames[state.frames.length - 1];
    const startTime = firstFrame?.time_seconds ?? 0;
    const endTime = lastFrame?.time_seconds ?? state.video?.duration ?? 0;
    const range = document.createElement('span'); range.textContent = `${formatTime(startTime, false)} → ${formatTime(endTime, false)}`;
    timelineMeta.append(coverage, range); timeline.append(timelineMeta);
    els.qualityTimeline.append(timeline);
    const fragment = document.createDocumentFragment();
    [...registrations].reverse().forEach((registration) => {
      const item = document.createElement('button'); item.type = 'button'; item.className = 'history-item'; item.classList.toggle('selected', String(registration.id) === String(state.selectedRegistrationId)); item.addEventListener('click', () => selectRegistration(registration));
      const marker = document.createElement('span'); marker.className = `history-marker ${statusClass(registration.status)}`;
      const body = document.createElement('span'); body.className = 'history-item-body';
      const line = document.createElement('span'); line.className = 'history-item-line';
      const title = document.createElement('strong'); title.textContent = `F${String(registration.frame_index ?? '—').padStart(4, '0')}`;
      const time = document.createElement('small'); time.textContent = formatTime(registration.time_seconds, false);
      line.append(title, time);
      const detail = document.createElement('span'); detail.className = 'history-item-detail'; detail.textContent = `${statusLabel(registration.status)} · ${registration.source === 'propagated' ? '전파' : '수동'}`;
      body.append(line, detail); const arrow = document.createElement('span'); arrow.className = 'history-arrow'; arrow.textContent = '›'; item.append(marker, body, arrow); fragment.append(item);
    });
    els.historyList.append(fragment);
  }

  async function selectRegistration(registration) {
    if (!registration) return;
    state.selectedRegistrationId = registration.id;
    state.selectedResult = registration;
    state.pairs = clonePairs(registration.points || []);
    state.field = {
      length: numericOrNull(registration.field?.length),
      width: numericOrNull(registration.field?.width),
      dimension_source: registration.field?.dimension_source || '',
      dimensions_verified: Boolean(registration.field?.dimensions_verified),
    };
    state.pendingImage = null;
    state.pendingPitch = null;
    state.preview = null;
    renderAll();
    if (Number.isFinite(Number(registration.frame_index)) && state.frames[registration.frame_index]) {
      els.propagationTarget.value = String(Math.min(Number(registration.frame_index) + 1, Math.max(0, state.frames.length - 1)));
      await selectFrame(Number(registration.frame_index), { fromHistory: true });
    }
  }

  function payloadForRegistration() {
    const frame = frameRecord();
    const field = collectField();
    state.field = field;
    return {
      frame_index: frame?.index ?? state.currentFrameIndex,
      field,
      points: state.pairs.map((pair) => ({ image: [pair.image[0], pair.image[1]], pitch: [pair.pitch[0], pair.pitch[1]], role: pair.role })),
      note: '',
    };
  }

  async function previewEstimate() {
    state.preview = localEstimate();
    renderEstimate();
    setInstruction(state.preview.ready ? '저장 조건을 확인했습니다. 등록 저장을 눌러 서버 geometry를 계산하세요.' : '표시된 사유를 해결한 뒤 다시 미리보기를 실행하세요.');
  }

  async function saveRegistration() {
    const estimate = localEstimate();
    state.preview = estimate;
    if (!canSave()) { renderAll(); showAlert('비어 있거나 숫자가 아닌 대응점은 저장할 수 없습니다.', 'warning'); return; }
    if (!state.video) return;
    const serial = nextSerial();
    setBusy('save', true); hideAlert();
    try {
      const response = await getJson(`/api/videos/${encodeURIComponent(state.video.id)}/registrations`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payloadForRegistration()) });
      if (serial !== state.requestSerial) return;
      state.selectedResult = response;
      state.selectedRegistrationId = response.id;
      state.pairs = clonePairs(response.points || state.pairs);
      state.preview = null;
      state.registrations = [...state.registrations, response];
      renderAll();
      showAlert(response.status === 'usable' ? '등록 결과를 저장했습니다.' : '결과를 저장했지만 검토가 필요합니다.', response.status === 'usable' ? 'success' : 'warning');
    } catch (error) {
      if (serial === state.requestSerial) showAlert(`등록 결과를 저장하지 못했습니다: ${error.message}`);
    } finally { setBusy('save', false); }
  }

  async function propagateRegistration() {
    if (!state.video || !state.selectedResult) return;
    const target = Number(els.propagationTarget.value);
    if (!Number.isInteger(target) || target < 0 || target >= state.frames.length) { showAlert('유효한 대상 프레임 인덱스를 입력하세요.'); return; }
    const serial = nextSerial(); setBusy('propagate', true); hideAlert();
    try {
      const response = await getJson(`/api/videos/${encodeURIComponent(state.video.id)}/propagate`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ registration_id: state.selectedResult.id, target_frame_index: target }) });
      if (serial !== state.requestSerial) return;
      state.registrations = [...state.registrations, response];
      await selectRegistration(response);
      showAlert(response.status === 'usable' ? '전파 결과를 저장했습니다. VALIDATION을 다시 확인하세요.' : '전파 결과가 저장됐지만 검토가 필요합니다.', 'warning');
    } catch (error) {
      if (serial === state.requestSerial) showAlert(`전파하지 못했습니다: ${error.message}`);
    } finally { setBusy('propagate', false); }
  }

  function renderVideoMeta() {
    const video = state.video;
    if (!video) return;
    els.sessionName.textContent = video.name || `video-${video.id}`;
    els.videoDimensions.textContent = video.width && video.height ? `${video.width} × ${video.height}` : '—';
    els.videoDuration.textContent = formatDuration(video.duration);
    els.videoFrameCount.textContent = state.frames.length ? `${state.frames.length.toLocaleString('ko-KR')} frames` : '—';
    els.videoTimeBase.textContent = video.time_base || '—';
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
    els.propagateButton.disabled = state.busy.has('propagate') || !state.selectedResult;
    if (state.selectedResult) {
      els.propagationHint.textContent = '전파 후 결과를 선택하고 새 VALIDATION 점으로 다시 확인하세요.';
      if (!els.propagationTarget.value) els.propagationTarget.value = String(Math.min(state.currentFrameIndex + 1, Math.max(0, state.frames.length - 1)));
    } else els.propagationHint.textContent = '저장된 등록 결과를 선택하면 사용할 수 있습니다.';
  }

  function bindEvents() {
    els.importForm.addEventListener('submit', importVideo);
    els.dismissAlert.addEventListener('click', hideAlert);
    els.refreshLibrary.addEventListener('click', refreshLibrary);
    els.sourceCanvas.addEventListener('click', handleSourceClick);
    els.pitchCanvas.addEventListener('click', handlePitchClick);
    els.pitchCanvas.addEventListener('mousemove', (event) => {
      if (!state.pendingImage) return;
      const point = pitchFromEvent(event);
      if (!point) return;
      state.pendingPitch = point;
      renderPitchCanvas();
    });
    els.pitchCanvas.addEventListener('mouseleave', () => { if (state.pendingPitch) { state.pendingPitch = null; renderPitchCanvas(); } });
    els.modeButtons.forEach((button) => button.addEventListener('click', () => updateMode(button.dataset.mode)));
    els.cancelPending.addEventListener('click', () => { state.pendingImage = null; state.pendingPitch = null; renderAll(); setInstruction('선택이 취소되었습니다. 소스 프레임에서 다시 시작하세요.'); });
    els.clearPoints.addEventListener('click', () => { state.pairs = []; state.pendingImage = null; state.pendingPitch = null; state.selectedRegistrationId = null; state.selectedResult = null; state.preview = null; renderAll(); setInstruction('대응점이 초기화되었습니다. 소스 프레임에서 FIT 대응점을 클릭하세요.'); });
    els.frameSlider.addEventListener('input', () => {
      const index = Number(els.frameSlider.value);
      state.sliderPreviewIndex = index;
      renderFrameReadout(index);
      renderEstimate();
    });
    els.frameSlider.addEventListener('change', () => selectFrame(Number(els.frameSlider.value)));
    els.previousFrame.addEventListener('click', () => selectFrame(state.currentFrameIndex - 1));
    els.nextFrame.addEventListener('click', () => selectFrame(state.currentFrameIndex + 1));
    [els.fieldLength, els.fieldWidth, els.dimensionSource, els.dimensionsVerified].forEach((input) => input.addEventListener('input', () => { state.field = collectField(); markDraftDirty(); renderField(); renderEstimate(); renderResult(); renderHistory(); }));
    els.estimateButton.addEventListener('click', previewEstimate);
    els.saveButton.addEventListener('click', saveRegistration);
    els.propagateButton.addEventListener('click', propagateRegistration);
    window.addEventListener('resize', () => { renderSourceCanvas(); renderPitchCanvas(); });
  }

  async function init() {
    cacheElements();
    bindEvents();
    clearSessionState();
    setApiState('loading', '연결 확인 중');
    await refreshLibrary();
  }

  window.addEventListener('DOMContentLoaded', init);
})();
