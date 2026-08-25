"""Apply the USL personal-AI UI to the exact Paperless v3.0.5 frontend."""

import sys
from hashlib import sha256
from pathlib import Path

SOURCE_ROOT = Path(sys.argv[1]).resolve()
MISMATCH_MESSAGE = "Paperless v3.0.5 frontend is incompatible with the USL overlay"


def patch_file(
    relative_path: str,
    expected_sha256: str,
    replacements: tuple[tuple[str, str], ...],
) -> None:
    path = SOURCE_ROOT / relative_path
    source_bytes = path.read_bytes()
    actual_hash = sha256(source_bytes).hexdigest()
    if actual_hash != expected_sha256:
        raise RuntimeError(
            f"{MISMATCH_MESSAGE}: {relative_path} "
            f"({actual_hash} != {expected_sha256})",
        )
    source = source_bytes.decode("utf-8")
    for anchor, replacement in replacements:
        if source.count(anchor) != 1:
            raise RuntimeError(f"{MISMATCH_MESSAGE}: {relative_path} anchor")
        source = source.replace(anchor, replacement, 1)
    path.write_text(source, encoding="utf-8")


patch_file(
    "src/app/data/user-profile.ts",
    "569a74b733e98af0cf431f2e2c37acc061444367f0e06151e0e69f93422bcded",
    (
        (
            "export interface PaperlessUserProfile {\n",
            """export interface PersonalAIProfile {
  provider: 'gemini'
  approved_models: string[]
  model_name: string
  metadata_suggestions_enabled: boolean
  document_chat_enabled: boolean
  api_key_configured: boolean
  credential_revision: number
  last_tested_at?: string
  privacy_disclosure: string
}

export interface PersonalAIUpdate {
  provider?: 'gemini'
  model_name?: string
  metadata_suggestions_enabled?: boolean
  document_chat_enabled?: boolean
  api_key?: string
}

export interface PaperlessUserProfile {
""",
        ),
    ),
)

patch_file(
    "src/app/services/profile.service.ts",
    "b3c02747f42ec5965a19fdca5b3728caf493acf7c6f874dd10d9b072ca8a4d71",
    (
        (
            "import { Injectable, inject } from '@angular/core'\n",
            "import { Injectable, inject, signal } from '@angular/core'\n",
        ),
        ("import { Observable } from 'rxjs'\n", "import { Observable, tap } from 'rxjs'\n"),
        (
            """  PaperlessUserProfile,
  SocialAccountProvider,
""",
            """  PaperlessUserProfile,
  PersonalAIProfile,
  PersonalAIUpdate,
  SocialAccountProvider,
""",
        ),
        (
            """  private endpoint = 'profile'

  get(): Observable<PaperlessUserProfile> {
""",
            """  private endpoint = 'profile'

  readonly personalAI = signal<PersonalAIProfile>(undefined)

  get(): Observable<PaperlessUserProfile> {
""",
        ),
        (
            """  deactivateTotp(): Observable<boolean> {
    return this.http.delete<boolean>(
      `${environment.apiBaseUrl}${this.endpoint}/totp/`,
      {}
    )
  }
}
""",
            """  deactivateTotp(): Observable<boolean> {
    return this.http.delete<boolean>(
      `${environment.apiBaseUrl}${this.endpoint}/totp/`,
      {}
    )
  }

  getPersonalAI(): Observable<PersonalAIProfile> {
    return this.http
      .get<PersonalAIProfile>(
        `${environment.apiBaseUrl}${this.endpoint}/personal_ai/`
      )
      .pipe(tap((profile) => this.personalAI.set(profile)))
  }

  updatePersonalAI(update: PersonalAIUpdate): Observable<PersonalAIProfile> {
    return this.http
      .patch<PersonalAIProfile>(
        `${environment.apiBaseUrl}${this.endpoint}/personal_ai/`,
        update
      )
      .pipe(tap((profile) => this.personalAI.set(profile)))
  }

  testPersonalAIConnection(): Observable<PersonalAIProfile> {
    return this.http
      .post<PersonalAIProfile>(
        `${environment.apiBaseUrl}${this.endpoint}/personal_ai/test/`,
        {}
      )
      .pipe(tap((profile) => this.personalAI.set(profile)))
  }

  disablePersonalAI(): Observable<PersonalAIProfile> {
    return this.http
      .post<PersonalAIProfile>(
        `${environment.apiBaseUrl}${this.endpoint}/personal_ai/disable/`,
        {}
      )
      .pipe(tap((profile) => this.personalAI.set(profile)))
  }

  deletePersonalAICredential(): Observable<PersonalAIProfile> {
    return this.http
      .delete<PersonalAIProfile>(
        `${environment.apiBaseUrl}${this.endpoint}/personal_ai/`
      )
      .pipe(tap((profile) => this.personalAI.set(profile)))
  }
}
""",
        ),
    ),
)

