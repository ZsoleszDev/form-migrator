/** Angular 22 / Optimus UI.
 * Add imports: @@IMPORT_HINT@@ (Angular core), FormGroup (Angular forms),
 * FormBlock and your form-block component from the company package, and environment.
 * Add the actual Optimus components to the Angular imports metadata below.
 * Source button events are exposed as actionRequested; bind business logic in the host.
 * This file builds the screen. It does not execute Forms triggers or start HTTP calls.
 */
@@DEFINITIONS@@

@Component({
  selector: '@@SELECTOR@@',
  standalone: true,
  template: `
@@HTML@@
  `,
  styles: [``],
})
export class @@CLASS@@Component {
@@FIELDS@@

@@METHODS@@
}
