import { api } from '../core/api.js';
import { h } from '../core/dom.js';
import { R, S } from '../core/state.js';
import { RELEASE_EL, STATE_EL } from '../core/strings.js';
import { openDialog } from './feedback.js';
import { describePending } from './rebuild.js';

// ------------------------------------------------------------------ plain-language job summary
// The pipeline writes FACT lines in its log (core/joblog.py); this turns them into a short Greek report. The raw
// log stays one click away (folded) for debugging.

const STAGE_EL = { load: 'Φόρτωση', 'compose pages': 'Σύνθεση σελίδων', 'specialised models (cache)': 'Ειδικά μοντέλα',
  'reviewer decisions': 'Οι αποφάσεις σου', 'inputs of the structure (calibration, model votes, lexicon)': 'Λεξικό & βαθμονόμηση', 'document structure': 'Δομή βιβλίου', entities: 'Κατηγορίες', spelling: 'Ορθογραφία',
  annotations: 'Σημειώσεις', 'audit + render + write': 'Έλεγχος και αρχεία εξόδου' };
const REVIEW_EL = { required: 'Τμήματα που απαιτούν έλεγχο', recommended: 'Τμήματα που συνιστάται να ελεγχθούν',
  omitted: 'Εκτός εξόδου (επιλογή σου)', furniture: 'Στοιχεία σελίδας (κεφαλίδες, αριθμοί σελίδας)' };
const ENGINE_EL = { unimernet: 'UniMERNet (εξισώσεις)', molscribe: 'MolScribe (μόρια)', rxnscribe: 'RxnScribe (αντιδράσεις)' };
const STATUS = { done: ['approved', 'Ολοκληρώθηκε'], failed: ['required', 'Απέτυχε'], cancelled: ['not_needed', 'Διακόπηκε'],
  interrupted: ['not_needed', 'Διακόπηκε (κλείσιμο εφαρμογής)'], running: ['recommended', 'Εκτελείται'], queued: ['not_needed', 'Σε αναμονή'] };

