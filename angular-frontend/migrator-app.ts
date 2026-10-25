import {
  Component,
  OnDestroy,
  OnInit,
  ViewEncapsulation,
  computed,
  inject,
  signal,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { DatePipe, NgTemplateOutlet } from '@angular/common';
import { FormBuilder, FormControl, ReactiveFormsModule, Validators } from '@angular/forms';
import { HttpBackend, HttpClient, HttpErrorResponse, HttpHeaders, HttpParams } from '@angular/common/http';
import { DomSanitizer, SafeHtml } from '@angular/platform-browser';
import { EmptyError, firstValueFrom, map } from 'rxjs';
import { AccordionModule } from '@openng/optimus-ui/accordion';
import { BadgeModule } from '@openng/optimus-ui/badge';
import { ButtonModule } from '@openng/optimus-ui/button';
import { CardModule } from '@openng/optimus-ui/card';
import { DialogModule } from '@openng/optimus-ui/dialog';
import { DividerModule } from '@openng/optimus-ui/divider';
import { FieldsetModule } from '@openng/optimus-ui/fieldset';
import { InputNumberModule } from '@openng/optimus-ui/inputnumber';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { ListboxModule } from '@openng/optimus-ui/listbox';
import { MessageModule } from '@openng/optimus-ui/message';
import { PanelModule } from '@openng/optimus-ui/panel';
import { ProgressBarModule } from '@openng/optimus-ui/progressbar';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TabsModule } from '@openng/optimus-ui/tabs';
import { TagModule } from '@openng/optimus-ui/tag';
import { ToggleSwitchModule } from '@openng/optimus-ui/toggleswitch';
import { ToolbarModule } from '@openng/optimus-ui/toolbar';
import { TooltipModule } from '@openng/optimus-ui/tooltip';
import { MigratorAssistant } from './migrator-assistant';
import { MigratorAiQueue } from './migrator-ai-queue';

// ---------------------------------------------------------------- contract --
// Mirrors frm_forms/web/models.py; the backend rejects unknown option keys.
type AiMode = 'off' | 'assist' | 'cached';
type JobStatus = 'queued' | 'running' | 'needs_input' | 'completed' | 'failed' | 'cancelled' | 'interrupted';
type Severity = 'success' | 'info' | 'warn' | 'danger' | 'secondary' | 'contrast' | undefined;
type Upload = 'schema' | 'rules' | 'screenOverrides' | 'fieldLengths';
type DetailTab = 'overview' | 'preview' | 'files' | 'logs';

interface Options {
  // One mode does everything (screen + Java + analysis); the backend defaults to it.
  generation_mode?: 'strict' | 'scaffold' | 'screen';
  screen_tab_layout: 'tabs' | 'accordion'; screen_infer_widgets: boolean; screen_repair_display_text: boolean;
  // The main window is asked when needed (needs_input), never typed in advance.
  screen_row_tolerance: number; screen_preserve_gaps: boolean; screen_primary_window?: string;
  // Survey runs: no window question, the first possible main window stands in.
  screen_window_selection?: 'all' | 'ask'; screen_primary_window_auto?: boolean;
  backend_live: boolean;
  screen_button_label_property: 'labelText' | 'btnLabel'; screen_fold_list_buttons: boolean;
  module: string | null; AWU_AZON: string; cl_package?: string; api_prefix: string; angular_selector_prefix: string;
  // The module's own folders in the project: the Java folders give the packages, the deploy writes there.
  project_layout?: Partial<Record<PartKey, string>>;
  wbs_base_url: string; dps_base_url: string; ollama_url: string;
  html_selectors: { form_block: string; button: string; table: string };
  form_block_types: { text: string; number: string; datetime: string; checkbox: string; select: string; radio: string; password: string; textarea: string };
  form_block_structure_type: string; form_block_columns: string; form_block_checkbox_boolean: boolean;
  emit_imports: boolean; layout_columns: 12 | 18 | 24; optimus_import_path: string; optimus_form_block_symbol: string;
  form_block_type_import_path: string; environment_import_path: string; table_import_path: string; table_symbol: string;
  calendar_blocks: string[]; table_blocks: string[];
  table_bindings: { rows: string; columns: string; field: string; header: string };
  endpoint_names: { list: string; create: string; update: string; delete: string };
  ai_mode: AiMode; max_ai_calls: number; ai_num_ctx: number; ai_num_predict: number;
  ai_timeout_seconds: number; ai_max_source_chars: number; ai_max_prompt_bytes: number;
  ai_think: 'default' | 'low' | 'medium' | 'high' | 'disabled'; ai_cache_salt: string;
  ollama_model: string; strict: boolean;
}
interface Summary {
  form: string; blocks: number; items: number; triggers: number; converted_triggers: number; review_triggers: number;
  ai: { mode: AiMode; attempted_calls: number; cache_hits: number; failures: number };
}
interface Choice {
  name: string;
  title: string;
  blocks: string[];
  first_navigation: boolean;
  items?: number;
  preview?: string;
}
interface Question { kind: 'primary_window' | 'windows'; message: string; choices: Choice[] }
interface Job {
  id: string; filename: string; source_type: string; options: Options; status: JobStatus; phase: string;
  created_at: string; error: string | null; summary: Summary | null; review_required: boolean;
  module: string | null; download_name?: string; files_count: number;
  batch?: string | null; question?: Question | null;
}
interface Blocker { reason: string; endpoints: number; forms: string[]; example: string }
interface PortfolioForm { folder: string; form?: string; status: string; triggers?: number; converted?: number; review?: number; endpoints?: number; enabled?: number; ready?: number; blocked?: number; error?: string | null }
interface BatchReport {
  batch: string; total: number; done: boolean; counts: Record<string, number>;
  jobs: { id: string; filename: string; status: JobStatus; module: string | null }[];
  report: { totals: Record<string, number>; endpoint_blockers: Blocker[]; external_routines: { routine: string; calls: number; forms: string[] }[]; forms: PortfolioForm[] };
  markdown: string;
}
interface SurveyCause {
  code: string; reason: string; endpoints: number; sole: number; forms: number; operations: Record<string, number>;
}
interface DeployFile {
  source: string; part: string | null; target: string | null; display: string | null; status: string; policy: string;
  reason?: string;
}
interface DeployHelper {
  name: HelperName; source: string; version: string | null; found: string | null; found_version: string | null;
  status: 'ok' | 'outdated' | 'newer' | 'missing' | 'unknown'; expected?: string;
}
interface DeployReport {
  project: string | null; dry_run: boolean; force: boolean; layout: Record<string, string | null>;
  exact?: Record<string, boolean>; packages?: Record<string, string>; helpers?: DeployHelper[];
  counts: Record<string, number>; files: DeployFile[]; markdown: string;
}
type DeployTarget = { kind: 'job' | 'batch'; id: string };
type PartKey = 'CL' | 'DPS' | 'WBS' | 'frontend';
// generate: the module's folders chosen before the generation; deploy: the folders of a job's deploy.
type FolderScope = 'generate' | 'deploy';
type HelperName = 'CommonMigrateTools.java' | 'wf-table.ts';
type FolderKind = 'java' | 'angular' | null;
interface FolderEntry { name: string; path: string; kind: FolderKind }
interface FolderListing {
  path: string | null; parent: string | null; kind: FolderKind; roots: FolderEntry[]; folders: FolderEntry[]; truncated: boolean;
}
interface SurveyApproximation {
  kind: string; label: string; unit: string; count: number; forms: number; details: Record<string, number>; now: string; fix: string;
}
interface SurveyResult {
  batch: string; markdown: string;
  report: { totals: Record<string, number>; causes: SurveyCause[]; approximations?: SurveyApproximation[] };
}
interface BatchSummary { id: string; total: number; completed: number; waiting: number; failed: number; active: number; created: string }
interface BatchProgress { done: number; total: number; waiting: boolean; failed: string[] }
interface Health { version: string; python: string; exporter: { status: 'available' | 'configured' | 'missing'; message: string } }
interface Defaults {
  options: Options; limits: { file_bytes: number; json_bytes: number; job_timeout_seconds: number; pending_jobs?: number }; cors_origins: string[];
  folders?: { dialog: boolean; limited: boolean };
  ollama_url?: string;
}
interface SourceFile { path: string; bytes: number }
interface Preview { path: string; text: string; truncated: boolean }
interface Issue { code: string; owner: string; scope: string; detail: string }

const API_KEY = 'frm-api-url-v2';
// v5: the gap default changed (fields fill their row); older saved settings would bring gaps back.
// v6: the UI offers no choices any more; stale saved choices must not stay active unseen.
const OPTIONS_KEY = 'frm-options-v6';
const AI_KEY = 'frm-ai-connection-v1';
// The module folders chosen for each module name: choosing the same module again fills them in.
const FOLDERS_KEY = 'frm-module-folders-v1';
const PART_KEYS: readonly PartKey[] = ['CL', 'DPS', 'WBS', 'frontend'];
const HELPERS: readonly HelperName[] = ['CommonMigrateTools.java', 'wf-table.ts'];
const URL_PATTERN = /^https?:\/\/[^\s]+$/;

// Every explanation lives in a tooltip, next to the control it explains.
const HELP = {
  olb: 'Az FMB által hivatkozott object library-k Forms2XML exportja. Több is választható; a fájlnév maradjon <név>_olb.xml.',
  mmb: 'A menümodul exportja (opcionális). Fájlnév: <név>_mmb.xml.',
  overrides: 'Képernyő-felülbírálások (JSON): az első generálás csomagjában lévő screen-overrides.template.json kitöltött változata. Forrásváltozáskor az érintett szabályt újra ellenőrizni kell.',
  schema: 'Adatbázisséma (schema.json): táblák, kulcsok, sequence. Az írást a séma writable: true beállítása engedélyezi; a generátor nem csatlakozik az adatbázishoz.',
  rules: 'Saját szabályok (rules.json): ellenőrzött trigger-helyettesítések.',
  demo: 'Betölti a beépített customer_fmb.xml példát és a hozzá tartozó sémát, AI nélkül.',
  generate: 'Feltölti a fájlokat és elindítja a generálást. A folyamat a Feladatok táblában követhető; a fájlok a backend gépén maradnak.',
  save: 'A jelenlegi beállításokat ebben a böngészőben megjegyzi a következő alkalomra.',
  sources: 'Egy vagy több Oracle Forms modul (.fmb vagy Forms2XML .xml). Több fájl esetén tömeges futtatás: formonként külön feladat, a végén közös összesítő.',
  module: 'Opcionális, csak egy forrásnál. Az Angular komponens és a Java modul neve; üresen a forrás technikai nevéből készül. camelCase vagy kötőjeles.',
  batch: 'Tömeges futtatás: a kiegészítők (OLB, séma, szabályok) minden formhoz ugyanazok. A modulnév formonként a forrásból készül; ha egy form több képernyős, a fő képernyőt a Feladatok fülön kérdezzük meg.',
  question: 'A form több, egyenrangú ablakból áll. A kiválasztott lesz a fő képernyő, a többi párbeszédablak; a generálás ugyanazzal a feladattal folytatódik.',
  preview: 'A generált képernyők előnézete (fő képernyő, párbeszédablakok, fülek): szerkezet adatok és működés nélkül.',
  batchReport: 'A tömeges futtatás összesítője: mely okok tiltják a legtöbb végpontot, és formonkénti állapot.',
  tabs: 'A Forms fülek (tab page) megjelenítése Optimus Tabs vagy Accordion komponenssel.',
  infer: 'Gomb-, checkbox- és naptárjavaslatok az XML-jelek alapján (WHEN-BUTTON-PRESSED, CheckedValue, KEY-LISTVAL). Minden következtetés a jegyzetben szerepel.',
  repair: 'A frmf2xml által elrontott ékezetek visszafordítható javítása a feliratokban. SQL és adatértékek nem változnak.',
  gaps: 'Alapból kikapcsolva: a mezők kitöltik a sort, üres hely csak az L_URES_* mezők helyén marad. Bekapcsolva a forrás vízszintes térközei colBefore-ként megmaradnak.',
  live: 'Bekapcsolva a generált végpontok azonnal működnek (MODULE_REVIEWED = true, írás engedélyezve, hacsak a séma writable:false-t nem mond). A triggerek eredeti PL/SQL-je az adatbázisban fut. Kikapcsolva minden végpont HTTP 501 az ellenőrzésig.',
  fieldLengths: 'Mezőhossz JSON: {"version": 1, "fields": {"formControlName": {"min": 2, "max": 10}}}. Kulcs lehet BLOKK.formControlName is. Minden formhoz ugyanaz a fájl; kitöltendő minta: analysis/field-lengths.template.json.',
  fold: 'A csak go_item + LIST_VALUES triggerű gomb beolvad a mező saját lenyitó gombjába.',
  tolerance: 'Sorillesztési tolerancia a kisebb mezőmagasság arányában. 0: csak azonos Y-koordináta; alapérték: 0,25.',
  buttonLabel: 'Melyik FormBlock property hordozza a gombfeliratot: labelText (önálló gomb) vagy btnLabel (inputGroup gomb).',
  folders: 'A modul saját mappái a projektben (CL, DPS, WBS, frontend), a „Tallózás…” gombbal. A fájlok pontosan ide kerülnek, új mappa nem készül. A Java-mappák útvonalából lesz a csomag (a src/main/java utáni rész, például hu.ceg.rendszer.cl.modules.rendeles): ezzel generálódnak a package sorok és az importok. A CommonMigrateTools.java és a wf-table.ts nem kerül a projektbe: a feladatnál külön letölthető, elég egyszer a projektbe tenni. A telepítés megkeresi őket, és az importokat a megtalált példányra igazítja (a CommonMigrateTools csomagját nem kell megadni). Üresen hagyva a fájlok csak a ZIP-ben vannak.',
  dps: 'A WBS RestClient célcíme. Generáláskor nem kapcsolódunk hozzá.',
  imports: 'Import-sorok generálása a céges útvonalakkal. Bekapcsolva az útvonalakat meg kell adni.',
  ai: 'Csak ahol a szabályok már nem elegendők. Az ismeretlen triggerek rövid részletei a megadott Ollama szerverhez kerülhetnek; a válasz ellenőrizendő javaslat.',
  ollama: 'A Python backend innen kér segítséget, csak a kiválasztott feladathoz.',
  salt: 'Emeld meg, ha az azonos modellnév tartalma megváltozott.',
  strict: 'A hiányosságokhoz 3-as kilépési kód tartozik; a kész csomag továbbra is letölthető.',
  refresh: 'Feladatlista frissítése.',
  all: 'Teljes csomag: Angular, Java és az elemzés.',
  frontend: 'Csak a frontend forrás.',
  backend: 'Csak a Java package.',
  cancel: 'Leállítja a generálást. Az ablak bezárása önmagában nem állítja le a háttérfeladatot.',
  retry: 'Új feladat ugyanazokkal a fájlokkal és beállításokkal.',
  delete: 'Törli a feladatot és az összes hozzá tartozó helyi fájlt.',
  connect: 'A migrátor teljes API URL-je az /api útvonallal. Az első kapcsolódásig nincs API-kérés; a cím ebben a böngészőben marad. Indítás: python -m frm_forms.web --port 8000',
  cache: 'Törli a helyben tárolt AI-javaslatokat. A generált csomagokat nem érinti; csak üres feladatsor mellett.',
  files: 'A generált fájlok előnézete: az első 1 MB látszik, a ZIP-ben a teljes fájl van.',
  logs: 'A folyamat naplójának utolsó 32 KB-ja. Hibánál a vége a hiba helyét és okát mutatja.',
  review: 'A nem támogatott vagy még nem engedélyezett műveleteket a generált kód blokkolja; a következő lépések a riportban vannak.',
} as const;

function apiUrl(value: string): string {
  const parsed = new URL(value.trim());
  if (!['http:', 'https:'].includes(parsed.protocol) || !parsed.hostname || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error('HTTP(S) API URL szükséges, például http://localhost:8000/api.');
  }
  return parsed.href.replace(/\/+$/, '');
}

function ollamaBaseUrl(value: string): string {
  return apiUrl(value).replace(/\/(?:api\/(?:chat|generate|tags|version)|api|chat|generate)$/, '');
}

function storedModuleFolders(): Record<string, Partial<Record<PartKey, string>>> {
  try {
    const saved: unknown = JSON.parse(localStorage.getItem(FOLDERS_KEY) ?? '{}');
    return saved && typeof saved === 'object' ? (saved as Record<string, Partial<Record<PartKey, string>>>) : {};
  } catch {
    return {};
  }
}

/** A mappa Java-csomagja: a src/main/java utáni rész, különben a „hu” mappától kezdve (mint a szerveren). */
function folderPackage(path: string): string | null {
  const parts = path.split(/[\\/]+/).filter((part) => part && !/^[A-Za-z]:$/.test(part));
  let rest: string[] = [];
  for (let i = parts.length - 3; i >= 0; i--) {
    if (parts[i] === 'src' && parts[i + 1] === 'main' && parts[i + 2] === 'java') {
      rest = parts.slice(i + 3);
      break;
    }
  }
  if (!rest.length && parts.includes('hu')) rest = parts.slice(parts.indexOf('hu'));
  return rest.length && rest.every((part) => /^[A-Za-z_$][A-Za-z0-9_$]*$/.test(part)) ? rest.join('.') : null;
}

function storedBase(): string {
  try { const saved = localStorage.getItem(API_KEY); return saved ? apiUrl(saved) : ''; } catch { return ''; }
}

// ------------------------------------------------------------ code viewer --
// The generated sources are browsed as a folder tree (Frontend, DPS, WBS, CL) and read in a
// highlighting viewer. The highlighter is a small local tokenizer: no dependency, every
// character is escaped, and it only emits <span class="nm-t-…"> (Angular's sanitizer keeps it).
type CodeLang = 'java' | 'ts' | 'html' | 'css' | 'json' | 'sql' | 'md' | 'text';
interface Segment {
  cls: string;
  text: string;
}
interface CodeFolder {
  key: string;
  name: string;
  folders: Map<string, CodeFolder>;
  files: SourceFile[];
}
interface CodeGroupTree {
  label: string;
  note: string;
  root: CodeFolder;
}
interface CodeRow {
  key: string;
  depth: number;
  kind: 'group' | 'folder' | 'file';
  name: string;
  note: string;
  open: boolean;
  count: number;
  file: SourceFile | null;
  lang: CodeLang;
}

const CODE_GROUPS = [
  { key: 'frontend', label: 'Frontend', note: 'Angular képernyő', prefix: 'frontend/' },
  { key: 'dps', label: 'DPS', note: 'Adatbázis-szolgáltatás', prefix: 'backend/DPS/' },
  { key: 'wbs', label: 'WBS', note: 'Webszolgáltatás', prefix: 'backend/WBS/' },
  { key: 'cl', label: 'CL', note: 'Közös DTO-k és REST-kliens', prefix: 'backend/CL/' },
] as const;

const LANG_LABELS: Record<CodeLang, string> = {
  java: 'Java',
  ts: 'TypeScript',
  html: 'HTML',
  css: 'CSS',
  json: 'JSON',
  sql: 'SQL',
  md: 'Markdown',
  text: 'Szöveg',
};

function codeLang(path: string): CodeLang {
  const extension = path.slice(path.lastIndexOf('.') + 1).toLowerCase();
  const byExtension: Record<string, CodeLang> = {
    java: 'java',
    ts: 'ts',
    js: 'ts',
    mjs: 'ts',
    html: 'html',
    htm: 'html',
    xml: 'html',
    svg: 'html',
    css: 'css',
    scss: 'css',
    json: 'json',
    sql: 'sql',
    md: 'md',
  };
  return byExtension[extension] ?? 'text';
}

function fileName(path: string): string {
  return path.slice(path.lastIndexOf('/') + 1);
}

function buildCodeTree(files: SourceFile[]): CodeGroupTree[] {
  return CODE_GROUPS.map((group) => {
    const root: CodeFolder = { key: group.key, name: group.label, folders: new Map(), files: [] };
    for (const file of files) {
      if (!file.path.startsWith(group.prefix)) continue;
      let folder = root;
      for (const part of file.path.slice(group.prefix.length).split('/').slice(0, -1)) {
        let next = folder.folders.get(part);
        if (!next) {
          next = { key: folder.key + '/' + part, name: part, folders: new Map(), files: [] };
          folder.folders.set(part, next);
        }
        folder = next;
      }
      folder.files.push(file);
    }
    return { label: group.label, note: group.note, root };
  });
}

function sortedFolders(folder: CodeFolder): CodeFolder[] {
  return [...folder.folders.values()].sort((a, b) => a.name.localeCompare(b.name));
}

function sortedFiles(folder: CodeFolder): SourceFile[] {
  return [...folder.files].sort((a, b) => a.path.localeCompare(b.path));
}

// Folders first, then files: the order of the tree, also used by the viewer's Előző/Következő.
function folderFiles(folder: CodeFolder): SourceFile[] {
  return [...sortedFolders(folder).flatMap(folderFiles), ...sortedFiles(folder)];
}

function codeRows(tree: CodeGroupTree[], expanded: ReadonlySet<string>, filter: string): CodeRow[] {
  const query = filter.trim().toLowerCase();
  const matches = (file: SourceFile) => !query || file.path.toLowerCase().includes(query);
  const rows: CodeRow[] = [];
  const walk = (folder: CodeFolder, depth: number): void => {
    for (const child of sortedFolders(folder)) {
      const count = folderFiles(child).filter(matches).length;
      if (!count) continue;
      const open = !!query || expanded.has(child.key);
      rows.push({ key: child.key, depth, kind: 'folder', name: child.name, note: '', open, count, file: null, lang: 'text' });
      if (open) walk(child, depth + 1);
    }
    for (const file of sortedFiles(folder).filter(matches)) {
      rows.push({
        key: file.path,
        depth,
        kind: 'file',
        name: fileName(file.path),
        note: '',
        open: false,
        count: 0,
        file,
        lang: codeLang(file.path),
      });
    }
  };
  for (const group of tree) {
    const count = folderFiles(group.root).filter(matches).length;
    if (!count) continue;
    const open = !!query || expanded.has(group.root.key);
    rows.push({ key: group.root.key, depth: 0, kind: 'group', name: group.label, note: group.note, open, count, file: null, lang: 'text' });
    if (open) walk(group.root, 1);
  }
  return rows;
}

