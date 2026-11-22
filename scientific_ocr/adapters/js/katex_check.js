// Checks LaTeX with the SAME KaTeX + mhchem build the app and the PDF export use (vendored, offline).
// stdin: [{id, latex, display}]  ->  stdout: {id: error message} for the ones that do not render.
const path = require('path'), Module = require('module');
const dir = process.argv[2];
const orig = Module._resolveFilename;
Module._resolveFilename = function (req, ...a) { return req === 'katex' ? path.join(dir, 'katex.min.js') : orig.call(this, req, ...a); };
const katex = require(path.join(dir, 'katex.min.js'));
require(path.join(dir, 'contrib', 'mhchem.min.js'));
let buf = '';
process.stdin.on('data', d => { buf += d; });
process.stdin.on('end', () => {
  const out = {};
  for (const it of JSON.parse(buf || '[]')) {
    try { katex.renderToString(it.latex, { displayMode: !!it.display, throwOnError: true, strict: 'ignore' }); }
    catch (e) { out[it.id] = String(e.message || e).replace(/^KaTeX parse error: /, '').slice(0, 300); }
  }
  process.stdout.write(JSON.stringify(out));
});
