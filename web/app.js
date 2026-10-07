import { inspectSave, generatePlayer } from './converter.js';

const $ = (id) => document.getElementById(id);
const form = $('export-form');
const titleInput = $('rom-title');
const projectSelect = $('project');
const generateButton = $('generate');
const state = {
  rom: null, save: null, projects: [], busy: false, outputUrl: null,
  reading: { rom: false, save: false }, version: { rom: 0, save: 0 }, titleEdited: false,
};
const fileSize = (bytes) => `${Number((bytes / 1024).toFixed(1))} KiB`;
const cleanName = (value) => String(value ?? '').replace(/\0/g, '').trim();
const defaultTitle = (value) => cleanName(value).toUpperCase().replace(/[^A-Z0-9 .+?\-]/g, '').slice(0, 8) || 'MYSONG';

function clearOutput() {
  if (state.outputUrl) URL.revokeObjectURL(state.outputUrl);
  state.outputUrl = null;
  $('download').removeAttribute('href');
  $('download-panel').hidden = true;
  generateButton.hidden = false;
  $('ready-hint').hidden = false;
}

function clearBuildError() {
  $('build-error').hidden = true;
  $('build-error').textContent = '';
}

function refreshAvailability() {
  const reading = state.reading.rom || state.reading.save;
  const ready = Boolean(state.rom && state.save && state.projects.length && projectSelect.value);
  generateButton.disabled = state.busy || reading || !ready;
  for (const kind of ['rom', 'save']) $(kind + '-picker').disabled = state.busy;
  projectSelect.disabled = state.busy || !state.projects.length || state.reading.save;
  titleInput.disabled = state.busy;
  $('low-range').disabled = state.busy;
  $('connect-bends').disabled = state.busy;
  form.setAttribute('aria-busy', String(state.busy));
  $('generate-label').textContent = state.busy ? 'Generating…' : 'Generate player';
  let hint = 'Choose both files to get started.';
  if (state.busy) hint = 'Keep this tab open while your player is being built.';
  else if (reading) hint = 'Reading your files…';
  else if (ready) hint = 'One ROM, ready for DMG and CGB.';
  else if (state.rom) hint = 'Choose your save to continue.';
  else if (state.save) hint = 'Choose your LSDj ROM to continue.';
  $('ready-hint').textContent = hint;
}

function setFileState(kind, { name, error, loading = false }) {
  const group = document.querySelector(`[data-kind="${kind}"]`);
  group.classList.toggle('loaded', Boolean(name) && !error && !loading);
  group.classList.toggle('invalid', Boolean(error));
  const picker = $(kind + '-picker');
  picker.setAttribute('aria-invalid', String(Boolean(error)));
  picker.querySelector('.upload-action').textContent = error ? '!' : loading ? '…' : name ? '✓' : '+';
  $(kind + '-file-name').textContent = loading ? 'Reading…' : name || (kind === 'rom' ? 'Choose a .gb file' : 'Choose a .sav file');
  $(kind + '-error').textContent = error || '';
  $(kind + '-error').hidden = !error;
}

function updateProjectDetails() {
  const project = state.projects.find((entry) => (entry.working ? 'working' : String(entry.index)) === projectSelect.value);
  if (project) {
    const details = [];
    if (Number.isFinite(project.song_rows)) details.push(`${project.song_rows} song ${project.song_rows === 1 ? 'row' : 'rows'}`);
    if (Number.isFinite(project.note_count)) details.push(`${project.note_count} ${project.note_count === 1 ? 'note' : 'notes'}`);
    $('project-hint').textContent = details.join(' · ') || (project.working ? 'Current project in working memory.' : 'Saved project.');
    if (!state.titleEdited) titleInput.value = defaultTitle(project.name);
  }
  updateTitle();
}

function updateTitle() {
  const text = titleInput.value;
  titleInput.setCustomValidity(/^[A-Z0-9 .+?\-]*$/i.test(text) ? '' : 'Use letters, numbers, spaces, or . + - ? in the title.');
  $('title-count').textContent = `${text.length} / 8`;
  $('preview-song').textContent = text.trim().toUpperCase() || 'YOURSONG';
}

