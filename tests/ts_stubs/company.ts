// Stand-ins for the company classes (ServiceBase, WFF, ToastService, FormBlock): TEST DOUBLES for tsc --noEmit only.
export class ServiceBase {
  protected readonly router = { url: '/', navigate: async (commands: unknown[], extras?: unknown): Promise<boolean> => { void commands; void extras; return true; } };
  protected url(path: string): string { return path; }
  get modName(): string { return this.router.url; }
}
export const WFF = {
  err(title: string, error: unknown): void { void title; void error; },
  debug(name: string, value: unknown): void { void name; void value; },
  trim(value: string, chars?: string): string { void chars; return value; },
};
export class ToastService {
  success(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
  warning(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
  danger(title: string, detail: string, history?: boolean, life?: number): void { void title; void detail; void history; void life; }
}
export class FormBlocksComponent {}
export namespace FormBlock { export type Structure = Record<string, unknown>; }
