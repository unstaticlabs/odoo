import { ComponentFixture, TestBed } from '@angular/core/testing'
import { provideHttpClient } from '@angular/common/http'
import { provideHttpClientTesting } from '@angular/common/http/testing'
import { of } from 'rxjs'
import { ProfileService } from 'src/app/services/profile.service'
import { ToastService } from 'src/app/services/toast.service'
import { PersonalAISettingsComponent } from './personal-ai-settings.component'

describe('PersonalAISettingsComponent', () => {
  let fixture: ComponentFixture<PersonalAISettingsComponent>
  let profileService: ProfileService

  const profile = {
    provider: 'gemini',
    approved_models: ['gemini-3.7-flash'],
    model_name: 'gemini-3.7-flash',
    metadata_suggestions_enabled: false,
    document_chat_enabled: false,
    api_key_configured: false,
    credential_revision: 0,
    last_tested_at: null,
    privacy_disclosure: 'Documents may be sent to Gemini.',
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [PersonalAISettingsComponent],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        ProfileService,
        ToastService,
      ],
    })
    profileService = TestBed.inject(ProfileService)
    jest.spyOn(profileService, 'getPersonalAI').mockReturnValue(of(profile))
    fixture = TestBed.createComponent(PersonalAISettingsComponent)
    fixture.detectChanges()
  })

  it('starts with both personal features disabled and never receives a key', () => {
    expect(fixture.componentInstance.form.value).toMatchObject({
      metadata_suggestions_enabled: false,
      document_chat_enabled: false,
      api_key: '',
    })
    expect(JSON.stringify(profile)).not.toContain('api_key_ciphertext')
  })

  it('sends a replacement key only from the password field and clears it', () => {
    const update = jest
      .spyOn(profileService, 'updatePersonalAI')
      .mockReturnValue(of({ ...profile, api_key_configured: true }))
    fixture.componentInstance.form.patchValue({
      api_key: 'temporary-browser-value',
      metadata_suggestions_enabled: true,
    })

    fixture.componentInstance.save()

    expect(update).toHaveBeenCalledWith(
      expect.objectContaining({
        api_key: 'temporary-browser-value',
        metadata_suggestions_enabled: true,
      })
    )
    expect(fixture.componentInstance.form.value.api_key).toBe('')
  })
})
