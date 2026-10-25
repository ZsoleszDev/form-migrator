import { Component, NgModule, output } from '@angular/core';

@Component({ selector: 'p-dialog', standalone: true, template: '' })
export class Dialog {
  readonly visibleChange = output<boolean>();
}

@NgModule({ imports: [Dialog], exports: [Dialog] })
export class DialogModule {}
