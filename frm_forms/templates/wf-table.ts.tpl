// wf-table: a képernyők táblázata, az Optimus p-table köré. Egyszer kell a projektbe tenni (a generált képernyők
// ezt használják); ha változik a p-table importja vagy selectora, csak ezt a fájlt kell módosítani.
//
// A p-table minden bemenete és kimenete átadható, a sablonjai is (#header, #body, #caption, #emptymessage ...):
//   <wf-table [value]="rows" [columns]="columns" [(selection)]="selected" [globalFilterFields]="['nev']">
//     <ng-template #caption> ... saját szűrők ... </ng-template>
//     <ng-template #body let-row let-columns="columns"> <tr [pSelectableRow]="row"> ... </tr> </ng-template>
//   </wf-table>
// Megadott sablon nélkül az alapértelmezett fejléc és sor jelenik meg a columns alapján ({ field, header, width }).
// Ha az Optimus valamelyik bemenetet nem ismeri, itt egy helyen törölhető.
import { Component, TemplateRef, contentChild, input, model, output } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { TableModule } from '@openng/optimus-ui/table';
import { ButtonModule } from '@openng/optimus-ui/button';

export const WF_TABLE_VERSION = '1';

export interface WfTableColumn {
  field: string;
  header: string;
  width?: string;
}

/** Gomb a sor végén (a Forms-blokk gombja); a kattintás kiválasztja a sort, és az action kimenet a gomb ownId-ját adja. */
export interface WfTableAction {
  ownId: string;
  label: string;
  disabled?: boolean;
}

