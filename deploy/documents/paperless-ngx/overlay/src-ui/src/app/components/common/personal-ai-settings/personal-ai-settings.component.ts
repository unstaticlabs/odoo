import { Component, inject, OnInit, signal } from '@angular/core'
import { FormControl, FormGroup, ReactiveFormsModule } from '@angular/forms'
import { first } from 'rxjs'
import { ProfileService } from 'src/app/services/profile.service'
import { ToastService } from 'src/app/services/toast.service'

@Component({
  selector: 'pngx-personal-ai-settings',
  templateUrl: './personal-ai-settings.component.html',
  imports: [ReactiveFormsModule],
})
export class PersonalAISettingsComponent implements OnInit {
  private profileService = inject(ProfileService)
  private toastService = inject(ToastService)

  readonly profile = this.profileService.personalAI
  readonly loading = signal(true)
  readonly saving = signal(false)
  readonly testing = signal(false)
  readonly error = signal<string>(undefined)
  readonly replaceKeyPlaceholder = $localize`:@@uslPersonalAIReplaceKey:Replace saved key`
  readonly enterKeyPlaceholder = $localize`:@@uslPersonalAIEnterKey:Enter API key`

  readonly form = new FormGroup({
    model_name: new FormControl('gemini-3.7-flash', { nonNullable: true }),
    metadata_suggestions_enabled: new FormControl(false, {
      nonNullable: true,
    }),
    document_chat_enabled: new FormControl(false, { nonNullable: true }),
    api_key: new FormControl('', { nonNullable: true }),
  })

  ngOnInit(): void {
    this.reload()
  }

  reload(): void {
    this.loading.set(true)
    this.error.set(undefined)
    this.profileService
      .getPersonalAI()
      .pipe(first())
      .subscribe({
        next: (profile) => {
          this.form.patchValue({
            model_name: profile.model_name,
            metadata_suggestions_enabled:
              profile.metadata_suggestions_enabled,
            document_chat_enabled: profile.document_chat_enabled,
            api_key: '',
          })
          this.loading.set(false)
        },
        error: (error) => {
          this.error.set(
            error?.error?.detail ??
              $localize`Personal Gemini settings are unavailable.`
          )
          this.loading.set(false)
        },
      })
  }

  save(): void {
    const value = this.form.getRawValue()
    const update: any = {
      provider: 'gemini',
      model_name: value.model_name,
      metadata_suggestions_enabled: value.metadata_suggestions_enabled,
      document_chat_enabled: value.document_chat_enabled,
    }
    if (value.api_key) update.api_key = value.api_key
    this.saving.set(true)
    this.error.set(undefined)
    this.profileService
      .updatePersonalAI(update)
      .pipe(first())
      .subscribe({
        next: () => {
          this.form.patchValue({ api_key: '' })
          this.saving.set(false)
          this.toastService.showInfo(
            $localize`Personal Gemini settings saved.`
          )
        },
        error: (error) => {
          this.error.set(
            error?.error?.detail ??
              $localize`Unable to save personal Gemini settings.`
          )
          this.saving.set(false)
        },
      })
  }

  testConnection(): void {
    this.testing.set(true)
    this.error.set(undefined)
    this.profileService
      .testPersonalAIConnection()
      .pipe(first())
      .subscribe({
        next: () => {
          this.testing.set(false)
          this.toastService.showInfo($localize`Gemini connection succeeded.`)
        },
        error: (error) => {
          this.error.set(
            error?.error?.detail ?? $localize`Gemini connection test failed.`
          )
          this.testing.set(false)
        },
      })
  }

  disable(): void {
    this.saving.set(true)
    this.profileService
      .disablePersonalAI()
      .pipe(first())
      .subscribe({
        next: (profile) => {
          this.form.patchValue({
            metadata_suggestions_enabled:
              profile.metadata_suggestions_enabled,
            document_chat_enabled: profile.document_chat_enabled,
          })
          this.saving.set(false)
          this.toastService.showInfo($localize`Personal Gemini features disabled.`)
        },
        error: (error) => {
          this.error.set(
            error?.error?.detail ??
              $localize`Unable to disable personal Gemini features.`
          )
          this.saving.set(false)
        },
      })
  }

  deleteCredential(): void {
    if (
      !window.confirm(
        $localize`Delete your encrypted Gemini API key and disable both features?`
      )
    )
      return
    this.saving.set(true)
    this.profileService
      .deletePersonalAICredential()
      .pipe(first())
      .subscribe({
        next: (profile) => {
          this.form.patchValue({
            metadata_suggestions_enabled:
              profile.metadata_suggestions_enabled,
            document_chat_enabled: profile.document_chat_enabled,
            api_key: '',
          })
          this.saving.set(false)
          this.toastService.showInfo($localize`Personal Gemini API key deleted.`)
        },
        error: (error) => {
          this.error.set(
            error?.error?.detail ??
              $localize`Unable to delete the personal Gemini API key.`
          )
          this.saving.set(false)
        },
      })
  }
}