function codeCrumbs(path: string): string[] {
  const group = CODE_GROUPS.find((candidate) => path.startsWith(candidate.prefix));
  const folders = (group ? path.slice(group.prefix.length) : path).split('/').slice(0, -1);
  return group ? [group.label, ...folders] : folders;
}

function lineCount(text: string): number {
  if (!text) return 0;
  const lines = text.split('\n').length;
  return text.endsWith('\n') ? lines - 1 : lines;
}

function prettyJson(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2) + '\n';
  } catch {
    return text;
  }
}

function push(out: Segment[], cls: string, text: string): void {
  if (!text) return;
  const previous = out[out.length - 1];
  if (previous && previous.cls === cls) previous.text += text;
  else out.push({ cls, text });
}

function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

const words = (list: string) => list.trim().split(/\s+/).join('|');
const COMMENT = String.raw`(?<c>\/\*[\s\S]*?(?:\*\/|$)|\/\/[^\n]*)`;
const QUOTED = String.raw`"(?:[^"\\\n]|\\.)*"?|'(?:[^'\\\n]|\\.)*'?`;
const NUMBER = String.raw`(?<n>\b(?:0[xX][\da-fA-F_]+|\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d+)?)[lLfFdDn]?\b)`;
const CONSTANT = String.raw`(?<cst>\b[A-Z][A-Z0-9_]+\b)`;
const TYPE = String.raw`(?<t>\b[A-Z][\w$]*)`;
const CALL = String.raw`(?<f>\b[a-z_$][\w$]*(?=\s*\())`;

const JAVA_RE = new RegExp(
  [
    COMMENT,
    String.raw`(?<s>"""[\s\S]*?(?:"""|$)|${QUOTED})`,
    String.raw`(?<a>@(?!interface\b)[A-Za-z_][\w.]*)`,
    NUMBER,
    String.raw`(?<k>\b(?:${words(`
      abstract assert boolean break byte case catch char class const continue default do double else enum
      extends final finally float for if implements import instanceof int interface long native new package
      private protected public return short static super switch synchronized this throw throws transient try
      var void volatile while record yield true false null`)})\b)`,
    CONSTANT,
    TYPE,
    CALL,
  ].join('|'),
  'g',
);

const TS_RE = new RegExp(
  [
    COMMENT,
    String.raw`(?<tl>\`(?:[^\`\\]|\\[\s\S])*\`?)`,
    String.raw`(?<s>${QUOTED})`,
    String.raw`(?<a>@[A-Za-z_][\w.]*)`,
    NUMBER,
    String.raw`(?<k>\b(?:${words(`
      abstract any as async await boolean break case catch class const constructor continue declare default
      delete do else enum export extends false finally for from function get if implements import in infer
      instanceof interface keyof let never new null number of private protected public readonly return
      satisfies set static string super switch this throw true try type typeof undefined unknown var void
      while yield`)})\b)`,
    CONSTANT,
    TYPE,
    CALL,
  ].join('|'),
  'g',
);

const CSS_RE = new RegExp(
  [
    COMMENT,
    String.raw`(?<s>${QUOTED})`,
    String.raw`(?<k>@[\w-]+|!important\b)`,
    String.raw`(?<n>#[\da-fA-F]{3,8}\b|(?<![\w-])-?(?:\d+\.?\d*|\.\d+)(?:px|rem|em|%|vh|vw|ch|fr|ms|s|deg)?)`,
    String.raw`(?<attr>^[ \t]*--?[A-Za-z][\w-]*(?=\s*:[^{\n]*(?:;|$)))`,
    String.raw`(?<t>[.#&][A-Za-z_-][\w-]*)`,
  ].join('|'),
  'gm',
);

const JSON_RE = new RegExp(
  [
    String.raw`(?<attr>"(?:[^"\\\n]|\\.)*"(?=\s*:))`,
    String.raw`(?<s>"(?:[^"\\\n]|\\.)*"?)`,
    String.raw`(?<n>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)`,
    String.raw`(?<k>\b(?:true|false|null)\b)`,
  ].join('|'),
  'g',
);

const SQL_RE = new RegExp(
  [
    String.raw`(?<c>--[^\n]*|\/\*[\s\S]*?(?:\*\/|$))`,
    String.raw`(?<s>'(?:[^']|'')*'?)`,
    String.raw`(?<a>:[A-Za-z_][\w$#.]*)`,
    String.raw`(?<n>\b\d+(?:\.\d+)?\b)`,
    String.raw`(?<k>\b(?:${words(`
      select from where and or not null is in exists insert into values update set delete merge using on join
        left right inner outer full cross group by order having union all distinct as case when then else end
      begin declare exception return returning cursor loop for while if elsif commit rollback fetch open close
      create table view index package body procedure function trigger number varchar2 date timestamp boolean
        integer like between offset rows only next first with true false nvl decode sysdate rownum dual out
        nocopy type rowtype raise`)})\b)`,
  ].join('|'),
  'gi',
);

const MD_RE = new RegExp(
  [
    String.raw`(?<s>\`\`\`[\s\S]*?(?:\`\`\`|(?![\s\S]))|\`[^\`\n]+\`)`,
    String.raw`(?<h>^#{1,6}[ \t][^\n]*)`,
    String.raw`(?<b>\*\*[^*\n]+\*\*)`,
    String.raw`(?<k>^[ \t]*(?:[-*+]|\d+\.)(?=[ \t]))`,
    String.raw`(?<f>\[[^\]\n]+\]\([^)\n]+\))`,
    String.raw`(?<c>^[ \t]*>[^\n]*)`,
  ].join('|'),
  'gm',
);

const PATTERNS: Partial<Record<CodeLang, RegExp>> = {
  java: JAVA_RE,
  ts: TS_RE,
  css: CSS_RE,
  json: JSON_RE,
  sql: SQL_RE,
  md: MD_RE,
};

function scan(
  text: string,
  pattern: RegExp,
  out: Segment[],
  special?: (cls: string, token: string, out: Segment[]) => boolean,
): void {
  pattern.lastIndex = 0;
  let last = 0;
  for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
    if (!match[0]) {
      pattern.lastIndex++;
      continue;
    }
    if (match.index > last) push(out, '', text.slice(last, match.index));
    const cls = Object.entries(match.groups ?? {}).find(([, value]) => value !== undefined)?.[0] ?? '';
    if (!special?.(cls, match[0], out)) push(out, cls, match[0]);
    last = pattern.lastIndex;
  }
  if (last < text.length) push(out, '', text.slice(last));
}

const HTML_RE =
  /<!--[\s\S]*?(?:-->|$)|<\/?[A-Za-z][\w:.-]*|\{\{[\s\S]*?(?:\}\}|$)|@(?:if|else|for|switch|case|default|empty|defer|placeholder|loading|let)\b|&#?\w+;/g;
const ATTR_RE = /\s+|\/?>|([^\s=>\/"']+)(?:(\s*=\s*)("[^"]*"?|'[^']*'?|[^\s>"']+))?/y;

