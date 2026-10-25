"""Developer workbench: one card per piece of manual work left after generation (analysis/MUNKAPAD.html).

Each card carries the original Forms code, the generator's reason, the suggested place (screen,
backend endpoint, review only), a starting skeleton and a status the developer sets (kept in the
browser). The cards can be exported as a CSV for Jira. A short Forms semantics cheat sheet sits
next to them: the things that are easiest to get wrong when porting by hand.
"""
from __future__ import annotations

import html
import json
from pathlib import Path

from .common import name, write_json
from .discovery import source_view
from .generate import write

PLACES = {'frontend': 'Képernyő (Angular komponens)', 'button': 'Gomb akció-végpontja (DPS ServiceImpl)',
          'backend': 'Adatvégpont (DPS ServiceImpl)', 'review': 'Csak ellenőrzés'}
BACKEND_SCOPES = {'all', 'read', 'write', 'create', 'update', 'delete'}
# Review codes worth a card: a decision or a check the developer must make.
REVIEW_CODES = {'STARTUP_ACCESS': 'Indítási jogosultság', 'KEY_DISABLES_OPERATION': 'Kikapcsolt Forms-művelet',
                'KEY_TRIGGER_WRAPPER': 'Billentyű-trigger saját rutinnal', 'ROWID_KEY': 'ROWID-kulcs',
                'STARTUP_ITEM_PROPERTY': 'Indításkor tiltott mező', 'QUERY_FILTER': 'Blokkszűrő (WHERE)',
                'MASTER_DETAIL': 'Master-detail', 'RUNTIME_BLOCK_PROPERTY': 'Futásidejű blokkbeállítás',
                'FRAMEWORK_DATA_TRIGGER': 'Keretrendszeri adattrigger', 'DYNAMIC_LOV': 'LOV-érték ellenőrzése'}

CHEAT_SHEET = [
    ('Rekordállapotok', 'NEW (üres új rekord), INSERT (új, kitöltött), QUERY (lekérdezett, változatlan), CHANGED (lekérdezett, '
     'módosított). A generált képernyőn: lekérdezett rekord = eredeti DTO (originals), módosítás = dirty űrlap; a mentés '
     'ebből dönti el a beszúrás/módosítás/törlés műveletet.'),
    ('FORM_TRIGGER_FAILURE', 'A trigger és a futó művelet leáll, a tranzakció NEM görgetődik vissza magától Formsban. '
     'A webes végpont ORA-20999 → HTTP 422 válasszal áll le, és a végpont tranzakciója visszagörget: mentéskor semmi sem marad félúton.'),
    ('Triggerek sorrendje mentéskor', 'Validálás (WHEN-VALIDATE-ITEM, -RECORD) → PRE-COMMIT → blokkonként (blokksorrendben): '
     'törlések (PRE-DELETE, ON-DELETE/DELETE, POST-DELETE), majd beszúrások/módosítások (PRE-INSERT/UPDATE, ON-…, POST-…) '
     '→ POST-FORMS-COMMIT → COMMIT. A commitForm végpont ugyanezt a sorrendet követi.'),
    ('WHEN-VALIDATE-ITEM kontra PRE-INSERT', 'A WHEN-VALIDATE-ITEM a mező elhagyásakor fut (itt: mentéskor, a validációs '
     'láncban); a PRE-INSERT csak a beszúrás előtt, kulcskiosztásra (szekvencia) való. Kulcsot csak PRE-INSERT/ON-INSERT írhat.'),
    ('POST-QUERY', 'Lekérdezett soronként fut; a lekérdezett adatbázismezőt nem írhatja (különben a rekord CHANGED lenne). '
     'Leírásmezők (megnevezések) kitöltésére való.'),
    (':SYSTEM változók', 'CURSOR_BLOCK/ITEM: ahol a kurzor áll (gombnyomáskor a gomb blokkja, ha Mouse Navigate=Yes); '
     'TRIGGER_BLOCK/ITEM: a futó trigger gazdája; MODE: a webes felületen mindig NORMAL. A generált képernyő a kéréssel küldi.'),
    (':GLOBAL', 'Munkamenet-szintű szöveges változók, formok között közösek. A képernyő a böngészőfülön (sessionStorage) '
     'tárolja, és minden kéréssel elküldi; amit a PL/SQL ír, azt a válaszból visszamenti.'),
    ('Master-detail', 'A detail rekord kulcsa a masterből jön (Copy Value from Item / reláció). Törlésnél: Non-Isolated = '
     'nem törölhető, ha van detail (ON-CHECK-DELETE-MASTER); Cascading = a detailek is törlődnek (PRE-DELETE).'),
    ('COMMIT_FORM a kód közepén', 'Formsban a mentés azonnal lefut, a következő utasítás már a mentett állapotot látja. '
     'A webes emuláció a Forms-hívásokat a kód VÉGÉN hajtja végre, ezért COMMIT_FORM/EXECUTE_QUERY után nem lehet több adatművelet.'),
    ('SHOW_ALERT', 'A webes kérés nem tud megállni egy kérdésnél: a válasz nélküli alert visszagörgeti a kérés munkáját, a '
     'képernyő megkérdezi a felhasználót, és a válasszal a kód elölről, ugyanígy fut le.'),
]


