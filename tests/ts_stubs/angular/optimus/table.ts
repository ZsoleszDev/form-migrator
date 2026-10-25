import { Component, NgModule, input, output } from '@angular/core';

// The p-table surface the generated code binds (wf-table.ts): TEST DOUBLE for the ngc (strictTemplates) check only.
@Component({ selector: 'p-table', standalone: true, template: '' })
export class Table {
  readonly value = input<any[]>();
  readonly columns = input<any[]>();
  readonly selection = input<any>();
  readonly selectionChange = output<any>();
  readonly firstChange = output<number>();
  readonly rowsChange = output<number>();
  readonly selectAllChange = output<boolean>();
  readonly contextMenuSelectionChange = output<any>();
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
}

@NgModule({ imports: [Table], exports: [Table] })
export class TableModule {}
