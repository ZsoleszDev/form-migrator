// Stand-ins for the company classes with real Angular decorators: TEST DOUBLES for the ngc (strictTemplates) check only.
import { Component, Injectable, inject, input, output } from '@angular/core';
import { FormGroup } from '@angular/forms';
import { Router } from '@angular/router';

export abstract class ServiceBase {
  protected readonly router = inject(Router);
  protected url(path: string): string { return path; }
  get modName(): string { return this.router.url; }
}
export const WFF = {
  err(title: string, error: unknown): void { void title; void error; },
  debug(name: string, value: unknown): void { void name; void value; },
  trim(value: string, chars?: string): string { void chars; return value; },
};
@Injectable({ providedIn: 'root' })
export class ToastService {
  success(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
  warning(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
  danger(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
}
export namespace FormBlock {
  export interface Structure { type: string; ownId?: string; formControlName?: string; labelText?: string; col?: string; [property: string]: unknown; }
}
@Component({ selector: 'ank-form-block', standalone: true, template: '' })
export class FormBlocksComponent {
  readonly formStructure = input.required<FormBlock.Structure[]>();
  readonly formGroupGenerated = output<FormGroup>();
}
