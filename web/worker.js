// SPDX-License-Identifier: GPL-2.0-or-later
import createLsdjEmulator from './generated/emulator.mjs';
import {convertPlayer} from './player-core.js';

async function getJSON(path) {
  const response = await fetch(new URL(path, import.meta.url));
  if (!response.ok) throw new Error('Could not load the converter assets. Refresh and retry.');
  return response.json();
}
self.onmessage = async ({data}) => {
  try {
    self.postMessage({type: 'progress', value: {progress: .01, message: 'Starting the local converter…'}});
    const [module, templates, compatibility] = await Promise.all([
      createLsdjEmulator(), getJSON('./generated/templates.json'), getJSON('./compatibility.json')]);
    const result = await convertPlayer({...data, onProgress: value => self.postMessage({type: 'progress', value})},
                                       {module, templates, compatibility});
    self.postMessage({type: 'done', buffer: result.buffer}, [result.buffer]);
  } catch (error) { self.postMessage({type: 'error', message: error.message || 'Conversion failed.'}); }
};
