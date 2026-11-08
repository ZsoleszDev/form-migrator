import { Component, DestroyRef, computed, effect, inject, input, output, signal, untracked } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { HttpBackend, HttpClient, HttpErrorResponse, HttpHeaders } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';
import { ButtonModule } from '@openng/optimus-ui/button';
import { MessageModule } from '@openng/optimus-ui/message';
import { ProgressBarModule } from '@openng/optimus-ui/progressbar';
import { TagModule } from '@openng/optimus-ui/tag';
import { ToggleSwitchModule } from '@openng/optimus-ui/toggleswitch';

/** Whether a request and its answer fit the context (frm_forms.frontend_assistant.plan). */
interface Fit { input_tokens: number; output_tokens: number; fits: boolean; required_num_ctx: number; required_num_predict: number }
/** One generated screen component and how it can be sent to the model. */
interface FrontendTask {
  id: string; target: string; name: string; bytes?: number; sent_bytes?: number; kept?: number; error?: string;
  whole?: Fit; parts?: Fit & {count: number; too_big: string[]}; recommended: 'whole' | 'parts' | null; busy: boolean;
}
interface FrontendProposalSummary {
  id: string; task_id: string; target: string; name: string; mode: 'whole' | 'parts'; status: 'proposal' | 'draft';
  calls: number; applied: boolean; warnings: string[]; problems: string[];
}
interface FrontendProposal extends FrontendProposalSummary {
  summary: string; notes: string[]; text: string; patch: string;
  validation: {structural_checks: string; typescript_syntax: 'passed' | 'failed' | 'not_available'};
}
interface FrontendIndex {
  enabled: boolean; connected: boolean; num_ctx: number; num_predict: number; notice: string;
  tasks: FrontendTask[]; proposals: FrontendProposalSummary[];
}
interface RunState { id: string; status: string; error?: string; result?: {id: string} }

const SYNTAX: Record<string, string> = {passed: 'rendben', failed: 'hibás', not_available: 'nem ellenőrizhető (nincs TypeScript a gépen)'};

/**
 * Optional: a generated screen rewritten by the local model the modern Angular way (standalone, signals, commented
 * code). The whole file is offered only when it fits the context; otherwise it goes in parts (the head with the
 * signals first, then the methods in groups). Nothing is written without review: the proposal shows the diff.
 */