async function loadFile(kind, file) {
  if (!file || state.busy) return;
  const version = ++state.version[kind];
  state[kind] = null;
  state.reading[kind] = true;
  if (kind === 'save') {
    state.projects = [];
    projectSelect.replaceChildren(new Option('Reading your save…', ''));
    $('project-hint').textContent = 'Checking working memory and saved projects.';
  }
  clearOutput();
  clearBuildError();
  setFileState(kind, { loading: true });
  refreshAvailability();
  try {
    if (kind === 'save' && ![65536, 131072].includes(file.size)) {
      throw new Error('Choose a 64 or 128 KiB LSDj .sav file.');
    }
    if (kind === 'rom' && (file.size < 131072 || file.size > 8388608 || file.size % 16384 !== 0)) {
      throw new Error('This does not look like a Game Boy ROM. Choose your LSDj 9.4.2 .gb file.');
    }
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (version !== state.version[kind]) return;
    if (kind === 'save') {
      const projects = await inspectSave(bytes);
      if (version !== state.version[kind]) return;
      if (!Array.isArray(projects) || !projects.length) throw new Error('No readable projects were found in this save.');
      state.projects = projects;
      const options = projects.map((project) => {
        const name = cleanName(project.name) || 'Untitled';
        return new Option(project.working ? `Working memory · ${name}` : name,
                          project.working ? 'working' : String(project.index));
      });
      projectSelect.replaceChildren(...options);
      const working = projects.find((project) => project.working);
      if (working) projectSelect.value = 'working';
      updateProjectDetails();
    }
    state[kind] = bytes;
    setFileState(kind, { name: `${file.name} · ${fileSize(file.size)}` });
  } catch (error) {
    if (version !== state.version[kind]) return;
    setFileState(kind, { error: error instanceof Error ? error.message : String(error) });
    if (kind === 'save') {
      projectSelect.replaceChildren(new Option('Load a save to choose', ''));
      $('project-hint').textContent = 'Working memory and saved projects will appear here.';
    }
  } finally {
    if (version === state.version[kind]) {
      state.reading[kind] = false;
      refreshAvailability();
    }
  }
}

for (const kind of ['rom', 'save']) {
  const input = $(kind + '-file');
  const picker = $(kind + '-picker');
  const group = document.querySelector(`[data-kind="${kind}"]`);
  picker.addEventListener('click', () => input.click());
  input.addEventListener('change', () => {
    void loadFile(kind, input.files[0]);
    input.value = '';
  });
  group.addEventListener('dragover', (event) => {
    event.preventDefault();
    if (state.busy) return;
    event.dataTransfer.dropEffect = 'copy';
    group.classList.add('dragging');
  });
  group.addEventListener('dragleave', (event) => {
    if (!group.contains(event.relatedTarget)) group.classList.remove('dragging');
  });
  group.addEventListener('drop', (event) => {
    event.preventDefault();
    group.classList.remove('dragging');
    if (state.busy) return;
    const files = event.dataTransfer.files;
    if (files.length !== 1) {
      setFileState(kind, { error: 'Drop one file into each box.' });
      return;
    }
    void loadFile(kind, files[0]);
  });
}

projectSelect.addEventListener('change', () => {
  clearOutput();
  clearBuildError();
  updateProjectDetails();
  refreshAvailability();
});
titleInput.addEventListener('input', () => {
  state.titleEdited = Boolean(titleInput.value.trim());
  const start = titleInput.selectionStart;
  const end = titleInput.selectionEnd;
  titleInput.value = titleInput.value.toUpperCase();
  titleInput.setSelectionRange(start, end);
  clearOutput();
  clearBuildError();
  updateTitle();
  refreshAvailability();
});
for (const id of ['low-range', 'connect-bends']) {
  $(id).addEventListener('change', () => {
    clearOutput();
    clearBuildError();
    drawPreview();
    refreshAvailability();
  });
}

