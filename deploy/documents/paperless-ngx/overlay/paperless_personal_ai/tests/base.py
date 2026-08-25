import base64
import json
import os
import tempfile
from pathlib import Path

from allauth.socialaccount.models import SocialAccount
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from paperless_personal_ai.crypto import MASTER_KEYS_FILE_ENV, SECRET_FORMAT


class PersonalAIKeyFileMixin:
    def setUp(self):
        super().setUp()
        self._master_key_directory = tempfile.TemporaryDirectory()
        self.master_key_path = Path(self._master_key_directory.name) / "keys.json"
        self._previous_master_key_path = os.environ.get(MASTER_KEYS_FILE_ENV)
        os.environ[MASTER_KEYS_FILE_ENV] = str(self.master_key_path)
        self.write_master_keys(active_version=1, versions=(1,))

    def tearDown(self):
        if self._previous_master_key_path is None:
            os.environ.pop(MASTER_KEYS_FILE_ENV, None)
        else:
            os.environ[MASTER_KEYS_FILE_ENV] = self._previous_master_key_path
        self._master_key_directory.cleanup()
        super().tearDown()

    def write_master_keys(
        self,
        *,
        active_version: int,
        versions: tuple[int, ...],
    ) -> None:
        payload = {
            "format": SECRET_FORMAT,
            "active_key_id": "test-key",
            "active_key_version": active_version,
            "keys": [
                {
                    "id": "test-key",
                    "version": version,
                    "key": base64.b64encode(bytes([version]) * 32).decode("ascii"),
                }
                for version in versions
            ],
        }
        self.master_key_path.write_text(json.dumps(payload), encoding="utf-8")

    @staticmethod
    def mapped_user(username: str, *, active: bool = True):
        user = get_user_model().objects.create_user(
            username=username,
            is_active=active,
        )
        group, _created = Group.objects.get_or_create(
            name="USL Odoo document users",
        )
        user.groups.add(group)
        SocialAccount.objects.create(
            user=user,
            provider="pocket-id",
            uid=f"subject-{username}",
            extra_data={"usl_odoo_managed": True},
        )
        return user
