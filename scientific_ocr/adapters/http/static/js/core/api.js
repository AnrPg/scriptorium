// ------------------------------------------------------------------ API client
export const actor = () => localStorage.getItem('ocr.actor') || 'reviewer';
export async function api(path, opts = {}) {
  const init = { method: opts.method || (opts.body !== undefined ? 'POST' : 'GET'), headers: { 'X-OCR-Client': '1', 'X-OCR-Actor': actor() } };
  if (opts.body !== undefined) {
    if (opts.raw) { init.body = opts.body; } else { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(opts.body); }
  }
  const r = await fetch(path, init);
  const ct = r.headers.get('Content-Type') || '';
  const data = ct.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error((data && data.error) || r.statusText);
  return data;
}
export const B = id => '/api/books/' + encodeURIComponent(id);