function showProgress({ progress, message } = {}) {
  $('build-progress').hidden = false;
  $('progress-message').textContent = message || 'Building your player…';
  if (Number.isFinite(progress)) {
    const percent = Math.max(0, Math.min(100, Math.round(progress * 100)));
    $('progress-bar').value = percent;
    $('progress-percent').textContent = `${percent}%`;
  } else {
    $('progress-bar').removeAttribute('value');
    $('progress-percent').textContent = '';
  }
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.busy || generateButton.disabled) return;
  updateTitle();
  if (!titleInput.reportValidity()) return;
  clearOutput();
  clearBuildError();
  state.busy = true;
  refreshAvailability();
  showProgress({ progress: 0, message: 'Preparing your files…' });
  const title = titleInput.value.trim().toUpperCase() || 'MYSONG';
  try {
    const output = await generatePlayer({
      rom: state.rom.slice(),
      save: state.save.slice(),
      project: projectSelect.value === 'working' ? 'working' : Number(projectSelect.value),
      title,
      lowRange: $('low-range').checked,
      connectBends: $('connect-bends').checked,
      onProgress: showProgress,
    });
    if (!(output instanceof Uint8Array) || output.length === 0) throw new Error('The player could not be generated. Please try again.');
    state.outputUrl = URL.createObjectURL(new Blob([output], { type: 'application/octet-stream' }));
    const filename = `${title.replace(/[^A-Z0-9_-]/g, '_') || 'MYSONG'}-player.gb`;
    $('download').href = state.outputUrl;
    $('download').download = filename;
    $('download-description').textContent = `${filename} · ${fileSize(output.length)} · DMG + CGB`;
    $('download-panel').hidden = false;
    generateButton.hidden = true;
    $('ready-hint').hidden = true;
    $('download').focus();
  } catch (error) {
    $('build-error').textContent = error instanceof Error ? error.message : String(error);
    $('build-error').hidden = false;
  } finally {
    state.busy = false;
    $('build-progress').hidden = true;
    refreshAvailability();
  }
});
$('build-again').addEventListener('click', () => {
  clearOutput();
  refreshAvailability();
  generateButton.focus();
});
window.addEventListener('pagehide', () => {
  if (state.outputUrl) URL.revokeObjectURL(state.outputUrl);
});

function drawPreview() {
  const low = $('low-range').checked;
  const bends = $('connect-bends').checked;
  const svgNamespace = 'http://www.w3.org/2000/svg';
  const element = (name, attributes) => {
    const node = document.createElementNS(svgNamespace, name);
    for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
    return node;
  };
  const grid = $('preview-grid');
  grid.replaceChildren();
  const step = low ? 24 : 12;
  for (let y = 0; y < 288; y += step) grid.append(element('path', { d: `M0 ${y + .5}H144` }));
  for (let x = 0; x < 144; x += 24) grid.append(element('path', { d: `M${x + .5} 0V288`, opacity: '.4' }));
  const keys = $('preview-keys');
  keys.replaceChildren();
  for (let y = 0; y < 288; y += (low ? 4 : 2)) {
    const pitch = (11 - Math.floor(y / (low ? 4 : 2)) % 12 + 12) % 12;
    const black = [1, 3, 6, 8, 10].includes(pitch);
    keys.append(element('rect', { x: 145, y, width: 14, height: low ? 4 : 2, fill: '#b9a9c9' }));
    if (black) keys.append(element('rect', { x: 145, y, width: 9, height: low ? 4 : 2, fill: '#211a2d' }));
  }
  const channels = [
    { color: '#75dee5', segments: [[0, 72, 18], [21, 72, 11], [34, 56, 15], [51, 48, 19], [72, 56, 11], [85, 76, 16], [103, 68, 13], [118, 56, 27]] },
    { color: '#f290c7', segments: [[0, 127, 26], [29, 119, 13], [44, 107, 21], [68, 119, 18], [88, 135, 15], [106, 119, 16], [124, 111, 21]] },
    { color: '#a7d888', segments: [[0, 222, 22], [25, 222, 13], [40, 198, 18], [60, 198, 16], [78, 214, 20], [100, 214, 16], [118, 190, 27]] },
  ];
  const notes = $('preview-notes');
  notes.replaceChildren();
  for (const { color, segments } of channels) {
    const positions = segments.map(([x, y, width]) => [x, low ? y : 50 + Math.round(y * .65), width]);
    let previous = null;
    for (const [x, y, width] of positions) {
      notes.append(element('path', { d: `M${x} ${y}h${width}`, stroke: color, 'stroke-width': low ? 3 : 2 }));
      if (bends && previous && Math.abs(previous[1] - y) > 0) {
        notes.append(element('path', { d: `M${x} ${previous[1]}V${y}`, stroke: color, opacity: '.9', 'stroke-width': 2 }));
      }
      previous = [x, y];
    }
  }
  if (!low) for (let x = 1; x < 144; x += 7) notes.append(element('path', { d: `M${x} ${242 + (x % 3) * 6}h2`, stroke: '#e9cb79' }));
  $('noise-legend').hidden = low;
}

updateTitle();
drawPreview();
refreshAvailability();