export function fmtDuration(s) {
  if (s == null) return '';
  s = Math.round(s);
  if (s < 60) return `${s} δευτ.`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m} λεπτ${m === 1 ? 'ό' : 'ά'}`;
  return `${Math.floor(m / 60)} ώρ${Math.floor(m / 60) === 1 ? 'α' : 'ες'} ${m % 60} λεπτά`;
}
const delta = ([a, b]) => { const d = b - a; return `${a} → ${b} (${d > 0 ? '+' : ''}${d})`; };
// A finding type is named by the reason that raises it (core/reasons.py: reason.finding = [type, severity]).
const FINDING_EL = { REVIEW_REQUIRED: 'Τμήματα για έλεγχο', INTEGRITY_CONTENT_NOT_EXPORTED: 'Περιεχόμενο που λείπει από την έξοδο',
  INTEGRITY_PAGE_MISSING: 'Σελίδα χωρίς περιεχόμενο', ENGINE_FAILED: 'Αποτυχία μηχανής OCR' };
export const findingName = (t) => {
  if (FINDING_EL[t]) return FINDING_EL[t];
  const r = Object.values((S.meta && S.meta.reasons) || {}).find(x => x.finding && x.finding[0] === t) || R(t);
  return r && r.el && r.el !== t ? r.el : t.toLowerCase().replaceAll('_', ' ');
};
const row = (label, value, cls = '') => h('div', { class: 'jl-row ' + cls }, h('span', { class: 'jl-k' }, label), h('span', { class: 'jl-v' }, value));
const section = (title, ...kids) => { kids = kids.flat().filter(Boolean); return kids.length ? h('div', { class: 'jl-sec' }, h('h3', {}, title), kids) : null; };

function triggerSection(t) {
  if (!t) return null;
  if (t.first) return section('Γιατί έτρεξε', h('p', {}, 'Πρώτη δημιουργία του βιβλίου (δεν υπήρχε προηγούμενο αποτέλεσμα για σύγκριση).'));
  if (!t.count) return section('Γιατί έτρεξε', h('p', {}, 'Δεν υπήρχαν νέες αλλαγές σου· έτρεξε λόγω νέου κώδικα, μοντέλων ή ρυθμίσεων.'));
  const counts = new Map();
  for (const e of t.events) { const d = e.type === 'page_profile' ? 'Προσεκτική επανεξέταση σελίδας' : describePending(e); counts.set(d, (counts.get(d) || 0) + 1); }
  const more = t.count - t.events.length;
  return section(`Τι ζήτησες (${t.count} αλλαγ${t.count === 1 ? 'ή' : 'ές'})`,
    h('ul', {}, [...counts].map(([d, n]) => h('li', {}, d, n > 1 ? h('span', { class: 'mut' }, ` ×${n}`) : null)),
      more > 0 ? h('li', { class: 'mut' }, `και ${more} ακόμα`) : null));
}

function changesSection(c, built) {
  if (!c) return null;
  const rows = [];
  for (const [k, v] of Object.entries(c.review || {})) rows.push(row(REVIEW_EL[k] || k, delta(v)));
  const e = c.entities || {};
  if (e.total && (e.total[0] !== e.total[1] || e.verified[0] !== e.verified[1]))
    rows.push(row('Κατηγορίες (επιβεβαιωμένες)', `${e.total[0]} (${e.verified[0]}) → ${e.total[1]} (${e.verified[1]})`));
  if (c.release && c.release[0] !== c.release[1]) rows.push(row('Κατάσταση βιβλίου', `${RELEASE_EL[c.release[0]] || c.release[0] || '—'} → ${RELEASE_EL[c.release[1]] || c.release[1]}`));
  const levels = (c.levels || []).map(l => h('li', {}, `«${l.title}»: `, l.before == null ? 'νέα επικεφαλίδα' : `επίπεδο ${l.before}`, ' → ', `επίπεδο ${l.after}`));
  if (c.levels_total > (c.levels || []).length) levels.push(h('li', { class: 'mut' }, `και ${c.levels_total - c.levels.length} ακόμα`));
  const fnd = Object.entries(c.findings || {}).map(([t, v]) => h('li', {}, `${findingName(t)}: `, delta(v)));
  const pages = (c.pages || []).map(p => h('li', {}, `σελ. ${p.page}: ${STATE_EL[p.before] || p.before || '—'} → ${STATE_EL[p.after] || p.after}`));
  const nothing = !rows.length && !levels.length && !fnd.length && !pages.length;
  return section('Τι άλλαξε στο βιβλίο',
    nothing ? h('p', { class: 'mut' }, 'Καμία ορατή αλλαγή στο αποτέλεσμα.') : null, rows,
    levels.length ? [h('div', { class: 'jl-sub' }, 'Επίπεδα επικεφαλίδων'), h('ul', {}, levels)] : null,
    fnd.length ? [h('div', { class: 'jl-sub' }, 'Ευρήματα ελέγχου'), h('ul', {}, fnd)] : null,
    pages.length ? [h('div', { class: 'jl-sub' }, 'Σελίδες'), h('ul', {}, pages)] : null,
    c.heading_proposals ? h('p', { class: 'small mut' }, `Ανοιχτές προτάσεις για επίπεδα επικεφαλίδων: ${c.heading_proposals} (στη «Δομή»).`) : null);
}

function processSection(facts) {
  const ex = facts.filter(f => f.kind === 'extraction'), dil = facts.find(f => f.kind === 'diligent'), mods = facts.filter(f => f.kind === 'models');
  if (!ex.length && !dil && !mods.length) return null;
  const failed = ex.flatMap(f => f.failed || []);
  return section(ex.length ? 'Επεξεργασία σελίδων' : 'Τι έτρεξε',
    ex.length ? row('Σελίδες που διαβάστηκαν', String(ex[0].pages)) : null,
    ex.length ? row('Αποτυχίες', failed.length ? failed.map(([e, p]) => `σελ. ${p} (${e})`).join(', ') : 'καμία', failed.length ? 'bad' : '') : null,
    dil ? row('Προσεκτική επανεξέταση', `${dil.pages.length} σελίδες με πολλά προβλήματα διάταξης`) : null,
    mods.length ? row('Ειδικά μοντέλα', mods.map(m => `${ENGINE_EL[m.engine] || m.engine}: ${m.crops}`).join(' · ')) : null);
}

function resultSection(b) {
  if (!b) return null;
  const stages = Object.entries(b.timings || {}).filter(([, s]) => s >= 0.5).sort((x, y) => y[1] - x[1])
    .map(([k, s]) => `${STAGE_EL[k] || k} ${fmtDuration(s)}`).join(' · ');
  return section('Το βιβλίο τώρα',
    row('Κατάσταση', RELEASE_EL[b.release_status] || b.release_status),
    row('Για έλεγχο', `${b.required} απαιτείται · ${b.recommended} συνιστάται`),
    row('Κρίσιμα ευρήματα', String(b.critical), b.critical ? 'bad' : ''),
    row('Διάρκεια ενημέρωσης', `${fmtDuration(b.seconds)}${stages ? ' — ' + stages : ''}`));
}

/** The job report dialog (replaces the raw log view). */
export async function jobSummaryDialog(j) {
  const d = openDialog('wide');
  const [cls, label] = j.warning === 'integrity' ? ['recommended', 'Ολοκληρώθηκε · κρίσιμο εύρημα ακεραιότητας'] : (STATUS[j.state] || ['not_needed', j.state]);
  const dur = j.started ? ((j.finished || Date.now() / 1000) - j.started) : null;
  d.append(h('h2', {}, j.label), h('div', { class: 'row small' }, h('span', { class: 'dot ' + cls }), h('b', {}, label),
    h('span', { class: 'mut' }, ` · ${new Date(j.created * 1000).toLocaleString('el-GR')}${dur != null ? ' · ' + fmtDuration(dur) : ''}`)));
  const body = h('div', { class: 'jl' }, h('p', { class: 'mut' }, 'Φόρτωση…'));
  const raw = h('pre', { class: 'jl-raw' }), det = h('details', {}, h('summary', {}, 'Τεχνικό log (για αποσφαλμάτωση)'), raw);
  det.addEventListener('toggle', async () => { if (det.open && !raw.textContent) raw.textContent = (await api(`/api/jobs/${j.id}/log`)) || '(κενό)'; });
  d.append(body, det, h('div', { class: 'row end' }, h('button', { onclick: () => d.close() }, 'Κλείσιμο')));
  d.showModal();
  let facts = [];
  try { facts = (await api(`/api/jobs/${j.id}/summary`)).facts; } catch { facts = []; }
  body.textContent = '';
  const one = (k) => [...facts].reverse().find(f => f.kind === k);
  const parts = [triggerSection(one('trigger')), processSection(facts), changesSection(one('changes'), one('built')), resultSection(one('built'))].filter(Boolean);
  if (j.state === 'failed') parts.unshift(h('div', { class: 'note' }, 'Η εργασία απέτυχε. Οι αιτίες είναι στο τεχνικό log παρακάτω· η επόμενη εκτέλεση συνεχίζει από εκεί που σταμάτησε.'));
  if (!parts.length) { parts.push(h('p', { class: 'mut' }, 'Δεν υπάρχει σύνοψη για αυτή την εργασία (έτρεξε πριν από τις απλές συνόψεις ή δεν έφτασε στο τέλος). Δες το τεχνικό log.')); det.open = true; }
  body.append(...parts);
}
