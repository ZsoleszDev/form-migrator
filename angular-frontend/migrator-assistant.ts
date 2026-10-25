import {
  Component, EventEmitter, HostListener, Input, OnChanges, OnDestroy, Output,
  SimpleChanges, computed, effect, inject, signal,
} from '@angular/core';
import { FormsModule, FormControl, ReactiveFormsModule } from '@angular/forms';
import { HttpBackend, HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { CheckboxModule } from '@openng/optimus-ui/checkbox';
import { InputTextModule } from '@openng/optimus-ui/inputtext';
import { MessageModule } from '@openng/optimus-ui/message';
import { PanelModule } from '@openng/optimus-ui/panel';
import { ProgressBarModule } from '@openng/optimus-ui/progressbar';
import { SelectModule } from '@openng/optimus-ui/select';
import { TableModule } from '@openng/optimus-ui/table';
import { TagModule } from '@openng/optimus-ui/tag';
import { AiProgress, AiRun, AiSummary, MigratorAiQueue } from './migrator-ai-queue';

interface AssistantJob { id: string; status: string; module: string | null; filename: string }
interface Task {
  id: string; method: string; owner: string; kind: string; blockers: string[]; pending_tables: string[]; tables: string[];
  event?: string; block?: string | null; done?: boolean;
}
interface TableDefinition {
  columns: Record<string, Record<string, unknown>>; primary_key?: string[];
}
interface SchemaTable {
  table: string; status: string; reasons: string[]; definition: TableDefinition | null;
  references: {owner: string; operation: string; line?: number}[];
}
interface SchemaSheet { revision: string; tables: SchemaTable[]; pending: string[]; warnings: string[] }
interface Binding { kind: 'field' | 'parameter' | 'manual'; source?: string; note?: string }
interface InputRow {
  id: string; endpoint: string; source: string; location: string; status: string;
  automatic: boolean; timing?: string; binding?: Binding | null;
}
interface InputSheet {
  available: boolean; revision?: string; file_changed?: boolean; contract_changed?: boolean;
  rows: InputRow[]; sources: {owner: string; representation: string}[];
  bindings?: Record<string, Binding>; warnings: string[];
}
interface ProposalSummary { id: string; method: string; status: string; applied: boolean }
/** What the migrator took from the model's answer (assistant_reply.Reply.report). */
interface ExtractionReport {
  format?: string; status?: string; truncated?: boolean;
  blocks: {index: number; label: string; kind: string; target: string; lines: number; used: boolean; note: string}[];
  notes: string[]; warnings: string[]; problems: string[]; missing: string[];
}
interface Proposal extends ProposalSummary {
  apply_allowed: boolean; helper_notes: string[]; exchange_id?: string; extraction?: Partial<ExtractionReport>;
  response: {summary: string; missing_context: string[]; assumptions: string[]; test_cases: string[]};
  files: {target: string; candidate: string}[];
  validation: {java_parse: string; deterministic_format?: Record<string, {status: string; reason?: string}>};
}
interface Run {
  id: string; status: string; task_id?: string; error?: string; result?: Proposal; proposal_id?: string; exchange_id?: string | null;
  applied?: boolean; position?: number | null;
}
interface ExchangeSummary {
  id: string; task_id: string; method: string; status: string; started: string | null; attempts: number; cached: boolean;
  proposal_id?: string | null; proposal_status?: string | null; error?: string | null;
}
interface ExchangeAttempt {
  n: number; seconds: number | null; correction: string | null; raw: string | null; thinking: string | null;
  done_reason: string | null; prompt_tokens: number | null; output_tokens: number | null;
  extraction: ExtractionReport | null; error: string | null; note?: string;
  tokens_per_second?: number | null; first_token_seconds?: number | null;
}
/** One AI run: the prompt that was sent and every raw answer (assistant_log.Exchange). */
interface Exchange {
  id: string; task_id: string; method: string; status: string; started: string | null; finished: string | null;
  model: string; url: string; cached: boolean; error: string | null; result: {proposal_id: string; status: string} | null;
  options: {num_ctx?: number; num_predict?: number};
  prompt: {system: string; user: string; bytes: number; estimated_input_tokens: number; repositories: string[]; omitted_tables: string[]};
  attempts: ExchangeAttempt[];
  /** While the model works: its live state (phase, tokens, speed, the text so far). */
  live?: AiProgress;
}
interface PromptPreview {
  task_id: string; method: string; system: string; user: string; bytes: number; estimated_input_tokens: number;
  required_tokens: number; num_ctx: number; repositories: string[]; omitted_tables: string[]; original_bytes: number; warnings: string[];
}
interface AssistantIndex {
  tasks: Task[]; proposals: ProposalSummary[]; schema: SchemaSheet; input_bindings: InputSheet;
  default_instructions: string;
  active_run?: Run | null;
  recent_run?: Run | null;
  auto?: AiSummary;
  /** Applied tasks (no longer offered, shown as done in the overview). */
  done_tasks?: Task[];
}
interface ColumnDraft {
  key: number; name: string; data_type: string; nullable: boolean | null; pk: boolean;
  length: number | null; length_semantics: string; precision: number | null; scale: number | null;
  default: string; comment: string;
}
interface CsvPreview { table: string; columns: Omit<ColumnDraft, 'key' | 'pk'>[]; warnings: string[] }

const EMPTY_INPUTS: InputSheet = {available: false, rows: [], sources: [], warnings: []};
const ORACLE_TYPES = ['VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR', 'NUMBER', 'FLOAT', 'DATE', 'TIMESTAMP',
  'CLOB', 'NCLOB', 'BLOB', 'RAW', 'BINARY_FLOAT', 'BINARY_DOUBLE',
  'TIMESTAMP WITH TIME ZONE', 'TIMESTAMP WITH LOCAL TIME ZONE'];

/** Endpoint-scoped assistant using the migrator's API and shared Ollama controls. */
@Component({
  selector: 'app-migrator-assistant',
  imports: [FormsModule, ReactiveFormsModule, ButtonModule, CheckboxModule, InputTextModule,
    MessageModule, PanelModule, ProgressBarModule, SelectModule, TableModule, TagModule, MigratorAiQueue],
  host: {class: 'block min-w-0'},
  styles: `
    .as-table { min-width: 0; width: 100%; overflow-x: auto; -webkit-overflow-scrolling: touch; }
    .as-two { display: grid; gap: 1.25rem; grid-template-columns: minmax(0, 1fr); }
    @media (min-width: 64rem) { .as-two { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); } }
    .as-head { display: grid; gap: 0.75rem; grid-template-columns: minmax(0, 1fr); align-items: end; }
    @media (min-width: 48rem) { .as-head { grid-template-columns: minmax(0, 1fr) auto; } }
    .as-code { max-height: 28rem; overflow: auto; white-space: pre; font-size: 0.75rem; }
    @media (max-width: 40rem) { .as-code { max-height: 18rem; } }
    .as-live { max-height: 16rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 0.75rem; }
  `,
  template: `
    <div class="flex min-w-0 w-full flex-col gap-5" data-testid="assistant-panel">
      <p class="m-0 text-sm opacity-80">Válassz modult és triggert, töltsd ki a táblaszerkezetet, majd kattints a Trigger javásítása gombra.
        A migrátor entityt és repositoryt készít, az AI pedig átírja a DPS ServiceImpl kiválasztott metódusát.</p>
      @if (error()) { <p-message severity="error" [closable]="true" (onClose)="error.set('')">{{ error() }}</p-message> }
      @if (notice()) { <p-message severity="success">{{ notice() }}</p-message> }
      <div class="as-head">
        <label class="flex min-w-0 flex-col gap-2"><span class="font-medium">Generált modul</span>
          <p-select appendTo="body" [formControl]="jobControl" [options]="completedJobs()" optionValue="id" optionLabel="label"
            placeholder="Előbb generálj egy modult" ariaLabel="Asszisztens modul" class="w-full"
            (onChange)="selectJob($event.value)" />
        </label>
        <p-button label="Modul frissítése" severity="secondary" variant="outlined" class="self-end"
          [disabled]="locked() || dirty() || !jobId()" (onClick)="reload()" />
      </div>
      @if (loading()) { <p-progressbar mode="indeterminate" [style]="{height:'4px'}" /> }
      @if (!completedJobs().length) {
        <p-message severity="info">A Generálás fülön készíts modult. A kész modul itt és a Feladatok fülön is kiválasztható.</p-message>
      }
      @if (index(); as workspace) {
              <app-migrator-ai-queue [apiBase]="apiBase" [jobId]="jobId()" [summary]="workspace.auto ?? null"
                [tasks]="overviewTasks()" [selectedTaskId]="taskId()" (finished)="queueFinished($event)" (pick)="selectTask($event)" />
              <label class="flex flex-col gap-2"><span class="font-medium">Végpont (egyenként)</span>
                <p-select appendTo="body" [ngModel]="taskId()" (ngModelChange)="selectTask($event)" [options]="taskOptions()" optionValue="value" optionLabel="label"
                  placeholder="Nincs befejezendő végpont" ariaLabel="Asszisztens végpont" [disabled]="locked() || dirty()" class="w-full" />
              </label>
              @if (selectedTask(); as task) {
                @for (blocker of task.blockers; track $index) { <p class="m-0 text-sm opacity-80">{{ blocker }}</p> }
                @if (task.pending_tables.length) { <p-message severity="warn">Előbb add meg a táblaszerkezeteket: {{ task.pending_tables.join(', ') }}</p-message> }
              }

        <p-panel header="1. Adatbázistáblák" [toggleable]="true" class="block min-w-0 w-full"
          [pt]="{contentWrapper: {class: 'min-w-0'}}">
          <div class="flex min-w-0 flex-col gap-4">
            <p class="m-0 text-sm opacity-80">{{ taskTables().length }} tábla tartozik a kiválasztott triggerhez. Importáld az oszlopokat, jelöld a valódi PK-t és ments.</p>
            @if (missingKeys().length) { <p-message severity="warn">Az entityhez jelöld az elsődleges kulcsot (PK): {{ missingKeys().join(', ') }}</p-message> }
            <div class="flex flex-wrap items-end gap-3">
              <label class="flex min-w-64 flex-1 flex-col gap-2"><span>Tábla / nézet</span>
                <p-select appendTo="body" [ngModel]="tableName()" (ngModelChange)="selectTable($event)" [options]="tableOptions()"
                  optionValue="value" optionLabel="label" ariaLabel="Adatbázistábla" [disabled]="locked() || schemaDirty() || bindingsDirty()" />
              </label>
              <p-button label="schema.json letöltése" severity="secondary" variant="text"
                [disabled]="locked()" (onClick)="download('/schema/download', 'schema.json')" />
            </div>
            @if (selectedTable(); as table) {
              <div class="flex min-w-0 flex-col gap-3 rounded-lg border border-surface-200 p-4 dark:border-surface-700">
                <span class="font-medium">Oszlopok beolvasása IntelliJ CSV-ből</span>
                <p class="m-0 text-sm opacity-80">Válassz CSV-fájlt, vagy illeszd be a tartalmát. A beolvasás kitölti az alábbi
                  szerkesztőt; ellenőrzés után kattints a Táblaszerkezet mentése gombra. A CSV egyetlen tábla adatait tartalmazza.</p>
                <label class="flex flex-col gap-2"><span>CSV-fájl</span>
                  <input type="file" accept=".csv,text/csv,text/plain" aria-label="Táblaszerkezet CSV-fájl"
                    [disabled]="locked() || dirty()" (change)="readCsvFile($event)" class="max-w-full text-sm" />
                </label>
                <details><summary class="cursor-pointer text-sm font-medium">CSV-szöveg beillesztése és exportminta</summary>
                  <div class="mt-3 flex flex-col gap-3">
                    <textarea pInputText [formControl]="csvText" rows="4" maxlength="1000000" aria-label="Táblaszerkezet CSV-szöveg"
                      placeholder="ID,NUMBER,22,20,0,N&#10;NEV,VARCHAR2,30,,,Y" [readOnly]="locked() || dirty()"></textarea>
                    <p-button label="CSV beolvasása" severity="secondary" variant="outlined"
                      [disabled]="locked() || dirty() || !csvText.value.trim()" (onClick)="importCsv()" />
                    <p class="m-0 text-xs opacity-70">Sorrend fejléc nélkül: COLUMN_NAME, DATA_TYPE, DATA_LENGTH,
                      DATA_PRECISION, DATA_SCALE, NULLABLE; bővített exportnál utána CHAR_LENGTH, CHAR_USED.
                      A 6 és 8 oszlopos változat is beolvasható fejléccel vagy anélkül.
                      A NUMBER 22-es értéke tárolási bytehossz, nem mezőhossz.</p>
                    <details><summary class="cursor-pointer text-sm">Pontos CHAR/BYTE export SQL</summary>
                      <pre class="overflow-auto whitespace-pre-wrap text-xs">SELECT column_name, data_type, data_length, data_precision,
       data_scale, nullable, char_length, char_used
FROM all_tab_columns
WHERE owner = 'SEMA_NEVE' AND table_name = 'TABLA_NEVE'
ORDER BY column_id;</pre>
                      <p class="m-0 text-xs opacity-70">Fejléccel és fejléc nélkül is beolvasható; az utolsó két oszlopot is hagyd az exportban.</p>
                    </details>
                  </div>
                </details>
                @for (warning of csvWarnings(); track $index) { <p-message severity="warn">{{ warning }}</p-message> }
              </div>
              @for (reason of table.reasons; track $index) { <p-message severity="warn">{{ reason }}</p-message> }
              <p class="m-0 text-xs opacity-70">{{ tableReferences() }}</p>
              <div class="as-table">
              <p-table class="block min-w-0 w-full" dataKey="key" [rowTrackBy]="trackColumn" [value]="columns()" [paginator]="columns().length > 10" [rows]="10" [first]="columnFirst()"
                (onPage)="columnFirst.set($event.first)" [scrollable]="true" size="small" [tableStyle]="{'min-width':'80rem'}">
                <ng-template #header><tr><th>Oszlopnév</th><th>Oracle-típus</th><th>Hossz</th><th>Mértékegység</th>
                  <th>Precision</th><th>Scale</th><th>Nullable</th><th>PK</th><th>Alapérték</th><th>Megjegyzés</th><th></th></tr></ng-template>
                <ng-template #body let-column let-at="rowIndex"><tr>
                  <td><input pInputText [ngModel]="column.name" (ngModelChange)="setColumn(column.key,'name',$event)" maxlength="128"
                    [disabled]="locked() || bindingsDirty()" aria-label="Oszlopnév" class="w-36" /></td>
                  <td><p-select appendTo="body" [ngModel]="column.data_type" (ngModelChange)="setColumn(column.key,'data_type',$event)" [options]="oracleTypes"
                    [disabled]="locked() || bindingsDirty()" placeholder="Válassz…" ariaLabel="Oracle-típus" class="min-w-40" /></td>
                  <td><input pInputText type="number" [ngModel]="column.length" (ngModelChange)="setColumn(column.key,'length',$event)" min="1" max="32767"
                    [disabled]="locked() || bindingsDirty() || !hasLength(column.data_type)" aria-label="Oszlophossz" class="w-24" /></td>
                  <td><p-select appendTo="body" [ngModel]="column.length_semantics" (ngModelChange)="setColumn(column.key,'length_semantics',$event)" [options]="lengthUnits"
                    [disabled]="locked() || bindingsDirty() || !isCharacter(column.data_type)" ariaLabel="BYTE vagy CHAR" class="w-28" /></td>
                  <td><input pInputText type="number" [ngModel]="column.precision" (ngModelChange)="setColumn(column.key,'precision',$event)"
                    [disabled]="locked() || bindingsDirty() || !hasPrecision(column.data_type)" aria-label="Precision" class="w-24" /></td>
                  <td><input pInputText type="number" [ngModel]="column.scale" (ngModelChange)="setColumn(column.key,'scale',$event)"
                    [disabled]="locked() || bindingsDirty() || column.data_type !== 'NUMBER'" aria-label="Scale" class="w-24" /></td>
                  <td><p-select appendTo="body" [ngModel]="column.nullable" (ngModelChange)="setColumn(column.key,'nullable',$event)" [options]="nullableOptions"
                    optionValue="value" optionLabel="label" placeholder="Válassz…" [disabled]="locked() || bindingsDirty()" ariaLabel="Nullable" class="w-28" /></td>
                  <td><p-checkbox [binary]="true" [ngModel]="column.pk" (ngModelChange)="setColumn(column.key,'pk',$event)"
                    [disabled]="locked() || bindingsDirty()" ariaLabel="Elsődleges kulcs" /></td>
                  <td><input pInputText [ngModel]="column.default" (ngModelChange)="setColumn(column.key,'default',$event)" maxlength="2000"
                    [disabled]="locked() || bindingsDirty()" aria-label="Alapérték" class="w-36" /></td>
                  <td><input pInputText [ngModel]="column.comment" (ngModelChange)="setColumn(column.key,'comment',$event)" maxlength="2000"
                    [disabled]="locked() || bindingsDirty()" aria-label="Oszlop megjegyzése" class="w-44" /></td>
                  <td><p-button label="Törlés" severity="danger" variant="text" size="small"
                    [disabled]="locked() || bindingsDirty()" (onClick)="removeColumn(column.key)" /></td>
                </tr></ng-template>
                <ng-template #emptymessage><tr><td colspan="11" class="p-5 text-center opacity-70">Tölts be CSV-t, vagy használd az Oszlop hozzáadása gombot.</td></tr></ng-template>
              </p-table>
              </div>
              <div class="flex flex-wrap gap-2">
                <p-button label="Oszlop hozzáadása" severity="secondary" variant="outlined" [disabled]="locked() || bindingsDirty()" (onClick)="addColumn()" />
                <p-button label="Táblaszerkezet mentése" [disabled]="locked() || !schemaDirty() || bindingsDirty()" (onClick)="saveSchema()" />
                <p-button label="Táblavázlat elvetése" severity="secondary" variant="text" [disabled]="locked() || !schemaDirty()" (onClick)="resetSchema()" />
              </div>
            }
            @for (warning of workspace.schema.warnings; track $index) { <p class="m-0 text-xs opacity-70">{{ warning }}</p> }
          </div>
        </p-panel>

        <p-panel header="2. Trigger javásítása">
          <div class="as-two">
            <div class="flex min-w-0 flex-col gap-3">
              <p-message severity="info">Az AI csak a kiválasztott DPS-metódust kapja (benne a kikommentezett Oracle-triggerrel), és a metódusban
                ténylegesen használt táblák repositoryjának néhány soros leírását. Az adatbázis-műveletek az entitykre épülő repositoryba kerülnek,
                a service meghívja őket. A meglévő Controller és végpont megmarad. Nem kell promptot írnod.</p-message>
              <details><summary class="cursor-pointer text-sm font-medium">Automatikus AI-feladat megtekintése</summary>
                <pre class="overflow-auto whitespace-pre-wrap text-xs">{{ workspace.default_instructions }}</pre>
              </details>
              <details><summary class="cursor-pointer text-sm">Kiegészítő forrás (ha az AI kéri)</summary>
              <label class="mt-3 flex flex-col gap-2"><span>Hívott rutin vagy céges API</span>
                <textarea pInputText [formControl]="projectContext" rows="5" maxlength="24000" placeholder="Céges API vagy a hiányzó eljárás teljes kódja. Ez forrásadat, az AI feladatát a migrátor már megadta." aria-label="Projektminta"></textarea>
              </label>
              </details>
              <details><summary class="cursor-pointer font-medium">Memória és futtatás</summary>
                <div class="mt-3 grid grid-cols-2 gap-3">
                  <label class="flex flex-col gap-1">Kontextus (token)<input pInputText type="number" [formControl]="numCtx" min="4096" max="65536" /></label>
                  <label class="flex flex-col gap-1">Kimeneti keret<input pInputText type="number" [formControl]="numPredict" min="512" max="16384" /></label>
                  <label class="flex flex-col gap-1">Időkorlát (mp)<input pInputText type="number" [formControl]="timeout" min="1" max="3600" /></label>
                  <label class="flex flex-col gap-1">Próbálkozások<input pInputText type="number" [formControl]="attempts" min="1" max="3" /></label>
                  <label class="col-span-2 flex flex-col gap-1">Gondolkodás<p-select appendTo="body" [formControl]="think" [options]="thinkOptions" optionValue="value" optionLabel="label" /></label>
                </div>
                <p class="text-xs opacity-70">A nagyobb kontextus több memóriát használ. CPU-s laptopon 16384 / 4096 a javasolt
                  kiindulás; ha egy trigger több helyet kér, a hibaüzenet megmondja, mennyit. A modell képességét a gondolkodási beállítás nem változtatja meg.</p>
              </details>
              @if (dirty()) { <p-message severity="info">A táblaszerkezetet vagy bekötési vázlatot előbb mentsd vagy vesd el.</p-message> }
              <div data-testid="assistant-run-feedback" aria-live="polite">
                @if (error()) { <p-message severity="error" role="alert">{{ error() }}</p-message> }
              </div>
              <p-button label="Trigger javásítása" [disabled]="!canGenerate()" [loading]="running()" (onClick)="generate()" />
              @if (running()) { <p-message severity="info">{{ runState() === 'queued' ? 'A kérés a sorban vár; az AI-munkasor mutatja, mi fut előtte.'
                : 'Az Ollama dolgozik; az élő állapotot fent az AI-munkasor mutatja. Közben a táblákat is szerkesztheted, másik fülre is átléphetsz.' }}</p-message> }
            </div>
            <div class="flex min-w-0 flex-col gap-3">
              <label class="flex flex-col gap-2"><span class="font-medium">Korábbi javaslat</span>
                <p-select appendTo="body" [ngModel]="proposal()?.id ?? ''" (ngModelChange)="showProposal($event)" [options]="proposalOptions()" optionValue="value" optionLabel="label"
                  placeholder="Még nincs javaslat" ariaLabel="Java-javaslat" [disabled]="locked()" />
              </label>
              @if (proposal(); as result) {
                <p class="m-0">{{ result.response.summary }}</p>
                <p-tag [value]="result.applied ? 'Átvéve' : result.status === 'needs_context' ? 'További adat szükséges' : 'Átnézendő javaslat'" />
                @if (result.response.missing_context.length) {
                  <p-message severity="warn">Az AI szerint ez hiányzik: {{ result.response.missing_context.join(' · ') }}
                    @if (result.status === 'needs_context') { A vázlat megtekinthető, de nem illeszthető be: add meg a hiányzó forrást a Kiegészítő forrás részben, és futtasd újra. }
                  </p-message>
                }
                @for (warning of result.extraction?.warnings ?? []; track $index) { <p-message severity="warn">{{ warning }}</p-message> }
                <details><summary class="cursor-pointer text-sm">Ellenőrzési részletek</summary>
                @for (section of reviewSections(); track section.label) {
                  @if (section.values.length) {
                    <div><h4 class="mb-1 font-medium">{{ section.label }}</h4><ul class="m-0 list-disc pl-5 text-sm">
                      @for (value of section.values; track $index) { <li>{{ value }}</li> }
                    </ul></div>
                  }
                }
                </details>
                <p class="m-0 text-xs opacity-70">Java-szintaxis: {{ result.validation.java_parse === 'passed' ? 'ellenőrizve' : 'nincs igazolva' }}.
                  A teljes céges fordítás és az Oracle-egyenértékűség még külön ellenőrzendő.</p>
                @if (result.files.length) {
                  <p-select appendTo="body" [ngModel]="previewPath()" (ngModelChange)="loadPreview($event)" [options]="previewOptions()" optionValue="value" optionLabel="label" ariaLabel="Javaslat fájlja" />
                  <pre class="as-code rounded border p-3" tabindex="0">{{ previewText() }}</pre>
                  <p-button label="Javaslat ZIP letöltése" severity="secondary" variant="outlined" [disabled]="locked()" (onClick)="download('/proposals/' + result.id + '/download', 'java-' + result.id + '.zip')" />
                  @if (result.apply_allowed && !result.applied) {
                    <p-button label="Beillesztés a fájlokba" [disabled]="locked() || dirty() || result.status !== 'proposal'" (onClick)="apply()" />
                  } @else if (!result.apply_allowed) {
                    <p-message severity="info">CRUD-végpont: a változtatást kézzel kell összefésülni a meglévő validációkkal és írási feltételekkel.</p-message>
                  }
                }
              } @else { <p class="text-sm opacity-70">Itt jelenik meg az AI válasza, a teljes Java-fájl és a diff. A javaslat átvétele külön lépés.</p> }
              <div class="flex flex-wrap gap-2">
                <p-button label="Friss modul ZIP" severity="secondary" variant="text" [disabled]="locked()" (onClick)="downloadModule('all')" />
                <p-button label="Frontend ZIP" severity="secondary" variant="text" [disabled]="locked()" (onClick)="downloadModule('frontend')" />
                <p-button label="Backend ZIP" severity="secondary" variant="text" [disabled]="locked()" (onClick)="downloadModule('backend')" />
              </div>
            </div>
          </div>
          <details class="mt-5 min-w-0" data-testid="assistant-ai-log" [open]="aiLogOpen()" (toggle)="toggleAiLog($any($event.target).open)">
            <summary class="cursor-pointer font-medium">AI-kommunikáció: mit küldtünk a modellnek, és mit válaszolt</summary>
            @if (aiLogOpen()) {
              <div class="mt-3 flex min-w-0 flex-col gap-3">
                <div class="flex flex-wrap items-end gap-3">
                  <p-button label="Küldendő prompt előnézete" severity="secondary" variant="outlined"
                    [disabled]="!selectedTask() || dirty() || aiLogLoading() || running()" (onClick)="previewPrompt()" />
                  <label class="flex min-w-64 flex-1 flex-col gap-1 text-sm"><span>Korábbi AI-hívás</span>
                    <p-select appendTo="body" [ngModel]="exchange()?.id ?? ''" (ngModelChange)="showExchange($event)" [options]="exchangeOptions()"
                      optionValue="value" optionLabel="label" placeholder="Még nem volt AI-hívás" ariaLabel="AI-hívás" />
                  </label>
                  <a class="text-sm underline" [href]="aiLogUrl()" target="_blank" rel="noopener">Megnyitás külön lapon</a>
                </div>
                @if (aiLogLoading()) { <p-progressbar mode="indeterminate" [style]="{height:'4px'}" /> }
                @if (promptPreview(); as preview) {
                  <div class="flex min-w-0 flex-col gap-2 rounded-lg border border-surface-200 p-3 dark:border-surface-700" data-testid="assistant-prompt-preview">
                    <span class="font-medium">Küldendő prompt — {{ preview.method }} (még nem lett elküldve)</span>
                    <span class="text-xs opacity-80">{{ preview.bytes }} byte, becslés ~{{ preview.estimated_input_tokens }} token;
                      szükséges kontextus {{ preview.required_tokens }} / {{ preview.num_ctx }} token.
                      Repositoryk: {{ preview.repositories.join(', ') || 'nincs' }}.</span>
                    @for (warning of preview.warnings; track $index) { <p-message severity="warn">{{ warning }}</p-message> }
                    @if (preview.omitted_tables.length) {
                      <span class="text-xs opacity-70">Nem küldött táblák (a metódus nem hivatkozik rájuk): {{ preview.omitted_tables.join(', ') }}</span>
                    }
                    <details><summary class="cursor-pointer text-sm">Rendszerprompt (system)</summary>
                      <pre class="mt-2 max-h-96 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap" tabindex="0">{{ preview.system }}</pre></details>
                    <details open><summary class="cursor-pointer text-sm">Felhasználói prompt (user), a kiválasztott metódussal</summary>
                      <pre class="mt-2 max-h-96 overflow-auto rounded border p-3 text-xs whitespace-pre" tabindex="0">{{ preview.user }}</pre></details>
                  </div>
                }
                @if (exchange(); as log) {
                  <div class="flex min-w-0 flex-col gap-2" data-testid="assistant-exchange">
                    <div class="flex flex-wrap items-center gap-2">
                      <span class="font-medium">{{ log.method }} · {{ exchangeStatus(log.status) }}</span>
                      @if (log.result) { <p-tag [value]="log.result.status === 'proposal' ? 'Javaslat' : 'Adatot kér'" /> }
                      <span class="text-xs opacity-70">{{ log.model }} · {{ log.url }} · {{ formatTime(log.started) }}</span>
                    </div>
                    @if (log.error) { <p-message severity="error">{{ log.error }}</p-message> }
                    <span class="text-xs opacity-80">Elküldve: {{ log.prompt.bytes }} byte (~{{ log.prompt.estimated_input_tokens }} token),
                      kontextus {{ log.options.num_ctx ?? '—' }}, kimeneti keret {{ log.options.num_predict ?? '—' }} token.
                      Repositoryk: {{ log.prompt.repositories.join(', ') || 'nincs' }}.</span>
                    <details><summary class="cursor-pointer text-sm">Rendszerprompt (system)</summary>
                      <pre class="mt-2 max-h-96 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap" tabindex="0">{{ log.prompt.system }}</pre></details>
                    <details><summary class="cursor-pointer text-sm">Elküldött kérés (user), a kiválasztott metódussal</summary>
                      <pre class="mt-2 max-h-96 overflow-auto rounded border p-3 text-xs whitespace-pre" tabindex="0">{{ log.prompt.user }}</pre></details>
                    @if (log.live; as live) {
                      <div class="flex min-w-0 flex-col gap-1 rounded-lg border border-surface-200 p-3 dark:border-surface-700" aria-live="polite">
                        <span class="text-sm font-medium">Élő állapot: {{ live.phase === 'prompt' ? 'a modell olvassa a kérést' : live.phase === 'thinking' ? 'gondolkodik'
                          : live.phase === 'writing' ? 'kódot ír' : live.phase }} · {{ live.elapsed }} mp
                          · {{ live.thinking_tokens + live.content_tokens }} token{{ live.tokens_per_second ? ' · ' + live.tokens_per_second + ' token/mp' : '' }}</span>
                        @if (live.tail) { <pre class="as-live m-0 rounded border p-2" tabindex="0">{{ live.tail }}</pre> }
                      </div>
                    }
                    @if (!log.attempts.length) {
                      <p class="m-0 text-sm opacity-70">{{ log.status === 'running' ? 'A modell még dolgozik…' : 'Nem történt AI-hívás.' }}</p>
                    }
                    @for (attempt of log.attempts; track $index) {
                      <div class="flex min-w-0 flex-col gap-2 border-l-4 pl-3" [class.border-red-400]="attemptRejected(attempt)"
                        [class.border-emerald-400]="!attemptRejected(attempt)">
                        <span class="text-sm font-medium">{{ attemptTitle(attempt) }}</span>
                        @if (attempt.note) { <span class="text-xs opacity-70">{{ attempt.note }}</span> }
                        @if (attempt.correction) {
                          <details><summary class="cursor-pointer text-sm">Javítási kérés a modellnek</summary>
                            <pre class="mt-2 max-h-60 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap" tabindex="0">{{ attempt.correction }}</pre></details>
                        }
                        @if (attempt.error) { <p-message severity="error">{{ attempt.error }}</p-message> }
                        @if (attempt.raw !== null) {
                          <details open><summary class="cursor-pointer text-sm">Az AI nyers válasza ({{ (attempt.raw ?? '').length }} karakter)</summary>
                            <pre class="mt-2 max-h-96 overflow-auto rounded border p-3 text-xs whitespace-pre" tabindex="0">{{ attempt.raw || '(üres válasz)' }}</pre></details>
                        }
                        @if (attempt.thinking) {
                          <details><summary class="cursor-pointer text-sm">Gondolkodási szöveg</summary>
                            <pre class="mt-2 max-h-60 overflow-auto rounded border p-3 text-xs whitespace-pre-wrap" tabindex="0">{{ attempt.thinking }}</pre></details>
                        }
                        @if (attempt.extraction; as report) {
                          <span class="text-sm">Átvett blokkok: {{ blockSummary(report) }}</span>
                          @for (problem of report.problems; track $index) { <p-message severity="error">{{ problem }}</p-message> }
                          @for (warning of report.warnings; track $index) { <p-message severity="warn">{{ warning }}</p-message> }
                          @if (report.notes.length) {
                            <ul class="m-0 list-disc pl-5 text-xs opacity-80">
                              @for (note of report.notes; track $index) { <li>{{ note }}</li> }
                            </ul>
                          }
                        }
                      </div>
                    }
                  </div>
                } @else if (!promptPreview()) {
                  <p class="m-0 text-sm opacity-70">Itt látszik a modellnek elküldött teljes kérés, minden nyers válasz (az elutasított is),
                    és hogy a migrátor mit vett át belőle. A „Küldendő prompt előnézete” AI-hívás nélkül mutatja a következő kérést.</p>
                }
              </div>
            }
          </details>
        </p-panel>
        <details [open]="bindingsOpen()" (toggle)="bindingsOpen.set($any($event.target).open)"><summary class="cursor-pointer text-sm opacity-80">Haladó: képernyőértékek bekötése</summary>
        @if (bindingsOpen()) {
        <p-panel header="Képernyőértékek bekötése (opcionális)" [toggleable]="true" class="block min-w-0 w-full"
          [pt]="{contentWrapper: {class: 'min-w-0'}}">
          <div class="flex min-w-0 flex-col gap-4">
            <p class="m-0 text-sm opacity-80">Ez a korábbi „Frontend–backend bemenetek” panel. A Java-nak meg kell kapnia azokat az értékeket,
              amelyeket a trigger az Oracle Formsban elért. Itt azt adod meg, honnan jöjjenek ezek a webes alkalmazásban.</p>
            <p class="m-0 text-sm"><strong>Példa:</strong> a trigger :GLOBAL.UGYFEL_ID értéket olvas.
              Ha ezt a képernyő UGYFEL.ID mezője tartalmazza, válaszd a Képernyőmező forrást és az UGYFEL.ID mezőt.
              A mentés ezután ezt az értéket teszi a Java-nak küldött kérésbe.</p>
            <div class="grid gap-2 text-sm md:grid-cols-3">
              <p class="m-0"><strong>Képernyőmező:</strong> az űrlap vagy a kijelölt táblázatsor már tartalmazza az értéket.</p>
              <p class="m-0"><strong>Modulparaméter:</strong> a modult megnyitó oldal adja át, például az ügyfél azonosítóját.
                Ezt a szülő komponensben is be kell kötni.</p>
              <p class="m-0"><strong>Kézi / szerveroldali:</strong> például a bejelentkezett felhasználó hiteles azonosítója.
                A megjegyzés rögzíti a teendőt; a szerveroldali bekötést fejlesztő végzi el.</p>
            </div>
            <p-message severity="info">{{ inputSummary() }} A már bekötött sorokkal nincs külön teendő.
              Csak az üzletileg megfelelő forrást válaszd; az azonos nevű mező nem feltétlenül ugyanaz az adat.</p-message>
            @if (!workspace.input_bindings.available) {
              <p-message severity="info">Ehhez a modulhoz nincs bemeneti leltár; generálj új képernyőt a migrátor aktuális verziójával.</p-message>
            } @else {
              <div class="as-table">
              <p-table class="block min-w-0 w-full" dataKey="id" [value]="workspace.input_bindings.rows"
                [paginator]="workspace.input_bindings.rows.length > 10" [rows]="10" [scrollable]="true" size="small" [tableStyle]="{'min-width':'60rem'}">
                <ng-template #header><tr><th>Melyik trigger melyik értéket használja?</th><th>Van hozzá érték?</th><th>Honnan jöjjön?</th><th>Mező / paraméter</th><th>Megjegyzés</th></tr></ng-template>
                <ng-template #body let-row><tr>
                  <td><span class="block font-medium">{{ row.endpoint }} · {{ row.source }}</span><small>{{ row.location }}</small></td>
                  <td><span class="text-sm">{{ bindingStatus(row.status) }}</span></td>
                  <td><p-select appendTo="body" [ngModel]="bindingDraft()[row.id]?.kind ?? ''" (ngModelChange)="setBindingKind(row.id,$event)"
                    [options]="bindingKinds(row)" optionValue="value" optionLabel="label" ariaLabel="Bemenet forrása"
                    [disabled]="bindingLocked()" class="min-w-44" /></td>
                  <td>
                    @if (bindingDraft()[row.id]?.kind === 'field') {
                      <p-select appendTo="body" [ngModel]="bindingDraft()[row.id]?.source ?? ''" (ngModelChange)="setBinding(row.id,'source',$event)"
                        [options]="workspace.input_bindings.sources" optionValue="owner" optionLabel="owner" placeholder="Válassz mezőt…"
                        ariaLabel="Képernyőmező" [disabled]="bindingLocked()" class="min-w-40" />
                    } @else if (bindingDraft()[row.id]?.kind === 'parameter') {
                      <input pInputText [ngModel]="bindingDraft()[row.id]?.source ?? ''" (ngModelChange)="setBinding(row.id,'source',$event)"
                        [disabled]="bindingLocked()" maxlength="128" placeholder="például ugyfelKod" aria-label="Modulparaméter neve" />
                    }
                  </td>
                  <td><input pInputText [ngModel]="bindingDraft()[row.id]?.note ?? ''" (ngModelChange)="setBinding(row.id,'note',$event)"
                    [disabled]="bindingLocked() || !bindingDraft()[row.id]" maxlength="2000" aria-label="Bekötés megjegyzése" /></td>
                </tr></ng-template>
                <ng-template #emptymessage><tr><td colspan="5" class="p-5 text-center opacity-70">Nincs felismert bemenet.</td></tr></ng-template>
              </p-table>
              </div>
              <div class="flex flex-wrap gap-2">
                <p-button label="Bekötések mentése a kódba" [disabled]="bindingLocked() || !bindingsDirty()" (onClick)="saveBindings()" />
                <p-button label="Bekötési vázlat elvetése" severity="secondary" variant="text" [disabled]="locked() || !bindingsDirty()" (onClick)="resetBindings()" />
                <p-button label="Bekötések exportálása" severity="secondary" variant="text" [disabled]="locked()" (onClick)="download('/bindings/download','input-bindings.config.json')" />
              </div>
            }
            @for (warning of workspace.input_bindings.warnings; track $index) { <p class="m-0 text-xs opacity-70">{{ warning }}</p> }
            @if (workspace.input_bindings.file_changed || workspace.input_bindings.contract_changed) {
              <p-message severity="warn">A komponens vagy a bemeneti szerződés módosult. A bekötéseket kézzel kell egyeztetni, az automatikus mentés letiltva.</p-message>
            }
          </div>
        </p-panel>

        }
        </details>

      }
    </div>
  `,
})
export class MigratorAssistant implements OnChanges, OnDestroy {
  @Input() apiBase = '';
  @Input() jobs: AssistantJob[] = [];
  @Input() openJobId: string | null = null;
  @Input({required: true}) ollamaUrl!: FormControl<string>;
  @Input({required: true}) ollamaModel!: FormControl<string>;
  @Output() busyChange = new EventEmitter<boolean>();
  @Output() dirtyChange = new EventEmitter<boolean>();
  @Output() moduleUpdated = new EventEmitter<string>();
  @Output() moduleSelected = new EventEmitter<string>();
  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly mutation = {headers: new HttpHeaders({'X-Frm-Client': 'local-ui'})};
  private epoch = 0;
  private previewEpoch = 0;
  private pollTimer?: ReturnType<typeof setTimeout>;
  private destroyed = false;
  private nextColumn = 0;
  readonly index = signal<AssistantIndex | null>(null);
  readonly jobId = signal('');
  readonly taskId = signal('');
  readonly tableName = signal('');
  readonly columns = signal<ColumnDraft[]>([]);
  readonly columnFirst = signal(0);
  readonly bindingsOpen = signal(false);
  readonly csvText = new FormControl('', {nonNullable: true});
  readonly csvWarnings = signal<string[]>([]);
  readonly bindingDraft = signal<Partial<Record<string, Binding>>>({});
  readonly proposal = signal<Proposal | null>(null);
  readonly previewPath = signal('');
  readonly previewText = signal('');
  readonly loading = signal(false);
  readonly mutating = signal(false);
  readonly running = signal(false);
  /** The state of this panel's own manual run: queued (another run is first) or running. */
  readonly runState = signal<'queued' | 'running' | ''>('');
  readonly schemaDirty = signal(false);
  readonly bindingsDirty = signal(false);
  readonly error = signal('');
  readonly notice = signal('');
  /** AI log: the prompt that was (or would be) sent and the model's raw answers. */
  readonly aiLogOpen = signal(false);
  readonly aiLogLoading = signal(false);
  readonly exchanges = signal<ExchangeSummary[]>([]);
  readonly exchange = signal<Exchange | null>(null);
  readonly promptPreview = signal<PromptPreview | null>(null);
  private pendingExchangeId = '';
  private readonly autoOpened = new Set<string>();
  private exchangeEpoch = 0;
  private exchangeTimer?: ReturnType<typeof setTimeout>;
  readonly exchangeOptions = computed(() => this.exchanges().map(item => ({value: item.id,
    label: item.method + ' · ' + this.exchangeStatus(item.status) + (item.proposal_status === 'needs_context' ? ' (adatot kér)' : '')
      + ' · ' + this.formatTime(item.started)})));
  // A running model does not lock the panel: tables can be filled in meanwhile (the queue picks them up).
  readonly locked = computed(() => this.loading() || this.mutating());
  readonly dirty = computed(() => this.schemaDirty() || this.bindingsDirty());
  readonly bindingLocked = computed(() => this.locked() || this.schemaDirty()
    || Boolean(this.index()?.input_bindings.file_changed || this.index()?.input_bindings.contract_changed));
  private readonly jobChoices = signal<{id: string; label: string}[]>([]);
  readonly completedJobs = this.jobChoices.asReadonly();
  readonly selectedTask = computed(() => this.index()?.tasks.find(t => t.id === this.taskId()));
  readonly overviewTasks = computed(() => [...(this.index()?.tasks ?? []), ...(this.index()?.done_tasks ?? [])]);
  readonly selectedTable = computed(() => this.index()?.schema.tables.find(t => t.table === this.tableName()));
  readonly taskOptions = computed(() => (this.index()?.tasks ?? []).map(t => ({value: t.id,
    label: (t.done ? '✓ ' : '') + (t.kind === 'hook' ? 'Trigger' : t.kind === 'rewrite_java' ? 'Java-átírás' : 'Végpont') + ' · ' + t.method
      + ' — ' + t.owner + (t.event ? ' / ' + t.event : '') + (t.pending_tables.length ? ' (táblára vár)' : '')})));
  readonly taskTables = computed(() => {
    const selected = this.selectedTask();
    const names = new Set(selected?.tables ?? []);
    return (this.index()?.schema.tables ?? []).filter(table => !selected || names.has(table.table));
  });
  readonly missingKeys = computed(() => this.taskTables().filter(table => table.definition && !table.definition.primary_key?.length).map(table => table.table));
  readonly tableOptions = computed(() => this.taskTables().map(t => ({value: t.table,
    label: t.table + (t.status === 'ready' ? ' · megadva' : ' · szerkezet szükséges')})));
  readonly tableReferences = computed(() => this.selectedTable()?.references.map(r => r.owner + ' / ' + r.operation + (r.line ? ', ' + r.line + '. sor' : '')).join(' · ') ?? '');
  readonly inputSummary = computed(() => {
    const rows = this.index()?.input_bindings.rows ?? [];
    const pending = rows.filter(row => row.status !== 'connected').length;
    return rows.length ? rows.length + ' felismert értékből ' + pending + ' igényel még bekötést vagy ellenőrzést.' : 'Nincs felismert bemenet.';
  });
  readonly proposalOptions = computed(() => (this.index()?.proposals ?? []).map(p => ({value: p.id,
    label: p.method + ' · ' + (p.applied ? 'átvéve' : p.status === 'needs_context' ? 'adatot kér' : 'javaslat')})));
  readonly previewOptions = computed(() => (this.proposal()?.files ?? []).flatMap(f => [
    {value: f.candidate + '.patch', label: 'Diff · ' + f.target}, {value: f.candidate, label: 'Teljes fájl · ' + f.target}]));
  readonly reviewSections = computed(() => {
    const result = this.proposal();
    if (!result) return [];
    return [{label: 'Feltételezések', values: result.response.assumptions}, {label: 'Ellenőrizendő tesztesetek', values: result.response.test_cases},
      {label: 'Mit vett át a migrátor az AI válaszából', values: result.extraction?.notes ?? []},
      {label: 'Adatmodell / repository', values: result.helper_notes},
      {label: 'Helyi formázás (AI-kérés nélkül)', values: Object.entries(result.validation.deterministic_format ?? {}).map(([file, info]) =>
        file + ': ' + (info.status === 'manual' ? 'kézi formázás szükséges — ' + info.reason : info.status === 'imports_only' ? 'importrendezés' : 'formázva'))}];
  });
  readonly jobControl = new FormControl('', {nonNullable: true});
  readonly projectContext = new FormControl('', {nonNullable: true});
  readonly numCtx = new FormControl(16384, {nonNullable: true});
  readonly numPredict = new FormControl(4096, {nonNullable: true});
  readonly timeout = new FormControl(900, {nonNullable: true});
  readonly attempts = new FormControl(2, {nonNullable: true});
  readonly think = new FormControl('default', {nonNullable: true});
  readonly reviewed = new FormControl(false, {nonNullable: true});
  readonly oracleTypes = ORACLE_TYPES;
  readonly trackColumn = (_index: number, column: ColumnDraft): number => column.key;
  readonly lengthUnits = ['BYTE', 'CHAR'];
  readonly nullableOptions = [{label: 'Igen', value: true}, {label: 'Nem', value: false}];
  readonly thinkOptions = ['default', 'disabled', 'low', 'medium', 'high'].map(value => ({value,
    label: value === 'default' ? 'Modell alapbeállítása' : value === 'disabled' ? 'Kikapcsolva' : value}));

  constructor() {
    effect(() => {
      const disabled = this.locked() || this.dirty();
      if (disabled === this.jobControl.disabled) return;
      if (disabled) this.jobControl.disable({emitEvent: false});
      else this.jobControl.enable({emitEvent: false});
    });
    effect(() => this.busyChange.emit(this.locked()));
    effect(() => this.dirtyChange.emit(this.dirty()));
  }

  ngOnChanges(changes: SimpleChanges): void {
    if (changes['apiBase']) {
      ++this.epoch; clearTimeout(this.pollTimer);
      this.index.set(null); this.jobId.set(''); this.running.set(false); this.runState.set(''); this.loading.set(false); this.mutating.set(false);
      this.schemaDirty.set(false); this.bindingsDirty.set(false); this.proposal.set(null);
      this.resetAiLog();
      this.loadPreferences();
    }
    this.jobChoices.set(this.jobs.filter(j => j.status === 'completed').map(j => ({id: j.id, label: (j.module || j.filename) + ' · ' + j.filename})));
    if (!this.apiBase || this.locked()) return;
    const requested = this.openJobId && this.completedJobs().some(j => j.id === this.openJobId) ? this.openJobId : null;
    const selected = this.jobId();
    if (requested && requested !== selected) void this.selectJob(requested);
    else if (!selected || !this.completedJobs().some(j => j.id === selected)) {
      if (this.dirty()) return;
      const first = this.completedJobs()[0]?.id;
      if (first) void this.selectJob(first);
      else { this.index.set(null); this.jobId.set(''); }
    }
  }

  ngOnDestroy(): void {
    this.destroyed = true; ++this.epoch; ++this.previewEpoch; ++this.exchangeEpoch;
    clearTimeout(this.pollTimer); clearTimeout(this.exchangeTimer);
  }

  @HostListener('window:beforeunload', ['$event'])
  beforeUnload(event: BeforeUnloadEvent): void {
    if (this.dirty()) { event.preventDefault(); event.returnValue = ''; }
  }

  private endpoint(path = ''): string { return this.apiBase + '/jobs/' + encodeURIComponent(this.jobId()) + '/assistant' + path; }
  private current(epoch: number): boolean { return !this.destroyed && this.epoch === epoch; }
  private async get<T>(path = ''): Promise<T> { return firstValueFrom(this.http.get<T>(this.endpoint(path))); }
  private async post<T>(path: string, body: unknown): Promise<T> { return firstValueFrom(this.http.post<T>(this.endpoint(path), body, this.mutation)); }
  private failure(error: unknown): void {
    let message = error instanceof Error ? error.message : String(error);
    if (error instanceof HttpErrorResponse) {
      const detail: unknown = error.error?.detail;
      if (typeof detail === 'string' && detail.trim()) message = detail;
      else if (Array.isArray(detail)) {
        message = detail.map((issue: {loc?: unknown[]; msg?: unknown}) =>
          (issue.loc?.filter(part => part !== 'body').join('.') || 'Kérés') + ': ' + String(issue.msg ?? 'Hibás érték')).join('; ');
      } else if (error.status === 0) message = 'A migrátor API-ja nem érhető el. Ellenőrizd, hogy fut-e a Python backend és helyes-e az API-cím.';
      else message = error.message;
    }
    message = message.trim() || 'Az AI-futás hibaüzenet nélkül megszakadt. A Python backend naplójában lehet további részlet.';
    this.error.set(message);
    console.error('[Migrátor] ' + message);
  }

  private options(): Record<string, string | number> {
    return {num_ctx: this.numCtx.value, num_predict: this.numPredict.value, timeout: this.timeout.value,
      attempts: this.attempts.value, think: this.think.value, data_access: 'jpa'};
  }
  private loadPreferences(): void {
    try {
      const saved = JSON.parse(localStorage.getItem('frm-assistant-options-v2:' + this.apiBase) ?? '{}') as Record<string, unknown>;
      for (const [key, control, low, high] of [
        ['num_ctx', this.numCtx, 4096, 65536], ['num_predict', this.numPredict, 512, 16384],
        ['timeout', this.timeout, 1, 3600], ['attempts', this.attempts, 1, 3],
      ] as const) {
        const value = saved[key]; if (typeof value === 'number' && Number.isInteger(value) && value >= low && value <= high) control.setValue(value);
      }
      if (typeof saved['think'] === 'string' && this.thinkOptions.some(option => option.value === saved['think'])) this.think.setValue(saved['think']);
    } catch { /* optional browser preferences */ }
  }

  async selectJob(id: string): Promise<void> {
    if (this.locked() || this.dirty()) {
      this.jobControl.setValue(this.jobId(), {emitEvent: false});
      this.error.set('Előbb mentsd vagy vesd el a vázlatot, illetve várd meg a futó feladatot.'); return;
    }
    if (!this.completedJobs().some(j => j.id === id)) return;
    this.jobId.set(id); this.jobControl.setValue(id, {emitEvent: false}); this.moduleSelected.emit(id);
    this.proposal.set(null); this.previewText.set(''); this.notice.set(''); this.error.set('');
    this.resetAiLog();
    this.updateLocation(id);
    await this.reload();
  }

  private updateLocation(id: string): void {
    if (typeof location === 'undefined') return;
    const url = new URL(location.href);
    if (url.searchParams.get('view') !== 'assistant') return;
    url.searchParams.set('view', 'assistant'); url.searchParams.set('job', id);
    history.replaceState(null, '', url.pathname + url.search + url.hash);
  }

  async reload(): Promise<void> {
    if (!this.jobId() || this.dirty()) return;
    const epoch = ++this.epoch; this.loading.set(true);
    try {
      const workspace = await this.get<AssistantIndex>();
      if (!this.current(epoch)) return;
      this.index.set(workspace);
      if (!workspace.tasks.some(t => t.id === this.taskId())) this.taskId.set(workspace.tasks[0]?.id ?? '');
      this.chooseTaskTable();
      this.loadColumnDraft(); this.resetBindings();
      if (workspace.active_run?.status === 'running' && this.running()) {
        // This panel's own run: keep following it (an automatic run is shown by the AI queue above).
        this.pendingExchangeId = workspace.active_run.exchange_id ?? this.pendingExchangeId;
      } else if (workspace.recent_run?.status === 'failed' && !this.running()) {
        this.failure(new Error('Legutóbbi AI-futás: ' + (workspace.recent_run.error || 'A kérés nem sikerült.')));
        this.openFailedExchange(workspace.recent_run.exchange_id);
      } else if (!this.proposal()) {
        const recentId = workspace.recent_run?.proposal_id ?? workspace.proposals.at(-1)?.id;
        if (recentId) await this.readProposal(recentId, epoch);
      }
    } catch (error) { if (this.current(epoch)) this.failure(error); }
    finally { if (this.current(epoch)) this.loading.set(false); }
  }

  selectTask(id: string): void {
    if (this.locked() || this.dirty()) return;
    this.taskId.set(id); this.chooseTaskTable(); this.loadColumnDraft(); this.reviewed.setValue(false);
    this.proposal.set(null); this.previewPath.set(''); this.previewText.set('');
    this.resetAiLog(true);
    if (this.aiLogOpen()) void this.loadAiLog();
  }
  private chooseTaskTable(): void {
    const tables = this.taskTables();
    if (!tables.some(t => t.table === this.tableName())) this.tableName.set(tables.find(t => t.status !== 'ready')?.table ?? tables[0]?.table ?? '');
  }
  selectTable(name: string): void { if (!this.locked() && !this.dirty()) { this.tableName.set(name); this.loadColumnDraft(); } }
  private loadColumnDraft(): void {
    this.csvText.setValue(''); this.csvWarnings.set([]); this.columnFirst.set(0);
    const definition = this.selectedTable()?.definition;
    this.columns.set(Object.entries(definition?.columns ?? {}).map(([name, c]) => {
      const type = String(c['data_type'] ?? ''); const timestamp = /^TIMESTAMP\((\d+)\)(.*)$/.exec(type);
      const number = (value: unknown): number | null => typeof value === 'number' ? value : null;
      return {key: ++this.nextColumn, name, data_type: timestamp ? 'TIMESTAMP' + timestamp[2] : type,
        nullable: typeof c['nullable'] === 'boolean' ? c['nullable'] : null, pk: definition?.primary_key?.includes(name) ?? false,
        length: number(c['length'] ?? c['char_length'] ?? c['max_length'] ?? c['data_length']),
        length_semantics: String(c['length_semantics'] ?? (c['char_used'] === 'C' ? 'CHAR' : c['char_used'] === 'B' ? 'BYTE' : '')),
        precision: timestamp ? Number(timestamp[1]) : number(c['datetime_precision'] ?? c['precision']), scale: number(c['scale']),
        default: String(c['default'] ?? ''), comment: String(c['comment'] ?? '')};
    })); this.schemaDirty.set(false);
  }
  isCharacter(type: string): boolean { return ['VARCHAR2', 'NVARCHAR2', 'CHAR', 'NCHAR'].includes(type); }
  hasLength(type: string): boolean { return this.isCharacter(type) || type === 'RAW'; }
  hasPrecision(type: string): boolean { return ['NUMBER', 'FLOAT'].includes(type) || type.startsWith('TIMESTAMP'); }
  addColumn(): void {
    this.columns.update(columns => [...columns, {key: ++this.nextColumn, name: '', data_type: '', nullable: null, pk: false,
      length: null, length_semantics: '', precision: null, scale: null, default: '', comment: ''}]); this.schemaDirty.set(true);
    this.columnFirst.set(Math.floor((this.columns().length - 1) / 10) * 10);
  }
  setColumn<K extends keyof ColumnDraft>(columnKey: number, key: K, value: ColumnDraft[K]): void {
    this.columns.update(columns => columns.map(column => column.key === columnKey ? {...column, [key]: value} : column)); this.schemaDirty.set(true);
  }
  removeColumn(columnKey: number): void {
    this.columns.update(columns => columns.filter(column => column.key !== columnKey));
    this.columnFirst.update(first => Math.min(first, Math.floor(Math.max(0, this.columns().length - 1) / 10) * 10));
    this.schemaDirty.set(true);
  }
  resetSchema(): void { this.loadColumnDraft(); }

  async readCsvFile(event: Event): Promise<void> {
    const input = event.target as HTMLInputElement; const file = input.files?.[0]; input.value = '';
    if (file) await this.importCsv(file);
  }
  async importCsv(file?: File): Promise<void> {
    const workspace = this.index();
    if (!workspace || !this.selectedTable() || this.locked() || this.dirty()) return;
    const epoch = this.epoch; const table = this.tableName();
    this.mutating.set(true); this.error.set(''); this.notice.set('');
    try {
      if (file && file.size > 1_000_000) throw new Error('Legfeljebb 1 MB méretű CSV-fájl tölthető be.');
      const text = file ? await file.text() : this.csvText.value;
      if (!this.current(epoch)) return;
      const preview = await this.post<CsvPreview>('/schema/import', {table, revision: workspace.schema.revision, text});
      if (!this.current(epoch) || table !== this.tableName()) return;
      const previous = new Map(this.columns().map(column => [column.name.trim().toUpperCase(), column]));
      this.columnFirst.set(0);
      this.columns.set(preview.columns.map(column => ({...column, key: ++this.nextColumn,
        pk: previous.get(column.name)?.pk ?? false, default: previous.get(column.name)?.default ?? column.default,
        comment: previous.get(column.name)?.comment ?? column.comment})));
      this.csvText.setValue(text); this.csvWarnings.set(preview.warnings); this.schemaDirty.set(true);
      this.notice.set(preview.columns.length + ' oszlop beolvasva a(z) ' + table + ' szerkesztőjébe. Ellenőrizd a kulcsot és a hosszakat, majd mentsd a táblaszerkezetet.');
    } catch (error) { if (this.current(epoch)) this.failure(error); }
    finally { if (this.current(epoch)) this.mutating.set(false); }
  }

  async saveSchema(): Promise<void> {
    const workspace = this.index(); if (!workspace || this.locked() || this.bindingsDirty()) return;
    const rows = this.columns();
    if (!rows.length || rows.some(c => !c.name.trim() || !c.data_type || c.nullable === null)) {
      this.error.set('Minden oszlopnál add meg a nevet, az Oracle-típust és a nullable értékét.'); return;
    }
    const columns = rows.map(c => ({name: c.name, data_type: c.data_type, nullable: c.nullable,
      ...(this.hasLength(c.data_type) ? {length: c.length, length_semantics: this.isCharacter(c.data_type) ? c.length_semantics : ''} : {}),
      ...(this.hasPrecision(c.data_type) ? {precision: c.precision} : {}), ...(c.data_type === 'NUMBER' ? {scale: c.scale} : {}),
      default: c.default, comment: c.comment}));
    this.mutating.set(true); this.error.set('');
    try {
      await this.post('/schema', {table: this.tableName(), revision: workspace.schema.revision, columns, primary_key: rows.filter(c => c.pk).map(c => c.name)});
      this.schemaDirty.set(false); await this.reload(); this.notice.set('A táblaszerkezet mentve. Az AI kontextusa és a letölthető csomag is frissült.');
      this.moduleUpdated.emit(this.jobId());
    } catch (error) { this.failure(error); }
    finally { this.mutating.set(false); }
  }

  bindingKinds(row: InputRow): {label: string; value: string}[] {
    const choices = [{label: 'Generált alapérték', value: ''}];
    if (row.automatic && row.timing !== 'constructor' && !row.source.startsWith('SYSTEM.'))
      choices.push({label: 'Képernyőmező', value: 'field'}, {label: 'Modulparaméter', value: 'parameter'});
    return [...choices, {label: 'Kézi / szerveroldali', value: 'manual'}];
  }
  bindingStatus(status: string): string {
    return ({connected: 'Bekötve', missing: 'Hiányzó érték', host_required: 'Szülőoldali input szükséges', manual: 'Kézi feladat',
      unwired: 'Hívó kód szükséges', review: 'Bemenetirány ellenőrzendő', lifecycle: 'Indítás időzítése kézi feladat',
      file_changed: 'Kód kézzel módosult', contract_changed: 'Szerződés megváltozott'} as Record<string, string>)[status] ?? status;
  }
  setBindingKind(id: string, kind: Binding['kind'] | ''): void {
    this.bindingDraft.update(previous => {const next = {...previous}; delete next[id];
      if (kind) next[id] = {kind, source: '', note: ''}; return next;}); this.bindingsDirty.set(true);
  }
  setBinding(id: string, key: 'source' | 'note', value: string): void {
    this.bindingDraft.update(previous => previous[id] ? {...previous, [id]: {...previous[id], [key]: value}} : previous); this.bindingsDirty.set(true);
  }
  resetBindings(): void { this.bindingDraft.set(structuredClone(this.index()?.input_bindings.bindings ?? {})); this.bindingsDirty.set(false); }
  async saveBindings(): Promise<void> {
    const revision = this.index()?.input_bindings.revision; if (!revision || this.bindingLocked()) return;
    this.mutating.set(true); this.error.set('');
    try {
      await this.post('/bindings', {revision, bindings: this.bindingDraft()});
      this.bindingsDirty.set(false); await this.reload(); this.notice.set('A bekötések és a generált kérésértékek mentve. A modulparaméter szülőoldali átadása és a kézi feladatok még ellenőrzendők.');
      this.moduleUpdated.emit(this.jobId());
    } catch (error) { this.failure(error); }
    finally { this.mutating.set(false); }
  }

  canGenerate(): boolean {
    return !this.locked() && !this.running() && !this.dirty() && Boolean(this.selectedTask()) && !this.selectedTask()?.pending_tables.length
      && !this.missingKeys().length;
  }
  /** A run of the queue finished for this module: the task list, the proposals and the files changed. */
  async queueFinished(run: AiRun): Promise<void> {
    if (run.job_id !== this.jobId() || this.dirty() || this.locked()) return;
    if (run.status === 'completed' && run.applied) this.moduleUpdated.emit(this.jobId());
    if (!this.running()) await this.reload();
  }
  async generate(): Promise<void> {
    if (!this.canGenerate()) return;
    if (!this.ollamaUrl.value.trim() || this.ollamaUrl.invalid || !this.ollamaModel.value.trim() || this.ollamaModel.invalid) {
      this.error.set('A közös AI-beállításoknál add meg a laptop Ollama alapcímét és a pontos modellnevet.'); return;
    }
    this.error.set(''); this.notice.set(''); this.running.set(true); const epoch = this.epoch;
    try { localStorage.setItem('frm-assistant-options-v2:' + this.apiBase, JSON.stringify(this.options())); } catch { /* inference can run without storage */ }
    try {
      const run = await this.post<Run>('', {task_id: this.taskId(), url: this.ollamaUrl.value.trim(), model: this.ollamaModel.value.trim(),
        project_context: this.projectContext.value, data_access: 'jpa',
        ...this.options()});
      if (!this.current(epoch)) return;
      this.pendingExchangeId = run.exchange_id ?? '';
      this.runState.set(run.status === 'queued' ? 'queued' : 'running');
      this.promptPreview.set(null);
      this.schedulePoll(run.id, epoch, 0);
    } catch (error) { if (this.current(epoch)) { this.running.set(false); this.failure(error); } }
  }
  private schedulePoll(id: string, epoch: number, delay = 1800): void {
    clearTimeout(this.pollTimer); this.pollTimer = setTimeout(() => void this.poll(id, epoch), delay);
  }
  private async poll(id: string, epoch: number): Promise<void> {
    try {
      const run = await this.get<Run>('/runs/' + id); if (!this.current(epoch)) return;
      if (run.exchange_id) this.pendingExchangeId = run.exchange_id;
      if (run.status === 'running' || run.status === 'queued') {
        this.runState.set(run.status);
        // While the model works, the open AI log already shows the request that was sent.
        if (this.aiLogOpen() && run.exchange_id && this.exchange()?.id !== run.exchange_id) void this.showExchange(run.exchange_id, true);
        this.schedulePoll(id, epoch); return;
      }
      this.running.set(false); this.runState.set('');
      if (run.status === 'cancelled') { this.notice.set('A futás leállítva.'); return; }
      if (run.status !== 'completed' || !run.result) {
        this.failure(new Error(run.error || 'Az AI-futás megszakadt.'));
        this.openFailedExchange(run.exchange_id);
        return;
      }
      await this.reload();
      if (this.proposal()?.id !== run.result.id) await this.showProposal(run.result.id);
      if (run.applied) this.notice.set('A javaslat automatikusan beillesztve (az eredeti fájl mentve, a diff a javaslatban). A letölthető ZIP frissült.');
    } catch (error) { if (this.current(epoch)) { this.running.set(false); this.failure(error); } }
  }
  async showProposal(id: string): Promise<void> {
    if (!id || this.locked()) return;
    const epoch = this.epoch; this.loading.set(true); this.reviewed.setValue(false);
    try {
      await this.readProposal(id, epoch);
    } catch (error) { if (this.current(epoch)) this.failure(error); }
    finally { if (this.current(epoch)) this.loading.set(false); }
  }
  private async readProposal(id: string, epoch: number): Promise<void> {
    const result = await this.get<Proposal>('/proposals/' + id); if (!this.current(epoch)) return;
    this.proposal.set(result); this.reviewed.setValue(false);
    if (result.exchange_id) {
      this.pendingExchangeId = result.exchange_id;
      if (this.aiLogOpen() && this.exchange()?.id !== result.exchange_id) void this.showExchange(result.exchange_id, true);
    }
    await this.loadPreview(result.files.find(file => file.target.endsWith('ServiceImpl.java'))?.candidate ?? this.previewOptions()[0]?.value ?? '');
  }
  async loadPreview(path: string): Promise<void> {
    const proposal = this.proposal(); const epoch = ++this.previewEpoch; this.previewPath.set(path); this.previewText.set('');
    if (!proposal || !path) return;
    const url = this.apiBase + '/jobs/' + encodeURIComponent(this.jobId()) + '/file?path=' +
      encodeURIComponent('analysis/backend-assistant/proposals/' + proposal.id + '/' + path);
    try {
      const file = await firstValueFrom(this.http.get<{text: string; truncated: boolean}>(url));
      if (!this.destroyed && epoch === this.previewEpoch) this.previewText.set(file.text + (file.truncated ? '\n[Az előnézet csonkolt; töltsd le a ZIP-et.]' : ''));
    } catch (error) { if (!this.destroyed && epoch === this.previewEpoch) this.failure(error); }
  }
  async apply(): Promise<void> {
    const result = this.proposal();
    if (!result?.apply_allowed || result.applied || this.locked() || this.dirty()) return;
    this.mutating.set(true); this.error.set('');
    try {
      await this.post('/proposals/' + result.id + '/apply', {reviewed: true});
      this.proposal.set({...result, applied: true}); this.reviewed.setValue(false);
      await this.reload(); this.notice.set('A Java-javaslat átvéve, a letölthető ZIP-ek frissültek. Fordítsd és teszteld a céges projektben.');
      this.moduleUpdated.emit(this.jobId());
    } catch (error) { this.failure(error); }
    finally { this.mutating.set(false); }
  }
  async download(path: string, filename: string): Promise<void> { await this.downloadUrl(this.endpoint(path), filename); }
  async downloadModule(kind: string): Promise<void> {
    await this.downloadUrl(this.apiBase + '/jobs/' + encodeURIComponent(this.jobId()) + '/download?kind=' + kind, (kind === 'all' ? 'module' : kind) + '.zip');
  }
  private async downloadUrl(url: string, filename: string): Promise<void> {
    try {
      const blob = await firstValueFrom(this.http.get(url, {responseType: 'blob'}));
      if (this.destroyed) return;
      const objectUrl = URL.createObjectURL(blob); const link = document.createElement('a');
      link.href = objectUrl; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    } catch (error) { this.failure(error); }
  }

  // --- AI log: what was sent to the model and what it answered ---------------------------------------------

  private resetAiLog(keepOpen = false): void {
    ++this.exchangeEpoch; clearTimeout(this.exchangeTimer);
    this.exchanges.set([]); this.exchange.set(null); this.promptPreview.set(null); this.pendingExchangeId = '';
    this.aiLogLoading.set(false);
    if (!keepOpen) this.aiLogOpen.set(false);
  }
  toggleAiLog(open: boolean): void {
    if (open === this.aiLogOpen()) return;
    this.aiLogOpen.set(open);
    if (open) void this.loadAiLog();
    else clearTimeout(this.exchangeTimer);
  }
  private openFailedExchange(exchangeId: string | null | undefined): void {
    // Once per failed run: later reloads (e.g. after saving a table) do not reopen a closed panel.
    if (!exchangeId || this.autoOpened.has(exchangeId)) return;
    this.autoOpened.add(exchangeId);
    this.pendingExchangeId = exchangeId;
    this.aiLogOpen.set(true);
    void this.loadAiLog();
  }
  private async loadAiLog(): Promise<void> {
    if (!this.jobId()) return;
    const epoch = ++this.exchangeEpoch; this.aiLogLoading.set(true);
    try {
      const task = this.taskId();
      const data = await this.get<{exchanges: ExchangeSummary[]}>('/exchanges' + (task ? '?task_id=' + encodeURIComponent(task) : ''));
      if (this.destroyed || epoch !== this.exchangeEpoch) return;
      this.exchanges.set(data.exchanges);
      const wanted = this.pendingExchangeId || this.exchange()?.id || data.exchanges[0]?.id || '';
      if (wanted && !this.promptPreview()) await this.readExchange(wanted, epoch, true);
    } catch (error) { if (epoch === this.exchangeEpoch) this.failure(error); }
    finally { if (epoch === this.exchangeEpoch) this.aiLogLoading.set(false); }
  }
  async showExchange(id: string, quiet = false): Promise<void> {
    if (!id || !this.jobId()) return;
    const epoch = ++this.exchangeEpoch;
    this.pendingExchangeId = id; this.promptPreview.set(null); this.aiLogLoading.set(true);
    try { await this.readExchange(id, epoch, quiet); }
    finally { if (epoch === this.exchangeEpoch) this.aiLogLoading.set(false); }
  }
  private async readExchange(id: string, epoch: number, quiet: boolean): Promise<void> {
    clearTimeout(this.exchangeTimer);
    try {
      const log = await this.get<Exchange>('/exchanges/' + encodeURIComponent(id));
      if (this.destroyed || epoch !== this.exchangeEpoch) return;
      this.exchange.set(log);
      if (!this.exchanges().some(item => item.id === log.id)) {
        const list = await this.get<{exchanges: ExchangeSummary[]}>('/exchanges' + (this.taskId() ? '?task_id=' + encodeURIComponent(this.taskId()) : ''));
        if (!this.destroyed && epoch === this.exchangeEpoch) this.exchanges.set(list.exchanges);
      }
      if (log.status === 'running' && this.aiLogOpen()) {
        this.exchangeTimer = setTimeout(() => { if (this.exchange()?.id === id) void this.showExchange(id, true); }, 2500);
      }
    } catch (error) {
      // A run's log file appears a moment after the start; the next poll shows it.
      if (!quiet && epoch === this.exchangeEpoch) this.failure(error);
    }
  }
  async previewPrompt(): Promise<void> {
    const task = this.selectedTask();
    if (!task || this.dirty()) return;
    const epoch = ++this.exchangeEpoch; clearTimeout(this.exchangeTimer);
    this.aiLogLoading.set(true); this.error.set('');
    try {
      const preview = await this.post<PromptPreview>('/prompt', {task_id: task.id, project_context: this.projectContext.value,
        data_access: 'jpa', num_ctx: this.numCtx.value, num_predict: this.numPredict.value});
      if (this.destroyed || epoch !== this.exchangeEpoch) return;
      this.exchange.set(null); this.promptPreview.set(preview);
    } catch (error) { if (epoch === this.exchangeEpoch) this.failure(error); }
    finally { if (epoch === this.exchangeEpoch) this.aiLogLoading.set(false); }
  }
  aiLogUrl(): string {
    const root = this.apiBase.replace(/\/api\/?$/, '');
    const id = this.exchange()?.id;
    return root + '/ai-log?job=' + encodeURIComponent(this.jobId()) + (this.taskId() ? '&task=' + encodeURIComponent(this.taskId()) : '')
      + (id ? '&exchange=' + encodeURIComponent(id) : '');
  }
  exchangeStatus(status: string): string {
    return ({running: 'fut', completed: 'kész', failed: 'sikertelen', cancelled: 'leállítva'} as Record<string, string>)[status] ?? status;
  }
  formatTime(value: string | null): string {
    if (!value) return '—';
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('hu-HU');
  }
  attemptRejected(attempt: ExchangeAttempt): boolean {
    return Boolean(attempt.error) || attempt.extraction?.status === 'rejected';
  }
  attemptTitle(attempt: ExchangeAttempt): string {
    const parts = [attempt.n ? attempt.n + '. AI-hívás' : 'Gyorsítótárból (nem volt új AI-hívás)'];
    if (attempt.seconds !== null) parts.push(attempt.seconds + ' mp');
    if (attempt.first_token_seconds !== null && attempt.first_token_seconds !== undefined) parts.push('első token ' + attempt.first_token_seconds + ' mp után');
    if (attempt.prompt_tokens !== null) parts.push('bemenet ' + attempt.prompt_tokens + ' token');
    if (attempt.output_tokens !== null) parts.push('kimenet ' + attempt.output_tokens + ' token');
    if (attempt.tokens_per_second) parts.push(attempt.tokens_per_second + ' token/mp');
    if (attempt.done_reason) parts.push(attempt.done_reason === 'length' ? 'a kimeneti keretnél megszakadt' : 'vége: ' + attempt.done_reason);
    parts.push(this.attemptRejected(attempt) ? 'elutasítva' : attempt.extraction?.status === 'needs_context' ? 'adatot kér' : 'átvéve');
    return parts.join(' · ');
  }
  blockSummary(report: ExtractionReport): string {
    if (!report.blocks.length) return 'nem volt kódblokk a válaszban';
    return report.blocks.map(block => block.index + '. ' + (block.label || block.kind) + ' → '
      + (block.used ? block.target || block.kind : 'kihagyva') + (block.note ? ' (' + block.note + ')' : '')).join('; ');
  }
}
