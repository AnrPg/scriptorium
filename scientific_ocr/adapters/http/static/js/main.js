/* Scientific OCR — local review app (no frameworks, no remote assets, no build step).
   Entry point: imports the modules and runs the load-time wiring in its original order. */
import { doRedo, doUndo, poll, renderJobsPop } from './core/chrome.js';
import { boundResync } from './inspector/boundloop.js';
import { $, installOverflowTitles } from './core/dom.js';
import { EMBED } from './ui/page_overlay.js';     // embedded page mode (?embed=1) before the first render
import { onKeydown, showHelp } from './core/keyboard.js';
import { navStep, render } from './core/nav.js';
import { installDropZone } from './library/upload.js';
import './core/api.js';
import './ui/feedback.js';
import './ui/math.js';
import './core/strings.js';
import './core/state.js';
import './library/library.js';
import './book/book.js';
import './export/wizard.js';
import './book/quality.js';
import './book/structure.js';
import './workspace/workspace.js';
import './inspector/inspector.js';
import './inspector/segment.js';
import './inspector/multi.js';
import './workspace/output.js';
import './workspace/hover.js';
import './inspector/suggestions.js';
import './inspector/captions.js';
import './inspector/history.js';
import './inspector/note.js';
import './inspector/lineage.js';
import './inspector/page.js';

window.addEventListener('hashchange', render);

$('#btn-back').onclick = () => navStep(-1);
$('#btn-fwd').onclick = () => navStep(1);

$('#btn-undo').onclick = () => doUndo().then(() => boundResync()); $('#btn-redo').onclick = () => doRedo().then(() => boundResync());

$('#jobs-pill').onclick = () => { const p = $('#jobs-pop'); p.hidden = !p.hidden; if (!p.hidden) renderJobsPop(); };
document.addEventListener('keydown', onKeydown);
$('#btn-help').onclick = showHelp;
document.addEventListener('click', (e) => { const p = $('#jobs-pop'); if (!p.hidden && !p.contains(e.target) && e.target !== $('#jobs-pill') && !$('#jobs-pill').contains(e.target)) p.hidden = true; });

installDropZone(); render();
if (!EMBED) poll();                              // the overlay's parent already polls; the iframe polls only after its own edits (kickPoll)
installOverflowTitles();                         // full text of anything cut with an ellipsis, on hover
