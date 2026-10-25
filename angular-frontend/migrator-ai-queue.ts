import {
  Component, EventEmitter, Input, OnChanges, OnDestroy, OnInit, Output, SimpleChanges, computed, inject, signal,
} from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpBackend, HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { ProgressBarModule } from '@openng/optimus-ui/progressbar';
import { TagModule } from '@openng/optimus-ui/tag';
import { ToggleSwitchModule } from '@openng/optimus-ui/toggleswitch';

/** The live state of a running request (frm_forms.backend_assistant.Live.snapshot). */
export interface AiProgress {
  attempt: number; phase: string; elapsed: number; prompt_seconds: number; first_token_seconds: number | null;
  thinking_tokens: number; content_tokens: number; tokens_per_second: number | null;
  prompt_tokens: number | null; output_tokens: number | null; tail: string; thinking_tail: string; updated: number;
}
/** One run of the assistant queue (frm_forms.web.assistant.AssistantManager). */
export interface AiRun {
  id: string; job_id: string; status: string; task_id: string; method: string; owner: string | null; event: string;
  kind: string | null; auto: boolean; queued: string; started: string | null; finished: string | null;
  exchange_id: string | null; progress: AiProgress | null; applied: boolean; apply_error?: string | null; error?: string | null;
  proposal_id?: string; proposal_status?: string; options?: {model: string; num_ctx: number; num_predict: number; timeout: number};
}
export interface AiQueue {
  active: AiRun | null; busy_with_other_job: boolean; queued: AiRun[]; finished: AiRun[]; server_time: number;
  settings: {url: string; model: string; auto_run: boolean; auto_apply: boolean; num_ctx: number; num_predict: number; timeout: number};
}
/** Per module: how many triggers are done, waiting for a table structure, queued ... (AssistantManager.auto_summary). */
export interface AiSummary { total: number; done: number; waiting_for_tables: number; in_queue: number; failed: number; ready: number }
export interface AiTask {
  id: string; method: string; owner: string; kind: string; event?: string; pending_tables: string[]; done?: boolean;
}

const PHASES: Record<string, string> = {
  connecting: 'Kapcsolódás az Ollamához', prompt: 'A modell olvassa a kérést', thinking: 'Gondolkodik',
  writing: 'Kódot ír', done: 'Válasz kész, ellenőrzés',
};

/**
 * The Java assistant's queue, live: what the local model does right now (phase, elapsed time, tokens, speed, the
 * last lines it wrote), what waits, what finished. compact: one line for the toolbar, visible from every tab.
 */
