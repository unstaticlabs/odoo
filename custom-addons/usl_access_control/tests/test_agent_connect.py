"""An owner connects an Agent to the MCP OAuth server from the browser."""

import base64
import datetime
import hashlib
import os
import time
from http import HTTPStatus
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

from lxml import html

from odoo import SUPERUSER_ID, Command, fields
from odoo.exceptions import AccessError
from odoo.http.requestlib import Request
from odoo.tests import HttpCase, tagged

from ..controllers.agent_connect import (
    CONNECT_PATH,
    IDENTITY_WINDOW_SECONDS,
    MCP_ORIGIN_VARIABLE,
    TOKEN_PATH,
)
from ..models.agent_connect import CONNECT_KEY_DAYS_PARAMETER

MCP_ORIGIN = "https://mcp.example.test"
RETURN_URL = f"{MCP_ORIGIN}/oauth/enroll/callback"
STATE = "s" * 43
VERIFIER = "v" * 43
CHALLENGE = base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()


@tagged("post_install", "-at_install", "usl_access_control", "usl_agent_connect")
class TestAgentConnect(HttpCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.second_company = cls.env["res.company"].create({"name": "[QA connect] Second company"})
        cls.owner = cls._user("qa.connect.owner", cls.company | cls.second_company)
        cls.stranger = cls._user("qa.connect.stranger", cls.company)
        cls.portal = cls._user("qa.connect.portal", cls.company, portal=True)
        cls.agent = cls._agent(cls.owner, "[QA connect] First", cls.company)
        # The backend list hides this Agent while only the first company is
        # selected, because its default company is the second one.
        cls.second_agent = cls._agent(cls.owner, "[QA connect] Second", cls.second_company)
        cls.foreign_agent = cls._agent(cls.stranger, "[QA connect] Foreign", cls.company)

    @classmethod
    def _user(cls, login, companies, portal=False):
        # The identity classification stays unset: a classified identity is
        # managed by Pocket ID and cannot open a local test session.
        group = cls.env.ref("base.group_portal" if portal else "base.group_user")
        return cls.env["res.users"].with_user(SUPERUSER_ID).with_context(
            no_reset_password=True,
            usl_governed_identity_provisioning=True,
        ).create(
            {
                "name": login,
                "login": login,
                "email": f"{login}@example.test",
                "share": portal,
                "company_id": companies[0].id,
                "company_ids": [Command.set(companies.ids)],
                "group_ids": [Command.set(group.ids)],
            },
        )

    @classmethod
    def _agent(cls, owner, name, company):
        return cls.env["usl.agent"].with_user(owner).create(
            {
                "name": name,
                "purpose": "Exercise the browser connection",
                "company_id": company.id,
                "company_ids": [Command.set(company.ids)],
            },
        )

    def setUp(self):
        super().setUp()
        environment = patch.dict(os.environ, {MCP_ORIGIN_VARIABLE: MCP_ORIGIN})
        environment.start()
        self.addCleanup(environment.stop)

    def _query(self, **overrides):
        return {
            "state": STATE,
            "code_challenge": CHALLENGE,
            "return_url": RETURN_URL,
            "client_name": "Synthetic Hub",
            **overrides,
        }

    def _sign_in(self, user, identity_age=0):
        self.authenticate(
            user.login,
            user.login,
            session_extra={"identity-check-last": time.time() - identity_age},
        )
        # The company switcher shows the first company only.
        self.opener.cookies["cids"] = str(self.company.id)

    def _page(self, **overrides):
        return self.url_open(f"{CONNECT_PATH}?{urlencode(self._query(**overrides))}", allow_redirects=False)

    def _submit(self, **form):
        return self.url_open(
            CONNECT_PATH,
            data={**self._query(), "csrf_token": Request.csrf_token(self), **form},
            allow_redirects=False,
        )

    def _code(self, response):
        self.assertEqual(response.status_code, HTTPStatus.SEE_OTHER, response.text[:400])
        location = urlsplit(response.headers["Location"])
        self.assertEqual(f"{location.scheme}://{location.netloc}{location.path}", RETURN_URL)
        query = parse_qs(location.query)
        self.assertEqual(query["state"], [STATE])
        return query["code"][0]

    def _exchange(self, code, verifier=VERIFIER):
        return self.url_open(
            TOKEN_PATH,
            data={"code": code, "code_verifier": verifier},
            headers={"X-Odoo-Database": self.env.cr.dbname},
            allow_redirects=False,
        )

    def _credentials(self, agent=None):
        return self.env["usl.agent.credential"].search([("agent_id", "=", (agent or self.agent).id)])

    def test_the_page_does_not_exist_until_the_deployment_names_its_mcp(self):
        self._sign_in(self.owner)
        with patch.dict(os.environ, {MCP_ORIGIN_VARIABLE: ""}):
            self.assertEqual(self._page().status_code, HTTPStatus.NOT_FOUND)
            self.assertEqual(self._exchange("c" * 43).status_code, HTTPStatus.NOT_FOUND)

    def test_only_the_configured_mcp_callback_can_receive_a_code(self):
        self._sign_in(self.owner)
        for overrides in (
            {"return_url": "https://attacker.example.test/oauth/enroll/callback"},
            {"return_url": f"{RETURN_URL}/extra"},
            {"state": "short"},
            {"code_challenge": "not-a-challenge"},
        ):
            with self.subTest(overrides=overrides):
                self.assertEqual(self._page(**overrides).status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(self._submit(agent=str(self.agent.id), return_url="https://attacker.example.test/").status_code,
                         HTTPStatus.BAD_REQUEST)
        self.assertFalse(self.env["usl.agent.connect.grant"].sudo().search([]))

    def test_a_visitor_without_a_session_gets_sign_in_and_the_manual_link(self):
        response = self._page()
        self.assertEqual(response.status_code, HTTPStatus.OK)
        page = html.fromstring(response.text)
        links = page.xpath("//div[contains(@class, 'o_usl_agent_connect')]//a/@href")
        login = urlsplit(links[0])
        self.assertEqual(login.path, "/web/login")
        redirect = urlsplit(parse_qs(login.query)["redirect"][0])
        self.assertEqual(redirect.path, CONNECT_PATH)
        self.assertEqual(parse_qs(redirect.query)["state"], [STATE])
        self.assertEqual(links[1], f"{RETURN_URL}?{urlencode({'error': 'manual_entry', 'state': STATE})}")
        self.assertNotIn(self.agent.name, response.text)
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")

    def test_an_old_identity_check_sends_the_owner_to_confirm_again(self):
        self._sign_in(self.owner, identity_age=IDENTITY_WINDOW_SECONDS + 5)
        for response in (self._page(), self._submit(agent=str(self.agent.id))):
            self.assertEqual(response.status_code, HTTPStatus.SEE_OTHER)
            location = urlsplit(response.headers["Location"])
            self.assertEqual(location.path, "/web/session/identity")
            self.assertEqual(urlsplit(parse_qs(location.query)["redirect"][0]).path, CONNECT_PATH)
        self.assertFalse(self.env["usl.agent.connect.grant"].sudo().search([]))

    def test_the_page_lists_every_owned_agent_in_all_companies(self):
        self._sign_in(self.owner)
        response = self._page()
        self.assertEqual(response.status_code, HTTPStatus.OK)
        page = html.fromstring(response.text)
        self.assertEqual(
            sorted(page.xpath("//input[@name='agent']/@value")),
            sorted([str(self.agent.id), str(self.second_agent.id), "new"]),
        )
        self.assertIn("Synthetic Hub", response.text)
        self.assertNotIn(self.foreign_agent.name, response.text)

    def test_the_owner_connects_an_agent_and_the_mcp_gets_a_working_key(self):
        self._sign_in(self.owner)
        code = self._code(self._submit(agent=str(self.second_agent.id)))
        # The approval alone creates no key.
        self.assertFalse(self._credentials(self.second_agent))
        grant = self.env["usl.agent.connect.grant"].sudo().search([])
        self.assertNotIn(code, (grant.code_hash, grant.code_challenge))

        before = fields.Datetime.now()
        response = self._exchange(code)

        self.assertEqual(response.status_code, HTTPStatus.OK, response.text)
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        result = response.json()
        credential = self._credentials(self.second_agent)
        self.assertEqual(len(credential), 1)
        self.assertEqual(result["credential"]["id"], credential.id)
        self.assertEqual(result["agent"], {"id": self.second_agent.id, "name": self.second_agent.name})
        self.assertEqual(result["database"], self.env.cr.dbname)
        self.assertEqual(
            credential.name,
            f"Synthetic Hub (OAuth, {fields.Date.to_string(fields.Date.context_today(grant))})",
        )
        self.assertEqual(credential.status, "active")
        self.assertAlmostEqual(
            credential.expiration_date,
            before + datetime.timedelta(days=365),
            delta=datetime.timedelta(minutes=1),
        )
        self.assertEqual(grant.credential_id, credential)

        # The key signs in as the Agent, not as its owner.
        self.assertEqual(
            self.env["res.users.apikeys"]._check_credentials(scope="rpc", key=result["api_key"]),
            self.second_agent.user_id.id,
        )

    def test_a_code_works_one_time(self):
        self._sign_in(self.owner)
        code = self._code(self._submit(agent=str(self.agent.id)))
        self.assertEqual(self._exchange(code).status_code, HTTPStatus.OK)

        replay = self._exchange(code)

        self.assertEqual(replay.status_code, HTTPStatus.BAD_REQUEST)
        self.assertEqual(replay.json(), {"error": "invalid_grant"})
        self.assertEqual(len(self._credentials()), 1)

    def test_a_wrong_verifier_gets_no_key_and_uses_up_the_code(self):
        self._sign_in(self.owner)
        code = self._code(self._submit(agent=str(self.agent.id)))

        self.assertEqual(self._exchange(code, "w" * 43).json(), {"error": "invalid_grant"})
        self.assertEqual(self._exchange(code).json(), {"error": "invalid_grant"})
        self.assertEqual(self._exchange("u" * 43).json(), {"error": "invalid_grant"})
        self.assertEqual(self._exchange(code, "short").json(), {"error": "invalid_request"})
        self.assertFalse(self._credentials())

    def test_an_expired_code_gets_no_key(self):
        self._sign_in(self.owner)
        code = self._code(self._submit(agent=str(self.agent.id)))
        self.env["usl.agent.connect.grant"].sudo().search([]).with_context(
            usl_agent_connect_internal=True,
        ).write({"expires_at": fields.Datetime.now() - datetime.timedelta(seconds=1)})

        self.assertEqual(self._exchange(code).json(), {"error": "invalid_grant"})
        self.assertFalse(self._credentials())

    def test_an_agent_that_changed_owner_or_state_gets_no_key(self):
        self._sign_in(self.owner)
        suspended = self._code(self._submit(agent=str(self.agent.id)))
        transferred = self._code(self._submit(agent=str(self.second_agent.id)))
        self.agent.with_user(self.owner).action_suspend()
        self.second_agent.with_user(SUPERUSER_ID).with_context(usl_agent_internal=True).write(
            {"owner_id": self.stranger.id},
        )

        self.assertEqual(self._exchange(suspended).json(), {"error": "invalid_grant"})
        self.assertEqual(self._exchange(transferred).json(), {"error": "invalid_grant"})
        self.assertFalse(self._credentials())
        self.assertFalse(self._credentials(self.second_agent))

    def test_the_owner_creates_a_read_only_agent_from_the_page(self):
        self._sign_in(self.owner)
        existing = self.env["usl.agent"].search([])

        code = self._code(self._submit(agent="new", name="[QA connect] Created", purpose="Read for the hub"))

        created = self.env["usl.agent"].search([]) - existing
        self.assertEqual(created.name, "[QA connect] Created")
        self.assertEqual(created.owner_id, self.owner)
        self.assertEqual(created.access_mode, "read_only")
        self.assertEqual(created.company_ids, self.owner.company_ids)
        self.assertEqual(self._exchange(code).json()["agent"]["id"], created.id)

    def test_a_refused_choice_shows_an_error_and_creates_nothing(self):
        self._sign_in(self.owner)
        self.agent.with_user(self.owner).action_suspend()
        existing = self.env["usl.agent"].search([])
        for form in (
            {"agent": str(self.foreign_agent.id)},
            {"agent": str(self.agent.id)},
            {"agent": "new", "name": "", "purpose": ""},
            {},
        ):
            with self.subTest(form=form):
                response = self._submit(**form)
                self.assertEqual(response.status_code, HTTPStatus.OK)
                self.assertTrue(html.fromstring(response.text).xpath("//p[@role='alert']"))
        self.assertEqual(self.env["usl.agent"].search([]), existing)
        self.assertFalse(self.env["usl.agent.connect.grant"].sudo().search([]))

    def test_a_portal_user_cannot_connect_an_agent(self):
        self._sign_in(self.portal)
        self.assertEqual(self._page().status_code, HTTPStatus.FORBIDDEN)
        self.assertEqual(self._submit(agent=str(self.agent.id)).status_code, HTTPStatus.FORBIDDEN)
        self.assertFalse(self.env["usl.agent.connect.grant"].sudo().search([]))

    def test_a_submit_without_the_csrf_token_is_refused(self):
        self._sign_in(self.owner)
        response = self._submit(csrf_token="")
        self.assertEqual(response.status_code, HTTPStatus.BAD_REQUEST)
        self.assertFalse(self.env["usl.agent.connect.grant"].sudo().search([]))

    def test_the_key_lifetime_follows_the_system_parameter(self):
        self.env["ir.config_parameter"].sudo().set_int(CONNECT_KEY_DAYS_PARAMETER, 90)
        self._sign_in(self.owner)
        code = self._code(self._submit(agent=str(self.agent.id)))
        before = fields.Datetime.now()

        self.assertEqual(self._exchange(code).status_code, HTTPStatus.OK)

        self.assertAlmostEqual(
            self._credentials().expiration_date,
            before + datetime.timedelta(days=90),
            delta=datetime.timedelta(minutes=1),
        )

    def test_grants_are_not_writable_through_the_orm(self):
        Grant = self.env["usl.agent.connect.grant"]
        with self.assertRaises(AccessError):
            Grant.sudo().create(
                {
                    "code_hash": "0" * 64,
                    "code_challenge": CHALLENGE,
                    "owner_id": self.owner.id,
                    "agent_id": self.agent.id,
                    "credential_name": "Direct",
                    "return_url": RETURN_URL,
                    "expires_at": fields.Datetime.now(),
                },
            )
        with self.assertRaises(AccessError):
            Grant.with_user(self.owner).search([])
