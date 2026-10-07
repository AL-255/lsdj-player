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
  for (const kind of ['rom', 'save']) $(kind + '-file').disabled = state.busy;
  projectSelect.disabled = state.busy || !state.projects.length || state.reading.save;
  titleInput.disabled = state.busy;
  $('low-range').disabled = state.busy;
  $('connect-bends').disabled = state.busy;
  form.setAttribute('aria-busy', String(state.busy));
  generateButton.textContent = state.busy ? 'Generating…' : 'Generate ROM';
  let hint = 'Choose a ROM and save to continue.';
  if (state.busy) hint = 'Keep this tab open.';
  else if (reading) hint = 'Reading your files…';
  else if (ready) hint = '';
  else if (state.rom) hint = 'Choose your save to continue.';
  else if (state.save) hint = 'Choose your LSDj ROM to continue.';
  $('ready-hint').textContent = hint;
}

function setFileError(kind, error = '') {
  $(kind + '-file').setAttribute('aria-invalid', String(Boolean(error)));
  $(kind + '-error').textContent = error;
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
}

async function loadFile(kind, file) {
  if (state.busy) return;
  const version = ++state.version[kind];
  state[kind] = null;
  state.reading[kind] = true;
  if (kind === 'save') {
    state.projects = [];
    projectSelect.replaceChildren(new Option('Reading your save…', ''));
    $('project-hint').textContent = '';
  }
  clearOutput();
  clearBuildError();
  setFileError(kind);
  refreshAvailability();
  try {
    if (!file) {
      if (kind === 'save') projectSelect.replaceChildren(new Option('Choose a save first', ''));
      return;
    }
    if (kind === 'save' && ![65536, 131072].includes(file.size)) {
      throw new Error('Choose a 64 or 128 KiB LSDj .sav file.');
    }
    if (kind === 'rom' && file.size !== 1048576) {
      throw new Error('Choose a 1 MiB LSDj 9.4.2 ROM.');
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
  } catch (error) {
    if (version !== state.version[kind]) return;
    setFileError(kind, error instanceof Error ? error.message : String(error));
    if (kind === 'save') {
      projectSelect.replaceChildren(new Option('Choose a save first', ''));
      $('project-hint').textContent = '';
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
  input.addEventListener('change', () => { void loadFile(kind, input.files[0]); });
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
  titleInput.value = titleInput.value.toUpperCase().slice(0, 8);
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

updateTitle();
refreshAvailability();