@Component({
  selector: 'app-migrator-ai-queue',
  imports: [FormsModule, ButtonModule, ProgressBarModule, TagModule, ToggleSwitchModule],
  host: {class: 'block min-w-0'},
  styles: `
    .aq { display: flex; flex-direction: column; gap: 0.875rem; min-width: 0; }
    .aq-card { display: flex; flex-direction: column; gap: 0.625rem; min-width: 0; padding: 0.875rem 1rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0); border-radius: var(--p-content-border-radius, 0.5rem); }
    .aq-live { border-color: var(--p-primary-color, #3b82f6);
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 5%, transparent); }
    .aq-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.875rem; min-width: 0; }
    .aq-grow { flex: 1 1 12rem; min-width: 0; }
    .aq-title { font-weight: 600; overflow-wrap: anywhere; }
    .aq-muted { font-size: 0.8125rem; color: var(--p-text-muted-color, #64748b); overflow-wrap: anywhere; }
    .aq-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(100%, 8.5rem), 1fr)); gap: 0.5rem; }
    .aq-stat { display: flex; flex-direction: column; gap: 0.125rem; padding: 0.5rem 0.75rem; min-width: 0;
      border-radius: var(--p-content-border-radius, 0.5rem);
      background: color-mix(in srgb, var(--p-text-color, #1e293b) 4%, transparent); }
    .aq-stat strong { font-size: 1.25rem; font-variant-numeric: tabular-nums; line-height: 1.2; }
    .aq-stat span { font-size: 0.75rem; color: var(--p-text-muted-color, #64748b); }
    .aq-tail { max-height: 16rem; margin: 0; padding: 0.625rem 0.75rem; overflow: auto; white-space: pre-wrap;
      overflow-wrap: anywhere; font-size: 0.75rem; line-height: 1.45;
      font-family: ui-monospace, 'Cascadia Code', 'JetBrains Mono', Consolas, monospace;
      border: 1px solid var(--p-content-border-color, #e2e8f0); border-radius: var(--p-content-border-radius, 0.5rem);
      background: var(--p-content-background, #fff); }
    .aq-list { display: flex; flex-direction: column; margin: 0; padding: 0; list-style: none; }
    .aq-item { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 0.25rem 0.75rem;
      padding: 0.5rem 0; border-top: 1px solid var(--p-content-border-color, #e2e8f0); }
    .aq-item:first-child { border-top: 0; }
    .aq-item-main { display: flex; flex-direction: column; gap: 0.125rem; min-width: 0; }
    .aq-item-side { display: flex; flex-wrap: wrap; justify-content: flex-end; align-items: center; gap: 0.375rem; }
    .aq-tasks { display: grid; grid-template-columns: repeat(auto-fill, minmax(min(100%, 19rem), 1fr)); gap: 0.5rem; }
    .aq-task { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.5rem 0.75rem; min-width: 0; text-align: left;
      cursor: pointer; font: inherit; color: inherit; background: transparent;
      border: 1px solid var(--p-content-border-color, #e2e8f0); border-radius: var(--p-content-border-radius, 0.5rem); }
    .aq-task:hover, .aq-task:focus-visible { border-color: var(--p-primary-color, #3b82f6); outline: none; }
    .aq-task-on { border-color: var(--p-primary-color, #3b82f6);
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 6%, transparent); }
    .aq-compact { display: inline-flex; align-items: center; gap: 0.375rem; max-width: min(100%, 26rem); min-width: 0;
      padding: 0.25rem 0.625rem; font: inherit; font-size: 0.8125rem; white-space: nowrap; cursor: pointer; color: inherit;
      border: 1px solid var(--p-primary-color, #3b82f6); border-radius: 999px;
      background: color-mix(in srgb, var(--p-primary-color, #3b82f6) 7%, transparent); }
    .aq-compact span { overflow: hidden; text-overflow: ellipsis; }
    .aq-dot { flex: none; width: 0.5rem; height: 0.5rem; border-radius: 999px; background: var(--p-primary-color, #3b82f6);
      animation: aq-pulse 1.2s ease-in-out infinite; }
    @keyframes aq-pulse { 50% { opacity: 0.25; } }
    @media (prefers-reduced-motion: reduce) { .aq-dot { animation: none; } }
  `,
  template: `
    @if (compact) {
      @if (queue()?.active; as run) {
        <button type="button" class="aq-compact" role="status" [title]="run.method + ' — ' + phaseLabel(run) + ' (megnyitás)'" (click)="open.emit()">
          <span class="aq-dot" aria-hidden="true"></span>
          <span>AI: {{ run.method }} · {{ clock(run) }}{{ run.progress?.tokens_per_second ? ' · ' + run.progress?.tokens_per_second + ' tok/s' : '' }}</span>
          @if (queue()?.queued?.length) { <span>· +{{ queue()?.queued?.length }} sorban</span> }
        </button>
      } @else if (queue()?.queued?.length) {
        <button type="button" class="aq-compact" role="status" (click)="open.emit()"><span>AI-sor: {{ queue()?.queued?.length }} vár</span></button>
      }
    } @else {
      <section class="aq" aria-label="AI-munkasor" data-testid="ai-queue">
        <div class="aq-row">
          <span class="aq-title aq-grow">AI-munkasor</span>
          <label class="aq-row aq-muted"><p-toggleswitch [ngModel]="queue()?.settings?.auto_run ?? true" (ngModelChange)="saveSetting('auto_run', $event)"
            [disabled]="!queue()" ariaLabel="Automatikus indítás" />Indítás migrálás és táblamentés után</label>
          <label class="aq-row aq-muted"><p-toggleswitch [ngModel]="queue()?.settings?.auto_apply ?? true" (ngModelChange)="saveSetting('auto_apply', $event)"
            [disabled]="!queue()" ariaLabel="Automatikus beillesztés" />Sikeres javaslat automatikus beillesztése</label>
        </div>
        @if (summary; as s) {
          <div class="aq-stats" aria-label="Triggerek állapota">
            <div class="aq-stat"><strong>{{ s.done }} / {{ s.total }}</strong><span>kész (beillesztve)</span></div>
            <div class="aq-stat"><strong>{{ s.in_queue }}</strong><span>sorban vagy fut</span></div>
            <div class="aq-stat"><strong>{{ s.ready }}</strong><span>indítható</span></div>
            <div class="aq-stat"><strong>{{ s.waiting_for_tables }}</strong><span>táblaszerkezetre vár</span></div>
            <div class="aq-stat"><strong>{{ s.failed }}</strong><span>sikertelen</span></div>
          </div>
        }
        <div class="aq-row">
          @if (jobId) {
            <p-button label="Indítható triggerek futtatása" size="small" [disabled]="!queue() || busy()" (onClick)="startAll(false)" />
            <p-button label="Újrapróbálás (sikertelenek is)" size="small" severity="secondary" variant="outlined"
              [disabled]="!queue() || busy()" (onClick)="startAll(true)" />
          }
          @if (queue()?.active || queue()?.queued?.length) {
            <p-button label="Sor leállítása" size="small" severity="danger" variant="text" [disabled]="busy()" (onClick)="stop(null, true)" />
          }
          @if (!queue()?.settings?.url) {
            <span class="aq-muted">Az automatikus futáshoz mentsd az AI-kapcsolatot (Ollama-cím és modell) a közös beállításoknál.</span>
          }
        </div>
        @if (error()) { <p class="m-0 text-sm text-red-600" role="alert">{{ error() }}</p> }
        @if (queue()?.busy_with_other_job) {
          <p class="aq-muted m-0">Az AI most egy másik modulon dolgozik; ez a modul utána következik.</p>
        }
        @if (activeHere(); as run) {
          <div class="aq-card aq-live" aria-live="polite" data-testid="ai-live">
            <div class="aq-row">
              <span class="aq-dot" aria-hidden="true"></span>
              <span class="aq-title aq-grow">{{ run.method }} <span class="aq-muted">{{ run.owner }}{{ run.event ? ' / ' + run.event : '' }}</span></span>
              <p-tag [value]="phaseLabel(run)" severity="info" />
              <p-button label="Leállítás" size="small" severity="danger" variant="outlined" [disabled]="busy()" (onClick)="stop(run.id, false)" />
            </div>
            <p-progressbar mode="indeterminate" [style]="{height: '4px'}" />
            <div class="aq-stats">
              <div class="aq-stat"><strong>{{ clock(run) }}</strong><span>eltelt idő</span></div>
              <div class="aq-stat"><strong>{{ firstToken(run) }}</strong><span>prompt feldolgozása / első token</span></div>
              <div class="aq-stat"><strong>{{ tokens(run) }}</strong><span>generált token{{ run.progress?.thinking_tokens ? ' (ebből gondolkodás ' + run.progress?.thinking_tokens + ')' : '' }}</span></div>
              <div class="aq-stat"><strong>{{ run.progress?.tokens_per_second ?? '—' }}</strong><span>token / mp</span></div>
              <div class="aq-stat"><strong>{{ budget(run) }}</strong><span>kimeneti keret felhasználva</span></div>
            </div>
            <span class="aq-muted">{{ run.options?.model }} · {{ run.progress?.attempt && run.progress!.attempt > 1 ? run.progress?.attempt + '. próbálkozás (javítási kérés) · ' : '' }}{{ run.auto ? 'automatikus' : 'kézi' }} indítás · időkorlát {{ run.options?.timeout }} mp</span>
            @if (run.progress?.tail) {
              <pre class="aq-tail" tabindex="0" aria-label="A modell eddigi válasza">{{ run.progress?.tail }}</pre>
            } @else {
              <span class="aq-muted">{{ run.progress?.phase === 'prompt' || !run.progress
                ? 'CPU-n a kérés beolvasása a válasz előtt percekig is tarthat; az első token után itt látszik a válasz.'
                : 'Még nincs kiírt szöveg.' }}</span>
            }
            @if (run.progress?.thinking_tail) {
              <details><summary class="cursor-pointer text-sm">Gondolkodási szöveg (utolsó részlet)</summary>
                <pre class="aq-tail">{{ run.progress?.thinking_tail }}</pre></details>
            }
          </div>
        }
        @if (queuedHere().length) {
          <div class="aq-card">
            <span class="aq-title">Sorban ({{ queuedHere().length }})</span>
            <ul class="aq-list">
              @for (run of queuedHere(); track run.id; let i = $index) {
                <li class="aq-item">
                  <span class="aq-item-main"><span>{{ i + 1 }}. {{ run.method }}</span>
                    <span class="aq-muted">{{ run.owner }}{{ run.event ? ' / ' + run.event : '' }} · {{ run.auto ? 'automatikus' : 'kézi' }}</span></span>
                  <span class="aq-item-side"><p-button label="Kivétel" size="small" severity="secondary" variant="text" [disabled]="busy()" (onClick)="stop(run.id, false)" /></span>
                </li>
              }
            </ul>
          </div>
        }
        @if (tasks.length) {
          <details class="aq-card" [open]="tasksOpen()" (toggle)="tasksOpen.set($any($event.target).open)">
            <summary class="cursor-pointer aq-title">Triggerek ({{ tasks.length }}) — kattints egyre a részletekért</summary>
            <div class="aq-tasks mt-2">
              @for (task of tasks; track task.id) {
                <button type="button" class="aq-task" [class.aq-task-on]="task.id === selectedTaskId" (click)="pick.emit(task.id)">
                  <span class="aq-row"><span class="aq-grow">{{ task.method }}</span><p-tag [value]="taskState(task).label" [severity]="taskState(task).severity" /></span>
                  <span class="aq-muted">{{ task.owner }}{{ task.event ? ' / ' + task.event : '' }}{{ task.pending_tables.length ? ' · táblák: ' + task.pending_tables.join(', ') : '' }}</span>
                </button>
              }
            </div>
          </details>
        }
        @if (finishedHere().length) {
          <details class="aq-card" [open]="finishedOpen()" (toggle)="finishedOpen.set($any($event.target).open)">
            <summary class="cursor-pointer aq-title">Befejezett futások ({{ finishedHere().length }})</summary>
            <ul class="aq-list mt-2">
              @for (run of finishedHere(); track run.id) {
                <li class="aq-item">
                  <span class="aq-item-main">
                    <span>{{ run.method }} <span class="aq-muted">· {{ duration(run) }}</span></span>
                    @if (run.error || run.apply_error) { <span class="aq-muted">{{ (run.error || run.apply_error || '').slice(0, 220) }}</span> }
                  </span>
                  <span class="aq-item-side">
                    <p-tag [value]="finishedLabel(run).label" [severity]="finishedLabel(run).severity" />
                    @if (run.exchange_id) { <a class="text-sm underline" [href]="logUrl(run)" target="_blank" rel="noopener">AI-napló</a> }
                  </span>
                </li>
              }
            </ul>
          </details>
        }
      </section>
    }
  `,
})
export class MigratorAiQueue implements OnInit, OnChanges, OnDestroy {
  @Input() apiBase = '';
  /** The module of the assistant panel (none: the toolbar's global view). */
  @Input() jobId = '';
  @Input() compact = false;
  @Input() summary: AiSummary | null = null;
  @Input() tasks: AiTask[] = [];
  @Input() selectedTaskId = '';
  /** A run of this module finished (the parent reloads its task list and proposals). */
  @Output() finished = new EventEmitter<AiRun>();
  @Output() pick = new EventEmitter<string>();
  @Output() activeChange = new EventEmitter<boolean>();
  /** compact: the developer clicked the toolbar indicator (the app opens the assistant tab). */
  @Output() open = new EventEmitter<void>();
  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly mutation = {headers: new HttpHeaders({'X-Frm-Client': 'local-ui'})};
  private timer?: ReturnType<typeof setTimeout>;
  private ticker?: ReturnType<typeof setInterval>;
  private destroyed = false;
  private skew = 0;
  private known = new Set<string>();
  private wasActive = false;
  readonly queue = signal<AiQueue | null>(null);
  readonly now = signal(Date.now());
  readonly busy = signal(false);
  readonly error = signal('');
  readonly tasksOpen = signal(true);
  readonly finishedOpen = signal(false);
  readonly activeHere = computed(() => {
    const active = this.queue()?.active;
    return active && (!this.jobId || active.job_id === this.jobId) ? active : null;
  });
  readonly queuedHere = computed(() => (this.queue()?.queued ?? []).filter(run => !this.jobId || run.job_id === this.jobId));
  readonly finishedHere = computed(() => (this.queue()?.finished ?? []).filter(run => !this.jobId || run.job_id === this.jobId).slice(0, 15));