patch_file(
    "src/app/components/common/profile-edit-dialog/profile-edit-dialog.component.ts",
    "cba5ffb18dee1bab5e60cf035686a6aba390f8b4805386ba8ba3e1e6ea207e3f",
    (
        (
            """import { PasswordComponent } from '../input/password/password.component'
import { TextComponent } from '../input/text/text.component'
""",
            """import { PasswordComponent } from '../input/password/password.component'
import { TextComponent } from '../input/text/text.component'
import { PersonalAISettingsComponent } from '../personal-ai-settings/personal-ai-settings.component'
""",
        ),
        (
            """    NgxBootstrapIconsModule,
  ],
})
""",
            """    NgxBootstrapIconsModule,
    PersonalAISettingsComponent,
  ],
})
""",
        ),
    ),
)

patch_file(
    "src/app/components/common/profile-edit-dialog/profile-edit-dialog.component.html",
    "28887ad594eb4853c63c98144737b74103ca23452c352b5812bcc6571e0adf55",
    (
        (
            """      </div>
    </div>
    </div>
    <div class="modal-footer">
""",
            """      </div>
    </div>
    <pngx-personal-ai-settings></pngx-personal-ai-settings>
    </div>
    <div class="modal-footer">
""",
        ),
    ),
)

patch_file(
    "src/app/components/app-frame/app-frame.component.ts",
    "7f8a12e035f6eeca1d5f78128a1b4acffca85ac4071fd58371e9131689f37e2b",
    (
        (
            """import { OpenDocumentsService } from 'src/app/services/open-documents.service'
import {
""",
            """import { OpenDocumentsService } from 'src/app/services/open-documents.service'
import { ProfileService } from 'src/app/services/profile.service'
import {
""",
        ),
        (
            """  private djangoMessagesService = inject(DjangoMessagesService)

  readonly appRemoteVersion = signal<AppRemoteVersion>(null)
""",
            """  private djangoMessagesService = inject(DjangoMessagesService)
  private profileService = inject(ProfileService)

  readonly appRemoteVersion = signal<AppRemoteVersion>(null)
""",
        ),
        (
            """  ngOnInit(): void {
    this.lastScrollY = window.scrollY

""",
            """  ngOnInit(): void {
    this.lastScrollY = window.scrollY
    this.profileService.getPersonalAI().subscribe({ error: () => undefined })

""",
        ),
        (
            """  get aiEnabled(): boolean {
    this.settingsService.trackChanges()
    return this.settingsService.get(SETTINGS_KEYS.AI_ENABLED)
  }
""",
            """  get aiEnabled(): boolean {
    this.settingsService.trackChanges()
    return (
      this.settingsService.get(SETTINGS_KEYS.AI_ENABLED) &&
      Boolean(this.profileService.personalAI()?.document_chat_enabled)
    )
  }
""",
        ),
    ),
)

patch_file(
    "src/app/components/document-detail/document-detail.component.ts",
    "7791dde38e037471c4582e94c923a3039a577c5f6b4f9d8b0dfe06b9c98f77ec",
    (
        (
            """import { OpenDocumentsService } from 'src/app/services/open-documents.service'
import {
""",
            """import { OpenDocumentsService } from 'src/app/services/open-documents.service'
import { ProfileService } from 'src/app/services/profile.service'
import {
""",
        ),
        (
            """  private settings = inject(SettingsService)
  private storagePathService = inject(StoragePathService)
""",
            """  private settings = inject(SettingsService)
  private profileService = inject(ProfileService)
  private storagePathService = inject(StoragePathService)
""",
        ),
        (
            """  get aiEnabled(): boolean {
    this.settings.trackChanges()
    return this.settings.get(SETTINGS_KEYS.AI_ENABLED)
  }
""",
            """  get aiEnabled(): boolean {
    this.settings.trackChanges()
    return (
      this.settings.get(SETTINGS_KEYS.AI_ENABLED) &&
      Boolean(
        this.profileService.personalAI()?.metadata_suggestions_enabled
      )
    )
  }
""",
        ),
    ),
)

