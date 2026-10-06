"""Account tests use temporary copies of the supplied database, never the original."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from backend import main, agent, tools
from backend.models import ShopDeps
from pydantic_ai import RunContext
from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


def setUpModule():
    global audit_directory, audit_patch
    audit_directory = tempfile.TemporaryDirectory()
    audit_patch = patch.object(tools, 'AUDIT_PATH', Path(audit_directory.name) / 'audit.json')
    audit_patch.start()


def tearDownModule():
    audit_patch.stop()
    audit_directory.cleanup()


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db_path = Path(self.directory.name) / 'accounts.db'
        with closing(main.connect_db()) as source, closing(sqlite3.connect(self.db_path)) as target:
            source.backup(target)
        self.patch = patch.object(main, 'DB_PATH', self.db_path)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.client = TestClient(main.app)
        self.addCleanup(self.client.close)
        with main.SESSION_LOCK:
            main.SESSIONS.clear()
        self.email = 'new-bulldog@example.com'
        self.password = secrets.token_urlsafe(18)
        self.signup = {
            'first_name': ' New ', 'last_name': ' Bulldog ',
            'email': self.email, 'password': self.password, 'confirm_password': self.password,
        }

    def stored_user(self, email):
        with closing(main.connect_db()) as connection:
            return connection.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()

    def assert_public_response(self, response):
        self.assertNotIn('password', response.text)
        self.assertNotIn(self.password, response.text)
        self.assertNotIn('$argon2', response.text)

    def test_signup_stores_real_schema_and_argon2_only_then_login(self):
        response = self.client.post('/api/auth/signup', json=self.signup)
        self.assertEqual(response.status_code, 201)
        self.assert_public_response(response)
        stored = self.stored_user(self.email)
        self.assertEqual(stored['first_name'], 'New')
        self.assertEqual(stored['last_name'], 'Bulldog')
        self.assertEqual(stored['name'], 'New Bulldog')
        self.assertTrue(stored['created_at'])
        self.assertTrue(stored['password_hash'].startswith('$argon2id$v=19$m=65536,t=3,p=4$'))
        self.assertTrue(main.PASSWORD_HASHER.verify(stored['password_hash'], self.password))
        self.assertNotEqual(stored['password_hash'], self.password)
        self.assertEqual(self.client.get('/api/auth/me').json()['user']['email'], self.email)
        self.client.post('/api/auth/logout')
        login = self.client.post('/api/auth/login', json={'email': self.email.upper(), 'password': self.password})
        self.assertEqual(login.status_code, 200)
        self.assert_public_response(login)

    def test_supplied_seed_credentials_still_work(self):
        response = self.client.post('/api/auth/login', json={'email': 'test@campuscustoms.yale.edu', 'password': 'password'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['user']['email'], 'test@campuscustoms.yale.edu')
        self.assert_public_response(response)
        stored = self.stored_user('test@campuscustoms.yale.edu')
        self.assertTrue(stored['password_hash'].startswith('$argon2id$'))
        self.assertTrue(main.PASSWORD_HASHER.verify(stored['password_hash'], 'password'))

    def test_legacy_hash_upgrades_only_after_correct_password(self):
        salt = 'fixture-salt-only'
        digest = hashlib.pbkdf2_hmac('sha256', b'password', salt.encode(), 120_000).hex()
        legacy = f'pbkdf2_sha256${salt}${digest}'
        with closing(main.connect_accounts()) as connection, connection:
            connection.execute('UPDATE users SET password_hash=? WHERE email=?', (legacy, 'test@campuscustoms.yale.edu'))
        bad = self.client.post('/api/auth/login', json={'email': 'test@campuscustoms.yale.edu', 'password': 'incorrect'})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(self.stored_user('test@campuscustoms.yale.edu')['password_hash'], legacy)
        good = self.client.post('/api/auth/login', json={'email': 'test@campuscustoms.yale.edu', 'password': 'password'})
        self.assertEqual(good.status_code, 200)
        self.assertTrue(self.stored_user('test@campuscustoms.yale.edu')['password_hash'].startswith('$argon2id$'))

    def test_wrong_email_and_wrong_password_have_same_error(self):
        self.client.post('/api/auth/signup', json=self.signup)
        self.client.post('/api/auth/logout')
        responses = [self.client.post('/api/auth/login', json=payload) for payload in [
            {'email': 'missing@example.com', 'password': self.password},
            {'email': self.email, 'password': 'incorrect'},
        ]]
        self.assertEqual(responses[0].status_code, 401)
        self.assertEqual(responses[1].status_code, 401)
        self.assertEqual(responses[0].json(), responses[1].json())
        self.assertEqual(responses[0].json()['detail'], 'Incorrect email or password.')
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)

    def test_duplicate_email_case_and_whitespace(self):
        self.assertEqual(self.client.post('/api/auth/signup', json=self.signup).status_code, 201)
        duplicate = {**self.signup, 'email': '  NEW-BULLDOG@EXAMPLE.COM  '}
        self.assertEqual(self.client.post('/api/auth/signup', json=duplicate).status_code, 409)
        with closing(main.connect_db()) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM users WHERE email=?', (self.email,)).fetchone()[0], 1)

    def test_concurrent_duplicate_signup_is_atomic(self):
        def create(email):
            with TestClient(main.app) as client:
                return client.post('/api/auth/signup', json={**self.signup, 'email': email}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(create, [self.email, self.email.upper()]))
        self.assertEqual(sorted(statuses), [201, 409])

    def test_validation_cannot_echo_password_or_body(self):
        invalid = [
            {**self.signup, 'first_name': '   '},
            {**self.signup, 'email': 'invalid'},
            {**self.signup, 'password': 'short'},
            {**self.signup, 'confirm_password': 'different-value'},
            {**self.signup, 'password': self.password * 20},
            {**self.signup, 'password': {'secret': self.password}},
            {**self.signup, self.password: self.password},
        ]
        for body in invalid:
            response = self.client.post('/api/auth/signup', json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(self.password, response.text)
            self.assertNotIn('input', response.json())
        self.assertIsNone(self.stored_user(self.email))

    def test_session_cookie_logout_replay_and_expiry(self):
        response = self.client.post('/api/auth/signup', json=self.signup)
        cookie_header = response.headers['set-cookie'].lower()
        for attribute in ['httponly', 'samesite=lax', 'path=/api', 'max-age=28800']:
            self.assertIn(attribute, cookie_header)
        token = self.client.cookies.get(main.SESSION_COOKIE)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 200)
        self.client.post('/api/auth/logout')
        self.assertEqual(self.client.get('/api/auth/me', headers={'Cookie': f'{main.SESSION_COOKIE}={token}'}).status_code, 401)
        self.client.post('/api/auth/login', json={'email': self.email, 'password': self.password})
        token = self.client.cookies.get(main.SESSION_COOKIE)
        with main.SESSION_LOCK:
            user_id, _ = main.SESSIONS[token]
            main.SESSIONS[token] = (user_id, 0)
        self.assertEqual(self.client.get('/api/auth/me').status_code, 401)

    def test_session_rotation_and_forged_cookie(self):
        self.client.post('/api/auth/signup', json=self.signup)
        old_token = self.client.cookies.get(main.SESSION_COOKIE)
        self.client.post('/api/auth/login', json={'email': self.email, 'password': self.password})
        self.assertNotEqual(self.client.cookies.get(main.SESSION_COOKIE), old_token)
        self.assertNotIn(old_token, main.SESSIONS)
        self.assertEqual(self.client.get('/api/auth/me', headers={'Cookie': f'{main.SESSION_COOKIE}=invented-token'}).status_code, 401)

    def test_plaintext_unknown_and_malformed_hashes_are_rejected(self):
        for value in [self.password, '$argon2id$malformed', 'pbkdf2_sha256$salt$' + 'z' * 64]:
            self.assertFalse(main.verify_password(value, self.password))

    def test_origin_check_and_no_store(self):
        self.assertEqual(self.client.post('/api/auth/signup', json=self.signup, headers={'Origin': 'https://unrelated.example'}).status_code, 403)
        self.assertIsNone(self.stored_user(self.email))
        response = self.client.post('/api/auth/signup', json=self.signup, headers={'Origin': 'http://localhost:5173'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/api/auth/me').headers['cache-control'], 'no-store')

    def test_password_is_not_trimmed_and_names_are_parameterized(self):
        exact_password = '  ' + self.password + '  '
        payload = {**self.signup, 'last_name': "O'Neil", 'password': exact_password, 'confirm_password': exact_password}
        self.assertEqual(self.client.post('/api/auth/signup', json=payload).status_code, 201)
        self.assertEqual(self.stored_user(self.email)['last_name'], "O'Neil")
        self.assertEqual(self.client.post('/api/auth/login', json={'email': self.email, 'password': self.password}).status_code, 401)
        self.assertEqual(self.client.post('/api/auth/login', json={'email': self.email, 'password': exact_password}).status_code, 200)


class ChatMemoryTests(unittest.TestCase):
    def setUp(self):
        AuthTests.setUp(self)
        self.tools_patch = patch.object(tools, 'DB_PATH', self.db_path)
        self.tools_patch.start()
        self.addCleanup(self.tools_patch.stop)
        agent.CONVERSATIONS.clear()
        self.addCleanup(agent.CONVERSATIONS.clear)
        self.contexts = []
        self.requests = []

        def reply(messages, info):
            self.requests.append(messages[:])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': 'Your preference is noted.', 'products': [],
            })])
        shop = agent.build_agent(FunctionModel(reply))

        @shop.instructions
        def capture(ctx: RunContext[ShopDeps]) -> str:
            self.contexts.append(ctx.deps)
            return ''

        self.agent_patch = patch.object(agent, 'get_agent', return_value=shop)
        self.agent_patch.start()
        self.addCleanup(self.agent_patch.stop)

    def login_seed(self):
        response = self.client.post('/api/auth/login', json={'email': 'test@campuscustoms.yale.edu', 'password': 'password'})
        self.assertEqual(response.status_code, 200)
        return response.json()['user']

    def message_count(self):
        with closing(main.connect_db()) as db:
            return db.execute('SELECT COUNT(*) FROM chat_messages').fetchone()[0]

    def test_saved_pair_reloads_after_new_session_and_reaches_model_history(self):
        user = self.login_seed()
        before = self.message_count()
        response = self.client.post('/api/chat', json={'message': 'Remember my navy-hoodie preference.'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.message_count(), before + 2)
        saved = self.client.get('/api/chat/history')
        self.assertEqual(saved.headers['cache-control'], 'no-store')
        self.assertEqual([m['role'] for m in saved.json()['messages'][-2:]], ['user', 'assistant'])
        self.assertEqual(saved.json()['messages'][-2]['content'], 'Remember my navy-hoodie preference.')
        self.client.post('/api/auth/logout')
        agent.CONVERSATIONS.clear()  # Simulate losing all in-memory chat context.
        self.login_seed()
        self.assertEqual(self.client.get('/api/chat/history').json(), saved.json())
        self.assertEqual(self.client.post('/api/chat', json={'message': 'What preference did I mention?'}).status_code, 200)
        previous_prompts = [p.content for m in self.requests[-1] for p in m.parts if isinstance(p, UserPromptPart)]
        self.assertIn('Remember my navy-hoodie preference.', previous_prompts)
        self.assertEqual(self.contexts[-1].customer.model_dump(), {key: user[key] for key in ('id', 'name', 'email')})

    def test_guests_never_access_saved_history_even_with_foreign_cookie(self):
        self.login_seed()
        self.client.post('/api/chat', json={'message': 'Private signed-in preference.'})
        foreign_cookie = self.client.cookies.get(agent.CONVERSATION_COOKIE)
        before = self.message_count()
        with TestClient(main.app) as guest, patch.object(tools, 'load_chat_history', side_effect=AssertionError('Guest read history')), patch.object(tools, 'save_chat_turn', side_effect=AssertionError('Guest wrote history')):
            self.assertEqual(guest.get('/api/chat/history?user_id=1').json(), {'messages': []})
            response = guest.post('/api/chat', json={'message': 'Hi from a guest'},
                                  headers={'Cookie': f'{agent.CONVERSATION_COOKIE}={foreign_cookie}'})
            self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.contexts[-1].customer)
        self.assertNotIn('Private signed-in preference.', str(self.requests[-1]))
        self.assertEqual(self.message_count(), before)

    def test_other_account_cannot_read_seed_history_or_supply_identity(self):
        self.login_seed()
        self.client.post('/api/chat', json={'message': 'Seed-only navy preference.'})
        self.client.post('/api/auth/logout')
        account = self.client.post('/api/auth/signup', json=self.signup)
        self.assertEqual(account.status_code, 201)
        self.assertEqual(self.client.get('/api/chat/history?user_id=1').json(), {'messages': []})
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Hello', 'user_id': 1}).status_code, 422)
        self.client.post('/api/chat', json={'message': 'Different account preference.'})
        self.assertNotIn('Seed-only navy preference.', str(self.requests[-1]))
        self.assertEqual(self.contexts[-1].customer.email, self.email)
        self.assertNotIn('Seed-only navy preference.', self.client.get('/api/chat/history').text)

    def test_expired_session_is_a_guest(self):
        self.login_seed()
        token = self.client.cookies.get(main.SESSION_COOKIE)
        main.SESSIONS[token] = (1, 0)
        before = self.message_count()
        self.assertEqual(self.client.get('/api/chat/history').json(), {'messages': []})
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Expired session hello'}).status_code, 200)
        self.assertIsNone(self.contexts[-1].customer)
        self.assertEqual(self.message_count(), before)

    def test_deps_take_current_page_context_on_every_request(self):
        self.login_seed()
        for product_id in ['champion-reverse-weave-hoodie-1', 'brooks-brothers-bomber-jacket-yale', None]:
            response = self.client.post('/api/chat', json={'message': 'Do you have this in pink?', 'page_context': {'product_id': product_id}})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.contexts[-1].page_product_id, product_id)
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Hi', 'page_context': {'product_id': 'x', 'email': 'forged@example.com'}}).status_code, 422)

    def test_failed_agent_run_saves_no_partial_messages(self):
        self.login_seed()
        before = self.message_count()
        with patch.object(agent, 'get_agent', side_effect=agent.MissingConfiguration('test')):
            self.assertEqual(self.client.post('/api/chat', json={'message': 'Unanswered message'}).status_code, 503)
        self.assertEqual(self.message_count(), before)

    def test_failed_second_insert_rolls_back_the_whole_exchange(self):
        self.login_seed()
        before = self.message_count()
        with closing(main.connect_accounts()) as db, db:
            db.execute("CREATE TRIGGER fail_assistant BEFORE INSERT ON chat_messages WHEN NEW.role='assistant' BEGIN SELECT RAISE(ABORT, 'test only'); END")
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Failed save'}).status_code, 503)
        self.assertEqual(self.message_count(), before)

    def test_legacy_cards_and_bad_json_load_safely(self):
        self.login_seed()
        with closing(main.connect_accounts()) as db, db:
            for encoded in [json.dumps([{'product_id': 'champion-reverse-weave-hoodie-1', 'price': 0.01, 'image_url': 'https://invalid.example'}]), '{bad json']:
                db.execute('INSERT INTO chat_messages(user_id,role,content,products_json) VALUES (?,?,?,?)', (1, 'assistant', 'Saved card', encoded))
        messages = self.client.get('/api/chat/history').json()['messages']
        self.assertEqual(messages[-2]['products'][0]['price'], 68)
        self.assertTrue(messages[-2]['products'][0]['image_url'].startswith('/images/'))
        self.assertEqual(messages[-1]['products'], [])


if __name__ == '__main__':
    unittest.main()