@Component({
  selector: 'app-migrator-frontend-ai',
  imports: [FormsModule, ButtonModule, MessageModule, ProgressBarModule, TagModule, ToggleSwitchModule],
  host: {class: 'block min-w-0'},
  styles: `
    .fa { display: flex; flex-direction: column; gap: 0.875rem; min-width: 0; }
    .fa-card { display: flex; flex-direction: column; gap: 0.5rem; min-width: 0; padding: 0.875rem 1rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0); border-radius: var(--p-content-border-radius, 0.5rem); }
    .fa-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem 0.875rem; min-width: 0; }
    .fa-title { font-weight: 600; overflow-wrap: anywhere; }
    .fa-muted { font-size: 0.8125rem; color: var(--p-text-muted-color, #64748b); overflow-wrap: anywhere; }
    .fa-code { max-height: 28rem; overflow: auto; white-space: pre; font-size: 0.75rem; margin: 0; padding: 0.75rem;
      border: 1px solid var(--p-content-border-color, #e2e8f0); border-radius: var(--p-content-border-radius, 0.5rem); }
    @media (max-width: 40rem) { .fa-code { max-height: 18rem; } }
  `,
  template: `
    <section class="fa" data-testid="frontend-ai" aria-label="Frontend AI-átírás">
      <div class="fa-row">
        <h3 class="m-0 grow text-base font-semibold">Frontend AI-átírás (opcionális)</h3>
        <label class="fa-row">
          <p-toggleswitch [ngModel]="index()?.enabled ?? false" (ngModelChange)="setEnabled($event)" [disabled]="busy()"
            ariaLabel="Frontend AI-átírás engedélyezése" />
          <span class="text-sm">Engedélyezve</span>
        </label>
      </div>
      <p class="fa-muted m-0">A generált képernyőt a helyi modell standalone, signalos, kommentezett Angular-kódra írja át,
        ugyanazzal a működéssel. Sosem fut automatikusan, és átnézés nélkül nem kerül a fájlba. {{ index()?.notice }}</p>
      @if (error()) { <p-message severity="error" [closable]="true" (onClose)="error.set('')">{{ error() }}</p-message> }
      @if (busy()) { <p-progressbar mode="indeterminate" [style]="{height: '4px'}" /> }
      @if (index(); as data) {
        @if (!data.connected) { <p-message severity="warn">Nincs mentett AI-kapcsolat (Ollama-cím és modell).</p-message> }
        @for (task of data.tasks; track task.id) {
          <div class="fa-card">
            <div class="fa-row">
              <span class="fa-title grow">{{ task.name }}</span>
              @if (task.error) { <p-tag severity="danger" value="Nem olvasható" /> }
              @else { <p-tag [severity]="fitSeverity(task)" [value]="fitLabel(task)" /> }
              @if (task.busy) { <p-tag severity="info" value="Sorban / fut" /> }
            </div>
            @if (task.error) { <span class="fa-muted">{{ task.error }}</span> }
            @if (task.whole && task.parts) {
              <span class="fa-muted">{{ task.bytes }} byte; az AI {{ task.sent_bytes }} byte-ot kap{{ task.kept ? ' (' + task.kept
                + ' nagy állandó értéket a migrátor megtart és visszatesz)' : '' }}.</span>
              <span class="fa-muted">Teljes fájl: {{ fitText(task.whole, data) }}</span>
              @if (!task.whole.fits) {
                <span class="fa-muted">{{ task.parts.fits
                  ? 'Részenként ' + task.parts.count + ' kérésben elfér: előbb a fej a signalokkal, utána a metódusok csoportokban.'
                  : 'Részenként sem fér el' + (task.parts.too_big.length ? ' (túl nagy metódus: ' + task.parts.too_big.join(', ') + ')' : '')
                    + ': num_ctx ≥ ' + task.parts.required_num_ctx + ', num_predict ≥ ' + task.parts.required_num_predict + ' kellene.' }}</span>
              }
              <div class="fa-row">
                <p-button label="Teljes fájl átírása AI-val" [severity]="task.whole.fits ? 'primary' : 'secondary'"
                  [disabled]="!ready() || !task.whole.fits || task.busy || busy()" (onClick)="run(task, 'whole')" />
                <p-button [label]="'Részenként (' + task.parts.count + ' kérés)'"
                  [severity]="!task.whole.fits && task.parts.fits ? 'primary' : 'secondary'" variant="outlined"
                  [disabled]="!ready() || !task.parts.fits || task.busy || busy()" (onClick)="run(task, 'parts')" />
              </div>
            }
          </div>
        } @empty { <p class="fa-muted m-0">Ebben a modulban nincs generált képernyő.</p> }
        @if (data.proposals.length) {
          <div class="fa-card">
            <span class="fa-title">Frontend-javaslatok</span>
            @for (item of data.proposals; track item.id) {
              <div class="fa-row">
                <span class="grow">{{ item.name }} · {{ item.mode === 'whole' ? 'teljes fájl' : 'részenként' }} · {{ item.calls }} hívás</span>
                <p-tag [severity]="item.applied ? 'success' : item.status === 'proposal' ? 'info' : 'warn'"
                  [value]="item.applied ? 'Beillesztve' : item.status === 'proposal' ? 'Átnézendő' : 'Hibás vázlat'" />
                <p-button label="Megnyitás" size="small" variant="text" (onClick)="open(item.id)" />
              </div>
            }
          </div>
        }
      }
      @if (proposal(); as result) {
        <div class="fa-card" data-testid="frontend-ai-proposal">
          <div class="fa-row">
            <span class="fa-title grow">{{ result.name }}: {{ result.summary }}</span>
            <p-button label="Bezárás" size="small" variant="text" (onClick)="proposal.set(null)" />
          </div>
          @for (problem of result.problems; track $index) { <p-message severity="error">{{ problem }}</p-message> }
          @for (warning of result.warnings; track $index) { <p-message severity="warn">{{ warning }}</p-message> }
          @for (note of result.notes; track $index) { <span class="fa-muted">{{ note }}</span> }
          <span class="fa-muted">TypeScript-szintaxis: {{ syntaxText(result) }}.</span>
          <details open><summary class="cursor-pointer text-sm">Változások (diff)</summary>
            <pre class="fa-code" tabindex="0">{{ result.patch }}</pre></details>
          <details><summary class="cursor-pointer text-sm">Az új fájl</summary>
            <pre class="fa-code" tabindex="0">{{ result.text }}</pre></details>
          <div class="fa-row">
            <p-button label="Beillesztés a képernyőbe" [disabled]="result.status !== 'proposal' || result.applied || busy()"
              (onClick)="apply(result)" />
          </div>
        </div>
      }
    </section>
  `,
})
export class MigratorFrontendAi {
  readonly apiBase = input('');
  readonly jobId = input('');
  /** The run settings the developer is about to use: the fit is measured against them. */
  readonly numCtx = input(16384);
  readonly numPredict = input(4096);
  readonly timeout = input(900);
  readonly attempts = input(2);
  readonly think = input('default');
  /** A proposal was written into the module's files (the module ZIP is fresh). */
  readonly applied = output<string>();

  private readonly http = new HttpClient(inject(HttpBackend));
  private readonly mutation = {headers: new HttpHeaders({'X-Frm-Client': 'local-ui'})};
  private pollTimer?: ReturnType<typeof setTimeout>;
  private epoch = 0;
  private watching = 0;

  readonly index = signal<FrontendIndex | null>(null);
  readonly proposal = signal<FrontendProposal | null>(null);
  readonly busy = signal(false);
  readonly error = signal('');
  readonly ready = computed(() => Boolean(this.index()?.enabled && this.index()?.connected));