patch_file(
    "src/app/data/paperless-config.ts",
    "2c1c1a775f85437030c1af3f78db6f39290df0a5a8af131a2ed3dbe436111175",
    (
        (
            """  {
    key: 'llm_backend',
    title: $localize`LLM Backend`,
    type: ConfigOptionType.Select,
    choices: mapToItems(LLMBackendConfig),
    config_key: 'PAPERLESS_AI_LLM_BACKEND',
    category: ConfigCategory.AI,
  },
  {
    key: 'llm_model',
    title: $localize`LLM Model`,
    type: ConfigOptionType.String,
    config_key: 'PAPERLESS_AI_LLM_MODEL',
    category: ConfigCategory.AI,
  },
  {
    key: 'llm_api_key',
    title: $localize`LLM API Key`,
    type: ConfigOptionType.Password,
    config_key: 'PAPERLESS_AI_LLM_API_KEY',
    category: ConfigCategory.AI,
  },
  {
    key: 'llm_endpoint',
    title: $localize`LLM Endpoint`,
    type: ConfigOptionType.String,
    config_key: 'PAPERLESS_AI_LLM_ENDPOINT',
    category: ConfigCategory.AI,
  },
  {
    key: 'llm_output_language',
    title: $localize`LLM Output Language`,
    type: ConfigOptionType.String,
    config_key: 'PAPERLESS_AI_LLM_OUTPUT_LANGUAGE',
    category: ConfigCategory.AI,
    note: $localize`Language to use for generated AI suggestions. When unset, AI suggestions use the user's display language if explicitly set.`,
  },
  {
    key: 'llm_request_timeout',
    title: $localize`LLM Request Timeout`,
    type: ConfigOptionType.Number,
    config_key: 'PAPERLESS_AI_LLM_REQUEST_TIMEOUT',
    category: ConfigCategory.AI,
    note: $localize`Timeout in seconds for LLM requests.`,
  },
""",
            "",
        ),
    ),
)