@Component({
  selector: 'wf-table',
  standalone: true,
  imports: [TableModule, ButtonModule, NgTemplateOutlet],
  template: `
    <p-table [value]="value()" [columns]="columns()" [dataKey]="dataKey()" [paginator]="paginator()" [rows]="rows()"
      [first]="first()" [totalRecords]="totalRecords()" [pageLinks]="pageLinks()" [rowsPerPageOptions]="rowsPerPageOptions()"
      [alwaysShowPaginator]="alwaysShowPaginator()" [paginatorPosition]="paginatorPosition()"
      [paginatorStyleClass]="paginatorStyleClass()" [paginatorDropdownAppendTo]="paginatorDropdownAppendTo()"
      [paginatorDropdownScrollHeight]="paginatorDropdownScrollHeight()" [currentPageReportTemplate]="currentPageReportTemplate()"
      [showCurrentPageReport]="showCurrentPageReport()" [showJumpToPageDropdown]="showJumpToPageDropdown()"
      [showJumpToPageInput]="showJumpToPageInput()" [showFirstLastIcon]="showFirstLastIcon()" [showPageLinks]="showPageLinks()"
      [paginatorLocale]="paginatorLocale()" [sortField]="sortField()" [sortOrder]="sortOrder()" [sortMode]="sortMode()"
      [multiSortMeta]="multiSortMeta()" [defaultSortOrder]="defaultSortOrder()" [resetPageOnSort]="resetPageOnSort()"
      [customSort]="customSort()" [showInitialSortBadge]="showInitialSortBadge()" [selectionMode]="selectionMode()"
      [selection]="selection()" [selectAll]="selectAll()" [selectionPageOnly]="selectionPageOnly()"
      [metaKeySelection]="metaKeySelection()" [rowSelectable]="rowSelectable()" [compareSelectionBy]="compareSelectionBy()"
      [contextMenu]="contextMenu()" [contextMenuSelection]="contextMenuSelection()"
      [contextMenuSelectionMode]="contextMenuSelectionMode()" [rowTrackBy]="rowTrackBy()" [lazy]="lazy()"
      [lazyLoadOnInit]="lazyLoadOnInit()" [filters]="filters()" [globalFilterFields]="globalFilterFields()"
      [filterDelay]="filterDelay()" [filterLocale]="filterLocale()" [expandedRowKeys]="expandedRowKeys()"
      [editingRowKeys]="editingRowKeys()" [rowExpandMode]="rowExpandMode()" [editMode]="editMode()"
      [rowGroupMode]="rowGroupMode()" [groupRowsBy]="groupRowsBy()" [groupRowsByOrder]="groupRowsByOrder()"
      [scrollable]="scrollable()" [scrollHeight]="scrollHeight()" [virtualScroll]="virtualScroll()"
      [virtualScrollItemSize]="virtualScrollItemSize()" [virtualScrollOptions]="virtualScrollOptions()"
      [virtualScrollDelay]="virtualScrollDelay()" [frozenColumns]="frozenColumns()" [frozenValue]="frozenValue()"
      [frozenWidth]="frozenWidth()" [resizableColumns]="resizableColumns()" [columnResizeMode]="columnResizeMode()"
      [reorderableColumns]="reorderableColumns()" [loading]="loading()" [loadingIcon]="loadingIcon()"
      [showLoader]="showLoader()" [rowHover]="rowHover()" [stateKey]="stateKey()" [stateStorage]="stateStorage()"
      [csvSeparator]="csvSeparator()" [exportFilename]="exportFilename()" [exportFunction]="exportFunction()"
      [exportHeader]="exportHeader()" [responsiveLayout]="responsiveLayout()" [breakpoint]="breakpoint()"
      [size]="size()" [showGridlines]="showGridlines()" [stripedRows]="stripedRows()" [style]="style()"
      [styleClass]="styleClass()" [tableStyle]="tableStyle()" [tableStyleClass]="tableStyleClass()"
      (selectionChange)="selection.set($event)" (firstChange)="first.set($event)" (rowsChange)="rows.set($event)"
      (selectAllChange)="selectAll.set($event)" (contextMenuSelectionChange)="contextMenuSelection.set($event)"
      (onRowSelect)="onRowSelect.emit($event)" (onRowUnselect)="onRowUnselect.emit($event)" (onPage)="onPage.emit($event)"
      (onSort)="onSort.emit($event)" (onFilter)="onFilter.emit($event)" (onLazyLoad)="onLazyLoad.emit($event)"
      (onRowExpand)="onRowExpand.emit($event)" (onRowCollapse)="onRowCollapse.emit($event)"
      (onContextMenuSelect)="onContextMenuSelect.emit($event)" (onColResize)="onColResize.emit($event)"
      (onColReorder)="onColReorder.emit($event)" (onRowReorder)="onRowReorder.emit($event)"
      (onEditInit)="onEditInit.emit($event)" (onEditComplete)="onEditComplete.emit($event)"
      (onEditCancel)="onEditCancel.emit($event)" (onHeaderCheckboxToggle)="onHeaderCheckboxToggle.emit($event)"
      (sortFunction)="sortFunction.emit($event)" (onStateSave)="onStateSave.emit($event)"
      (onStateRestore)="onStateRestore.emit($event)">
      @if (captionTemplate(); as template) {
        <ng-template #caption><ng-container *ngTemplateOutlet="template" /></ng-template>
      }
      @if (colgroupTemplate(); as template) {
        <ng-template #colgroup let-columns><ng-container *ngTemplateOutlet="template; context: { $implicit: columns }" /></ng-template>
      }
      <ng-template #header let-columns>
        @if (headerTemplate(); as template) {
          <ng-container *ngTemplateOutlet="template; context: { $implicit: columns }" />
        } @else {
          <tr>
            @for (col of columns; track col.field) { <th [style.width]="col.width" class="whitespace-nowrap">{{ col.header }}</th> }
            @if (actions().length) { <th>Műveletek</th> }
          </tr>
        }
      </ng-template>
      <ng-template #body let-row let-columns="columns" let-rowIndex="rowIndex" let-expanded="expanded" let-editing="editing">
        @if (bodyTemplate(); as template) {
          <ng-container *ngTemplateOutlet="template; context: { $implicit: row, columns: columns, rowIndex: rowIndex, expanded: expanded, editing: editing }" />
        } @else {
          <tr [pSelectableRow]="row">
            @for (col of columns; track col.field) { <td>{{ row[col.field] }}</td> }
            @if (actions().length) {
              <td class="whitespace-nowrap">
                @for (button of actions(); track button.ownId) {
                  <button pButton type="button" [disabled]="!!button.disabled"
                    (click)="$event.stopPropagation(); selection.set(row); action.emit(button.ownId)">{{ button.label }}</button>
                }
              </td>
            }
          </tr>
        }
      </ng-template>
      @if (rowexpansionTemplate(); as template) {
        <ng-template #rowexpansion let-row let-columns="columns" let-rowIndex="rowIndex">
          <ng-container *ngTemplateOutlet="template; context: { $implicit: row, columns: columns, rowIndex: rowIndex }" />
        </ng-template>
      }
      @if (groupheaderTemplate(); as template) {
        <ng-template #groupheader let-row let-rowIndex="rowIndex">
          <ng-container *ngTemplateOutlet="template; context: { $implicit: row, rowIndex: rowIndex }" />
        </ng-template>
      }
      @if (groupfooterTemplate(); as template) {
        <ng-template #groupfooter let-row let-rowIndex="rowIndex">
          <ng-container *ngTemplateOutlet="template; context: { $implicit: row, rowIndex: rowIndex }" />
        </ng-template>
      }
      @if (loadingbodyTemplate(); as template) {
        <ng-template #loadingbody let-columns="columns"><ng-container *ngTemplateOutlet="template; context: { columns: columns }" /></ng-template>
      }
      <ng-template #emptymessage let-columns>
        @if (emptymessageTemplate(); as template) {
          <ng-container *ngTemplateOutlet="template; context: { $implicit: columns }" />
        } @else {
          <tr><td [attr.colspan]="(columns?.length ?? 0) + (actions().length ? 1 : 0)" class="py-4 text-center">{{ emptyMessage() }}</td></tr>
        }
      </ng-template>
      @if (footerTemplate(); as template) {
        <ng-template #footer let-columns><ng-container *ngTemplateOutlet="template; context: { $implicit: columns }" /></ng-template>
      }
      @if (summaryTemplate(); as template) {
        <ng-template #summary><ng-container *ngTemplateOutlet="template" /></ng-template>
      }
      @if (paginatorleftTemplate(); as template) {
        <ng-template #paginatorleft let-state><ng-container *ngTemplateOutlet="template; context: { $implicit: state }" /></ng-template>
      }
      @if (paginatorrightTemplate(); as template) {
        <ng-template #paginatorright let-state><ng-container *ngTemplateOutlet="template; context: { $implicit: state }" /></ng-template>
      }
    </p-table>
  `,
})
export class WfTable {
  // Adatok és oszlopok
  readonly value = input<any[]>([]);
  readonly columns = input<any[]>([]);
  readonly dataKey = input<string | undefined>(undefined);
  // Lapozás (alapérték: lapozó, 15 sor)
  readonly paginator = input<boolean>(true);
  readonly rows = model<number | undefined>(15);
  readonly first = model<number | null | undefined>(0);
  readonly totalRecords = input<number>(0);
  readonly pageLinks = input<number>(5);
  readonly rowsPerPageOptions = input<any[] | undefined>(undefined);
  readonly alwaysShowPaginator = input<boolean>(true);
  readonly paginatorPosition = input<'top' | 'bottom' | 'both'>('bottom');
  readonly paginatorStyleClass = input<string | undefined>(undefined);
  readonly paginatorDropdownAppendTo = input<any>(undefined);
  readonly paginatorDropdownScrollHeight = input<string>('200px');
  readonly currentPageReportTemplate = input<string>('{currentPage} of {totalPages}');
  readonly showCurrentPageReport = input<boolean | undefined>(undefined);
  readonly showJumpToPageDropdown = input<boolean | undefined>(undefined);
  readonly showJumpToPageInput = input<boolean | undefined>(undefined);
  readonly showFirstLastIcon = input<boolean>(true);
  readonly showPageLinks = input<boolean>(true);
  readonly paginatorLocale = input<string | undefined>(undefined);
  // Rendezés
  readonly sortField = input<string | null | undefined>(undefined);
  readonly sortOrder = input<number>(1);
  readonly sortMode = input<'single' | 'multiple'>('single');
  readonly multiSortMeta = input<any[] | null | undefined>(undefined);
  readonly defaultSortOrder = input<number>(1);
  readonly resetPageOnSort = input<boolean>(true);
  readonly customSort = input<boolean | undefined>(undefined);
  readonly showInitialSortBadge = input<boolean>(true);
  // Kiválasztás (alapérték: egy sor)
  readonly selectionMode = input<'single' | 'multiple' | null | undefined>('single');
  readonly selection = model<any>(null);
  readonly selectAll = model<boolean | null>(null);
  readonly selectionPageOnly = input<boolean | undefined>(undefined);
  readonly metaKeySelection = input<boolean | undefined>(false);
  readonly rowSelectable = input<any>(undefined);
  readonly compareSelectionBy = input<'equals' | 'deepEquals'>('deepEquals');
  readonly contextMenu = input<any>(undefined);
  readonly contextMenuSelection = model<any>(undefined);
  readonly contextMenuSelectionMode = input<string>('separate');
  readonly rowTrackBy = input<(index: number, item: any) => any>((index: number, item: any) => item);
  // Lusta betöltés, szűrés
  readonly lazy = input<boolean>(false);
  readonly lazyLoadOnInit = input<boolean>(true);
  readonly filters = input<Record<string, any>>({});
  readonly globalFilterFields = input<string[] | undefined>(undefined);
  readonly filterDelay = input<number>(300);
  readonly filterLocale = input<string | undefined>(undefined);
  // Kibontás, szerkesztés, csoportosítás
  readonly expandedRowKeys = input<Record<string, boolean>>({});
  readonly editingRowKeys = input<Record<string, boolean>>({});
  readonly rowExpandMode = input<'multiple' | 'single'>('multiple');
  readonly editMode = input<'cell' | 'row'>('cell');
  readonly rowGroupMode = input<'subheader' | 'rowspan' | undefined>(undefined);
  readonly groupRowsBy = input<any>(undefined);
  readonly groupRowsByOrder = input<number>(1);
  // Görgetés, rögzített oszlopok
  readonly scrollable = input<boolean | undefined>(true);
  readonly scrollHeight = input<string | undefined>(undefined);
  readonly virtualScroll = input<boolean | undefined>(undefined);
  readonly virtualScrollItemSize = input<number | undefined>(undefined);
  readonly virtualScrollOptions = input<any>(undefined);
  readonly virtualScrollDelay = input<number>(250);
  readonly frozenColumns = input<any[] | undefined>(undefined);
  readonly frozenValue = input<any[] | undefined>(undefined);
  readonly frozenWidth = input<string | undefined>(undefined);
  // Oszlopok méretezése, sorrendje, betöltésjelző, állapotmentés, export
  readonly resizableColumns = input<boolean | undefined>(undefined);
  readonly columnResizeMode = input<string>('fit');
  readonly reorderableColumns = input<boolean | undefined>(undefined);
  readonly loading = input<boolean | undefined>(undefined);
  readonly loadingIcon = input<string | undefined>(undefined);
  readonly showLoader = input<boolean>(true);
  readonly rowHover = input<boolean | undefined>(undefined);
  readonly stateKey = input<string | undefined>(undefined);
  readonly stateStorage = input<'session' | 'local'>('session');
  readonly csvSeparator = input<string>(',');
  readonly exportFilename = input<string>('download');
  readonly exportFunction = input<any>(undefined);
  readonly exportHeader = input<string | undefined>(undefined);
  readonly responsiveLayout = input<string>('scroll');
  readonly breakpoint = input<string>('960px');
  // Megjelenés (alapérték: kicsi, rácsvonalas)
  readonly size = input<'small' | 'large' | undefined>('small');
  readonly showGridlines = input<boolean | undefined>(true);
  readonly stripedRows = input<boolean | undefined>(undefined);
  readonly style = input<Record<string, any> | null | undefined>(undefined);
  readonly styleClass = input<string | undefined>(undefined);
  readonly tableStyle = input<Record<string, any> | null | undefined>(undefined);
  readonly tableStyleClass = input<string | undefined>(undefined);
  // A sor végi gombok és az üres tábla szövege
  readonly actions = input<readonly WfTableAction[]>([]);
  readonly emptyMessage = input<string>('Nincs megjeleníthető adat.');

