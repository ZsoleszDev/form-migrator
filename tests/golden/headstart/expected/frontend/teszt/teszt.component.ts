// CREATE_ONCE: szerkeszthető képernyő. Migrációs részletek: MIGRATION_NOTES.md.
import { Component, inject } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { FormGroup } from '@angular/forms';
import { EMPTY, catchError, tap } from 'rxjs';
// TODO: importáld a saját csomagodból: ToastService (config: toast_service_import_path).
// TODO: importáld a saját csomagodból: ServiceBase, WFF (java-imports.json).
import { WfTable } from '../wf-table';
// TODO: importáld a saját csomagodból: FormBlocksComponent és FormBlock.Structure.

@Component({
  selector: 'app-teszt',
  standalone: true,
  imports: [FormBlocksComponent, WfTable],
  template: `
    <ank-form-block [formStructure]="structures['vElekAdlap']" (formGroupGenerated)="onFormGroupGenerated('vElekAdlap', $event)" />
    <wf-table [value]="aitRows" [columns]="aitColumns" [rows]="15" [(selection)]="aitSelection" />
    <ank-form-block [formStructure]="structures['cgnvW011']" (formGroupGenerated)="onFormGroupGenerated('cgnvW011', $event)" />
  `,
})
export class TesztComponent extends ServiceBase {
  protected readonly toast = inject(ToastService);
  protected readonly toastLife = { success: 3000, warning: 8000, danger: 6000 };
  private readonly http = inject(HttpClient);

  // A FormBlock-régiók FormGroupjai (onFormGroupGenerated).
  protected readonly forms: Record<string, FormGroup> = {};

  protected readonly structures: Record<string, FormBlock.Structure[]> = {
    vElekAdlap: [
      { type: 'autocomplete', ownId: 'V_ELEK_ADLAP.UBI_INPTIP_KOD', formControlName: 'ubiInptipKod', labelText: 'Adatlap típus', col: '3', maxLenght: 10, dropdown: true, optionLabel: 'label', optionValue: 'value', suggestions: [] },
      { type: 'text', ownId: 'V_ELEK_ADLAP.UBI_INPTIP_KOD_NEV', formControlName: 'ubiInptipKodNev', labelText: 'Megnevezés', col: '9', readonly: true, maxLenght: 80 },
      { type: 'label', ownId: 'V_ELEK_ADLAP.L_URES_1', labelText: '', col: '12' },
      { type: 'calendar', ownId: 'V_ELEK_ADLAP.DATUM_TOL', formControlName: 'datumTol', labelText: 'Dátum', col: '5', dateFormat: 'yy.mm.dd', showIcon: true, showTime: true },
      { type: 'label', ownId: 'V_ELEK_ADLAP.L_URES_2', labelText: '', col: '2' },
      { type: 'label', ownId: 'V_ELEK_ADLAP.DATUM_IG#separator', labelText: '-', col: '1' },
      { type: 'calendar', ownId: 'V_ELEK_ADLAP.DATUM_IG', formControlName: 'datumIg', labelText: '', col: '4', dateFormat: 'yy.mm.dd', showIcon: true, showTime: true },
      { type: 'checkBox', ownId: 'V_ELEK_ADLAP.UBI_E04', formControlName: 'ubiE04', labelText: 'Elektronikus', col: '12', binary: true },
    ],
    cgnvW011: [
      { type: 'button', ownId: 'CGNV$W01_1.PB_RESZLETEK', labelText: 'Részletek', col: '2', colBefore: '10', btnSeverity: 'primary', onClick: () => this.onPbReszletekClick() },
    ],
  };

  // AIT: a táblázat oszlopai, sorai (a backend DTO-mezőivel) és a kiválasztott sor.
  protected readonly aitColumns = [
    { field: 'aitKulcs', header: 'Kulcs', width: '20.0%' },
    { field: 'aitTipus', header: 'Típus', width: '20.0%' },
    { field: 'aitStatus', header: 'Státusz', width: '10.0%' },
    { field: 'aitMegj', header: 'Megjegyzés', width: '50.0%' },
  ];
  protected aitRows: Record<string, unknown>[] = [];
  protected aitSelection: Record<string, unknown> | null = null;

  constructor() {
    super();
  }

  aitUpdate(body: unknown) {
    return this.http.put<any>(this.url('ait/update'), body).pipe(
      tap(res => WFF.debug(this.modName + '.aitUpdate', res)),
      catchError(error => {
        WFF.err('Hiba', error);
        return EMPTY;
      }),
    );
  }

  aitSearch(body: unknown) {
    return this.http.post<any>(this.url('ait/query/search'), body).pipe(
      tap(res => WFF.debug(this.modName + '.aitSearch', res)),
      catchError(error => {
        WFF.err('Hiba', error);
        return EMPTY;
      }),
    );
  }

  lovInptip(body: unknown) {
    return this.http.post<any>(this.url('lov/inptip'), body).pipe(
      tap(res => WFF.debug(this.modName + '.lovInptip', res)),
      catchError(error => {
        WFF.err('Hiba', error);
        return EMPTY;
      }),
    );
  }

  commitForm(body: unknown) {
    return this.http.post<any>(this.url('commit'), body).pipe(
      tap(res => WFF.debug(this.modName + '.commitForm', res)),
      catchError(error => {
        WFF.err('Hiba', error);
        return EMPTY;
      }),
    );
  }

  // A FormBlock elkészítette a régió FormGroupját.
  protected onFormGroupGenerated(region: string, group: FormGroup): void {
    this.forms[region] = group;
  }

  // CGNV$W01_1.PB_RESZLETEK
  protected onPbReszletekClick(): void {
    const vElekAdlap = this.forms['vElekAdlap']?.getRawValue() ?? {};
    this.aitSearch({ criteria: { vElekAdlapUbiInptipKod: vElekAdlap.ubiInptipKod }, offset: 0, limit: 200 }).subscribe(res => {
      this.aitRows = res.rows ?? [];
    });
  }
}