patch_file(
    "src/locale/messages.fr_FR.xlf",
    "eba0a34cffed8a3dceae04bfe76f17d288cb5f5b82133abda205f6b472e60589",
    (
        (
            """    </body>
""",
            """      <trans-unit id="7482972695398722944" datatype="html" approved="yes">
        <source>Personal Gemini settings are unavailable.</source>
        <target state="final">Les paramètres Gemini personnels sont indisponibles.</target>
      </trans-unit>
      <trans-unit id="8623869322170608605" datatype="html" approved="yes">
        <source>Personal Gemini settings saved.</source>
        <target state="final">Les paramètres Gemini personnels ont été enregistrés.</target>
      </trans-unit>
      <trans-unit id="374516605659584783" datatype="html" approved="yes">
        <source>Unable to save personal Gemini settings.</source>
        <target state="final">Impossible d’enregistrer les paramètres Gemini personnels.</target>
      </trans-unit>
      <trans-unit id="2757633245859325800" datatype="html" approved="yes">
        <source>Gemini connection succeeded.</source>
        <target state="final">Connexion à Gemini réussie.</target>
      </trans-unit>
      <trans-unit id="8345626904316780407" datatype="html" approved="yes">
        <source>Gemini connection test failed.</source>
        <target state="final">Échec du test de connexion à Gemini.</target>
      </trans-unit>
      <trans-unit id="830994774159658182" datatype="html" approved="yes">
        <source>Personal Gemini features disabled.</source>
        <target state="final">Les fonctionnalités Gemini personnelles ont été désactivées.</target>
      </trans-unit>
      <trans-unit id="8159939329026313032" datatype="html" approved="yes">
        <source>Unable to disable personal Gemini features.</source>
        <target state="final">Impossible de désactiver les fonctionnalités Gemini personnelles.</target>
      </trans-unit>
      <trans-unit id="1343634662923761920" datatype="html" approved="yes">
        <source>Delete your encrypted Gemini API key and disable both features?</source>
        <target state="final">Supprimer votre clé API Gemini chiffrée et désactiver les deux fonctionnalités ?</target>
      </trans-unit>
      <trans-unit id="5067522747890707734" datatype="html" approved="yes">
        <source>Personal Gemini API key deleted.</source>
        <target state="final">La clé API Gemini personnelle a été supprimée.</target>
      </trans-unit>
      <trans-unit id="8681384018180566285" datatype="html" approved="yes">
        <source>Unable to delete the personal Gemini API key.</source>
        <target state="final">Impossible de supprimer la clé API Gemini personnelle.</target>
      </trans-unit>
      <trans-unit id="7588353546795719811" datatype="html" approved="yes">
        <source>Personal Gemini</source>
        <target state="final">Gemini personnel</target>
      </trans-unit>
      <trans-unit id="8232773653071363045" datatype="html" approved="yes">
        <source>Provider</source>
        <target state="final">Fournisseur</target>
      </trans-unit>
      <trans-unit id="7671866193012676261" datatype="html" approved="yes">
        <source>The endpoint is fixed by USL.</source>
        <target state="final">Le point de terminaison est défini par USL.</target>
      </trans-unit>
      <trans-unit id="4446521625317546576" datatype="html" approved="yes">
        <source>Approved model</source>
        <target state="final">Modèle approuvé</target>
      </trans-unit>
      <trans-unit id="4284857355651085878" datatype="html" approved="yes">
        <source>Gemini API key</source>
        <target state="final">Clé API Gemini</target>
      </trans-unit>
      <trans-unit id="5479473557490819047" datatype="html" approved="yes">
        <source>The saved key is never displayed again.</source>
        <target state="final">La clé enregistrée n’est jamais réaffichée.</target>
      </trans-unit>
      <trans-unit id="7663910147564225730" datatype="html" approved="yes">
        <source> Gemini metadata suggestions </source>
        <target state="final"> Suggestions de métadonnées Gemini </target>
      </trans-unit>
      <trans-unit id="1406650357978529350" datatype="html" approved="yes">
        <source> Gemini document chat </source>
        <target state="final"> Discussion Gemini sur les documents </target>
      </trans-unit>
      <trans-unit id="6625621357979758969" datatype="html" approved="yes">
        <source> Both features are off by default and may be enabled independently. Suggestions never apply metadata changes automatically, and chat cannot run tools or modify documents. </source>
        <target state="final"> Les deux fonctionnalités sont désactivées par défaut et peuvent être activées indépendamment. Les suggestions n’appliquent jamais automatiquement de modifications aux métadonnées, et la discussion ne peut ni exécuter d’outils ni modifier les documents. </target>
      </trans-unit>
      <trans-unit id="3500182649664900847" datatype="html" approved="yes">
        <source>Save or replace key</source>
        <target state="final">Enregistrer ou remplacer la clé</target>
      </trans-unit>
      <trans-unit id="7574483915462663728" datatype="html" approved="yes">
        <source>Test connection</source>
        <target state="final">Tester la connexion</target>
      </trans-unit>
      <trans-unit id="7787939617770110101" datatype="html" approved="yes">
        <source>Disable both</source>
        <target state="final">Tout désactiver</target>
      </trans-unit>
      <trans-unit id="7227898151862172948" datatype="html" approved="yes">
        <source>Delete API key</source>
        <target state="final">Supprimer la clé API</target>
      </trans-unit>
      <trans-unit id="uslPersonalAIReplaceKey" datatype="html" approved="yes">
        <source>Replace saved key</source>
        <target state="final">Remplacer la clé enregistrée</target>
      </trans-unit>
      <trans-unit id="uslPersonalAIEnterKey" datatype="html" approved="yes">
        <source>Enter API key</source>
        <target state="final">Saisir la clé API</target>
      </trans-unit>
      <trans-unit id="uslPersonalAIPrivacyDisclosure" datatype="html" approved="yes">
        <source>When you enable a personal Gemini feature, the relevant document text, filename, metadata, and your prompt are sent to Google Gemini under your own account. Local upload, OCR, indexing, search, and MCP operations never use Gemini. Document content is untrusted data and cannot authorize actions.</source>
        <target state="final">Lorsque vous activez une fonctionnalité Gemini personnelle, le texte, le nom de fichier et les métadonnées du document concerné, ainsi que votre requête, sont envoyés à Google Gemini sous votre propre compte. Le téléversement local, l’OCR, l’indexation, la recherche et les opérations MCP n’utilisent jamais Gemini. Le contenu des documents constitue une donnée non fiable et ne peut autoriser aucune action.</target>
      </trans-unit>
    </body>
""",
        ),
    ),
)
