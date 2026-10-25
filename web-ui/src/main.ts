import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient } from '@angular/common/http';
import { provideAnimationsAsync } from '@angular/platform-browser/animations/async';
import { provideOptimus } from '@openng/optimus-ui/config';
import Aura from '@openng/optimus-ui-themes/aura';
import { Migrator } from '../../angular-frontend/migrator-app';
bootstrapApplication(Migrator, {providers: [provideHttpClient(), provideAnimationsAsync(), provideOptimus({theme: {preset: Aura}})]}).catch(error => console.error(error));