def cards(model: dict, discovery: dict) -> list[dict]:
    """The manual work of one module: untranslated triggers, endpoints left manual, review decisions."""
    sources = {t['id']: t for t in model['triggers']}
    result = []
    for issue in model['issues']:
        if issue['code'] != 'UNSUPPORTED_TRIGGER':
            continue
        trigger_id, _, reason = issue['detail'].partition(': ')
        trigger = sources.get(trigger_id)
        if not trigger:
            continue
        scope = issue['scope']
        place = 'button' if scope == 'button' else 'backend' if scope in BACKEND_SCOPES else 'frontend'
        result.append({'id': trigger_id, 'kind': 'trigger', 'title': trigger_id, 'event': trigger['event'],
                       'place': place, 'blocking': scope in BACKEND_SCOPES or scope == 'button', 'reason': reason,
                       'source': source_view(trigger['source'])[0], 'skeleton': skeleton(trigger, place, model)})
    for issue in model['issues']:
        label = REVIEW_CODES.get(issue['code'])
        if not label or issue['scope'] != 'review':
            continue
        owner = issue['owner'].replace('@FORM:', '')
        result.append({'id': issue['code'] + ':' + issue['owner'] + ':' + str(len(result)), 'kind': 'review', 'title': label + ': ' + owner,
                       'event': issue['owner'], 'place': 'review', 'blocking': False, 'reason': issue['detail'],
                       'source': '', 'skeleton': ''})
    return result


def skeleton(trigger: dict, place: str, model: dict) -> str:
    """A starting point in the right file: a commented screen method, or where the backend code goes."""
    method = 'on' + name((trigger['owner'] + '_' + trigger['event']).replace('.', '_').replace('-', '_'), 'pascal')
    if place == 'frontend':
        return (f"// {trigger['id']}: a képernyő-komponensben (frontend/.../*.component.ts)\n"
                f"private {method}(): void {{\n"
                "  // Forms-hívások: a képernyő saját metódusai, például this.queryB() (EXECUTE_QUERY) – lásd MIGRATION_NOTES.\n"
                "  // Adatbázis-munka: egy akció-végponton át (ActionRequest: blocks + parameters).\n"
                "}")
    if place == 'button':
        return (f"// {trigger['id']}: a DPS ServiceImpl gombmetódusában (HTTP 501 helyett).\n"
                "// Az eredeti kód kommentként már ott van; ami az adatbázisban futhat, az DbCalls.call-lal futtatható,\n"
                "// a Forms-hívások ActionResult.commands-ként mehetnek vissza a képernyőnek.")
    return (f"// {trigger['id']}: a DPS ServiceImpl {trigger['block'] or 'modul'} blokkjának szabálymetódusában\n"
            "// (validateRules/preInsert/... – lásd analysis/backend-evidence.md), a végpont tranzakciójában.")


def write_workbench(model: dict, output: Path, discovery: dict | None = None) -> None:
    items = cards(model, discovery or {})
    write_json(output / 'analysis' / 'workbench.json', {'version': 1, 'module': model['name'], 'cards': items})
    data = json.dumps({'module': model['name'], 'cards': items, 'places': PLACES, 'cheat': CHEAT_SHEET},
                      ensure_ascii=False).replace('</', '<\\/')
    write(output / 'analysis' / 'MUNKAPAD.html', PAGE.replace('__TITLE__', html.escape(model['name'])).replace('__DATA__', data))