  ngOnInit(): void {
    this.ticker = setInterval(() => this.now.set(Date.now()), 1000);
    void this.refresh();
  }
  ngOnChanges(changes: SimpleChanges): void {
    if ((changes['apiBase'] && !changes['apiBase'].firstChange) || (changes['jobId'] && !changes['jobId'].firstChange)) {
      this.known.clear(); void this.refresh();
    }
  }
  ngOnDestroy(): void { this.destroyed = true; clearTimeout(this.timer); clearInterval(this.ticker); }

  private path(path: string): string {
    return this.apiBase + (this.jobId ? '/jobs/' + encodeURIComponent(this.jobId) + '/assistant' : '/assistant') + path;
  }
  private schedule(delay: number): void { clearTimeout(this.timer); if (!this.destroyed) this.timer = setTimeout(() => void this.refresh(), delay); }

  async refresh(): Promise<void> {
    if (!this.apiBase) { this.schedule(4000); return; }
    try {
      const state = await firstValueFrom(this.http.get<AiQueue>(this.path('/queue')));
      if (this.destroyed) return;
      this.skew = Date.now() / 1000 - state.server_time;
      // The runs finished before this view opened are history, not news for the parent.
      const initial = this.known.size === 0;
      for (const run of state.finished) {
        if (!this.known.has(run.id)) {
          this.known.add(run.id);
          if (!initial && (!this.jobId || run.job_id === this.jobId)) this.finished.emit(run);
        }
      }
      if (this.known.size === 0) this.known.add('-');
      this.queue.set(state);
      const active = Boolean(state.active || state.queued.length);
      if (active !== this.wasActive) this.activeChange.emit(active);
      this.wasActive = active;
      this.error.set('');
      this.schedule(active ? 1000 : 5000);
    } catch (error) {
      if (this.destroyed) return;
      if (!this.compact) this.error.set(this.text(error));
      this.schedule(8000);
    }
  }