  // A p-table eseményei, változatlanul továbbadva
  readonly action = output<string>();
  readonly onRowSelect = output<any>();
  readonly onRowUnselect = output<any>();
  readonly onPage = output<any>();
  readonly onSort = output<any>();
  readonly onFilter = output<any>();
  readonly onLazyLoad = output<any>();
  readonly onRowExpand = output<any>();
  readonly onRowCollapse = output<any>();
  readonly onContextMenuSelect = output<any>();
  readonly onColResize = output<any>();
  readonly onColReorder = output<any>();
  readonly onRowReorder = output<any>();
  readonly onEditInit = output<any>();
  readonly onEditComplete = output<any>();
  readonly onEditCancel = output<any>();
  readonly onHeaderCheckboxToggle = output<any>();
  readonly sortFunction = output<any>();
  readonly onStateSave = output<any>();
  readonly onStateRestore = output<any>();

  // A képernyő saját sablonjai (<ng-template #név> a wf-table elemen belül)
  readonly captionTemplate = contentChild<TemplateRef<any>>('caption');
  readonly colgroupTemplate = contentChild<TemplateRef<any>>('colgroup');
  readonly headerTemplate = contentChild<TemplateRef<any>>('header');
  readonly bodyTemplate = contentChild<TemplateRef<any>>('body');
  readonly rowexpansionTemplate = contentChild<TemplateRef<any>>('rowexpansion');
  readonly groupheaderTemplate = contentChild<TemplateRef<any>>('groupheader');
  readonly groupfooterTemplate = contentChild<TemplateRef<any>>('groupfooter');
  readonly loadingbodyTemplate = contentChild<TemplateRef<any>>('loadingbody');
  readonly emptymessageTemplate = contentChild<TemplateRef<any>>('emptymessage');
  readonly footerTemplate = contentChild<TemplateRef<any>>('footer');
  readonly summaryTemplate = contentChild<TemplateRef<any>>('summary');
  readonly paginatorleftTemplate = contentChild<TemplateRef<any>>('paginatorleft');
  readonly paginatorrightTemplate = contentChild<TemplateRef<any>>('paginatorright');
}
