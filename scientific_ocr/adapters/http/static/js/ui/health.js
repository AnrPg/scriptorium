import { api } from '../core/api.js';
import { h } from '../core/dom.js';
import { dialogOpen, modal } from './feedback.js';

// ---------------------------------------------------------------- health check reports (application/health.py)
// The server stops processes that can never finish (a job stuck after its work was done, a job orphaned by an app
// that stopped, a leftover engine server): when the app starts, every 2 hours, and when the app is opened here.
// What it stopped is shown once, in a dialog, until «Εντάξει» marks it seen.
const WHY = {
  finished_hung: 'Η δουλειά είχε ολοκληρωθεί, αλλά η διεργασία κόλλησε στο κλείσιμο',
  orphan: 'Η εφαρμογή που την ξεκίνησε είχε σταματήσει· το αποτέλεσμά της δεν θα καταγραφόταν ποτέ',
  engine_server: 'Βοηθητικός server μηχανής που έμεινε από διακοπείσα επεξεργασία',
};
const TODO = {
  finished_hung: 'Τίποτα να ξανακάνεις· η εργασία καταγράφηκε όπως τελείωσε.',
  orphan: 'Ξανατρέξε την εργασία (π.χ. «Ενημέρωση βιβλίου» ή την επεξεργασία των σελίδων της).',
  engine_server: 'Τίποτα να κάνεις.',
};
const TRIGGER = { start: 'στην εκκίνηση του server', periodic: 'στον περιοδικό έλεγχο (κάθε 2 ώρες)', browser: 'στο άνοιγμα της εφαρμογής' };
const CMD = { 'reproject-pages': 'ενημέρωση σελίδων', reproject: 'ενημέρωση βιβλίου', 'ocr-regions': 'OCR περιοχών', process: 'επεξεργασία σελίδων',
  variants: 'εκδόσεις προφίλ', render: 'απόδοση εξόδων' };

const dur = (s) => s >= 86400 ? `${Math.floor(s / 86400)} μ. ${Math.floor(s % 86400 / 3600)} ώ.` : s >= 3600 ? `${Math.floor(s / 3600)} ώ. ${Math.floor(s % 3600 / 60)} λ.` : `${Math.max(1, Math.round(s / 60))} λ.`;
let shown = new Set();

function item(x) {
  return h('li', { class: 'health-item' },
    h('div', {}, h('b', {}, x.reason === 'engine_server' ? `Server μηχανής (pid ${x.pid})` : `${x.label || CMD[x.command] || x.command}`),
      x.book ? h('span', { class: 'mut' }, ` · ${x.book}`) : null),
    h('div', { class: 'small' }, WHY[x.reason] || x.reason,
      x.age_s != null ? h('span', { class: 'mut' }, ` · έτρεχε ${dur(x.age_s)}${x.cpu ? `, ${Math.round(x.cpu)}% CPU` : ''} · pid ${x.pid}`) : null,
      x.stopped === false ? h('b', { class: 'bad' }, ' · δεν σταμάτησε!') : null),
    x.last_log ? h('div', { class: 'small mut health-log', title: 'Τελευταία γραμμή του log της' }, x.last_log) : null,
    h('div', { class: 'small' }, TODO[x.reason] || ''));
}

function show(reports) {
  const fresh = reports.filter(r => !shown.has(r.id));
  if (!fresh.length || dialogOpen()) return;                  // never on top of another dialog: next poll
  fresh.forEach(r => shown.add(r.id));
  const items = fresh.flatMap(r => r.stopped.map(x => ({ ...x, r })));
  modal(`Έλεγχος υγείας: σταμάτησ${items.length === 1 ? 'ε 1 κολλημένη διεργασία' : `αν ${items.length} κολλημένες διεργασίες`}`,
    h('div', { class: 'health-report' },
      fresh.map(r => h('div', {},
        h('div', { class: 'small mut' }, `${new Date(r.ts * 1000).toLocaleString('el-GR')} · ${TRIGGER[r.trigger] || r.trigger}`),
        h('ul', { class: 'health-list' }, r.stopped.map(item))))),
    [{ label: 'Εντάξει', cls: 'primary', run: () => api('/api/health/seen', { body: { ids: fresh.map(r => r.id) } }).catch(() => {}) }]);
}

/** On opening the app: run the check now and show what it (or an earlier run) stopped. */
export async function healthOnOpen() {
  try { show((await api('/api/health/check', { body: {} })).reports || []); } catch { /* server restarting */ }
}
/** From the poll loop (about once a minute): what the periodic check stopped meanwhile. */
let last = 0;
export async function healthPoll() {
  if (Date.now() - last < 60000) return;
  last = Date.now();
  try { show((await api('/api/health')).reports || []); } catch { /* server restarting */ }
}