  async saveSetting(key: 'auto_run' | 'auto_apply', value: boolean): Promise<void> {
    await this.mutate(() => firstValueFrom(this.http.post(this.apiBase + '/assistant/settings', {...this.settingsBody(), [key]: value}, this.mutation)));
  }
  private settingsBody(): Record<string, unknown> {
    const s = this.queue()?.settings;
    return s ? {url: s.url, model: s.model, num_ctx: s.num_ctx, num_predict: s.num_predict, timeout: s.timeout,
      auto_run: s.auto_run, auto_apply: s.auto_apply} : {};
  }
  async startAll(force: boolean): Promise<void> {
    await this.mutate(() => firstValueFrom(this.http.post(this.path('/auto'), {force}, this.mutation)));
  }
  async stop(runId: string | null, clearQueue: boolean): Promise<void> {
    await this.mutate(() => firstValueFrom(this.http.post(this.path('/cancel'), {run_id: runId, clear_queue: clearQueue}, this.mutation)));
  }
  private async mutate(action: () => Promise<unknown>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true); this.error.set('');
    try { await action(); } catch (error) { this.error.set(this.text(error)); }
    finally { this.busy.set(false); await this.refresh(); }
  }
  private text(error: unknown): string {
    if (error instanceof HttpErrorResponse) {
      const detail: unknown = error.error?.detail;
      if (typeof detail === 'string' && detail.trim()) return detail;
      if (error.status === 0) return 'A migrátor API-ja nem érhető el.';
      return error.message;
    }
    return error instanceof Error ? error.message : String(error);
  }

  phaseLabel(run: AiRun): string {
    return PHASES[run.progress?.phase ?? 'connecting'] ?? run.progress?.phase ?? '';
  }
  /** Elapsed time, ticking every second between two polls. */
  clock(run: AiRun): string {
    const started = run.started ? Date.parse(run.started) : NaN;
    const seconds = Number.isNaN(started) ? (run.progress?.elapsed ?? 0) : Math.max(0, (this.now() - started) / 1000 - this.skew);
    return this.format(seconds);
  }
  firstToken(run: AiRun): string {
    const p = run.progress;
    if (!p) return '—';
    if (p.first_token_seconds !== null) return this.format(p.first_token_seconds);
    return p.phase === 'prompt' ? 'folyamatban' : '—';
  }
  tokens(run: AiRun): string {
    const p = run.progress;
    return p ? String(p.thinking_tokens + p.content_tokens) : '0';
  }
  budget(run: AiRun): string {
    const p = run.progress; const limit = run.options?.num_predict;
    if (!p || !limit) return '—';
    return Math.min(100, Math.round(100 * (p.thinking_tokens + p.content_tokens) / limit)) + ' %';
  }
  duration(run: AiRun): string {
    const start = run.started ? Date.parse(run.started) : NaN; const end = run.finished ? Date.parse(run.finished) : NaN;
    return Number.isNaN(start) || Number.isNaN(end) ? '—' : this.format((end - start) / 1000);
  }
  private format(seconds: number): string {
    const total = Math.round(seconds); const m = Math.floor(total / 60); const s = total % 60;
    return m >= 60 ? Math.floor(m / 60) + ':' + String(m % 60).padStart(2, '0') + ':' + String(s).padStart(2, '0')
      : m + ':' + String(s).padStart(2, '0');
  }
  finishedLabel(run: AiRun): {label: string; severity: 'success' | 'info' | 'warn' | 'danger' | 'secondary'} {
    if (run.status === 'completed' && run.applied) return {label: 'Beillesztve', severity: 'success'};
    if (run.status === 'completed' && run.proposal_status === 'needs_context') return {label: 'Adatot kér', severity: 'warn'};
    if (run.status === 'completed') return {label: run.apply_error ? 'Javaslat, nem illeszthető be' : 'Átnézendő javaslat', severity: 'info'};
    if (run.status === 'cancelled') return {label: 'Leállítva', severity: 'secondary'};
    return {label: 'Sikertelen', severity: 'danger'};
  }
  taskState(task: AiTask): {label: string; severity: 'success' | 'info' | 'warn' | 'danger' | 'secondary'} {
    if (task.done) return {label: 'Kész', severity: 'success'};
    const active = this.activeHere();
    if (active?.task_id === task.id) return {label: 'Fut', severity: 'info'};
    if (this.queuedHere().some(run => run.task_id === task.id)) return {label: 'Sorban', severity: 'info'};
    if (task.pending_tables.length) return {label: 'Táblára vár', severity: 'warn'};
    const last = this.finishedHere().find(run => run.task_id === task.id);
    if (last && last.status === 'failed') return {label: 'Sikertelen', severity: 'danger'};
    if (last && last.status === 'completed' && !last.applied) return {label: 'Átnézendő', severity: 'info'};
    return {label: 'Indítható', severity: 'secondary'};
  }
  logUrl(run: AiRun): string {
    const root = this.apiBase.replace(/\/api\/?$/, '');
    return root + '/ai-log?job=' + encodeURIComponent(run.job_id) + '&task=' + encodeURIComponent(run.task_id)
      + (run.exchange_id ? '&exchange=' + encodeURIComponent(run.exchange_id) : '');
  }
}
