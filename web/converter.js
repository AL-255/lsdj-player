// SPDX-License-Identifier: GPL-2.0-or-later
export {inspectSave} from './save-format.js';

export function generatePlayer({onProgress = () => {}, signal, ...options}) {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL('./worker.js', import.meta.url), {type: 'module'});
    const finish = () => { worker.terminate(); signal?.removeEventListener('abort', cancel); };
    const cancel = () => { finish(); reject(new Error('Conversion cancelled.')); };
    if (signal?.aborted) { cancel(); return; }
    signal?.addEventListener('abort', cancel, {once: true});
    worker.onmessage = ({data}) => {
      if (data.type === 'progress') onProgress(data.value);
      else if (data.type === 'done') { finish(); resolve(new Uint8Array(data.buffer)); }
      else if (data.type === 'error') { finish(); reject(new Error(data.message)); }
    };
    worker.onerror = () => { finish(); reject(new Error('The converter could not load. Refresh the page and try again.')); };
    // Structured cloning retains the caller’s selected files for retrying.
    worker.postMessage(options);
  });
}