PAGE = '''<!doctype html>
<html lang="hu">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Munkapad – __TITLE__</title>
<style>
:root { --bg: #f7f7f5; --panel: #ffffff; --text: #1d1d1b; --muted: #66665f; --line: #deded8; --accent: #2c5d8f;
        --warn: #9a5b00; --ok: #2e7d4f; --code: #f1f1ec; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161615; --panel: #1f1f1d; --text: #ecece6; --muted: #a5a59c; --line: #34342f; --accent: #7fb0e0;
          --warn: #e0a64f; --ok: #6fc28f; --code: #262623; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); font: 15px/1.5 system-ui, sans-serif; }
header, main { max-width: 1100px; margin: 0 auto; padding: 16px; }
h1 { font-size: 22px; margin: 8px 0; }
.bar { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
select, input, button { font: inherit; padding: 6px 10px; border: 1px solid var(--line); border-radius: 6px;
                        background: var(--panel); color: var(--text); }
button { cursor: pointer; }
.stats { color: var(--muted); }
.card { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px 14px; margin: 10px 0; }
.card h2 { font-size: 16px; margin: 0 0 4px; overflow-wrap: anywhere; }
.meta { color: var(--muted); font-size: 13px; display: flex; flex-wrap: wrap; gap: 6px 14px; }
.tag { border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; }
.tag.blocking { color: var(--warn); border-color: var(--warn); }
.card.done { opacity: .6; }
pre { background: var(--code); padding: 10px; border-radius: 6px; overflow: auto; font-size: 13px; max-height: 360px; }
details summary { cursor: pointer; color: var(--accent); }
.cheat dt { font-weight: 600; margin-top: 10px; }
.cheat dd { margin: 2px 0 0; color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Munkapad – __TITLE__</h1>
  <p class="stats" id="stats"></p>
  <div class="bar">
    <select id="place"><option value="">Minden hely</option></select>
    <select id="state"><option value="">Minden állapot</option><option value="todo">Teendő</option>
      <option value="doing">Folyamatban</option><option value="done">Kész</option></select>
    <input id="search" type="search" placeholder="Keresés (trigger, ok, kód)">
    <button id="csv" type="button">Jira CSV</button>
  </div>
</header>
<main>
  <div id="cards"></div>
  <details class="card cheat"><summary>Forms-szemantika puska</summary><dl id="cheat"></dl></details>
</main>
<script>
const DATA = __DATA__;
const KEY = 'frm.workbench.' + DATA.module;
let states = {};
try { states = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) { states = {}; }
const save = () => { try { localStorage.setItem(KEY, JSON.stringify(states)); } catch (e) { /* tárhely nélkül csak ebben a nézetben */ } };
const el = (tag, attrs = {}, text = '') => { const n = document.createElement(tag); Object.assign(n, attrs); if (text) n.textContent = text; return n; };
const place = document.getElementById('place');
for (const [key, label] of Object.entries(DATA.places)) place.append(el('option', { value: key }, label));
const cheat = document.getElementById('cheat');
for (const [term, text] of DATA.cheat) { cheat.append(el('dt', {}, term)); cheat.append(el('dd', {}, text)); }
function visible() {
  const p = place.value, s = document.getElementById('state').value, q = document.getElementById('search').value.toLowerCase();
  return DATA.cards.filter(c => (!p || c.place === p) && (!s || (states[c.id] || 'todo') === s)
    && (!q || (c.title + ' ' + c.reason + ' ' + c.source).toLowerCase().includes(q)));
}
function render() {
  const root = document.getElementById('cards');
  root.replaceChildren();
  const done = DATA.cards.filter(c => states[c.id] === 'done').length;
  document.getElementById('stats').textContent = DATA.cards.length + ' feladat, ebből kész: ' + done
    + '; végpontot tilt: ' + DATA.cards.filter(c => c.blocking).length + '.';
  for (const c of visible()) {
    const card = el('section', { className: 'card' + (states[c.id] === 'done' ? ' done' : '') });
    card.append(el('h2', {}, c.title));
    const meta = el('div', { className: 'meta' });
    meta.append(el('span', { className: 'tag' }, DATA.places[c.place]));
    if (c.event) meta.append(el('span', {}, c.event));
    if (c.blocking) meta.append(el('span', { className: 'tag blocking' }, 'végpontot tilt'));
    const select = el('select');
    for (const [value, label] of [['todo', 'Teendő'], ['doing', 'Folyamatban'], ['done', 'Kész']]) select.append(el('option', { value }, label));
    select.value = states[c.id] || 'todo';
    select.addEventListener('change', () => { states[c.id] = select.value; save(); render(); });
    meta.append(select);
    card.append(meta);
    card.append(el('p', {}, c.reason));
    if (c.source) { const d = el('details'); d.append(el('summary', {}, 'Eredeti Forms-kód')); d.append(el('pre', {}, c.source)); card.append(d); }
    if (c.skeleton) { const d = el('details'); d.append(el('summary', {}, 'Javasolt hely és váz')); d.append(el('pre', {}, c.skeleton)); card.append(d); }
    root.append(card);
  }
}
for (const id of ['place', 'state', 'search']) document.getElementById(id).addEventListener('input', render);
document.getElementById('csv').addEventListener('click', () => {
  const cell = v => '"' + String(v || '').replace(/"/g, '""') + '"';
  const rows = [['Summary', 'Description', 'Labels', 'Status']].concat(visible().map(c => [
    DATA.module + ': ' + c.title, c.reason + '\\n\\n' + c.source, 'forms-migracio ' + c.place, states[c.id] || 'todo']));
  const blob = new Blob(['\\ufeff' + rows.map(r => r.map(cell).join(';')).join('\\r\\n')], { type: 'text/csv;charset=utf-8' });
  const link = el('a', { href: URL.createObjectURL(blob), download: DATA.module + '-munkapad.csv' });
  link.click();
  URL.revokeObjectURL(link.href);
});
render();
</script>
</body>
</html>
'''