  constructor() {
    // A new module or new run settings: measure the screens again.
    effect(() => {
      const job = this.jobId(), base = this.apiBase(), ctx = this.numCtx(), predict = this.numPredict();
      untracked(() => { if (job && base) void this.reload(job, ctx, predict); else this.index.set(null); });
    });
    inject(DestroyRef).onDestroy(() => { ++this.epoch; ++this.watching; clearTimeout(this.pollTimer); });
  }

  fitLabel(task: FrontendTask): string {
    if (task.whole?.fits) return 'Egyben belefér';
    return task.parts?.fits ? 'Részenként fér bele (' + task.parts.count + ' kérés)' : 'Nem fér bele';
  }
  fitSeverity(task: FrontendTask): 'success' | 'warn' | 'danger' {
    return task.whole?.fits ? 'success' : task.parts?.fits ? 'warn' : 'danger';
  }
  fitText(fit: Fit, data: FrontendIndex): string {
    return '~' + fit.input_tokens + ' token kérés + ~' + fit.output_tokens + ' token válasz; num_ctx ≥ ' + fit.required_num_ctx
      + ', num_predict ≥ ' + fit.required_num_predict + ' kell (most ' + data.num_ctx + ' / ' + data.num_predict + ').';
  }
  syntaxText(result: FrontendProposal): string { return SYNTAX[result.validation.typescript_syntax] ?? '—'; }

  async setEnabled(value: boolean): Promise<void> {
    await this.mutate(async () => {
      await firstValueFrom(this.http.post(this.apiBase() + '/assistant/settings', {frontend_ai: value}, this.mutation));
      await this.reload(this.jobId(), this.numCtx(), this.numPredict());
    });
  }

  async run(task: FrontendTask, mode: 'whole' | 'parts'): Promise<void> {
    await this.mutate(async () => {
      const body = {task_id: task.id, mode, num_ctx: this.numCtx(), num_predict: this.numPredict(), timeout: this.timeout(),
        attempts: this.attempts(), think: this.think()};
      const started = await firstValueFrom(this.http.post<RunState>(this.path(''), body, this.mutation));
      await this.reload(this.jobId(), this.numCtx(), this.numPredict());
      this.watch(started.id, ++this.watching);
    });
  }

  async open(id: string): Promise<void> {
    try { this.proposal.set(await firstValueFrom(this.http.get<FrontendProposal>(this.path('/proposals/' + encodeURIComponent(id))))); }
    catch (error) { this.error.set(this.text(error)); }
  }

  async apply(result: FrontendProposal): Promise<void> {
    if (!window.confirm('Beilleszted az átírt komponenst (' + result.name + ')? Az eredeti fájl mentése a javaslat mellé kerül.')) return;
    await this.mutate(async () => {
      await firstValueFrom(this.http.post(this.path('/proposals/' + encodeURIComponent(result.id) + '/apply'), {reviewed: true}, this.mutation));
      await this.open(result.id);
      await this.reload(this.jobId(), this.numCtx(), this.numPredict());
      this.applied.emit(this.jobId());
    });
  }

  /** Follows one run until it finishes (the AI queue shows its live state), then shows its proposal. */
  private watch(runId: string, epoch: number): void {
    clearTimeout(this.pollTimer);
    this.pollTimer = setTimeout(async () => {
      if (epoch !== this.watching) return;
      try {
        const state = await firstValueFrom(this.http.get<RunState>(this.apiBase() + '/jobs/' + encodeURIComponent(this.jobId())
          + '/assistant/runs/' + encodeURIComponent(runId)));
        if (state.status === 'queued' || state.status === 'running') { this.watch(runId, epoch); return; }
        await this.reload(this.jobId(), this.numCtx(), this.numPredict());
        if (state.result?.id) await this.open(state.result.id);
        else if (state.error) this.error.set(state.error);
      } catch (error) { this.error.set(this.text(error)); }
    }, 2000);
  }

  private async reload(job: string, ctx: number, predict: number): Promise<void> {
    const epoch = ++this.epoch;
    try {
      const params = '?num_ctx=' + encodeURIComponent(ctx) + '&num_predict=' + encodeURIComponent(predict);
      const value = await firstValueFrom(this.http.get<FrontendIndex>(this.apiBase() + '/jobs/' + encodeURIComponent(job)
        + '/frontend-assistant' + params));
      if (epoch === this.epoch) this.index.set(value);
    } catch (error) {
      if (epoch === this.epoch) { this.index.set(null); this.error.set(this.text(error)); }
    }
  }

  private path(suffix: string): string {
    return this.apiBase() + '/jobs/' + encodeURIComponent(this.jobId()) + '/frontend-assistant' + suffix;
  }
  private async mutate(action: () => Promise<void>): Promise<void> {
    if (this.busy()) return;
    this.busy.set(true); this.error.set('');
    try { await action(); } catch (error) { this.error.set(this.text(error)); }
    finally { this.busy.set(false); }
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
}