// HTML and Angular templates: tags, attributes and bindings, {{ }} and @if/@for blocks.
function htmlSegments(text: string, out: Segment[]): void {
  HTML_RE.lastIndex = 0;
  let last = 0;
  for (let match = HTML_RE.exec(text); match; match = HTML_RE.exec(text)) {
    const token = match[0];
    if (match.index > last) push(out, '', text.slice(last, match.index));
    if (token.startsWith('<!--')) push(out, 'c', token);
    else if (token.startsWith('<')) {
      push(out, 'tag', token);
      let position = HTML_RE.lastIndex;
      while (position < text.length) {
        ATTR_RE.lastIndex = position;
        const attribute = ATTR_RE.exec(text);
        if (!attribute || !attribute[0]) break;
        position = ATTR_RE.lastIndex;
        if (attribute[1] === undefined) {
          if (attribute[0].endsWith('>')) {
            push(out, 'tag', attribute[0]);
            break;
          }
          push(out, '', attribute[0]);
          continue;
        }
        push(out, /^[[(*#@]/.test(attribute[1]) ? 'bind' : 'attr', attribute[1]);
        if (attribute[2]) push(out, '', attribute[2]);
        if (attribute[3]) push(out, 's', attribute[3]);
      }
      HTML_RE.lastIndex = position;
    } else if (token.startsWith('{{')) push(out, 'bind', token);
    else if (token.startsWith('@')) push(out, 'k', token);
    else push(out, 'n', token);
    last = HTML_RE.lastIndex;
  }
  if (last < text.length) push(out, '', text.slice(last));
}

// An inline Angular template (`<div>…`) is highlighted as HTML inside the TypeScript.
function templateLiteral(cls: string, token: string, out: Segment[]): boolean {
  if (cls !== 'tl') return false;
  const closed = token.length > 1 && token.endsWith('`');
  const inner = token.slice(1, closed ? -1 : undefined);
  if (!/<[A-Za-z!/]|@(?:if|for|switch)\b/.test(inner)) {
    push(out, 's', token);
    return true;
  }
  push(out, 's', '`');
  htmlSegments(inner, out);
  if (closed) push(out, 's', '`');
  return true;
}

// One block per line: the line number is a CSS counter, so copying never picks it up.
function highlight(text: string, lang: CodeLang): string {
  if (!text) return '';
  const source = text.replace(/\r\n?/g, '\n');
  const segments: Segment[] = [];
  const pattern = PATTERNS[lang];
  if (lang === 'html') htmlSegments(source, segments);
  else if (pattern) scan(source, pattern, segments, lang === 'ts' ? templateLiteral : undefined);
  else push(segments, '', source);
  const lines: string[] = [];
  let line = '';
  for (const { cls, text: part } of segments) {
    part.split('\n').forEach((piece, index) => {
      if (index > 0) {
        lines.push(line);
        line = '';
      }
      if (piece) line += cls ? `<span class="nm-t-${cls}">${escapeHtml(piece)}</span>` : escapeHtml(piece);
    });
  }
  if (line || !source.endsWith('\n')) lines.push(line);
  return lines.map((content) => `<span class="nm-l"><span class="nm-lc">${content}</span></span>`).join('');
}

@Component({
  selector: 'app-migrator',
  imports: [
    ReactiveFormsModule,
    MigratorAssistant,
    MigratorAiQueue,
    DatePipe,
    NgTemplateOutlet,
    AccordionModule,
    BadgeModule,
    ButtonModule,
    CardModule,
    DialogModule,
    DividerModule,
    FieldsetModule,
    InputNumberModule,
    InputTextModule,
    ListboxModule,
    MessageModule,
    PanelModule,
    ProgressBarModule,
    SelectModule,
    TableModule,
    TabsModule,
    TagModule,
    ToggleSwitchModule,
    ToolbarModule,
    TooltipModule,
  ],
  // Colours come from the Optimus theme (its CSS variables) and the host application. The few
  // local rules only shape the drop zone, the code tree and the code viewer. They are global
  // (the highlighted code arrives as HTML), so every class starts with "nm-".
  encapsulation: ViewEncapsulation.None,
  host: { class: 'block' },
  styles: `
    .nm-drop {
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      gap: 0.375rem;
      padding: 1.75rem 1.25rem;
      text-align: center;
      cursor: pointer;
      border: 1.5px dashed var(--p-content-border-color, #cbd5e1);
      border-radius: var(--p-content-border-radius, 0.5rem);
      transition:
        border-color 0.15s ease,
        background-color 0.15s ease;
    }
    .nm-drop:hover,
    .nm-drop-active {
      border-color: var(--p-primary-color, #3b82f6);
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 6%, transparent);
    }
    .nm-drop:focus-visible {
      outline: 2px solid var(--p-primary-color, #3b82f6);
      outline-offset: 2px;
    }
    .nm-drop[aria-disabled='true'] {
      cursor: progress;
      opacity: 0.6;
    }
    .nm-drop-icon {
      width: 1.75rem;
      height: 1.75rem;
      fill: none;
      stroke: var(--p-primary-color, #3b82f6);
      stroke-width: 1.6;
      stroke-linecap: round;
      stroke-linejoin: round;
    }
    .nm-sources {
      display: flex;
      flex-direction: column;
      gap: 0.375rem;
      margin: 0;
      padding: 0;
      list-style: none;
    }
    .nm-source {
      display: flex;
      align-items: center;
      gap: 0.75rem;
      padding: 0.25rem 0.25rem 0.25rem 0.75rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-survey {
      display: flex;
      flex-direction: column;
      gap: 0.75rem;
      padding: 0.75rem 1rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
      transition:
        border-color 0.15s ease,
        background-color 0.15s ease;
    }
    .nm-survey-on {
      border-color: var(--p-primary-color, #3b82f6);
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 5%, transparent);
    }
    .nm-companions,
    .nm-survey-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.75rem;
    }
    .nm-survey-bar {
      padding: 0.625rem 0.75rem 0.625rem 1rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-deploy {
      display: flex;
      flex-direction: column;
      gap: 0.625rem;
      padding: 0.75rem 1rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-deploy-grid {
      display: grid;
      grid-template-columns: max-content minmax(0, 1fr) max-content;
      align-items: center;
      gap: 0.5rem 0.75rem;
    }
    @media (max-width: 40rem) {
      .nm-deploy-grid {
        grid-template-columns: minmax(0, 1fr) max-content;
      }
      .nm-deploy-grid > .nm-deploy-label {
        grid-column: 1 / -1;
      }
    }
    .nm-deploy-layout {
      display: grid;
      grid-template-columns: max-content minmax(0, 1fr);
      gap: 0.125rem 0.75rem;
      margin: 0;
      font-size: 0.8125rem;
    }
    .nm-deploy-layout dd {
      margin: 0;
      overflow-wrap: anywhere;
    }
    .nm-folders {
      display: flex;
      flex-direction: column;
      min-height: 8rem;
      max-height: 22rem;
      overflow: auto;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-folder {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      width: 100%;
      padding: 0.375rem 0.75rem;
      border: 0;
      border-bottom: 1px solid var(--p-content-border-color, #e2e8f0);
      background: transparent;
      color: inherit;
      font: inherit;
      font-size: 0.875rem;
      text-align: left;
      cursor: pointer;
    }
    .nm-folder:last-child {
      border-bottom: 0;
    }
    .nm-folder:hover,
    .nm-folder:focus-visible {
      background: var(--p-content-hover-background, rgba(100, 116, 139, 0.12));
      outline: none;
    }
    .nm-folder-name {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .nm-actions {
      padding-top: 1rem;
      border-top: 1px solid var(--p-content-border-color, #e2e8f0);
    }
    /* 4.35: the page uses the width of a large monitor, and a narrow screen keeps every control reachable. */
    .nm-shell {
      width: 100%;
      max-width: 100rem;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 1rem;
      padding: 0.75rem;
      box-sizing: border-box;
      min-width: 0;
    }
    @media (min-width: 48rem) {
      .nm-shell {
        padding: 1.5rem;
      }
    }
    .nm-shell .p-tabpanels,
    .nm-shell .p-tabpanel,
    .nm-shell .p-panel-content,
    .nm-shell .p-fieldset-content {
      min-width: 0;
    }
    .nm-scroll {
      width: 100%;
      min-width: 0;
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .nm-row-actions {
      display: flex;
      flex-wrap: wrap;
      justify-content: flex-end;
      align-items: center;
      gap: 0.25rem;
    }
    .nm-toolbar-end {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      justify-content: flex-end;
      gap: 0.5rem;
      min-width: 0;
    }
    .nm-shell .p-toolbar {
      flex-wrap: wrap;
      gap: 0.5rem;
    }
    @media (max-width: 40rem) {
      .nm-hide-sm {
        display: none;
      }
      .nm-shell .p-tablist-tab-list .p-tab {
        padding-inline: 0.75rem;
      }
      .nm-shell .p-dialog {
        width: 100vw !important;
        max-width: 100vw !important;
        max-height: 100dvh !important;
        margin: 0 !important;
        border-radius: 0;
      }
    }

    .nm-summary {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 1.25rem;
    }
    .nm-stat {
      font-size: 0.875rem;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-stat strong {
      margin-right: 0.25rem;
      font-size: 1.125rem;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
      color: var(--p-text-color, inherit);
    }

    .nm-log {
      overflow: hidden;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-log-head {
      display: flex;
      align-items: center;
      gap: 0.25rem;
      padding: 0.375rem 0.5rem 0.375rem 1rem;
      border-bottom: 1px solid var(--p-content-border-color, #e2e8f0);
    }
    .nm-log-text {
      max-height: 22rem;
      margin: 0;
      padding: 0.75rem 1rem;
      overflow: auto;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.75rem;
      line-height: 1.5;
    }

    .nm-tree {
      overflow: hidden;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
    }
    .nm-tree-head {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.75rem;
      padding: 0.625rem 0.75rem 0.625rem 1rem;
      border-bottom: 1px solid var(--p-content-border-color, #e2e8f0);
      background: color-mix(in srgb, var(--p-text-color, #1e293b) 3%, transparent);
    }
    .nm-tree-filter {
      width: 15rem;
      max-width: 100%;
    }
    .nm-tree-rows {
      max-height: 28rem;
      overflow-y: auto;
      padding: 0.25rem 0;
    }
    .nm-tree-empty {
      padding: 1.5rem 1rem;
      font-size: 0.875rem;
      text-align: center;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-row {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      width: 100%;
      min-height: 2.25rem;
      padding: 0.25rem 1rem 0.25rem 0.75rem;
      border: 0;
      background: none;
      color: inherit;
      font: inherit;
      text-align: left;
      cursor: pointer;
    }
    .nm-row:hover {
      background: var(--p-content-hover-background, color-mix(in srgb, var(--p-text-color, #1e293b) 5%, transparent));
    }
    .nm-row:focus-visible {
      outline: 2px solid var(--p-primary-color, #3b82f6);
      outline-offset: -2px;
    }
    .nm-row-group {
      font-weight: 600;
    }
    .nm-row-group:not(:first-child) {
      margin-top: 0.25rem;
      border-top: 1px solid var(--p-content-border-color, #e2e8f0);
    }
    .nm-row-name {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .nm-row-note {
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      font-size: 0.8125rem;
      font-weight: 400;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-row-meta {
      margin-left: auto;
      padding-left: 1rem;
      white-space: nowrap;
      font-size: 0.8125rem;
      font-weight: 400;
      font-variant-numeric: tabular-nums;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-chevron {
      flex: none;
      width: 1rem;
      height: 1rem;
      fill: none;
      stroke: currentColor;
      stroke-width: 1.75;
      stroke-linecap: round;
      stroke-linejoin: round;
      opacity: 0.7;
      transition: transform 0.15s ease;
    }
    .nm-row[aria-expanded='true'] .nm-chevron {
      transform: rotate(90deg);
    }
    .nm-folder {
      flex: none;
      width: 1.125rem;
      height: 1.125rem;
      fill: color-mix(in srgb, var(--p-primary-color, #3b82f6) 18%, transparent);
      stroke: color-mix(in srgb, var(--p-primary-color, #3b82f6) 80%, var(--p-text-color, #1e293b));
      stroke-width: 1.5;
      stroke-linejoin: round;
    }
    .nm-file-icon {
      flex: none;
      width: 1rem;
      height: 1.125rem;
      margin-left: 1.5rem;
      fill: none;
      stroke: var(--p-text-muted-color, #64748b);
      stroke-width: 1.5;
      stroke-linejoin: round;
    }
    .nm-file-icon[data-lang='java'] {
      stroke: color-mix(in srgb, var(--p-orange-500, #f97316) 85%, var(--p-text-color, #1e293b));
    }
    .nm-file-icon[data-lang='ts'] {
      stroke: color-mix(in srgb, var(--p-blue-500, #3b82f6) 85%, var(--p-text-color, #1e293b));
    }
    .nm-file-icon[data-lang='html'] {
      stroke: color-mix(in srgb, var(--p-pink-500, #ec4899) 85%, var(--p-text-color, #1e293b));
    }
    .nm-file-icon[data-lang='json'],
    .nm-file-icon[data-lang='css'] {
      stroke: color-mix(in srgb, var(--p-amber-500, #f59e0b) 85%, var(--p-text-color, #1e293b));
    }
    .nm-file-icon[data-lang='md'] {
      stroke: color-mix(in srgb, var(--p-teal-500, #14b8a6) 85%, var(--p-text-color, #1e293b));
    }

    .nm-viewer-bar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.5rem 0.75rem;
    }
    .nm-crumbs {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.375rem;
      font-size: 0.875rem;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-crumbs > span:first-child {
      font-weight: 600;
      color: var(--p-text-color, inherit);
    }
    .nm-crumb-sep {
      opacity: 0.5;
    }
    .nm-code {
      min-height: 12rem;
      max-height: calc(100vh - 17rem);
      overflow: auto;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
      background: color-mix(in srgb, var(--p-text-color, #1e293b) 3%, var(--p-content-background, #ffffff));
    }
    .p-dialog-maximized .nm-code {
      max-height: calc(100vh - 11rem);
    }
    .nm-code:focus-visible {
      outline: 2px solid var(--p-primary-color, #3b82f6);
      outline-offset: 2px;
    }
    .nm-code pre {
      min-width: max-content;
      margin: 0;
      padding: 0.75rem 0;
      counter-reset: nm-line;
      font-family: ui-monospace, 'Cascadia Code', 'JetBrains Mono', 'Fira Code', Consolas, 'SF Mono', Menlo, monospace;
      font-size: 0.8125rem;
      line-height: 1.65;
      tab-size: 4;
      color: var(--p-text-color, inherit);
    }
    .nm-code-wrap pre {
      min-width: 0;
    }
    .nm-code code {
      display: block;
      padding: 0;
      border: 0;
      background: none;
      font: inherit;
      color: inherit;
    }
    .nm-l {
      display: grid;
      grid-template-columns: var(--nm-gutter, 4ch) minmax(0, 1fr);
      column-gap: 1.25rem;
      padding: 0 1.5rem 0 0.75rem;
      counter-increment: nm-line;
    }
    .nm-l::before {
      content: counter(nm-line);
      text-align: right;
      color: var(--p-text-muted-color, #94a3b8);
      opacity: 0.7;
      user-select: none;
    }
    .nm-l:hover {
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 7%, transparent);
    }
    .nm-lc {
      white-space: pre;
    }
    .nm-code-wrap .nm-lc {
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .nm-t-c {
      font-style: italic;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-t-k {
      color: color-mix(in srgb, var(--p-purple-500, #8b5cf6) 80%, var(--p-text-color, #1e293b));
    }
    .nm-t-s {
      color: color-mix(in srgb, var(--p-green-600, #16a34a) 82%, var(--p-text-color, #1e293b));
    }
    .nm-t-n,
    .nm-t-cst {
      color: color-mix(in srgb, var(--p-orange-500, #f97316) 82%, var(--p-text-color, #1e293b));
    }
    .nm-t-a {
      color: color-mix(in srgb, var(--p-amber-600, #d97706) 85%, var(--p-text-color, #1e293b));
    }
    .nm-t-t {
      color: color-mix(in srgb, var(--p-sky-600, #0284c7) 82%, var(--p-text-color, #1e293b));
    }
    .nm-t-f {
      color: color-mix(in srgb, var(--p-blue-600, #2563eb) 65%, var(--p-text-color, #1e293b));
    }
    .nm-t-tag {
      color: color-mix(in srgb, var(--p-pink-600, #db2777) 80%, var(--p-text-color, #1e293b));
    }
    .nm-t-attr {
      color: color-mix(in srgb, var(--p-orange-600, #ea580c) 80%, var(--p-text-color, #1e293b));
    }
    .nm-t-bind {
      color: color-mix(in srgb, var(--p-teal-600, #0d9488) 85%, var(--p-text-color, #1e293b));
    }
    .nm-t-h {
      font-weight: 600;
      color: var(--p-primary-color, inherit);
    }
    .nm-t-b {
      font-weight: 600;
    }
    .nm-windows {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(min(100%, 22rem), 1fr));
      gap: 0.75rem;
    }
    .nm-window {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
      padding: 0.75rem;
      cursor: pointer;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: var(--p-content-border-radius, 0.5rem);
      transition:
        border-color 0.15s ease,
        background-color 0.15s ease;
    }
    .nm-window:hover {
      border-color: color-mix(in srgb, var(--p-primary-color, #3b82f6) 45%, var(--p-content-border-color, #e2e8f0));
    }
    .nm-window-on {
      border-color: var(--p-primary-color, #3b82f6);
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 6%, transparent);
    }
    .nm-window:has(.nm-check:focus-visible) {
      outline: 2px solid var(--p-primary-color, #3b82f6);
      outline-offset: 2px;
    }
    .nm-window-head {
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .nm-check {
      flex: none;
      width: 1.125rem;
      height: 1.125rem;
      margin: 0;
      cursor: pointer;
      accent-color: var(--p-primary-color, #3b82f6);
    }
    .nm-window-count {
      flex: none;
      white-space: nowrap;
      font-size: 0.8125rem;
      font-variant-numeric: tabular-nums;
      color: var(--p-text-muted-color, #64748b);
    }
    .nm-window-preview {
      width: 100%;
      height: 13rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0);
      border-radius: calc(var(--p-content-border-radius, 0.5rem) - 2px);
      background: #ffffff;
    }
    @media (prefers-reduced-motion: reduce) {
      .nm-drop,
      .nm-window,
      .nm-chevron {
        transition: none;
      }
    }
  `,
  template: `
    <div class="nm-shell">
      <p-toolbar>
        <ng-template #start>
          <div class="flex flex-col">
            <span class="text-lg font-semibold">FRM Migration Studio</span>
            <span class="text-xs opacity-70">Oracle Forms → Angular + Java</span>
          </div>
        </ng-template>
        <ng-template #end>
          <div class="nm-toolbar-end">
            @if (online()) {
              <!-- The local model's queue from every tab: what runs, how long, how fast. -->
              <app-migrator-ai-queue [apiBase]="base()" [compact]="true" (open)="switchView('assistant')" />
            }
            <p-tag [value]="connecting() ? 'Kapcsolódás…' : online() ? 'Kapcsolódva' : 'Nincs kapcsolat'" [severity]="online() ? 'success' : 'warn'" />
            <span [pTooltip]="help.connect" tooltipPosition="bottom"><p-button label="Kapcsolat" size="small" severity="secondary" variant="text" [disabled]="assistantBusy() || assistantDirty()" (onClick)="settingsOpen.set(true)" /></span>
          </div>
        </ng-template>
      </p-toolbar>

      @if (connectionError()) {
        <p-message severity="warn"
        ><span class="flex flex-wrap items-center gap-3"
        ><span>{{ connectionError() }}</span>
            <p-button
              label="Kapcsolat beállítása"
              size="small"
              severity="warn"
              variant="outlined"
              (onClick)="settingsOpen.set(true)" /></span
        ></p-message>
      }
      @if (error()) {
        <p-message severity="error" [closable]="true" (onClose)="error.set('')">{{
            error()
          }}</p-message>
      }
      @if (notice()) {
        <p-message severity="success" [closable]="true" (onClose)="notice.set('')">{{
            notice()
          }}</p-message>
      }

      <p-panel [header]="aiHeader()" [toggleable]="true" [collapsed]="aiCollapsed()" (collapsedChange)="aiCollapsed.set($event)">
        <div class="flex flex-col gap-3">
          <div class="grid gap-4 md:grid-cols-2">
            <label class="flex flex-col gap-2"><span class="font-medium">Ollama URL · az AI-t futtató laptop</span>
              <input pInputText [formControl]="form.controls.ollama_url" [readonly]="assistantBusy()"
                placeholder="http://192.168.1.50:11434" aria-label="Közös Ollama URL" />
            </label>
            <label class="flex flex-col gap-2"><span class="font-medium">Modell pontos neve</span>
              <input pInputText [formControl]="form.controls.ollama_model" [readonly]="assistantBusy()"
                placeholder="Az Ollamába betöltött modell neve" aria-label="Közös Ollama modell" />
            </label>
          </div>
          <p class="m-0 text-sm opacity-70">A migrátor a PC-ről hívja az AI-t a laptop belső IP-címén.
            Ugyanezt a kapcsolatot használja a generálás és a Java asszisztens. Ollama alapcím szükséges, például http://192.168.1.50:11434.
            A portot is add meg; a /api/chat útvonalat a program teszi hozzá. Mentéskor ellenőrizzük a szervert és a modell nevét.</p>
          @if (ollamaChatEndpoint()) { <p class="m-0 break-all text-xs opacity-70">Java-átírás végpontja: POST {{ ollamaChatEndpoint() }}</p> }
          <div><p-button label="AI-kapcsolat mentése" severity="secondary" variant="outlined" size="small"
            [loading]="checkingAi()" [disabled]="assistantBusy() || checkingAi()" (onClick)="saveAiConnection()" /></div>
        </div>
      </p-panel>
      <p-tabs [value]="view()" (valueChange)="switchView($event)">
        <p-tablist>
          <p-tab value="generate">Generálás</p-tab>
          <p-tab value="jobs"
          ><span class="flex items-center gap-2"
          >Feladatok
            @if (waitingJobs().length) {
              <p-badge [value]="waitingJobs().length" severity="warn" />
            } @else if (jobs().length) {
              <p-badge
                [value]="activeJobs().length || jobs().length"
                [severity]="activeJobs().length ? 'info' : 'secondary'"
              />
            }</span
          ></p-tab>
          <p-tab value="assistant">Java asszisztens</p-tab>
        </p-tablist>
        <p-tabpanels>
          <!-- Generation -------------------------------------------------------->
          <p-tabpanel value="generate">
            <form
              [formGroup]="form"
              (ngSubmit)="submit()"
              class="flex flex-col gap-5"
              (dragover)="$event.preventDefault(); dragging.set(true)"
              (dragleave)="dragging.set(false)"
              (drop)="drop($event)"
            >
              <input
                #sourceInput
                type="file"
                class="hidden"
                accept=".fmb,.xml,.pld,.pll"
                multiple
                (change)="onSources($event)"
              />

              <p-fieldset legend="1. Forrás">
                <div class="flex flex-col gap-3">
                  <div
                    class="nm-drop"
                    role="button"
                    tabindex="0"
                    [class.nm-drop-active]="dragging()"
                    [attr.aria-disabled]="uploading()"
                    (click)="browse(sourceInput)"
                    (keydown.enter)="browse(sourceInput)"
                    (keydown.space)="$event.preventDefault(); browse(sourceInput)"
                  >
                    <svg class="nm-drop-icon" viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M12 15V4m0 0L8 8m4-4 4 4M5 15v3a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-3" />
                    </svg>
                    <span class="font-medium">{{
                        dragging()
                          ? 'Engedd el a fájlokat'
                          : files().length
                            ? 'További formok hozzáadása'
                            : 'Húzd ide a formokat, vagy kattints a tallózáshoz'
                      }}</span>
                    <span class="text-sm opacity-70 cursor-help" [pTooltip]="sourceHelp()"
                    >.fmb vagy Forms2XML .xml, egyszerre több is; mellé a csatolt PL/SQL-könyvtárak (.pld)
                      <span class="opacity-60">ⓘ</span></span
                    >
                  </div>
                  @if (files().length) {
                    <ul class="nm-sources">
                      @for (source of files(); track source.name) {
                        <li class="nm-source">
                          <p-tag
                            [value]="source.name.toLowerCase().endsWith('.fmb') ? 'FMB' : 'XML'"
                            severity="secondary"
                          />
                          <span class="min-w-0 flex-1 truncate font-medium">{{ source.name }}</span>
                          <span class="whitespace-nowrap text-sm opacity-60">{{
                              bytes(source.size)
                            }}</span>
                          <p-button
                            label="Eltávolítás"
                            size="small"
                            severity="secondary"
                            variant="text"
                            [disabled]="uploading()"
                            (onClick)="removeSource(source)"
                          />
                        </li>
                      }
                    </ul>
                    <div class="flex flex-wrap items-center gap-2">
                      @if (files().length > 1) {
                        <p-message severity="info" size="small"
                        ><span class="cursor-help" [pTooltip]="help.batch"
                        >Tömeges futtatás: {{ files().length }} form, formonként külön feladat
                            és közös összesítő. <span class="opacity-60">ⓘ</span></span
                        ></p-message
                        >
                      }
                      <span class="flex-1"></span>
                      <p-button
                        label="Mind törlése"
                        size="small"
                        severity="secondary"
                        variant="text"
                        [disabled]="uploading()"
                        (onClick)="clearSources()"
                      />
                    </div>
                  }
                  @if (pld().length) {
                    <ul class="nm-sources">
                      @for (library of pld(); track library.name) {
                        <li class="nm-source">
                          <p-tag value="PLD" severity="secondary" />
                          <span class="min-w-0 flex-1 truncate font-medium">{{ library.name }}</span>
                          <span class="whitespace-nowrap text-sm opacity-60">{{
                              bytes(library.size)
                            }}</span>
                          <p-button
                            label="Eltávolítás"
                            size="small"
                            severity="secondary"
                            variant="text"
                            [disabled]="uploading()"
                            (onClick)="removeLibrary(library)"
                          />
                        </li>
                      }
                    </ul>
                  }
                  @if (fmbWithoutExporter()) {
                    <p-message severity="warn" size="small"
                    >Az Oracle exportáló nem érhető el a backenden: tölts fel előre exportált
                      XML-t.</p-message
                    >
                  }
                  <div class="nm-survey" [class.nm-survey-on]="survey()">
                    <label class="flex cursor-pointer items-start gap-3">
                      <p-toggleswitch [formControl]="surveyControl" />
                      <span class="flex flex-col gap-1">
                        <span class="font-medium">Felmérés</span>
                        <span class="text-sm opacity-70"
                        >Nem a kódért fut: megméri, mi tiltja a generált végpontokat, és
                          megosztható riportot ad (FELMERES_HU.md). Nem kér AWU_AZON-t, és a több
                          ablakos formoknál sem kérdez.</span
                        >
                      </span>
                    </label>
                    @if (survey()) {
                      <div class="nm-companions">
                        <input
                          #olbInput
                          type="file"
                          accept=".xml"
                          multiple
                          hidden
                          (change)="onCompanions($event, 'olb')"
                        />
                        <input
                          #schemaInput
                          type="file"
                          accept=".json"
                          hidden
                          (change)="onFile($event, 'schema')"
                        />
                        <p-button
                          label="OLB-k hozzáadása"
                          size="small"
                          severity="secondary"
                          variant="outlined"
                          (onClick)="olbInput.click()"
                        />
                        <p-button
                          label="schema.json"
                          size="small"
                          severity="secondary"
                          variant="outlined"
                          (onClick)="schemaInput.click()"
                        />
                        @if (companionSummary()) {
                          <span class="min-w-0 truncate text-sm opacity-70">{{ companionSummary() }}</span>
                          <p-button
                            label="Törlés"
                            size="small"
                            severity="secondary"
                            variant="text"
                            (onClick)="clearCompanions()"
                          />
                        }
                      </div>
                      <span class="text-xs opacity-70"
                      >Az OLB-exportok (…_olb.xml) akkor kellenek, ha a formok könyvtárra
                        hivatkoznak; a schema.json elhagyható.</span
                      >
                    }
                  </div>
                </div>
              </p-fieldset>

              @if (!survey()) {
                <p-fieldset legend="2. Adatok">
                  <div class="flex flex-col gap-5">
                    <div class="grid gap-4 md:grid-cols-2">
                      @if (files().length <= 1) {
                        <label class="flex flex-col gap-2"
                        ><span class="text-sm font-medium cursor-help" [pTooltip]="help.module"
                        >Modulnév <span class="opacity-60">ⓘ</span></span
                        >
                          <input
                            pInputText
                            formControlName="module"
                            placeholder="pl. fadlek"
                            class="w-full"
                          /></label>
                        <label class="flex flex-col gap-2"
                        ><span class="text-sm font-medium">AWU_AZON</span>
                          <input
                            pInputText
                            name="AWU_AZON"
                            formControlName="AWU_AZON"
                            inputmode="numeric"
                            maxlength="30"
                            placeholder="pl. 1234"
                            class="w-full"
                            [attr.aria-invalid]="!!awuBlocker()"
                          />
                        </label>
                      } @else {
                        <div class="flex flex-col gap-2">
                          <span class="text-sm font-medium">Modulnév</span>
                          <span class="text-sm opacity-70"
                          >Tömeges futtatásnál formonként a forrás technikai nevéből készül.</span
                          >
                        </div>
                        @for (source of files(); track source.name) {
                          <label class="flex flex-col gap-2"
                          ><span class="text-sm font-medium">AWU_AZON · {{ source.name }}</span>
                            <input
                              pInputText
                              name="AWU_AZON"
                              inputmode="numeric"
                              maxlength="30"
                              placeholder="pl. 1234"
                              class="w-full"
                              [disabled]="uploading()"
                              [value]="batchAwu()[source.name] ?? ''"
                              (input)="setBatchAwu(source, $event)"
                            /></label>
                        }
                      }
                    </div>

                    @if (files().length <= 1) {
                      <div class="flex flex-col gap-2">
                        <span class="text-sm font-medium cursor-help" [pTooltip]="help.folders"
                        >Célmappák a projektben <span class="opacity-60">ⓘ</span></span
                        >
                        <span class="text-xs opacity-70"
                        >A modul saját mappái: a fájlok pontosan ide kerülnek, a Java-mappák útvonalából lesz a
                          csomag. Üresen hagyva a fájlok csak a ZIP-ben vannak.</span
                        >
                        <div class="nm-deploy-grid">
                          @for (part of parts; track part.key) {
                            <span class="nm-deploy-label text-sm font-medium">{{ part.label }}</span>
                            <div class="flex min-w-0 flex-col gap-1">
                              <input
                                pInputText
                                class="min-w-0"
                                [placeholder]="part.hint"
                                [attr.aria-label]="part.label + ' célmappa'"
                                [formControl]="generateFolders[part.key]"
                                (input)="foldersFromMemory = false"
                              />
                              @if (part.key !== 'frontend' && generateFolders[part.key].value.trim()) {
                                <small [class.text-red-600]="!packageOf(generateFolders[part.key].value)" class="opacity-80"
                                >{{ packageText(generateFolders[part.key].value) }}</small
                                >
                              }
                            </div>
                            <p-button
                              label="Tallózás…"
                              size="small"
                              severity="secondary"
                              variant="outlined"
                              [disabled]="!!picking() || uploading()"
                              (onClick)="browseFolder('generate', part.key)"
                            />
                          }
                        </div>
                      </div>
                    }

                    <!--<p-accordion [multiple]="true">
                      <p-accordion-panel value="target"><p-accordion-header>Célkörnyezet és Java</p-accordion-header><p-accordion-content>

                      </p-accordion-content></p-accordion-panel>
                      <p-accordion-panel value="formblock"><p-accordion-header>Céges FormBlock és importok</p-accordion-header><p-accordion-content>
                        <div class="mb-4 max-w-md"><div class="flex flex-col gap-2"><span class="text-sm font-medium cursor-help" [pTooltip]="help.buttonLabel">Gombfelirat property <span class="opacity-60">ⓘ</span></span>
                          <p-select formControlName="screen_button_label_property" [options]="buttonLabels" optionLabel="label" optionValue="value" class="w-full" /></div></div>
                        <div class="grid gap-4 md:grid-cols-2">
                          <label class="flex flex-col gap-2"><span class="text-sm font-medium">Structure típusnév</span><input pInputText formControlName="form_block_structure_type" class="w-full" /></label>
                          <div class="flex flex-col gap-2"><span class="text-sm font-medium">Elrendezési rács</span><p-select formControlName="layout_columns" [options]="gridSizes" optionLabel="label" optionValue="value" class="w-full" /></div>
                          <ng-container formGroupName="html_selectors">
                            <label class="flex flex-col gap-2"><span class="text-sm font-medium">FormBlock selector</span><input pInputText formControlName="form_block" class="w-full" /></label>
                            <label class="flex flex-col gap-2"><span class="text-sm font-medium">Táblázat selector</span><input pInputText formControlName="table" class="w-full" /></label>
                          </ng-container>
                          <ng-container formGroupName="form_block_types">
                            @for (type of blockTypes; track type.control) {
                              <label class="flex flex-col gap-2"><span class="text-sm font-medium">{{ type.label }}</span><input pInputText [formControlName]="type.control" class="w-full" /></label>
                            }
                          </ng-container>
                          <ng-container formGroupName="endpoint_names">
                            @for (endpoint of endpointNames; track endpoint.control) {
                              <label class="flex flex-col gap-2"><span class="text-sm font-medium">{{ endpoint.label }}</span><input pInputText [formControlName]="endpoint.control" class="w-full" /></label>
                            }
                          </ng-container>
                          <div class="flex items-center gap-3 text-sm md:col-span-2" [pTooltip]="help.imports"><p-toggleswitch formControlName="emit_imports" /><span>Importok generálása</span></div>
                          @if (emitImports()) {
                            @for (path of importPaths; track path.control) {
                              <label class="flex flex-col gap-2"><span class="text-sm font-medium">{{ path.label }}</span><input pInputText [formControlName]="path.control" class="w-full" /></label>
                            }
                          }
                        </div>
                      </p-accordion-content></p-accordion-panel>
                    </p-accordion>-->
                  </div>
                </p-fieldset>
              }

              <p class="m-0 text-sm opacity-70">A generálás a triggerekhez csak a Java-keretet és a kikommentezett Forms-kódot
                készíti el. Utána a helyi AI magától nekiáll (egyszerre egy kérés, a laptop kímélése miatt): amelyik triggernél
                a táblaszerkezet is kell, az a tábla mentése után indul. Az állapot a Java asszisztens fülön látszik.</p>
              @if (formInvalid() && form.touched) {
                <p-message severity="error" size="small"
                >Ellenőrizd a jelölt mezőket: a modulnév camelCase vagy kötőjeles lehet, az URL-ek
                  http(s)://-sel kezdődnek.</p-message
                >
              }

              @if (batchProgress(); as progress) {
                <div class="flex items-center gap-3">
                  <div class="flex flex-1 flex-col gap-2">
                    <span class="text-sm font-medium">{{
                        progress.waiting
                          ? 'Várakozás szabad helyre a feladatsorban…'
                          : 'Feltöltés: ' + progress.done + ' / ' + progress.total
                      }}</span>
                    <p-progressbar
                      [value]="progress.total ? (100 * progress.done) / progress.total : 0"
                      [showValue]="false"
                      [style]="{ height: '6px' }"
                    />
                  </div>
                  @if (uploading()) {
                    <p-button
                      label="Feltöltés leállítása"
                      size="small"
                      severity="secondary"
                      variant="outlined"
                      (onClick)="stopBatch()"
                    />
                  }
                </div>
                @for (failure of progress.failed; track failure) {
                  <p-message severity="error" size="small">{{ failure }}</p-message>
                }
              }

              <div class="nm-actions flex flex-wrap items-center justify-end gap-2">
                <span [pTooltip]="help.save"
                ><p-button
                  label="Beállítások mentése"
                  severity="secondary"
                  variant="text"
                  (onClick)="savePreferences()"
                /></span>
                <span [pTooltip]="generateHint()"
                ><p-button
                  type="submit"
                  [label]="submitLabel()"
                  [loading]="uploading()"
                  [disabled]="!!generateBlocker()"
                /></span>
              </div>
            </form>
          </p-tabpanel>

          <!-- Jobs -------------------------------------------------------------->
          <p-tabpanel value="jobs">
            <div class="flex flex-col gap-4">
              <div class="flex flex-wrap items-center justify-between gap-2">
                <div class="flex flex-wrap items-center gap-2">
                  <p-tag [value]="jobs().length + ' összesen'" severity="secondary" />
                  <p-tag [value]="completedCount() + ' kész'" severity="success" />
                  @if (activeJobs().length) {
                    <p-tag [value]="activeJobs().length + ' folyamatban'" severity="info" />
                  }
                  @if (waitingJobs().length) {
                    <p-tag [value]="waitingJobs().length + ' döntésre vár'" severity="warn" />
                  }
                </div>
                <div class="flex items-center gap-2">
                  <p-select
                    [formControl]="filterControl"
                    [options]="filters"
                    optionLabel="label"
                    optionValue="value"
                    size="small"
                    ariaLabel="Feladatszűrő"
                  />
                  <span [pTooltip]="help.refresh"
                  ><p-button
                    label="Frissítés"
                    size="small"
                    severity="secondary"
                    variant="outlined"
                    [disabled]="!online()"
                    (onClick)="refreshJobs()"
                  /></span>
                </div>
              </div>
              @if (batches().length) {
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium cursor-help" [pTooltip]="help.batchReport"
                  >Tömeges futtatások <span class="opacity-60">ⓘ</span></span
                  >
                  @for (batch of batches(); track batch.id) {
                    <p-button
                      size="small"
                      severity="secondary"
                      variant="outlined"
                      (onClick)="openBatch(batch.id)"
                      [label]="
                        (batch.created | date: 'MM.dd. HH:mm') +
                        ' · ' +
                        batch.completed +
                        '/' +
                        batch.total +
                        ' kész' +
                        (batch.waiting ? ' · ' + batch.waiting + ' döntésre vár' : '') +
                        (batch.failed ? ' · ' + batch.failed + ' hibás' : '')
                      "
                    />
                  }
                </div>
              }
              <div class="nm-scroll">
              <p-table
                [value]="filteredJobs()"
                dataKey="id"
                size="small"
                [rowHover]="true"
                [paginator]="filteredJobs().length > 12"
                [rows]="12"
                [tableStyle]="{ 'min-width': '20rem' }"
              >
                <ng-template #header>
                  <tr>
                    <th>Forrás</th>
                    <th>Állapot</th>
                    <th class="nm-hide-sm">Létrehozva</th>
                    <th class="text-right">Művelet</th>
                  </tr>
                </ng-template>
                <ng-template #body let-job>
                  <tr class="cursor-pointer" (click)="openJob(job)">
                    <td>
                      <div class="max-w-80 truncate font-medium">
                        {{ job.module ?? job.filename }}
                      </div>
                      <div class="max-w-80 truncate text-xs opacity-70">
                        {{ job.filename }}
                        @if (job.batch) {
                          · tömeges
                        }
                      </div>
                    </td>
                    <td>
                      <p-tag
                        [value]="statusLabel(job)"
                        [severity]="statusSeverity(job)"
                        [pTooltip]="terminal(job) ? '' : phaseLabel(job.phase)"
                      />
                    </td>
                    <td class="nm-hide-sm whitespace-nowrap text-sm">
                      {{ job.created_at | date: 'MM.dd. HH:mm' }}
                    </td>
                    <td class="text-right" (click)="$event.stopPropagation()"><div class="nm-row-actions">
                      @if (job.status === 'completed') {
                        <p-button label="Java asszisztens" size="small" severity="secondary" variant="text"
                          [disabled]="(assistantBusy() || assistantDirty()) && assistantJobId() !== job.id"
                          (onClick)="openAssistant(job)" />
                        <span [pTooltip]="help.all"
                        ><p-button
                          label="Letöltés"
                          size="small"
                          variant="outlined"
                          [disabled]="!!downloading()"
                          (onClick)="download(job, 'all')"
                        /></span>
                      } @else if (job.status === 'needs_input') {
                        <span [pTooltip]="help.question"
                        ><p-button
                          [label]="
                              job.question?.kind === 'windows'
                                ? 'Ablakok kiválasztása'
                                : 'Fő képernyő kiválasztása'
                            "
                          size="small"
                          severity="warn"
                          (onClick)="openJob(job)"
                        /></span>
                      } @else if (!terminal(job)) {
                        <span [pTooltip]="help.cancel"
                        ><p-button
                          label="Leállítás"
                          size="small"
                          severity="secondary"
                          variant="text"
                          [disabled]="job.phase === 'cancelling'"
                          (onClick)="cancel(job)"
                        /></span>
                      }
                    </div></td>
                  </tr>
                </ng-template>
                <ng-template #emptymessage>
                  <tr>
                    <td colspan="4" class="py-10 text-center text-sm opacity-70">
                      {{
                        jobs().length
                          ? 'Nincs ilyen állapotú feladat.'
                          : 'Még nincs feladat. Indíts egy generálást a Generálás fülön.'
                      }}
                    </td>
                  </tr>
                </ng-template>
              </p-table>
              </div>
            </div>
          </p-tabpanel>
          <p-tabpanel value="assistant">
            <app-migrator-assistant [apiBase]="base()" [jobs]="jobs()" [openJobId]="assistantJobId()"
              [ollamaUrl]="form.controls.ollama_url" [ollamaModel]="form.controls.ollama_model"
              (busyChange)="assistantBusy.set($event)" (dirtyChange)="assistantDirty.set($event)"
              (moduleSelected)="assistantJobId.set($event)" (moduleUpdated)="assistantUpdated($event)" />
          </p-tabpanel>
        </p-tabpanels>
      </p-tabs>
    </div>

    <!-- Job details --------------------------------------------------------------->
    <p-dialog
      [(visible)]="detailOpen"
      [pt]="{ header: 'h-[1rem]' }"
      [modal]="true"
      [draggable]="false"
      [dismissableMask]="true"
      [style]="{ width: 'min(72rem, 96vw)' }"
      [header]="selectedJob()?.module ?? selectedJob()?.filename ?? 'Feladat'"
    >
      @if (selectedJob(); as job) {
        <div class="flex flex-col gap-4">
          @if (job.status === 'needs_input' && job.question?.kind === 'windows') {
            <p-panel header="Mely ablakok készüljenek el?">
              <div class="flex flex-col gap-4">
                <p class="m-0 text-sm">
                  A(z) <b>{{ job.filename }}</b> több ablakból áll. Jelöld ki, melyikből készüljön
                  képernyő; a kihagyott ablakokból (például a Forms segédablakaiból) semmi nem
                  generálódik. Az előnézet egyszerűsített.
                </p>
                <div class="flex flex-wrap items-center gap-2">
                  <span class="text-sm font-medium"
                  >{{ chosenWindows().length }} / {{ job.question.choices.length }} ablak
                    kijelölve</span
                  >
                  <span class="flex-1"></span>
                  <p-button
                    label="Mind"
                    size="small"
                    severity="secondary"
                    variant="text"
                    [disabled]="answering()"
                    (onClick)="chooseAllWindows(job)"
                  />
                  <p-button
                    label="Egyik sem"
                    size="small"
                    severity="secondary"
                    variant="text"
                    [disabled]="answering()"
                    (onClick)="chosenWindows.set([])"
                  />
                </div>
                <div class="nm-windows">
                  @for (choice of job.question.choices; track choice.name) {
                    <label class="nm-window" [class.nm-window-on]="chosenWindows().includes(choice.name)">
                      <span class="nm-window-head">
                        <input
                          type="checkbox"
                          class="nm-check"
                          [checked]="chosenWindows().includes(choice.name)"
                          [disabled]="answering()"
                          (change)="toggleWindow(choice.name)"
                        />
                        <span class="flex min-w-0 flex-1 flex-col">
                          <span class="truncate font-semibold">{{ choice.title || choice.name }}</span>
                          @if (choice.title && choice.title !== choice.name) {
                            <span class="truncate text-xs opacity-70">{{ choice.name }}</span>
                          }
                        </span>
                        <span class="nm-window-count">{{ choice.items ?? 0 }} mező</span>
                      </span>
                      @if (choice.blocks.length) {
                        <span class="text-xs opacity-70">Blokkok: {{ choice.blocks.join(', ') }}</span>
                      }
                      @if (choice.preview) {
                        <iframe
                          class="nm-window-preview"
                          sandbox=""
                          title="Az ablak egyszerűsített előnézete"
                          [srcdoc]="windowPreview(job, choice)"
                        ></iframe>
                      }
                    </label>
                  }
                </div>
                <div class="flex justify-end">
                  <p-button
                    label="Generálás a kijelölt ablakokkal"
                    [loading]="answering()"
                    [disabled]="!chosenWindows().length || answering()"
                    (onClick)="answerWindows(job)"
                  />
                </div>
              </div>
            </p-panel>
          } @else if (job.status === 'needs_input' && job.question) {
            <p-panel header="Melyik legyen a fő képernyő?">
              <div class="flex flex-col gap-3">
                <span class="text-sm cursor-help" [pTooltip]="help.question"
                >A(z) <b>{{ job.filename }}</b> több képernyőből áll. A kiválasztott lesz a fő
                  képernyő, a többi párbeszédablak; a generálás ezután folytatódik.
                  <span class="opacity-60">ⓘ</span></span
                >
                <div class="flex flex-wrap items-center gap-2">
                  <p-select
                    [formControl]="windowControl"
                    [options]="windowChoices()"
                    optionLabel="label"
                    optionValue="value"
                    placeholder="Válassz képernyőt"
                    class="min-w-72 flex-1"
                  />
                  <p-button
                    label="Tovább"
                    [loading]="answering()"
                    [disabled]="!windowControl.value || answering()"
                    (onClick)="answer(job)"
                  />
                </div>
              </div>
            </p-panel>
          } @else if (!terminal(job)) {
            <div class="flex items-center gap-3">
              <div class="flex flex-1 flex-col gap-2">
                <span class="text-sm font-medium">{{ phaseLabel(job.phase) }}</span
                ><p-progressbar mode="indeterminate" [style]="{ height: '4px' }" />
              </div>
              <span [pTooltip]="help.cancel"
              ><p-button
                label="Leállítás"
                size="small"
                severity="secondary"
                variant="outlined"
                [disabled]="job.phase === 'cancelling'"
                (onClick)="cancel(job)"
              /></span>
            </div>
          }
          @if (job.error) {
            <p-message severity="error">{{ job.error }}</p-message>
          }
          @if (job.status === 'failed' || job.status === 'interrupted') {
            <!-- The log tab is hidden: a failed run shows the end of its log here, under the error. -->
            <section class="nm-log" aria-label="Hibanapló">
              <div class="nm-log-head">
                <span class="font-semibold" [pTooltip]="help.logs">Hibanapló</span>
                <span class="flex-1"></span>
                <p-button
                  [label]="logCopyLabel()"
                  size="small"
                  severity="secondary"
                  variant="text"
                  [disabled]="!logs()"
                  (onClick)="copyLogs()"
                />
                <p-button
                  label="Mentés"
                  size="small"
                  severity="secondary"
                  variant="text"
                  [disabled]="!logs()"
                  (onClick)="saveLogs(job)"
                />
              </div>
              <pre class="nm-log-text">{{ logs() || 'A napló betöltése…' }}</pre>
            </section>
          }

          <p-tabs [value]="detailTab()" (valueChange)="switchTab($event)">
            <p-tablist>
              <p-tab value="overview">Áttekintés</p-tab>
              <p-tab value="preview" [disabled]="job.status !== 'completed'"
              ><span [pTooltip]="help.preview">Előnézet</span></p-tab
              >
              <!--<p-tab value="files" [disabled]="job.status !== 'completed'"><span [pTooltip]="help.files">Forráskódok</span></p-tab>
              <p-tab value="logs"><span [pTooltip]="help.logs">Napló</span></p-tab>-->
            </p-tablist>
            <p-tabpanels>
              <p-tabpanel value="overview">
                <div class="flex flex-col gap-4">
                  <div class="nm-summary">
                    <p-tag [value]="statusLabel(job)" [severity]="statusSeverity(job)" />
                    @if (job.summary; as summary) {
                      @for (metric of metrics(summary); track metric.label) {
                        <span class="nm-stat"
                        ><strong>{{ metric.value }}</strong>{{ metric.label }}</span
                        >
                      }
                    }
                    <span class="whitespace-nowrap text-sm opacity-70">{{
                        job.created_at | date: 'yyyy.MM.dd. HH:mm'
                      }}</span>
                    <span class="flex-1"></span>
                    @if (job.status === 'completed') {
                      <p-button label="Java asszisztens" size="small" severity="secondary" variant="outlined"
                        [disabled]="(assistantBusy() || assistantDirty()) && assistantJobId() !== job.id"
                        (onClick)="openAssistant(job)" />
                      <span [pTooltip]="help.all"
                      ><p-button
                        label="Teljes csomag letöltése"
                        size="small"
                        [loading]="downloading() === 'all'"
                        [disabled]="!!downloading()"
                        (onClick)="download(job, 'all')"
                      /></span>
                    }
                  </div>
                  @if (job.summary) {
                    @if (job.review_required) {
                      <p-message severity="warn" size="small"
                      ><span class="cursor-help" [pTooltip]="help.review"
                      >A csomag elkészült, ellenőrzést igényel.
                          <span class="opacity-60">ⓘ</span></span
                      ></p-message
                      >
                    } @else {
                      <p-message severity="success" size="small"
                      >A támogatott elemek generálása elkészült.</p-message
                      >
                    }
                  }
                  @if (job.status === 'completed') {
<ng-container *ngTemplateOutlet="deployPanel; context: { $implicit: { kind: 'job', id: job.id } }"></ng-container>
                  }
                  @if (job.status === 'completed') {
                    <section class="nm-tree" aria-label="Generált kód">
                      <div class="nm-tree-head">
                        <span class="font-semibold">Generált kód</span>
                        <span class="text-sm opacity-70">{{ codeFiles().length }} fájl</span>
                        <span class="flex-1"></span>
                        <input
                          pInputText
                          type="search"
                          class="nm-tree-filter"
                          placeholder="Fájl keresése"
                          aria-label="Fájl keresése"
                          [formControl]="codeFilterControl"
                        />
                      </div>
                      <div class="nm-tree-rows">
                        @for (row of codeRows(); track row.key) {
                          <button
                            type="button"
                            class="nm-row"
                            [class.nm-row-group]="row.kind === 'group'"
                            [style.padding-left.rem]="0.75 + row.depth * 1.25"
                            [attr.aria-expanded]="row.kind === 'file' ? null : row.open"
                            (click)="activateRow(row)"
                          >
                            @if (row.kind === 'file') {
                              <svg
                                class="nm-file-icon"
                                viewBox="0 0 16 18"
                                aria-hidden="true"
                                [attr.data-lang]="row.lang"
                              >
                                <path d="M3 1.75h6.5L13 5.25v11H3z M9.5 1.75v3.5H13" />
                              </svg>
                              <span class="nm-row-name">{{ row.name }}</span>
                              <span class="nm-row-meta">{{ bytes(row.file?.bytes ?? 0) }}</span>
                            } @else {
                              <svg class="nm-chevron" viewBox="0 0 16 16" aria-hidden="true">
                                <path d="m6 3.5 4.5 4.5L6 12.5" />
                              </svg>
                              <svg class="nm-folder" viewBox="0 0 24 24" aria-hidden="true">
                                <path
                                  d="M3 6.5A1.5 1.5 0 0 1 4.5 5h4.6l2 2.2h8.4A1.5 1.5 0 0 1 21 8.7v9.8a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 3 18.5z"
                                />
                              </svg>
                              <span class="nm-row-name">{{ row.name }}</span>
                              @if (row.note) {
                                <span class="nm-row-note">{{ row.note }}</span>
                              }
                              <span class="nm-row-meta">{{ row.count }} fájl</span>
                            }
                          </button>
                        } @empty {
                          <p class="nm-tree-empty">
                            {{
                              codeFilter()
                                ? 'Nincs a keresésnek megfelelő fájl.'
                                : sourceFiles().length
                                  ? 'Ehhez a feladathoz nem készült forráskód.'
                                  : 'A fájllista betöltése…'
                            }}
                          </p>
                        }
                      </div>
                    </section>
                  } @else if (!job.summary) {
                    <p class="py-6 text-sm opacity-70">
                      {{
                        terminal(job)
                          ? 'Ehhez a feladathoz nem készült letölthető csomag.'
                          : 'Az eredmény a generálás végén jelenik meg.'
                      }}
                    </p>
                  }
                </div>
              </p-tabpanel>
              <p-tabpanel value="preview">
                @if (screenLoading()) {
                  <p-progressbar mode="indeterminate" [style]="{ height: '4px' }" />
                }
                @if (screenHtml(); as html) {
                  <!-- Static, script-free HTML; screens and tab pages switch with CSS inside. -->
                  <iframe
                    [srcdoc]="html"
                    sandbox=""
                    title="A generált képernyő előnézete"
                    class="w-full rounded border-0"
                    style="height: 70vh"
                  ></iframe>
                } @else if (!screenLoading()) {
                  <p class="py-6 text-sm opacity-70">
                    {{ screenMissing() || 'Az előnézet a generálás végén jelenik meg.' }}
                  </p>
                }
              </p-tabpanel>
              <!--<p-tabpanel value="files">
                <div class="grid gap-3 md:grid-cols-[18rem_minmax(0,1fr)]">
                  <p-listbox [formControl]="fileControl" [options]="sourceFiles()" optionLabel="path" dataKey="path" [filter]="true" filterPlaceHolder="Fájl keresése…"
                             scrollHeight="28rem" class="w-full" (onChange)="openFile($event.value)" />
                  <p-panel [header]="preview()?.path ?? 'Válassz egy fájlt'">
                    @if (previewLoading()) { <p-progressbar mode="indeterminate" [style]="{ height: '4px' }" /> }
                    <pre class="max-h-[28rem] overflow-auto text-xs leading-relaxed">{{ preview()?.text ?? '' }}</pre>
                  </p-panel>
                </div>
              </p-tabpanel>
              <p-tabpanel value="logs">
                <pre class="max-h-[32rem] overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">{{ logs() || 'Még nincs naplóbejegyzés.' }}</pre>
              </p-tabpanel>-->
            </p-tabpanels>
          </p-tabs>
        </div>
      }
    </p-dialog>

    <!-- Code viewer --------------------------------------------------------------->
    <p-dialog
      [(visible)]="viewerOpen"
      [modal]="true"
      [draggable]="false"
      [dismissableMask]="true"
      [maximizable]="true"
      [style]="{ width: 'min(84rem, 96vw)' }"
      [header]="viewerName()"
    >
      <div class="flex flex-col gap-3">
        <div class="nm-viewer-bar">
          <nav class="nm-crumbs" aria-label="A fájl helye">
            @for (crumb of viewerCrumbs(); track $index; let last = $last) {
              <span>{{ crumb }}</span>
              @if (!last) {
                <span class="nm-crumb-sep" aria-hidden="true">/</span>
              }
            }
          </nav>
          <p-tag [value]="viewerLangLabel()" severity="secondary" />
          @if (viewerLines()) {
            <span class="whitespace-nowrap text-sm opacity-70"
            >{{ viewerLines() }} sor, {{ bytes(viewerFile()?.bytes ?? 0) }}</span
            >
          }
          <span class="flex-1"></span>
          <label class="flex cursor-pointer items-center gap-2 text-sm"
          ><p-toggleswitch [formControl]="wrapControl" />Sortörés</label
          >
          <p-button
            label="Előző"
            size="small"
            severity="secondary"
            variant="text"
            [disabled]="viewerIndex() <= 0"
            (onClick)="stepCode(-1)"
          />
          <p-button
            label="Következő"
            size="small"
            severity="secondary"
            variant="text"
            [disabled]="viewerIndex() < 0 || viewerIndex() >= codeFiles().length - 1"
            (onClick)="stepCode(1)"
          />
          <p-button
            [label]="copyLabel()"
            size="small"
            severity="secondary"
            variant="outlined"
            [disabled]="!viewerText()"
            (onClick)="copyCode()"
          />
          <p-button
            label="Letöltés"
            size="small"
            severity="secondary"
            variant="outlined"
            [disabled]="!viewerText()"
            (onClick)="saveCode()"
          />
        </div>
        @if (viewerTruncated()) {
          <p-message severity="info" size="small"
          >Csak az első 1 MB látszik; a teljes fájl a letöltött csomagban van.</p-message
          >
        }
        @if (viewerError()) {
          <p-message severity="error" size="small">{{ viewerError() }}</p-message>
        }
        <div
          class="nm-code"
          role="region"
          tabindex="0"
          [class.nm-code-wrap]="viewerWrap()"
          [style.--nm-gutter]="viewerGutter()"
          [attr.aria-label]="viewerName()"
          [attr.aria-busy]="viewerLoading()"
        >
          @if (viewerLoading()) {
            <p-progressbar mode="indeterminate" [style]="{ height: '3px' }" />
          }
          <pre><code [innerHTML]="codeHtml()"></code></pre>
        </div>
      </div>
    </p-dialog>

    <!-- Batch run ----------------------------------------------------------------->
    <p-dialog
      [(visible)]="batchOpen"
      [modal]="true"
      [draggable]="false"
      [dismissableMask]="true"
      [style]="{ width: 'min(72rem, 96vw)' }"
      header="Tömeges futtatás"
    >
      @if (batchReport(); as b) {
        <div class="flex flex-col gap-4">
          <div class="flex flex-wrap items-center gap-2">
            <p-tag [value]="b.total + ' form'" severity="secondary" />
            <p-tag [value]="(b.counts['completed'] ?? 0) + ' kész'" severity="success" />
            @if (b.counts['needs_input']) {
              <p-tag [value]="b.counts['needs_input'] + ' döntésre vár'" severity="warn" />
            }
            @if ((b.counts['queued'] ?? 0) + (b.counts['running'] ?? 0)) {
              <p-tag
                [value]="(b.counts['queued'] ?? 0) + (b.counts['running'] ?? 0) + ' folyamatban'"
                severity="info"
              />
            }
            @if ((b.counts['failed'] ?? 0) + (b.counts['interrupted'] ?? 0)) {
              <p-tag
                [value]="(b.counts['failed'] ?? 0) + (b.counts['interrupted'] ?? 0) + ' hibás'"
                severity="danger"
              />
            }
            <span class="flex-1"></span>
            <p-button
              label="Összesítő ZIP"
              size="small"
              [disabled]="!(b.counts['completed'] ?? 0) || !!downloading()"
              [loading]="downloading() === 'batch'"
              (onClick)="downloadBatch(b.batch)"
            />
            <p-button
              label="Riport (Markdown)"
              size="small"
              severity="secondary"
              variant="outlined"
              (onClick)="saveText(b.markdown, 'PORTFOLIO_HU.md')"
            />
          </div>
          <div class="nm-survey-bar">
            <span class="font-medium">Felmérés</span>
            <span class="text-sm opacity-70"
            >Megosztható riport arról, mi tiltja a végpontokat; a nevek helyén
              helyettesítők.</span
            >
            <span class="flex-1"></span>
            <p-button
              label="Markdown"
              size="small"
              severity="secondary"
              variant="outlined"
              [disabled]="!b.done || surveyLoading()"
              [loading]="surveyLoading()"
              (onClick)="downloadSurvey(b.batch, 'md')"
            />
            <p-button
              label="JSON"
              size="small"
              severity="secondary"
              variant="outlined"
              [disabled]="!b.done || surveyLoading()"
              (onClick)="downloadSurvey(b.batch, 'json')"
            />
            <span pTooltip="Valódi nevekkel, csak helyi használatra: ezt ne oszd meg."
            ><p-button
              label="Nevekkel"
              size="small"
              severity="secondary"
              variant="text"
              [disabled]="!b.done || surveyLoading()"
              (onClick)="downloadSurvey(b.batch, 'md', true)"
            /></span>
          </div>
          @if (!b.done) {
            <p-progressbar mode="indeterminate" [style]="{ height: '4px' }" />
          }
          @if (b.counts['needs_input']) {
            <p-message severity="warn"
            ><span class="flex flex-wrap items-center gap-2"
            >Néhány form több képernyős: válaszd ki a fő képernyőjüket.
                <p-button
                  label="Következő döntés"
                  size="small"
                  severity="warn"
                  (onClick)="nextQuestion(b)" /></span
            ></p-message>
          }
          <div class="grid grid-cols-2 gap-3 md:grid-cols-4">
            @for (metric of batchMetrics(b); track metric.label) {
              <p-card
              ><div class="text-2xl font-semibold">{{ metric.value }}</div>
                <div class="text-sm opacity-70">{{ metric.label }}</div></p-card
              >
            }
          </div>
          @if (surveyCauses().length) {
            <p-panel header="Mi tiltja a végpontokat" [toggleable]="true">
              <p-table [value]="surveyCauses()" size="small" [scrollable]="true" scrollHeight="22rem">
                <ng-template #header
                ><tr>
                  <th>Ok</th>
                  <th class="text-right">Végpont</th>
                  <th
                    class="text-right"
                    pTooltip="Ennyi végpontot csak ez az ok tilt: a megszüntetésével ennyi nyílik meg."
                  >
                    Egyedüli ok
                  </th>
                  <th class="text-right">Form</th>
                </tr></ng-template
                >
                <ng-template #body let-row
                ><tr>
                  <td class="text-sm">
                    <span class="font-medium">{{ row.code }}</span>
                    <div class="opacity-80">{{ row.reason }}</div>
                  </td>
                  <td class="text-right">{{ row.endpoints }}</td>
                  <td class="text-right">{{ row.sole }}</td>
                  <td class="text-right">{{ row.forms }}</td>
                </tr></ng-template
                >
              </p-table>
            </p-panel>
          } @else if (b.report.endpoint_blockers.length) {
            <p-panel header="Mit érdemes először javítani" [toggleable]="true">
              <p-table
                [value]="topBlockers()"
                size="small"
                [scrollable]="true"
                scrollHeight="20rem"
              >
                <ng-template #header
                ><tr>
                  <th>Ok</th>
                  <th class="text-right">Végpont</th>
                  <th class="text-right">Form</th>
                </tr></ng-template
                >
                <ng-template #body let-row
                ><tr>
                  <td class="text-sm" [pTooltip]="row.example">{{ row.reason }}</td>
                  <td class="text-right">{{ row.endpoints }}</td>
                  <td class="text-right">{{ row.forms.length }}</td>
                </tr></ng-template
                >
              </p-table>
            </p-panel>
          }
          @if (surveyApproximations().length) {
            <p-panel header="Eltérések a Forms-működéstől" [toggleable]="true">
              <p class="mb-2 text-sm opacity-80">
                Elkészül és működik, de nem pontosan úgy, mint a Formsban. A végpontokat nem tiltja.
              </p>
              <p-table [value]="surveyApproximations()" size="small" [scrollable]="true" scrollHeight="22rem">
                <ng-template #header
                ><tr>
                  <th>Eltérés</th>
                  <th class="text-right">Előfordulás</th>
                  <th class="text-right">Form</th>
                </tr></ng-template
                >
                <ng-template #body let-row
                ><tr>
                  <td class="text-sm" [pTooltip]="'Javítás: ' + row.fix">
                    <span class="font-medium">{{ row.label }}</span>
                    <div class="opacity-80">{{ row.now }}</div>
                  </td>
                  <td class="text-right">{{ row.count }} {{ row.unit }}</td>
                  <td class="text-right">{{ row.forms }}</td>
                </tr></ng-template
                >
              </p-table>
            </p-panel>
          }
          <p-table [value]="b.jobs" size="small" [scrollable]="true" scrollHeight="20rem">
            <ng-template #header
            ><tr>
              <th>Forrás</th>
              <th>Állapot</th>
              <th class="text-right">Művelet</th>
            </tr></ng-template
            >
            <ng-template #body let-row>
              <tr>
                <td class="text-sm">
                  {{ row.module ?? row.filename }}
                  <div class="text-xs opacity-70">{{ row.filename }}</div>
                </td>
                <td><p-tag [value]="statusLabel(row)" [severity]="statusSeverity(row)" /></td>
                <td class="text-right">
                  <p-button
                    [label]="row.status === 'needs_input' ? 'Kiválasztás' : 'Megnyitás'"
                    size="small"
                    variant="text"
                    (onClick)="openJobById(row.id)"
                  />
                </td>
              </tr>
            </ng-template>
          </p-table>
        </div>
      } @else {
        <p-progressbar mode="indeterminate" [style]="{ height: '4px' }" />
      }
    </p-dialog>

    <!-- Connection and environment ----------------------------------------------->
    <p-dialog
      [(visible)]="settingsOpen"
      [modal]="true"
      [draggable]="false"
      [style]="{ width: 'min(40rem, 96vw)' }"
      header="Kapcsolat és környezet"
    >
      <div class="flex flex-col gap-5">
        <label class="flex flex-col gap-2"
        ><span class="text-sm font-medium cursor-help" [pTooltip]="help.connect"
        >Migrátor API URL <span class="opacity-60">ⓘ</span></span
        >
          <div class="flex gap-2">
            <input
              pInputText
              class="w-full"
              [value]="apiBaseInput"
              (input)="apiBaseInput = $any($event.target).value"
              [placeholder]="suggestedApi"
            />
            <p-button
              label="Kapcsolódás"
              [loading]="connecting()"
              (onClick)="saveConnection()"
            /></div
          ></label>
        <p-divider />
        <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
          <dt class="opacity-70">Migrátor</dt>
          <dd>{{ health()?.version ?? '–' }}</dd>
          <dt class="opacity-70">Python</dt>
          <dd>{{ health()?.python ?? '–' }}</dd>
          <dt class="opacity-70">Oracle Forms2XML</dt>
          <dd>
            <p-tag
              [value]="exporterLabel()"
              [severity]="
                health()?.exporter?.status === 'missing'
                  ? 'warn'
                  : health()
                    ? 'success'
                    : 'secondary'
              "
              [pTooltip]="health()?.exporter?.message ?? ''"
            />
          </dd>
          <dt class="opacity-70">Fájlméret-limit</dt>
          <dd>{{ bytes(defaults()?.limits?.file_bytes ?? 33554432) }}</dd>
          <dt class="opacity-70">Futásidőkorlát</dt>
          <dd>{{ defaults()?.limits?.job_timeout_seconds ?? 1800 }} s</dd>
          @if (defaults()?.cors_origins?.length) {
            <dt class="opacity-70">Engedélyezett címek</dt>
            <dd class="break-all text-xs">{{ defaults()?.cors_origins?.join(', ') }}</dd>
          }
        </dl>
        <div>
          <span [pTooltip]="help.cache"
          ><p-button
            label="AI gyorsítótár törlése"
            size="small"
            severity="danger"
            variant="text"
            [disabled]="!!activeJobs().length || !online()"
            (onClick)="clearCache()"
          /></span>
        </div>
      </div>
    </p-dialog>

    <!-- Deploy into the project: the panel of a completed job ------------------------------>
    <ng-template #deployPanel let-target>
      <section class="nm-deploy" aria-label="Telepítés a projektbe">
        <div class="flex flex-wrap items-center gap-2">
          <span class="font-semibold">Telepítés a projektbe</span>
          <span class="text-sm opacity-70"
          >A fájlok pontosan a modul kiválasztott mappáiba kerülnek, új mappa nem készül. A CREATE_ONCE fájlok
            (ServiceImpl, ControllerImpl, komponens) csak akkor íródnak, ha még nincsenek meg.</span
          >
        </div>
        <div class="nm-deploy-grid">
          @for (part of parts; track part.key) {
            <span class="nm-deploy-label text-sm font-medium cursor-help" [pTooltip]="part.help"
            >{{ part.label }} <span class="opacity-60">ⓘ</span></span
            >
            <input
              pInputText
              class="min-w-0"
              [placeholder]="part.hint"
              [attr.aria-label]="part.label"
              [formControl]="deployFolders[part.key]"
            />
            <p-button
              label="Tallózás…"
              size="small"
              severity="secondary"
              variant="outlined"
              [disabled]="!!picking() || !!deploying()"
              (onClick)="browseFolder('deploy', part.key)"
            />
          }
        </div>
        <div class="flex flex-wrap items-center gap-2">
          <span class="flex-1 text-sm opacity-70">{{ deployHint() }}</span>
          <p-button
            label="Előnézet"
            size="small"
            severity="secondary"
            variant="outlined"
            [loading]="deploying() === 'preview'"
            [disabled]="!deployChosen() || !!deploying()"
            (onClick)="runDeploy(target, true)"
          />
          <span pTooltip="Előbb nézd meg az előnézetet ugyanezekre a mappákra."
          ><p-button
            label="Telepítés"
            size="small"
            [loading]="deploying() === 'deploy'"
            [disabled]="!deployReady(target) || !!deploying()"
            (onClick)="runDeploy(target, false)"
          /></span>
        </div>
        <div class="flex flex-wrap items-center gap-2 text-sm">
          <span class="font-medium">Segédfájlok</span>
          <span class="opacity-70"
          >Nem kerülnek a projektbe: elég egyszer letölteni, és új változatnál lecserélni.</span
          >
          <span class="flex-1"></span>
          @for (name of helpers; track name) {
            <p-button
              [label]="name"
              size="small"
              severity="secondary"
              variant="text"
              [loading]="downloading() === name"
              [disabled]="!!downloading()"
              (onClick)="downloadHelper(target.id, name)"
            />
          }
        </div>
        @if (deployResultFor(target); as r) {
          <dl class="nm-deploy-layout" aria-label="Célmappák">
            @for (entry of deployLayout(r); track entry.key) {
              <dt class="font-medium">{{ entry.label }}</dt>
              <dd>
                @if (entry.value) {
                  {{ entry.value }}
                  @if (r.packages?.[entry.key]; as pkg) {
                    <span class="opacity-70">(csomag: {{ pkg }})</span>
                  }
                } @else {
                  <p-tag value="nincs kiválasztva: a fájljai kimaradnak" severity="warn" />
                }
              </dd>
            }
            @for (helper of r.helpers ?? []; track helper.name) {
              <dt class="font-medium">{{ helper.name }}</dt>
              <dd>
                <p-tag [value]="helperStatus(helper)" [severity]="helperSeverity(helper)" />
                @if (helper.found ?? helper.expected; as where) {
                  <span class="opacity-70"> {{ where }}</span>
                }
              </dd>
            }
          </dl>
          <div class="flex flex-wrap items-center gap-2 text-sm">
            @for (entry of deployCounts(r); track entry.key) {
              <p-tag [value]="entry.label + ': ' + entry.value" [severity]="entry.severity" />
            }
            <span class="flex-1"></span>
            <p-button
              label="Riport"
              size="small"
              variant="text"
              (onClick)="saveText(r.markdown, 'PROJECT_DEPLOY_HU.md')"
            />
          </div>
          <p-table [value]="r.files" size="small" [scrollable]="true" scrollHeight="18rem">
            <ng-template #header
            ><tr>
              <th>Fájl</th>
              <th>Cél</th>
              <th>Állapot</th>
            </tr></ng-template
            >
            <ng-template #body let-row
            ><tr>
              <td class="text-sm">{{ row.source }}</td>
              <td class="text-sm" [pTooltip]="row.target ?? ''">{{ row.display ?? '—' }}</td>
              <td class="text-sm" [pTooltip]="row.reason ?? ''">{{ deployStatus(row.status) }}</td>
            </tr></ng-template
            >
          </p-table>
        }
      </section>
    </ng-template>

    <!-- The machine's own folder dialog is open (the server opened it) ---------------------->
    <p-dialog
      [visible]="!!picking()"
      (visibleChange)="!$event && cancelPick(false)"
      [modal]="true"
      [draggable]="false"
      [style]="{ width: 'min(32rem, 96vw)' }"
      header="Mappa kiválasztása"
    >
      <div class="flex flex-col gap-4">
        <p class="m-0 text-sm">
          Megnyílt a mappaválasztó ablak: válaszd ki benne a(z) <b>{{ part(picking()?.key)?.label }}</b> mappáját.
          Ha nem látod, a tálcán találod.
        </p>
        <div class="flex flex-wrap justify-end gap-2">
          <p-button
            label="Inkább itt, a böngészőben"
            size="small"
            severity="secondary"
            variant="outlined"
            (onClick)="cancelPick(true)"
          />
          <p-button label="Mégse" size="small" severity="secondary" variant="text" (onClick)="cancelPick(false)" />
        </div>
      </div>
    </p-dialog>

    <!-- In-page folder browser (where the folder dialog cannot open) ------------------------->
    <p-dialog
      [(visible)]="browserOpen"
      [modal]="true"
      [draggable]="false"
      [style]="{ width: 'min(46rem, 96vw)' }"
      [header]="part(browserFor()?.key)?.title ?? 'Mappa kiválasztása'"
    >
      <div class="flex flex-col gap-3">
        @if (browser()?.path) {
          <div class="flex flex-wrap items-center gap-2">
            @for (root of browser()?.roots ?? []; track root.path) {
              <p-button
                [label]="root.name"
                size="small"
                severity="secondary"
                variant="text"
                [disabled]="browserLoading()"
                (onClick)="browseTo(root.path)"
              />
            }
          </div>
        }
        <div class="flex items-center gap-2">
          <p-button
            label="↑ Fel"
            size="small"
            severity="secondary"
            variant="outlined"
            [disabled]="!browser()?.parent || browserLoading()"
            (onClick)="browseTo(browser()?.parent ?? '')"
          />
          <input
            pInputText
            class="min-w-0 flex-1"
            aria-label="A mappa útvonala"
            placeholder="Válassz egy kiindulópontot, vagy írd be az útvonalat"
            [value]="browserPath"
            (input)="browserPath = $any($event.target).value"
            (keydown.enter)="browseTo(browserPath)"
          />
        </div>
        @if (browserLoading()) {
          <p-progressbar mode="indeterminate" [style]="{ height: '4px' }" />
        }
        @if (browserError()) {
          <p-message severity="error" size="small">{{ browserError() }}</p-message>
        }
        <div class="nm-folders" role="list" aria-label="Almappák">
          @for (folder of browser()?.folders ?? []; track folder.path) {
            <button
              type="button"
              class="nm-folder"
              role="listitem"
              [disabled]="browserLoading()"
              (click)="browseTo(folder.path)"
            >
              <span aria-hidden="true">📁</span>
              <span class="nm-folder-name">{{ folder.name }}</span>
              @if (folder.kind) {
                <p-tag [value]="folderKind(folder.kind)" severity="info" />
              }
            </button>
          } @empty {
            <p class="m-0 p-3 text-sm opacity-70">
              {{ browser()?.path ? 'Ebben a mappában nincs almappa.' : browserLoading() ? 'Betöltés…' : '' }}
            </p>
          }
        </div>
        @if (browser()?.truncated) {
          <p class="m-0 text-xs opacity-70">Csak az első 500 almappa látszik; írd be a pontos útvonalat.</p>
        }
        <div class="flex flex-wrap items-center gap-2">
          <span class="min-w-0 flex-1 text-sm" style="overflow-wrap: anywhere">
            @if (browser()?.path; as current) {
              Kiválasztva: <b>{{ current }}</b>
              @if (browser()?.kind; as kind) {
                ({{ folderKind(kind) }})
              }
            } @else {
              Lépj be abba a mappába, amelyet választani szeretnél.
            }
          </span>
          <p-button label="Mégse" size="small" severity="secondary" variant="text" (onClick)="browserOpen.set(false)" />
          <p-button
            label="Ezt a mappát választom"
            size="small"
            [disabled]="!browser()?.path || browserLoading()"
            (onClick)="chooseBrowsed()"
          />
        </div>
      </div>
    </p-dialog>
  `,
})
export class Migrator implements OnInit, OnDestroy {
  // A migrátor saját, helyi backendje: a projekt HTTP-interceptorai (céges fejlécek, hibakezelés) nélkül.
  // Ezek egy része nem JSON-választ (pl. az Előnézet HTML-jét) üres válaszként adná tovább.
  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly fb = inject(FormBuilder);
  private readonly sanitizer = inject(DomSanitizer);
  private readonly mutation = { headers: new HttpHeaders({ 'X-Frm-Client': 'local-ui' }) };
  protected readonly help = HELP;

  // -------------------------------------------------------------- options --
  readonly form = this.fb.nonNullable.group({
    screen_tab_layout: ['tabs' as Options['screen_tab_layout']],
    screen_infer_widgets: [true],
    screen_repair_display_text: [true],
    screen_row_tolerance: [0.25, [Validators.required, Validators.min(0), Validators.max(0.5)]],
    screen_preserve_gaps: [false],
    backend_live: [true],
    screen_button_label_property: ['labelText' as Options['screen_button_label_property']],
    screen_fold_list_buttons: [true],
    module: [
      '',
      [Validators.pattern(/^[a-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$/), Validators.maxLength(70)],
    ],
    AWU_AZON: [''],
    api_prefix: ['/api/forms', [Validators.required, Validators.pattern(/^\/[A-Za-z0-9/_-]*$/)]],
    angular_selector_prefix: [
      'app',
      [Validators.required, Validators.pattern(/^[a-z][a-z0-9-]*$/)],
    ],
    wbs_base_url: ['', Validators.pattern(URL_PATTERN)],
    dps_base_url: ['', Validators.pattern(URL_PATTERN)],
    ollama_url: ['', [Validators.required, Validators.pattern(URL_PATTERN)]],
    form_block_structure_type: [
      'FormBlock.Structure',
      [Validators.required, Validators.pattern(/^[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*$/)],
    ],
    form_block_columns: [
      '6',
      [Validators.required, Validators.pattern(/^(?:[1-9]|1[0-9]|2[0-4])$/)],
    ],
    form_block_checkbox_boolean: [true],
    html_selectors: this.fb.nonNullable.group({
      form_block: ['ank-form-block', Validators.required],
      button: ['button', Validators.required],
      table: ['p-table', Validators.required],
    }),
    form_block_types: this.fb.nonNullable.group({
      text: ['text', Validators.required],
      number: ['inputNumber', Validators.required],
      datetime: ['calendar', Validators.required],
      checkbox: ['checkBox', Validators.required],
      select: ['dropdown', Validators.required],
      radio: ['radioButton', Validators.required],
      password: ['password'],
      textarea: ['inputTextarea', Validators.required],
    }),
    emit_imports: [false],
    layout_columns: [12 as Options['layout_columns']],
    optimus_import_path: [''],
    optimus_form_block_symbol: [''],
    form_block_type_import_path: [''],
    environment_import_path: [''],
    table_import_path: [''],
    table_symbol: [''],
    calendar_blocks: this.fb.nonNullable.control<string[]>([]),
    table_blocks: this.fb.nonNullable.control<string[]>([]),
    table_bindings: this.fb.nonNullable.group({
      rows: ['value', Validators.required],
      columns: ['columns', Validators.required],
      field: ['field', Validators.required],
      header: ['header', Validators.required],
    }),
    endpoint_names: this.fb.nonNullable.group({
      list: ['getdata', Validators.required],
      create: ['create', Validators.required],
      update: ['update', Validators.required],
      delete: ['delete', Validators.required],
    }),
    max_ai_calls: [3, [Validators.required, Validators.min(0), Validators.max(10)]],
    ai_num_ctx: [2048, [Validators.required, Validators.min(1024), Validators.max(8192)]],
    ai_num_predict: [256, [Validators.required, Validators.min(64), Validators.max(2048)]],
    ai_timeout_seconds: [120, [Validators.required, Validators.min(1), Validators.max(600)]],
    ai_max_source_chars: [1200, [Validators.required, Validators.min(100), Validators.max(8000)]],
    ai_max_prompt_bytes: [3200, [Validators.required, Validators.min(500), Validators.max(16000)]],
    ai_think: ['default' as Options['ai_think']],
    ai_cache_salt: ['1', Validators.required],
    ollama_model: ['frm-model', [Validators.required, Validators.pattern(/^[A-Za-z0-9_./:@-]+$/)]],
    strict: [false],
  });
  // Signals keep the zoneless view in step with programmatic form updates.
  readonly ollamaAddress = toSignal(this.form.controls.ollama_url.valueChanges, {
    initialValue: this.form.controls.ollama_url.value,
  });
  readonly ollamaChatEndpoint = computed(() => {
    try { return ollamaBaseUrl(this.ollamaAddress()) + '/api/chat'; } catch { return ''; }
  });
  readonly checkingAi = signal(false);
  readonly emitImports = toSignal(this.form.controls.emit_imports.valueChanges, {
    initialValue: this.form.controls.emit_imports.value,
  });
  readonly formInvalid = toSignal(
    this.form.statusChanges.pipe(map((status) => status === 'INVALID')),
    { initialValue: this.form.invalid },
  );

  readonly tabLayouts = [
    { label: 'Optimus Tabs', value: 'tabs' },
    { label: 'Optimus Accordion', value: 'accordion' },
  ];
  readonly buttonLabels = [
    { label: 'labelText (önálló gomb)', value: 'labelText' },
    { label: 'btnLabel (inputGroup)', value: 'btnLabel' },
  ];
  readonly gridSizes = [
    { label: '12 oszlop', value: 12 },
    { label: '18 oszlop', value: 18 },
    { label: '24 oszlop', value: 24 },
  ];
  readonly thinkLevels = [
    { label: 'Modell alapértelmezése', value: 'default' },
    { label: 'Low', value: 'low' },
    { label: 'Medium', value: 'medium' },
    { label: 'High', value: 'high' },
    { label: 'Kikapcsolva', value: 'disabled' },
  ];
  readonly filters = [
    { label: 'Összes', value: 'all' },
    { label: 'Aktív', value: 'active' },
    { label: 'Döntésre vár', value: 'needs_input' },
    { label: 'Kész', value: 'completed' },
    { label: 'Hibás', value: 'failed' },
  ];
  readonly screenToggles = [
    { control: 'screen_infer_widgets', label: 'Típusjavaslatok az XML-jelekből', help: HELP.infer },
    {
      control: 'screen_repair_display_text',
      label: 'Ékezetjavítás a feliratokban',
      help: HELP.repair,
    },
    { control: 'screen_preserve_gaps', label: 'Térközök megtartása', help: HELP.gaps },
    { control: 'backend_live', label: 'Azonnal éles backend', help: HELP.live },
    {
      control: 'screen_fold_list_buttons',
      label: 'Listanyitó gombok beolvasztása',
      help: HELP.fold,
    },
  ] as const;
  readonly blockTypes = [
    { control: 'text', label: 'Szöveg típusa' },
    { control: 'number', label: 'NUMBER típusa' },
    { control: 'datetime', label: 'Dátum típusa' },
    { control: 'checkbox', label: 'Checkbox típusa' },
    { control: 'select', label: 'Lista típusa' },
    { control: 'radio', label: 'Rádiócsoport típusa' },
    { control: 'textarea', label: 'Többsoros szöveg típusa' },
    { control: 'password', label: 'Jelszó típusa' },
  ] as const;
  readonly endpointNames = [
    { control: 'list', label: 'Lekérdezés műveletneve' },
    { control: 'create', label: 'Beszúrás műveletneve' },
    { control: 'update', label: 'Módosítás műveletneve' },
    { control: 'delete', label: 'Törlés műveletneve' },
  ] as const;
  readonly importPaths = [
    { control: 'optimus_import_path', label: 'FormBlock komponens import útvonala' },
    { control: 'optimus_form_block_symbol', label: 'FormBlock komponens neve' },
    { control: 'form_block_type_import_path', label: 'FormBlock típus import útvonala' },
    { control: 'environment_import_path', label: 'Environment import útvonala' },
    { control: 'table_import_path', label: 'Táblázat import útvonala' },
    { control: 'table_symbol', label: 'Táblázat exportált neve' },
  ] as const;
  readonly aiLimits = [
    { control: 'max_ai_calls', label: 'Legfeljebb ennyi AI-kérés', min: 0, max: 10, step: 1 },
    { control: 'ai_num_ctx', label: 'Kontextusméret', min: 1024, max: 8192, step: 1024 },
    { control: 'ai_num_predict', label: 'Válasz tokenlimit', min: 64, max: 2048, step: 64 },
    { control: 'ai_timeout_seconds', label: 'Timeout (másodperc)', min: 1, max: 600, step: 1 },
    {
      control: 'ai_max_source_chars',
      label: 'Trigger karakterlimit',
      min: 100,
      max: 8000,
      step: 100,
    },
    { control: 'ai_max_prompt_bytes', label: 'Prompt bájtlimit', min: 500, max: 16000, step: 100 },
  ] as const;

  // ---------------------------------------------------------------- state --
  readonly view = signal<'generate' | 'jobs' | 'assistant'>(typeof location !== 'undefined' && new URLSearchParams(location.search).get('view') === 'assistant' ? 'assistant' : 'generate');
  readonly assistantJobId = signal<string | null>(typeof location !== 'undefined' ? new URLSearchParams(location.search).get('job') : null);
  readonly assistantBusy = signal(false);
  readonly assistantDirty = signal(false);
  /** A saved AI connection folds its panel: the header still says where the model runs. */
  readonly aiCollapsed = signal(Boolean(this.savedAiConnection().ollama_url));
  readonly aiHeader = computed(() => {
    const address = this.ollamaAddress().trim();
    const model = this.form.controls.ollama_model.value.trim();
    return 'AI-kapcsolat · közös beállítások' + (address ? ' · ' + (model ? model + ' @ ' : '') + address : '');
  });
  readonly fileControl = new FormControl<SourceFile | null>(null);
  readonly base = signal(storedBase());
  readonly health = signal<Health | null>(null);
  readonly defaults = signal<Defaults | null>(null);
  readonly jobs = signal<Job[]>([]);
  readonly filterControl = new FormControl('all', { nonNullable: true });
  readonly filter = toSignal(this.filterControl.valueChanges, { initialValue: 'all' });
  readonly selectedId = signal<string | null>(null);
  detailOpen = signal(false);
  settingsOpen = signal(false);
  readonly files = signal<File[]>([]);
  readonly batchAwu = signal<Record<string, string>>({});
  readonly awuValue = toSignal(this.form.controls.AWU_AZON.valueChanges, { initialValue: '' });
  readonly awuBlocker = computed(() => {
    if (this.survey()) return '';
    const values =
      this.files().length > 1
        ? this.files().map((source) => this.batchAwu()[source.name] ?? '')
        : [this.awuValue()];
    if (values.some((value) => !/^[0-9]{1,30}$/.test(value))) return 'Add meg az AWU_AZON számot';
    return new Set(values).size === values.length
      ? ''
      : 'A formokhoz különböző AWU_AZON számokat adj meg.';
  });
  readonly olb = signal<File[]>([]);
  // Attached PL/SQL libraries (.pld, or .pll for frmcmp): dropped with the forms, sent with every job.
  readonly pld = signal<File[]>([]);
  // Survey: a batch run for the report, not the code (no AWU_AZON, no window question).
  readonly surveyControl = new FormControl(false, { nonNullable: true });
  readonly survey = toSignal(this.surveyControl.valueChanges, { initialValue: false });
  readonly surveyResult = signal<SurveyResult | null>(null);
  readonly surveyLoading = signal(false);
  readonly mmb = signal<File | null>(null);
  readonly schema = signal<File | null>(null);
  readonly rules = signal<File | null>(null);
  readonly screenOverrides = signal<File | null>(null);
  readonly fieldLengths = signal<File | null>(null);
  readonly dragging = signal(false);
  readonly uploading = signal(false);
  readonly connecting = signal(false);
  readonly error = signal('');
  readonly connectionError = signal('');
  readonly notice = signal('');
  readonly detailTab = signal<DetailTab>('overview');
  readonly sourceFiles = signal<SourceFile[]>([]);
  readonly preview = signal<Preview | null>(null);
  readonly previewLoading = signal(false);
  readonly report = signal('');
  readonly issues = signal<Issue[]>([]);
  readonly issueCount = signal(0);
  readonly logs = signal('');
  readonly downloading = signal('');
  readonly batchProgress = signal<BatchProgress | null>(null);
  batchOpen = signal(false);
  readonly batchReport = signal<BatchReport | null>(null);
  readonly screenHtml = signal<SafeHtml | null>(null);
  readonly screenLoading = signal(false);
  readonly screenMissing = signal('');
  readonly answering = signal(false);
  readonly windowControl = new FormControl<string | null>(null);
  // Windows question: all ticked at first; the previews are trusted once (same server, no scripts, sandboxed).
  readonly chosenWindows = signal<string[]>([]);
  private readonly previews = new Map<string, SafeHtml>();

  // Generated code: folder tree on the overview tab, highlighted viewer in its own dialog.
  readonly codeFilterControl = new FormControl('', { nonNullable: true });
  // A modul saját mappái a projektben: a generálás előtt (generate) és a feladat telepítésénél (deploy).
  readonly parts: readonly { key: PartKey; label: string; title: string; hint: string; help: string }[] = [
    { key: 'CL', label: 'CL', title: 'Válaszd ki a modul CL-mappáját', hint: 'pl. …/src/main/java/hu/ceg/…/cl/modules/xy',
      help: 'A modul CL-mappája (DTO-k, Constants, RestClient). A fájlok pontosan ide kerülnek; a mappa útvonala a csomag.' },
    { key: 'DPS', label: 'DPS', title: 'Válaszd ki a modul DPS-mappáját', hint: 'pl. …/src/main/java/hu/ceg/…/dps/xy',
      help: 'A modul DPS-mappája. A fájlok pontosan ide kerülnek; a mappa útvonala a csomag.' },
    { key: 'WBS', label: 'WBS', title: 'Válaszd ki a modul WBS-mappáját', hint: 'pl. …/src/main/java/hu/ceg/…/wbs/xy',
      help: 'A modul WBS-mappája. A fájlok pontosan ide kerülnek; a mappa útvonala a csomag.' },
    { key: 'frontend', label: 'Frontend', title: 'Válaszd ki a modul frontend-mappáját', hint: 'pl. …/src/app/pages/xy',
      help: 'A modul képernyőjének mappája. A komponens közvetlenül ide kerül, modulnevű almappa nélkül.' },
  ];
  readonly helpers = HELPERS;
  readonly generateFolders = this.folderControls();
  readonly deployFolders = this.folderControls();
  // A modulnévhez megjegyzett mappák; foldersFromMemory: a mezők egy másik modul emlékéből jöttek.
  private folderMemory = storedModuleFolders();
  foldersFromMemory = false;
  private folderKey = '';
  readonly deploying = signal<'' | 'preview' | 'deploy'>('');
  private readonly deployResult = signal<{ key: string; folders: string; report: DeployReport } | null>(null);
  // Mappaválasztás: a gép saját mappaválasztó ablaka (a szerver nyitja meg), ha az nem nyílik, a beépített böngésző.
  readonly picking = signal<{ scope: FolderScope; key: PartKey } | null>(null);
  private folderDialogMissing = false;
  browserOpen = signal(false);
  readonly browserFor = signal<{ scope: FolderScope; key: PartKey } | null>(null);
  readonly browser = signal<FolderListing | null>(null);
  readonly browserLoading = signal(false);
  readonly browserError = signal('');
  browserPath = '';
  readonly codeFilter = toSignal(this.codeFilterControl.valueChanges, { initialValue: '' });
  readonly wrapControl = new FormControl(false, { nonNullable: true });
  readonly viewerWrap = toSignal(this.wrapControl.valueChanges, { initialValue: false });
  readonly expandedFolders = signal<ReadonlySet<string>>(new Set());
  viewerOpen = signal(false);
  readonly viewerFile = signal<SourceFile | null>(null);
  readonly viewerLoading = signal(false);
  readonly viewerError = signal('');
  readonly copyState = signal<'idle' | 'done' | 'failed'>('idle');
  readonly codeTree = computed(() => buildCodeTree(this.sourceFiles()));
  readonly codeRows = computed(() =>
    codeRows(this.codeTree(), this.expandedFolders(), this.codeFilter()),
  );
  readonly codeFiles = computed(() => this.codeTree().flatMap((group) => folderFiles(group.root)));
  readonly viewerLang = computed(() => codeLang(this.viewerFile()?.path ?? ''));
  readonly viewerLangLabel = computed(() => LANG_LABELS[this.viewerLang()]);
  readonly viewerName = computed(() => fileName(this.viewerFile()?.path ?? '') || 'Forráskód');
  readonly viewerCrumbs = computed(() => codeCrumbs(this.viewerFile()?.path ?? ''));
  readonly viewerText = computed(() => {
    const preview = this.preview(),
      file = this.viewerFile();
    if (!preview || !file || preview.path !== file.path) return '';
    return this.viewerLang() === 'json' && !preview.truncated ? prettyJson(preview.text) : preview.text;
  });
  readonly viewerTruncated = computed(() => !!this.viewerText() && !!this.preview()?.truncated);
  readonly viewerLines = computed(() => lineCount(this.viewerText()));
  readonly viewerGutter = computed(() => String(Math.max(this.viewerLines(), 1)).length + 'ch');
  readonly viewerIndex = computed(() =>
    this.codeFiles().findIndex((file) => file.path === this.viewerFile()?.path),
  );
  readonly codeHtml = computed(() => highlight(this.viewerText(), this.viewerLang()));
  readonly copyLabel = computed(
    () => ({ idle: 'Másolás', done: 'Kimásolva', failed: 'Nem sikerült' })[this.copyState()],
  );

  readonly online = computed(() => !!this.health() && !this.connectionError());
  readonly selectedJob = computed(
    () => this.jobs().find((job) => job.id === this.selectedId()) ?? null,
  );
  // needs_input waits for the user: it is neither running nor finished.
  readonly activeJobs = computed(() =>
    this.jobs().filter((job) => !this.terminal(job) && job.status !== 'needs_input'),
  );
  readonly waitingJobs = computed(() => this.jobs().filter((job) => job.status === 'needs_input'));
  readonly batches = computed<BatchSummary[]>(() => {
    const groups = new Map<string, Job[]>();
    for (const job of this.jobs())
      if (job.batch) groups.set(job.batch, [...(groups.get(job.batch) ?? []), job]);
    return [...groups.entries()]
      .map(([id, jobs]) => ({
        id,
        total: jobs.length,
        completed: jobs.filter((j) => j.status === 'completed').length,
        waiting: jobs.filter((j) => j.status === 'needs_input').length,
        failed: jobs.filter((j) => ['failed', 'interrupted'].includes(j.status)).length,
        active: jobs.filter((j) => !this.terminal(j) && j.status !== 'needs_input').length,
        created: jobs.map((j) => j.created_at).sort()[0] ?? '',
      }))
      .sort((a, b) => b.created.localeCompare(a.created));
  });
  readonly completedCount = computed(
    () => this.jobs().filter((job) => job.status === 'completed').length,
  );
  readonly filteredJobs = computed(() =>
    this.jobs().filter(
      (job) =>
        this.filter() === 'all' ||
        (this.filter() === 'active'
          ? !this.terminal(job) && job.status !== 'needs_input'
          : job.status === this.filter()),
    ),
  );
  // Why the primary action is unavailable, shown in its tooltip instead of guessing.
  readonly generateBlocker = computed(() =>
    !this.online()
      ? 'Nincs kapcsolat a backenddel: állítsd be a Kapcsolat gombbal.'
      : !this.files().length
        ? 'Előbb válassz forrásfájlt.'
        : this.formInvalid()
          ? 'Javítsd a jelölt beállításokat.'
          : this.awuBlocker() || (this.uploading() ? 'Feltöltés folyamatban…' : ''),
  );
  readonly generateHint = computed(() => this.generateBlocker() || HELP.generate);
  readonly fmbWithoutExporter = computed(
    () =>
      this.files().some((f) => f.name.toLowerCase().endsWith('.fmb')) &&
      this.health()?.exporter.status === 'missing',
  );
  // Memoised: a fresh array in a property binding would fail the dev-mode check (NG0100).
  readonly windowChoices = computed(() => {
    const question = this.selectedJob()?.question;
    return question ? this.windowOptions(question) : [];
  });
  readonly surveyCauses = computed(() => this.surveyResult()?.report.causes.slice(0, 15) ?? []);
  readonly surveyApproximations = computed(() =>
    (this.surveyResult()?.report.approximations ?? []).filter((row) => row.count > 0));
  readonly companionSummary = computed(() =>
    [...this.olb().map((file) => file.name), this.schema()?.name ?? ''].filter(Boolean).join(', '),
  );
  readonly topBlockers = computed(
    () => this.batchReport()?.report.endpoint_blockers.slice(0, 15) ?? [],
  );
  readonly submitLabel = computed(() =>
    this.uploading()
      ? 'Feltöltés…'
      : this.survey()
        ? `Felmérés indítása (${this.files().length} form)`
        : this.files().length > 1
          ? `Tömeges generálás (${this.files().length} form)`
          : 'Generálás indítása',
  );
  readonly exporterLabel = computed(() =>
    this.health()?.exporter.status === 'missing'
      ? 'Beállítandó'
      : this.health()
        ? 'Beállítva'
        : 'Ismeretlen',
  );

  apiBaseInput = this.base();
  readonly suggestedApi =
    typeof location !== 'undefined' && location.protocol.startsWith('http')
      ? location.origin + '/api'
      : 'http://localhost:8000/api';
  private timer?: ReturnType<typeof setInterval>;
  private refreshing = false;
  private loadedDefaults = false;
  private previewTicket = 0;
  private viewerTicket = 0;
  private copyTimer?: ReturnType<typeof setTimeout>;
  private destroyed = false;
  private stopRequested = false;
  // Waiting jobs already shown once: closing the question does not reopen it on every poll.
  private readonly asked = new Set<string>();

  async ngOnInit(): Promise<void> {
    this.form.controls.module.valueChanges.subscribe(() => this.syncModuleFolders());
    if (this.base()) await this.connect();
    else if (typeof location !== 'undefined' && location.protocol.startsWith('http') && !/:420[01]$/.test(location.host)) {
      // Served by the migrator backend itself: its own API, no question on the first visit.
      this.apiBaseInput = this.suggestedApi;
      this.base.set(this.suggestedApi);
      await this.connect();
      if (!this.online()) { this.base.set(''); this.settingsOpen.set(true); }
    } else {
      this.apiBaseInput = this.suggestedApi;
      this.settingsOpen.set(true);
    }
    if (this.destroyed) return;
    this.timer = setInterval(() => {
      if (
        (this.activeJobs().length || (this.batchOpen() && !this.batchReport()?.done)) &&
        !this.uploading()
      )
        void this.refreshJobs();
    }, 1200);
  }

  ngOnDestroy(): void {
    this.destroyed = true;
    clearInterval(this.timer);
    clearTimeout(this.copyTimer);
    clearTimeout(this.logCopyTimer);
  }

  // ------------------------------------------------------------------ api --
  private url(path: string): string {
    if (!this.base()) throw new Error('Előbb add meg a migrátor API URL-jét.');
    return this.base() + path;
  }

  async connect(): Promise<void> {
    if (this.connecting() || !this.base()) return;
    const base = this.base();
    this.connecting.set(true);
    try {
      const [health, defaults, jobs] = await Promise.all([
        firstValueFrom(this.http.get<Health>(this.url('/health'))),
        firstValueFrom(this.http.get<Defaults>(this.url('/defaults'))),
        firstValueFrom(this.http.get<{ jobs: Job[] }>(this.url('/jobs'))),
      ]);
      if (this.destroyed || this.base() !== base) return;
      this.health.set(health);
      this.defaults.set(defaults);
      this.jobs.set(jobs.jobs);
      this.connectionError.set('');
      if (!this.loadedDefaults) {
        let saved = {};
        try {
          saved = JSON.parse(localStorage.getItem(OPTIONS_KEY) ?? '{}');
        } catch {
          /* defaults remain */
        }
        this.form.patchValue({
          ...defaults.options,
          module: defaults.options.module ?? '',
          ollama_url: defaults.options.ollama_url || (defaults.ollama_url?.includes('://xx:') ? '' : defaults.ollama_url ?? ''),
          ...saved,
          ...this.savedAiConnection(),
          AWU_AZON: defaults.options.AWU_AZON ?? '',
        });
        this.loadedDefaults = true;
      }
    } catch {
      this.health.set(null);
      this.connectionError.set(
        'A backend nem érhető el. Indítsd el a Python szervert (python -m frm_forms.web --port 8000), majd kapcsolódj újra.',
      );
    } finally {
      this.connecting.set(false);
    }
  }

  async saveConnection(): Promise<void> {
    if (this.assistantBusy() || this.assistantDirty()) {
      this.error.set('Kapcsolatváltás előtt mentsd vagy vesd el az asszisztens vázlatát, és várd meg a futó feladatot.'); return;
    }
    this.error.set('');
    try {
      const origin = apiUrl(this.apiBaseInput);
      this.base.set(origin);
      try {
        localStorage.setItem(API_KEY, origin);
      } catch {
        /* storage may be disabled */
      }
      this.loadedDefaults = false;
      this.jobs.set([]);
      this.health.set(null);
      this.selectedId.set(null);
      this.clearDetails();
      await this.connect();
      if (this.online()) this.settingsOpen.set(false);
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  async refreshJobs(): Promise<void> {
    if (this.refreshing || !this.base()) return;
    this.refreshing = true;
    const before = this.selectedJob();
    const base = this.base();
    try {
      const result = await firstValueFrom(this.http.get<{ jobs: Job[] }>(this.url('/jobs')));
      if (this.destroyed || this.base() !== base) return;
      this.jobs.set(result.jobs);
      this.connectionError.set('');
      const selected = this.selectedJob();
      if (selected?.status === 'completed' && before?.status !== 'completed')
        await this.loadDetails(selected);
      if (selected && this.detailOpen() && this.detailTab() === 'logs')
        await this.loadLogs(selected);
      else if (selected && this.detailOpen() && ['failed', 'interrupted'].includes(selected.status)
               && before?.status !== selected.status)
        await this.loadLogs(selected);  // it has just failed: its log under the error
      if (this.batchOpen() && this.batchReport())
        await this.refreshBatch(this.batchReport()!.batch);
      this.askWaiting();
    } catch (error) {
      this.connectionError.set(this.errorText(error));
    } finally {
      this.refreshing = false;
    }
  }

  // --------------------------------------------------------------- uploads --
  sourceHelp(): string {
    return (
      'Oracle Forms modul: .fmb vagy Forms2XML export (.xml), legfeljebb ' +
      this.bytes(this.defaults()?.limits.file_bytes ?? 33554432) +
      '. Az XML közvetlenül feldolgozható; az FMB-hez Oracle Forms2XML kell a backend gépén. Export: frmf2xml USE_PROPERTY_IDS=NO DUMP=ALL OVERWRITE=YES. ' +
      'A formhoz csatolt PL/SQL-könyvtárakat (.pld) is ide húzd: a könyvtári eljárások így a form saját eljárásaihoz hasonlóan fordulnak. ' +
      '.pll-ből: frmcmp_batch module=KONYVTAR.pll module_type=LIBRARY script=YES'
    );
  }

  onSources(event: Event): void {
    const input = event.target as HTMLInputElement;
    const picked = Array.from(input.files ?? []);
    input.value = '';
    this.selectSources(picked);
  }

  removeSource(source: File): void {
    this.files.update((files) => files.filter((f) => f !== source));
    if (this.files().length <= 1)
      this.form.controls.AWU_AZON.setValue(
        this.files().length ? (this.batchAwu()[this.files()[0].name] ?? '') : '',
      );
    this.syncModuleFolders();
  }

  clearSources(): void {
    this.files.set([]);
    this.pld.set([]);
    this.batchAwu.set({});
    this.form.controls.AWU_AZON.setValue('');
    this.syncModuleFolders();
  }

  removeLibrary(library: File): void {
    this.pld.update((files) => files.filter((f) => f !== library));
  }

  private selectSources(picked: File[]): void {
    this.error.set('');
    this.notice.set('');
    const limit = this.defaults()?.limits.file_bytes ?? 33554432;
    const rejected: string[] = [];
    const libraries = picked.filter((f) => /\.(pld|pll)$/i.test(f.name) && f.size > 0 && f.size <= limit);
    if (libraries.length)
      this.pld.update((files) => [
        ...files.filter((f) => !libraries.some((l) => l.name.toLowerCase() === f.name.toLowerCase())),
        ...libraries,
      ]);
    picked = picked.filter((f) => !libraries.includes(f));
    const accepted = picked.filter((f) => {
      const ok =
        /\.(fmb|xml)$/i.test(f.name) &&
        !/_(olb|mmb)\.xml$/i.test(f.name) &&
        f.size > 0 &&
        f.size <= limit;
      if (!ok) rejected.push(f.name);
      return ok;
    });
    // Same name twice would overwrite each other's result folder: the later pick wins.
    const previous = this.files();
    if (previous.length === 1)
      this.batchAwu.update((values) => ({
        ...values,
        [previous[0].name]: this.form.controls.AWU_AZON.value,
      }));
    this.files.update((files) => [
      ...files.filter((f) => !accepted.some((a) => a.name === f.name)),
      ...accepted,
    ]);
    if (rejected.length)
      this.error.set(
        'Kihagyva (nem .fmb/.xml form, OLB/MMB, üres vagy ' +
        this.bytes(limit) +
        ' fölötti): ' +
        rejected.join(', '),
      );
    if (this.files().length > 1) this.screenOverrides.set(null);
    this.syncModuleFolders();
  }

  onFile(event: Event, kind: Upload): void {
    const input = event.target as HTMLInputElement;
    const picked = input.files?.[0];
    if (picked) this.selectFile(picked, kind);
    input.value = '';
  }

  onCompanions(event: Event, kind: 'olb' | 'mmb'): void {
    const input = event.target as HTMLInputElement;
    const files = Array.from(input.files ?? []);
    input.value = '';
    if (files.some((f) => !f.name.toLowerCase().endsWith('_' + kind + '.xml'))) {
      this.error.set('Őrizd meg az export fájlnevét: <név>_' + kind + '.xml');
      return;
    }
    if (kind === 'olb') this.olb.set(files);
    else this.mmb.set(files[0] ?? null);
  }

  drop(event: DragEvent): void {
    event.preventDefault();
    this.dragging.set(false);
    this.selectSources(Array.from(event.dataTransfer?.files ?? []));
  }

  clearCompanions(): void {
    this.olb.set([]);
    this.mmb.set(null);
    this.screenOverrides.set(null);
    this.schema.set(null);
    this.rules.set(null);
    this.fieldLengths.set(null);
  }

  private selectFile(picked: File, kind: Upload): void {
    this.error.set('');
    this.notice.set('');
    const limit = this.defaults()?.limits.json_bytes ?? 1048576;
    if (!/\.json$/i.test(picked.name)) {
      this.error.set('.json fájlt válassz.');
      return;
    }
    if (!picked.size || picked.size > limit) {
      this.error.set('A fájl üres vagy nagyobb a megengedettnél: ' + this.bytes(limit));
      return;
    }
    ({
      schema: this.schema,
      rules: this.rules,
      screenOverrides: this.screenOverrides,
      fieldLengths: this.fieldLengths,
    })[kind].set(picked);
  }

  async demo(): Promise<void> {
    this.error.set('');
    try {
      const [xml, schema] = await Promise.all([
        firstValueFrom(
          this.http.get(this.url('/examples/customer_fmb.xml'), { responseType: 'blob' }),
        ),
        firstValueFrom(this.http.get(this.url('/examples/schema.json'), { responseType: 'blob' })),
      ]);
      this.files.set([new File([xml], 'customer_fmb.xml', { type: 'application/xml' })]);
      this.clearCompanions();
      this.schema.set(new File([schema], 'schema.json', { type: 'application/json' }));
      this.form.patchValue({ module: '', AWU_AZON: '' });
      this.batchAwu.set({});
      this.notice.set('A példa betöltve. A generálás a közös AI-kapcsolatot használja.');
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  switchView(value: string | number | undefined): void {
    const view = value === 'assistant' ? 'assistant' : value === 'jobs' ? 'jobs' : 'generate';
    this.view.set(view);
    if (typeof location !== 'undefined') {
      const url = new URL(location.href); url.searchParams.set('view', view);
      if (view === 'assistant' && this.assistantJobId()) url.searchParams.set('job', this.assistantJobId()!);
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
  }

  openAssistant(job: Job): void {
    if (job.status !== 'completed') return;
    if ((this.assistantBusy() || this.assistantDirty()) && this.assistantJobId() !== job.id) {
      this.error.set('Előbb mentsd vagy vesd el az asszisztens vázlatát, és várd meg a futó feladatot.'); return;
    }
    this.assistantJobId.set(job.id); this.detailOpen.set(false); this.switchView('assistant');
  }

  async assistantUpdated(id: string): Promise<void> {
    await this.refreshJobs();
    const selected = this.selectedJob();
    if (selected?.id === id && this.detailOpen()) await this.loadDetails(selected);
  }

  private savedAiConnection(): Partial<Pick<Options, 'ollama_url' | 'ollama_model'>> {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(AI_KEY) ?? '{}');
      if (!saved || typeof saved !== 'object') return {};
      const value = saved as Record<string, unknown>;
      return typeof value['ollama_url'] === 'string' && typeof value['ollama_model'] === 'string'
        ? {ollama_url: value['ollama_url'], ollama_model: value['ollama_model']} : {};
    } catch { return {}; }
  }

  async saveAiConnection(): Promise<void> {
    if (this.checkingAi()) return;
    this.error.set('');
    try {
      const url = ollamaBaseUrl(this.form.controls.ollama_url.value);
      const model = this.form.controls.ollama_model.value.trim();
      if (!model || !/^[A-Za-z0-9_./:@-]{1,120}$/.test(model)) throw new Error('Add meg a modell pontos Ollama-nevét.');
      this.checkingAi.set(true);
      const connection = await firstValueFrom(this.http.post<{url: string}>(this.url('/ollama/check'), {url, model}, this.mutation));
      this.form.patchValue({ollama_url: connection.url, ollama_model: model});
      localStorage.setItem(AI_KEY, JSON.stringify({ollama_url: connection.url, ollama_model: model}));
      this.notice.set('Az Ollama elérhető, a modell megtalálható. A közös AI-kapcsolat elmentve.');
    } catch (error) { this.error.set(this.errorText(error)); }
    finally { this.checkingAi.set(false); }
  }

  savePreferences(): void {
    this.form.markAllAsTouched();
    if (this.form.invalid) {
      this.error.set('Javítsd a jelölt beállításokat.');
      return;
    }
    try {
      const {ollama_url, ollama_model, ...preferences} = this.form.getRawValue();
      localStorage.setItem(OPTIONS_KEY, JSON.stringify({...preferences, AWU_AZON: ''}));
      localStorage.setItem(AI_KEY, JSON.stringify({ollama_url, ollama_model}));
      this.notice.set('A beállítások ebben a böngészőben elmentve.');
    } catch {
      this.error.set('A böngésző nem engedélyezi a beállítások mentését.');
    }
  }

  async submit(): Promise<void> {
    this.form.markAllAsTouched();
    const sources = this.files();
    if (!sources.length || this.form.invalid || this.uploading()) {
      this.error.set('Válassz forrásfájlt, és ellenőrizd a beállításokat.');
      return;
    }
    if (this.awuBlocker()) {
      this.error.set(this.awuBlocker());
      return;
    }
    this.uploading.set(true);
    this.error.set('');
    this.notice.set('');
    this.batchProgress.set(null);
    try {
      const survey = this.survey();
      const options: Options = {
        ...this.form.getRawValue(),
        ai_mode: 'assist',
        max_ai_calls: Math.max(1, this.form.controls.max_ai_calls.value),
        module: sources.length === 1 && !survey ? this.form.controls.module.value || null : null,
        ...(survey
          ? { AWU_AZON: '', screen_window_selection: 'all' as const, screen_primary_window_auto: true }
          : {}),
      };
      if (sources.length === 1 && !survey) {
        // A modul saját mappái: a Java-csomagok ezekből, a telepítés ide.
        const layout = this.generationLayout();
        const job = await firstValueFrom(
          this.http.post<Job>(
            this.url('/jobs'),
            this.jobBody(sources[0], { ...options, project_layout: layout }, null),
            this.mutation,
          ),
        );
        this.rememberModuleFolders(layout);
        this.jobs.update((jobs) => [job, ...jobs]);
        this.view.set('jobs');
        await this.openJob(job);
      } else {
        await this.uploadBatch(sources, options);
      }
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.uploading.set(false);
    }
  }

  stopBatch(): void {
    this.stopRequested = true;
  }

  setBatchAwu(source: File, event: Event): void {
    const value = (event.target as HTMLInputElement).value;
    this.batchAwu.update((values) => ({ ...values, [source.name]: value }));
  }

  // One job per form, sequentially. A full queue (HTTP 429) is waited out, not reported as an error.
  private async uploadBatch(sources: File[], options: Options): Promise<void> {
    const awuByFile = { ...this.batchAwu() };
    const batch = 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
    this.stopRequested = false;
    this.batchProgress.set({ done: 0, total: sources.length, waiting: false, failed: [] });
    for (const source of sources) {
      if (this.stopRequested || this.destroyed) break;
      for (;;) {
        try {
          const job = await firstValueFrom(
            this.http.post<Job>(
              this.url('/jobs'),
              this.jobBody(
                source,
                this.survey() ? options : { ...options, AWU_AZON: awuByFile[source.name] },
                batch,
              ),
              this.mutation,
            ),
          );
          this.jobs.update((jobs) => [job, ...jobs]);
          break;
        } catch (error) {
          if (
            error instanceof HttpErrorResponse &&
            error.status === 429 &&
            !this.stopRequested &&
            !this.destroyed
          ) {
            this.batchProgress.update((p) => p && { ...p, waiting: true });
            await new Promise((resolve) => setTimeout(resolve, 2000));
            await this.refreshJobs();
            continue;
          }
          this.batchProgress.update(
            (p) => p && { ...p, failed: [...p.failed, source.name + ': ' + this.errorText(error)] },
          );
          break;
        }
      }
      this.batchProgress.update((p) => p && { ...p, done: p.done + 1, waiting: false });
    }
    const progress = this.batchProgress();
    if (progress && progress.done - progress.failed.length > 0) {
      this.notice.set(
        `${this.survey() ? 'Felmérés' : 'Tömeges futtatás'} elindítva: ${progress.done - progress.failed.length} form a feladatsorban.` +
        (this.stopRequested ? ' A feltöltést leállítottad.' : ''),
      );
      this.view.set('jobs');
      await this.openBatch(batch);
    }
  }

  private jobBody(source: File, options: Options, batch: string | null): FormData {
    const body = new FormData();
    body.append('file', source);
    body.append('options', JSON.stringify(options));
    // Libraries and schema are chosen in survey mode only (the simplified form hides them).
    const survey = this.survey();
    if (survey) for (const companion of this.olb()) body.append('olb_files', companion);
    for (const library of this.pld()) body.append('pld_files', library);
    const mmb = this.mmb(),
      schema = survey ? this.schema() : null,
      rules = this.rules(),
      overrides = this.screenOverrides(),
      lengths = this.fieldLengths();
    // Keyed by formControlName: one file serves every form of a batch.
    if (lengths) body.append('field_lengths_file', lengths);
    if (mmb) body.append('mmb_file', mmb);
    if (schema) body.append('schema_file', schema);
    if (rules) body.append('rules_file', rules);
    // Overrides are fingerprinted per form: only for a single source.
    if (overrides && !batch) body.append('screen_overrides_file', overrides);
    if (batch) body.append('batch', batch);
    return body;
  }

  // ------------------------------------------------------------- decisions --
  windowOptions(question: Question): { label: string; value: string }[] {
    return question.choices.map((c) => ({
      label:
        (c.title && c.title !== c.name ? c.title + ' (' + c.name + ')' : c.name) +
        (c.blocks.length ? ' · ' + c.blocks.join(', ') : '') +
        (c.first_navigation ? ' · kezdő blokk' : ''),
      value: c.name,
    }));
  }

  async answer(job: Job): Promise<void> {
    const window = this.windowControl.value;
    if (!window || this.answering()) return;
    this.answering.set(true);
    this.error.set('');
    try {
      const updated = await firstValueFrom(
        this.http.post<Job>(
          this.url(`/jobs/${job.id}/answer`),
          { screen_primary_window: window },
          this.mutation,
        ),
      );
      this.replace(updated);
      this.notice.set(`A fő képernyő: ${window}. A generálás folytatódik.`);
      const next = this.waitingJobs().find(
        (j) => j.id !== job.id && j.batch && j.batch === job.batch,
      );
      if (next) await this.openJob(next); // the next decision of the same batch
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.answering.set(false);
    }
  }

  // Ask as soon as a job waits for a decision, but only once per job.
  private askWaiting(): void {
    if (this.detailOpen() || this.batchOpen() || this.uploading()) return;
    const waiting = this.waitingJobs().find((job) => !this.asked.has(job.id));
    if (waiting) void this.openJob(waiting);
  }

  // ----------------------------------------------------------------- batch --
  toggleWindow(name: string): void {
    this.chosenWindows.update((list) =>
      list.includes(name) ? list.filter((n) => n !== name) : [...list, name],
    );
  }

  chooseAllWindows(job: Job): void {
    this.chosenWindows.set(job.question?.choices.map((c) => c.name) ?? []);
  }

  windowPreview(job: Job, choice: Choice): SafeHtml {
    const key = job.id + '/' + choice.name;
    const cached = this.previews.get(key);
    if (cached) return cached;
    const html = this.sanitizer.bypassSecurityTrustHtml(choice.preview ?? '');
    this.previews.set(key, html);
    return html;
  }

  async answerWindows(job: Job): Promise<void> {
    const windows = this.chosenWindows();
    if (!windows.length || this.answering()) return;
    this.answering.set(true);
    this.error.set('');
    try {
      const updated = await firstValueFrom(
        this.http.post<Job>(
          this.url(`/jobs/${job.id}/answer`),
          { screen_windows: windows },
          this.mutation,
        ),
      );
      this.replace(updated);
      this.notice.set(`Generált ablakok: ${windows.join(', ')}. A generálás folytatódik.`);
      const next = this.waitingJobs().find(
        (j) => j.id !== job.id && j.batch && j.batch === job.batch,
      );
      if (next) await this.openJob(next); // the next decision of the same batch
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.answering.set(false);
    }
  }

  async openBatch(batch: string): Promise<void> {
    this.batchReport.set(null);
    this.surveyResult.set(null);
    this.batchOpen.set(true);
    await this.refreshBatch(batch);
  }

  private async refreshBatch(batch: string): Promise<void> {
    try {
      const report = await firstValueFrom(
        this.http.get<BatchReport>(this.url(`/batches/${encodeURIComponent(batch)}`)),
      );
      if (this.batchOpen() && (!this.batchReport() || this.batchReport()!.batch === batch)) {
        this.batchReport.set(report);
        if (report.done && this.surveyResult()?.batch !== batch && !this.surveyLoading())
          void this.loadSurvey(batch);
      }
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  private async loadSurvey(batch: string, names = false): Promise<SurveyResult | null> {
    this.surveyLoading.set(true);
    try {
      const result = await firstValueFrom(
        this.http.get<SurveyResult>(this.url(`/batches/${encodeURIComponent(batch)}/survey`), {
          params: new HttpParams().set('names', String(names)),
        }),
      );
      if (!names && this.batchReport()?.batch === batch) this.surveyResult.set(result);
      return result;
    } catch (error) {
      this.error.set(this.errorText(error));
      return null;
    } finally {
      this.surveyLoading.set(false);
    }
  }

  async downloadSurvey(batch: string, kind: 'md' | 'json', names = false): Promise<void> {
    const cached = this.surveyResult();
    const result = !names && cached?.batch === batch ? cached : await this.loadSurvey(batch, names);
    if (!result) return;
    const suffix = names ? '_nevekkel' : '';
    if (kind === 'md') this.saveText(result.markdown, `FELMERES_HU${suffix}.md`);
    else
      this.saveBlob(
        new Blob([JSON.stringify(result.report, null, 2)], { type: 'application/json' }),
        `felmeres${suffix}.json`,
      );
  }

  async nextQuestion(report: BatchReport): Promise<void> {
    const waiting = report.jobs.find((j) => j.status === 'needs_input');
    if (waiting) {
      this.batchOpen.set(false);
      await this.openJobById(waiting.id);
    }
  }

  async openJobById(id: string): Promise<void> {
    const job = this.jobs().find((j) => j.id === id);
    if (job) {
      this.batchOpen.set(false);
      await this.openJob(job);
    }
  }

  batchMetrics(report: BatchReport): { label: string; value: string | number }[] {
    const t = report.report.totals;
    return [
      { label: 'generált végpont', value: t['endpoints'] ?? 0 },
      { label: 'kész, MODULE_REVIEWED-re vár', value: t['ready'] ?? 0 },
      { label: 'saját okkal tiltott', value: t['blocked'] ?? 0 },
      { label: 'átültetendő trigger', value: t['review'] ?? 0 },
    ];
  }

  async downloadBatch(batch: string): Promise<void> {
    if (this.downloading()) return;
    this.downloading.set('batch');
    try {
      const result = await firstValueFrom(
        this.http.get(this.url(`/batches/${encodeURIComponent(batch)}/download`), {
          responseType: 'blob',
        }),
      );
      this.saveBlob(result, 'tomeges-' + batch + '.zip');
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.downloading.set('');
    }
  }

  // ------------------------------------------------------------ project folders --
  private folderControls(): Record<PartKey, FormControl<string>> {
    return Object.fromEntries(PART_KEYS.map((key) => [key, new FormControl('', { nonNullable: true })])) as Record<
      PartKey,
      FormControl<string>
    >;
  }

  part(key: PartKey | undefined): { key: PartKey; label: string; title: string; hint: string; help: string } | undefined {
    return this.parts.find((part) => part.key === key);
  }

  packageOf(path: string): string | null {
    return folderPackage(path.trim());
  }

  packageText(path: string): string {
    const pkg = this.packageOf(path);
    return pkg ? 'csomag: ' + pkg : 'Ebből az útvonalból nem állapítható meg a csomag: a src/main/java alatti modulmappát válaszd.';
  }

  /** A kiválasztott mappák (csak a kitöltöttek). */
  private layoutOf(controls: Record<PartKey, FormControl<string>>): Partial<Record<PartKey, string>> {
    const layout: Partial<Record<PartKey, string>> = {};
    for (const key of PART_KEYS) if (controls[key].value.trim()) layout[key] = controls[key].value.trim();
    return layout;
  }

  /** A modulnév (vagy az egyetlen forrás) a megjegyzett mappák kulcsa. */
  private moduleKey(): string {
    const files = this.files();
    return (this.form.controls.module.value.trim() || (files.length === 1 ? files[0].name.replace(/\.[^.]+$/, '') : '')).toLowerCase();
  }

  /** Másik modul: az ő megjegyzett mappái; ha nincs ilyen, egy másik modul emlékéből jött mappák törlődnek. */
  syncModuleFolders(): void {
    const key = this.moduleKey();
    if (key === this.folderKey) return;
    this.folderKey = key;
    const remembered = key ? this.folderMemory[key] : undefined;
    if (remembered) {
      for (const part of PART_KEYS) this.generateFolders[part].setValue(remembered[part] ?? '');
      this.foldersFromMemory = true;
    } else if (this.foldersFromMemory) {
      for (const part of PART_KEYS) this.generateFolders[part].setValue('');
      this.foldersFromMemory = false;
    }
  }

  private rememberModuleFolders(layout: Partial<Record<PartKey, string>>): void {
    const key = this.moduleKey();
    if (!key || !Object.keys(layout).length) return;
    this.folderMemory = { ...this.folderMemory, [key]: layout };
    try { localStorage.setItem(FOLDERS_KEY, JSON.stringify(this.folderMemory)); } catch { /* privát mód */ }
  }

  /** A generálás célmappái a kérésbe (egy form esetén). */
  generationLayout(): Partial<Record<PartKey, string>> {
    return this.layoutOf(this.generateFolders);
  }

  // ------------------------------------------------------------ deploy into the project --
  private openDeploy(job: Job): void {
    const layout = job.options.project_layout ?? {};
    for (const key of PART_KEYS) this.deployFolders[key].setValue(layout[key] ?? '');
    this.deployResult.set(null);
  }

  deployChosen(): boolean {
    return Object.keys(this.layoutOf(this.deployFolders)).length > 0;
  }

  deployHint(): string {
    const layout = this.layoutOf(this.deployFolders);
    if (!Object.keys(layout).length) return 'Tallózd ki a modul mappáit.';
    const missing = PART_KEYS.filter((key) => !layout[key]);
    return missing.length
      ? 'Nincs kiválasztva: ' + missing.map((key) => this.part(key)?.label ?? key).join(', ') + ' – ezek fájljai kimaradnak.'
      : '';
  }

  deployResultFor(target: DeployTarget): DeployReport | null {
    const result = this.deployResult();
    return result?.key === target.kind + ':' + target.id ? result.report : null;
  }

  /** Telepíteni csak az ugyanezekre a mappákra készült előnézet után lehet. */
  deployReady(target: DeployTarget): boolean {
    const result = this.deployResult();
    return !!result && result.key === target.kind + ':' + target.id && result.report.dry_run
      && result.folders === JSON.stringify(this.layoutOf(this.deployFolders));
  }

  deployLayout(report: DeployReport): { key: string; label: string; value: string | null }[] {
    return Object.entries(report.layout).map(([key, value]) => ({ key, label: this.part(key as PartKey)?.label ?? key, value }));
  }

  deployCounts(report: DeployReport): { key: string; label: string; value: number; severity: 'success' | 'info' | 'warn' | 'danger' | 'secondary' }[] {
    const severities: Record<string, 'success' | 'info' | 'warn' | 'danger' | 'secondary'> = {
      new: 'success', updated: 'success', overwritten: 'warn', unchanged: 'secondary', kept: 'info',
      conflict: 'danger', skipped: 'warn',
    };
    return Object.entries(report.counts).map(([key, value]) => ({
      key, label: this.deployStatus(key), value, severity: severities[key] ?? 'secondary',
    }));
  }

  deployStatus(status: string): string {
    return {
      new: 'új', updated: 'frissítve', unchanged: 'változatlan', kept: 'megőrizve (CREATE_ONCE)',
      conflict: 'ütközés – nem írtuk felül', overwritten: 'felülírva', skipped: 'kihagyva',
    }[status] ?? status;
  }

  helperStatus(helper: DeployHelper): string {
    return {
      ok: 'megvan a projektben',
      outdated: `régebbi változat (${helper.found_version}) van a projektben: töltsd le az újat (${helper.version}), és cseréld le`,
      newer: `újabb változat (${helper.found_version}) van a projektben, mint amit ez a generálás vár (${helper.version})`,
      missing: 'nincs a projektben: töltsd le, és tedd ide',
      unknown: 'a rész nincs kiválasztva',
    }[helper.status];
  }

  helperSeverity(helper: DeployHelper): 'success' | 'warn' | 'danger' | 'secondary' {
    return ({ ok: 'success', outdated: 'warn', newer: 'warn', missing: 'danger', unknown: 'secondary' } as const)[helper.status];
  }

  async downloadHelper(jobId: string, name: HelperName): Promise<void> {
    if (this.downloading()) return;
    this.downloading.set(name);
    try {
      const blob = await firstValueFrom(
        this.http.get(this.url(`/jobs/${jobId}/helpers/${encodeURIComponent(name)}`), { responseType: 'blob' }),
      );
      this.saveBlob(blob, name);
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.downloading.set('');
    }
  }

  async runDeploy(target: DeployTarget, dryRun: boolean): Promise<void> {
    const layout = this.layoutOf(this.deployFolders);
    if (!Object.keys(layout).length || this.deploying()) return;
    this.deploying.set(dryRun ? 'preview' : 'deploy');
    this.error.set('');
    try {
      const report = await firstValueFrom(
        this.http.post<DeployReport>(this.url(`/jobs/${target.id}/deploy`), { layout, dry_run: dryRun, force: false }, this.mutation),
      );
      this.deployResult.set({ key: target.kind + ':' + target.id, folders: JSON.stringify(layout), report });
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.deploying.set('');
    }
  }

  // ------------------------------------------------------------ folder choice ("Tallózás…") --
  folderKind(kind: FolderKind): string {
    return kind === 'java' ? 'Java-projekt' : kind === 'angular' ? 'Angular-projekt' : '';
  }

  private controlsOf(scope: FolderScope): Record<PartKey, FormControl<string>> {
    return scope === 'generate' ? this.generateFolders : this.deployFolders;
  }

  /** Honnan induljon a tallózás: a rész eddigi mappája, különben egy másik rész mellől. */
  private browseStart(scope: FolderScope, key: PartKey): string | null {
    const controls = this.controlsOf(scope);
    const own = controls[key].value.trim();
    if (own) return own;
    const other = PART_KEYS.map((k) => controls[k].value.trim()).find((value) => value)
      ?? Object.values(this.folderMemory).flatMap((layout) => Object.values(layout)).find((value) => value);
    return other ? other.replace(/[\\/][^\\/]+[\\/]?$/, '') || other : null;
  }

  private chooseFolder(scope: FolderScope, key: PartKey, path: string): void {
    this.controlsOf(scope)[key].setValue(path);
    if (scope === 'generate') this.foldersFromMemory = false;
  }

  async browseFolder(scope: FolderScope, key: PartKey): Promise<void> {
    if (this.picking() || this.deploying()) return;
    const initial = this.browseStart(scope, key);
    if (this.defaults()?.folders?.dialog !== false && !this.folderDialogMissing) {
      this.picking.set({ scope, key });
      try {
        const picked = await firstValueFrom(this.http.post<{ path: string | null; cancelled: boolean }>(
          this.url('/fs/pick'), { title: this.part(key)?.title ?? 'Mappa kiválasztása', initial }, this.mutation));
        if (picked.path) this.chooseFolder(scope, key, picked.path);
        return;
      } catch (error) {
        if (!(error instanceof HttpErrorResponse && error.status === 501)) {
          this.error.set(this.errorText(error));
          return;
        }
        this.folderDialogMissing = true;  // nincs mappaválasztó ablak ezen a gépen: innentől a beépített böngésző
      } finally {
        this.picking.set(null);
      }
    }
    await this.openBrowser(scope, key, initial);
  }

  /** A nyitva lévő mappaválasztó ablak bezárása; browser: helyette a beépített böngésző. */
  async cancelPick(browser: boolean): Promise<void> {
    const picking = this.picking();
    if (!picking) return;
    this.picking.set(null);
    try {
      await firstValueFrom(this.http.post(this.url('/fs/pick/cancel'), {}, this.mutation));
    } catch { /* már bezárult */ }
    if (browser) await this.openBrowser(picking.scope, picking.key, this.browseStart(picking.scope, picking.key));
  }

  async openBrowser(scope: FolderScope, key: PartKey, initial: string | null): Promise<void> {
    this.browserFor.set({ scope, key });
    this.browser.set(null);
    this.browserPath = '';
    this.browserOpen.set(true);
    if (!(await this.browseTo(initial ?? '')) && initial) await this.browseTo('');
  }

  async browseTo(path: string): Promise<boolean> {
    this.browserLoading.set(true);
    this.browserError.set('');
    try {
      const listing = await firstValueFrom(
        this.http.post<FolderListing>(this.url('/fs/folders'), { path: path.trim() || null }, this.mutation),
      );
      this.browser.set(listing);
      this.browserPath = listing.path ?? '';
      return true;
    } catch (error) {
      this.browserError.set(this.errorText(error));
      return false;
    } finally {
      this.browserLoading.set(false);
    }
  }

  chooseBrowsed(): void {
    const target = this.browserFor();
    const path = this.browser()?.path;
    if (!target || !path) return;
    this.chooseFolder(target.scope, target.key, path);
    this.browserOpen.set(false);
  }

  saveText(text: string, name: string): void {
    this.saveBlob(new Blob([text], { type: 'text/markdown;charset=utf-8' }), name);
  }

  private saveBlob(blob: Blob, name: string): void {
    const href = URL.createObjectURL(blob),
      anchor = document.createElement('a');
    anchor.href = href;
    anchor.download = name;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  // ------------------------------------------------------------ job detail --
  async openJob(job: Job): Promise<void> {
    this.selectedId.set(job.id);
    this.clearDetails();
    this.openDeploy(job);
    this.error.set('');
    this.detailOpen.set(true);
    if (job.status === 'needs_input') {
      this.asked.add(job.id);
      const first =
        job.question?.choices.find((c) => c.first_navigation) ?? job.question?.choices[0];
      this.windowControl.setValue(first?.name ?? null);
      if (job.question?.kind === 'windows')
        this.chosenWindows.set(job.question.choices.map((c) => c.name));
    }
    if (job.status === 'completed') await this.loadDetails(job);
    if (job.status === 'failed' || job.status === 'interrupted') await this.loadLogs(job);
  }

  async switchTab(tab: string | number | undefined): Promise<void> {
    const next = (
      tab === 'files' || tab === 'logs' || tab === 'preview' ? tab : 'overview'
    ) as DetailTab;
    this.detailTab.set(next);
    const job = this.selectedJob();
    if (!job) return;
    if (next === 'logs') await this.loadLogs(job);
    if (next === 'preview' && !this.screenHtml()) await this.loadScreenPreview(job);
    if (next === 'files' && !this.preview() && this.sourceFiles().length) {
      await this.openFile(
        this.sourceFiles().find((f) => f.path.endsWith('.component.ts')) ?? this.sourceFiles()[0],
      );
    }
  }

  async openFile(entry: SourceFile | null): Promise<void> {
    const job = this.selectedJob();
    if (!job || !entry) return;
    if (this.fileControl.value?.path !== entry.path)
      this.fileControl.setValue(entry, { emitEvent: false });
    const ticket = ++this.previewTicket;
    this.previewLoading.set(true);
    try {
      const result = await firstValueFrom(
        this.http.get<Preview>(this.url(`/jobs/${job.id}/file`), {
          params: new HttpParams().set('path', entry.path),
        }),
      );
      if (ticket === this.previewTicket && job.id === this.selectedId()) this.preview.set(result);
    } catch (error) {
      if (ticket === this.previewTicket) this.error.set(this.errorText(error));
    } finally {
      if (ticket === this.previewTicket) this.previewLoading.set(false);
    }
  }

  browse(input: HTMLInputElement): void {
    if (!this.uploading()) input.click();
  }

  // Folders open and close; a folder holding a single folder opens with it (frontend/<modul>/).
  activateRow(row: CodeRow): void {
    if (row.file) {
      void this.openCode(row.file);
      return;
    }
    const folder = this.findFolder(row.key);
    this.expandedFolders.update((current) => {
      const next = new Set(current);
      if (next.has(row.key)) next.delete(row.key);
      else {
        next.add(row.key);
        let inner: CodeFolder | undefined = folder;
        while (inner && !inner.files.length && inner.folders.size === 1) {
          inner = inner.folders.values().next().value;
          if (inner) next.add(inner.key);
        }
      }
      return next;
    });
  }

  async openCode(file: SourceFile): Promise<void> {
    const job = this.selectedJob();
    if (!job) return;
    this.viewerFile.set(file);
    this.viewerError.set('');
    this.copyState.set('idle');
    this.viewerOpen.set(true);
    if (this.preview()?.path === file.path) return;
    const ticket = ++this.viewerTicket;
    this.preview.set(null);
    this.viewerLoading.set(true);
    try {
      const result = await firstValueFrom(
        this.http.get<Preview>(this.url(`/jobs/${job.id}/file`), {
          params: new HttpParams().set('path', file.path),
        }),
      );
      if (ticket === this.viewerTicket && job.id === this.selectedId()) this.preview.set(result);
    } catch (error) {
      if (ticket === this.viewerTicket) this.viewerError.set(this.errorText(error));
    } finally {
      if (ticket === this.viewerTicket) this.viewerLoading.set(false);
    }
  }

  async stepCode(offset: number): Promise<void> {
    const next = this.codeFiles()[this.viewerIndex() + offset];
    if (next) await this.openCode(next);
  }

  readonly logCopyState = signal<'idle' | 'done' | 'failed'>('idle');
  readonly logCopyLabel = computed(
    () => ({ idle: 'Másolás', done: 'Kimásolva', failed: 'Nem sikerült' })[this.logCopyState()],
  );
  private logCopyTimer?: ReturnType<typeof setTimeout>;

  async copyLogs(): Promise<void> {
    clearTimeout(this.logCopyTimer);
    try {
      await navigator.clipboard.writeText(this.logs());
      this.logCopyState.set('done');
    } catch {
      this.logCopyState.set('failed');
    }
    this.logCopyTimer = setTimeout(() => this.logCopyState.set('idle'), 1800);
  }

  saveLogs(job: Job): void {
    this.saveBlob(new Blob([this.logs()], { type: 'text/plain;charset=utf-8' }), 'naplo-' + job.id + '.txt');
  }

  async copyCode(): Promise<void> {
    clearTimeout(this.copyTimer);
    try {
      await navigator.clipboard.writeText(this.preview()?.text ?? '');
      this.copyState.set('done');
    } catch {
      this.copyState.set('failed');
    }
    this.copyTimer = setTimeout(() => this.copyState.set('idle'), 1800);
  }

  saveCode(): void {
    const preview = this.preview();
    if (preview)
      this.saveBlob(new Blob([preview.text], { type: 'text/plain;charset=utf-8' }), fileName(preview.path));
  }

  private findFolder(key: string): CodeFolder | undefined {
    for (const group of this.codeTree()) {
      const stack: CodeFolder[] = [group.root];
      for (let folder = stack.pop(); folder; folder = stack.pop()) {
        if (folder.key === key) return folder;
        stack.push(...folder.folders.values());
      }
    }
    return undefined;
  }

  async cancel(job: Job): Promise<void> {
    try {
      const updated = await firstValueFrom(
        this.http.post<Job>(this.url(`/jobs/${job.id}/cancel`), null, this.mutation),
      );
      this.replace(updated);
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  async retry(job: Job): Promise<void> {
    try {
      const next = await firstValueFrom(
        this.http.post<Job>(this.url(`/jobs/${job.id}/retry`), null, this.mutation),
      );
      this.jobs.update((jobs) => [next, ...jobs]);
      await this.openJob(next);
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  async deleteJob(job: Job): Promise<void> {
    if (
      !window.confirm(
        `Törlöd a(z) ${job.filename} feladatot és az összes hozzá tartozó helyi fájlt?`,
      )
    )
      return;
    try {
      await firstValueFrom(this.http.delete(this.url(`/jobs/${job.id}`), this.mutation));
      this.jobs.update((jobs) => jobs.filter((j) => j.id !== job.id));
      if (this.selectedId() === job.id) {
        this.selectedId.set(null);
        this.detailOpen.set(false);
        this.clearDetails();
      }
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  async download(job: Job, kind: 'all' | 'frontend' | 'backend'): Promise<void> {
    if (this.downloading()) return;
    this.downloading.set(kind);
    try {
      const result = await firstValueFrom(
        this.http.get(this.url(`/jobs/${job.id}/download`), {
          params: { kind },
          responseType: 'blob',
          observe: 'response',
        }),
      );
      if (!result.body) throw new Error('Üres letöltési válasz.');
      this.saveBlob(
        result.body,
        `${job.download_name ?? job.module ?? 'module'}${kind === 'all' ? '' : '-' + kind}.zip`,
      );
    } catch (error) {
      this.error.set(this.errorText(error));
    } finally {
      this.downloading.set('');
    }
  }

  async clearCache(): Promise<void> {
    if (
      !window.confirm(
        'Törlöd a helyi AI-gyorsítótárat? A következő AI-futtatás új kéréseket igényelhet.',
      )
    )
      return;
    try {
      const result = await firstValueFrom(
        this.http.delete<{ deleted_entries: number }>(this.url('/cache'), this.mutation),
      );
      this.notice.set(`${result.deleted_entries} gyorsítótár-bejegyzés törölve.`);
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  private async loadDetails(job: Job): Promise<void> {
    try {
      const files = await firstValueFrom(
        this.http.get<{ files: SourceFile[] }>(this.url(`/jobs/${job.id}/files`)),
      );
      const issuePath = files.files.some((f) => f.path === 'analysis/issues-summary.json')
        ? 'analysis/issues-summary.json'
        : 'analysis/issues.json';
      const read = (path: string) =>
        firstValueFrom(
          this.http.get<Preview>(this.url(`/jobs/${job.id}/file`), {
            params: new HttpParams().set('path', path),
          }),
        );
      const [report, issues] = await Promise.all([read('migration-report.md'), read(issuePath)]);
      if (this.selectedId() !== job.id) return;
      this.sourceFiles.set(files.files);
      this.report.set(
        report.text +
        (report.truncated
          ? '\n\n[Előnézet: a teljes riport a letöltött csomagban található.]'
          : ''),
      );
      if (!issues.truncated) {
        const value = JSON.parse(issues.text) as Issue[] | { total: number; issues: Issue[] };
        this.issues.set(Array.isArray(value) ? value : value.issues);
        this.issueCount.set(Array.isArray(value) ? value.length : value.total);
      }
    } catch (error) {
      if (this.selectedId() === job.id) this.error.set(this.errorText(error));
    }
  }

  private async loadScreenPreview(job: Job): Promise<void> {
    this.screenLoading.set(true);
    this.screenMissing.set('');
    try {
      const html = await firstValueFrom(
        this.http.get(this.url(`/jobs/${job.id}/preview`), { responseType: 'text' }),
      );
      // Our own static, escaped HTML, rendered in an iframe with an empty sandbox (no scripts, no same-origin).
      if (this.selectedId() === job.id)
        this.screenHtml.set(this.sanitizer.bypassSecurityTrustHtml(html));
    } catch (error) {
      if (this.selectedId() === job.id)
        this.screenMissing.set(
          error instanceof HttpErrorResponse && error.status === 404
            ? 'Ehhez a feladathoz nem készült képernyő-előnézet.'
            : this.errorText(error),
        );
    } finally {
      if (this.selectedId() === job.id) this.screenLoading.set(false);
    }
  }

  private async loadLogs(job: Job): Promise<void> {
    try {
      const result = await firstValueFrom(
        this.http.get<{ text: string }>(this.url(`/jobs/${job.id}/logs`)),
      );
      if (this.selectedId() === job.id) this.logs.set(result.text || 'A napló üres: a folyamat nem írt bele semmit.');
    } catch (error) {
      this.error.set(this.errorText(error));
    }
  }

  private clearDetails(): void {
    this.previewTicket++;
    this.previewLoading.set(false);
    this.sourceFiles.set([]);
    this.preview.set(null);
    this.report.set('');
    this.issues.set([]);
    this.issueCount.set(0);
    this.logs.set('');
    this.detailTab.set('overview');
    this.fileControl.setValue(null, { emitEvent: false });
    this.screenHtml.set(null);
    this.screenLoading.set(false);
    this.screenMissing.set('');
    this.viewerTicket++;
    this.viewerOpen.set(false);
    this.viewerFile.set(null);
    this.viewerLoading.set(false);
    this.viewerError.set('');
    this.expandedFolders.set(new Set());
    this.codeFilterControl.setValue('');
  }

  private replace(updated: Job): void {
    this.jobs.update((jobs) => jobs.map((j) => (j.id === updated.id ? updated : j)));
  }

  // --------------------------------------------------------------- display --
  metrics(summary: Summary): { label: string; value: string | number }[] {
    return [
      { label: 'adatblokk', value: summary.blocks },
      { label: 'mező / gomb', value: summary.items },
      // { label: 'támogatott trigger', value: summary.converted_triggers + ' / ' + summary.triggers },
      // { label: 'AI-kérés', value: summary.ai.attempted_calls },
    ];
  }

  terminal(job: Job): boolean {
    return ['completed', 'failed', 'cancelled', 'interrupted'].includes(job.status);
  }

  statusLabel(job: Pick<Job, 'status'> & Partial<Job>): string {
    if (job.status === 'completed') return job.review_required ? 'Ellenőrzendő' : 'Elkészült';
    return (
      {
        queued: 'Várakozik',
        running: 'Folyamatban',
        needs_input: 'Döntésre vár',
        failed: 'Hibás',
        cancelled: 'Megszakítva',
        interrupted: 'Megszakadt',
      } as const
    )[job.status];
  }

  statusSeverity(job: Pick<Job, 'status'> & Partial<Job>): Severity {
    if (job.status === 'completed') return job.review_required ? 'warn' : 'success';
    return (
      {
        queued: 'secondary',
        running: 'info',
        needs_input: 'warn',
        failed: 'danger',
        cancelled: 'secondary',
        interrupted: 'warn',
      } as const
    )[job.status];
  }

  phaseLabel(phase: string): string {
    const labels: Record<string, string> = {
      queued: 'Várakozás a sorban',
      starting: 'Generálás indítása',
      exporting: 'Oracle FMB exportálása',
      parsing: 'Forms XML feldolgozása',
      analyzing: 'Szabályok és függőségek elemzése',
      ai: 'Ollama-javaslatok feldolgozása',
      java: 'Java package generálása',
      angular: 'Angular komponens generálása',
      reporting: 'Migrációs riport készítése',
      packaging: 'Letölthető csomagok készítése',
      cancelling: 'Generálás leállítása',
      needs_input: 'Döntésre vár: a fő képernyő kiválasztása',
    };
    return labels[phase] ?? phase;
  }

  bytes(value: number): string {
    return value < 1048576
      ? (value / 1024).toFixed(1) + ' KB'
      : (value / 1048576).toFixed(1) + ' MB';
  }

  private errorText(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      if (error.status === 0)
        return 'A backend nem érhető el. Ellenőrizd, hogy fut-e, és jó-e az API URL.';
      const body: unknown = error.error;
      if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string')
        return body.detail;
      return `A kérés nem sikerült (HTTP ${error.status}).`;
    }
    if (error instanceof EmptyError)
      return 'A kérés válasz nélkül ért véget (egy HTTP-interceptor elnyelhette a választ vagy a hibát).';
    return error instanceof Error ? error.message : 'A művelet nem sikerült.';
  }
}
