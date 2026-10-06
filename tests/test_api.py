"""API contract/security checks against the supplied DB; never writes to it."""
from contextlib import closing
import asyncio
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import quote

from fastapi.testclient import TestClient
from backend import main, agent, tools
from backend.models import ProductMatch
from pydantic_ai.messages import ModelRequest, ModelResponse, SystemPromptPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel


PRODUCT_ID = 'champion-reverse-weave-hoodie-1'


def setUpModule():
    global audit_directory, audit_patch
    audit_directory = tempfile.TemporaryDirectory()
    audit_patch = patch.object(tools, 'AUDIT_PATH', Path(audit_directory.name) / 'audit.json')
    audit_patch.start()


def tearDownModule():
    audit_patch.stop()
    audit_directory.cleanup()


def card(product_id=PRODUCT_ID):
    return ProductMatch.model_validate(tools.read_product(product_id).model_dump()).model_dump(mode='json')


def product_model(messages, info):
    if any(isinstance(part, UserPromptPart) for part in messages[-1].parts):
        return ModelResponse(parts=[
            ToolCallPart('get_product_price', {'product_id': PRODUCT_ID}),
            ToolCallPart('get_product_stock', {'product_id': PRODUCT_ID, 'size': 'XL'}),
        ])
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
        'reply': 'The Champion Reverse Weave Hoodie 1 costs $68. XL is out of stock.',
        'products': [card()],
    })])


def greeting_model(messages, info):
    return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {'reply': 'Welcome, Bulldog!', 'products': []})])


class ShopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.digest = hashlib.sha256(main.DB_PATH.read_bytes()).hexdigest()
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        assert hashlib.sha256(main.DB_PATH.read_bytes()).hexdigest() == cls.digest, "Database changed!"

    def test_catalogue_matches_database_and_all_images_are_served(self):
        response = self.client.get('/api/products')
        self.assertEqual(response.status_code, 200)
        products = response.json()
        with closing(main.connect_db()) as connection:
            rows = connection.execute('SELECT product_id, name, price FROM catalogue').fetchall()
        self.assertEqual(len(products), len(rows))
        expected = {row['product_id']: row for row in rows}
        for product in products:
            self.assertEqual(product['name'], expected[product['id']]['name'])
            self.assertEqual(product['price'], expected[product['id']]['price'])
            self.assertLessEqual(len(product['short_description']), 155)
            self.assertIsNotNone(product['image_url'])
            image = self.client.get(product['image_url'])
            self.assertEqual(image.status_code, 200)
            self.assertTrue(image.headers['content-type'].startswith('image/'))

    def test_detail_full_description_and_inventory_match(self):
        product_id = 'champion-reverse-weave-hoodie-1'
        response = self.client.get('/api/products/' + product_id)
        self.assertEqual(response.status_code, 200)
        product = response.json()
        with closing(main.connect_db()) as connection:
            description = connection.execute('SELECT description FROM catalogue WHERE product_id=?', (product_id,)).fetchone()[0]
            stock = dict(connection.execute('SELECT size,quantity FROM inventory WHERE product_id=?', (product_id,)).fetchall())
        self.assertEqual(product['description'], description)
        self.assertEqual({row['size']: row['quantity'] for row in product['sizes']}, stock)
        self.assertEqual([row['size'] for row in product['sizes']], ['XS', 'S', 'M', 'L', 'XL', 'XXL'])
        self.assertTrue(any(row['quantity'] == 0 for row in product['sizes']))

    def test_unknown_and_sql_injection_ids_are_404(self):
        for product_id in ['not-a-real-product', "' OR 1=1 --"]:
            self.assertEqual(self.client.get('/api/products/' + quote(product_id, safe='')).status_code, 404)

    def test_chat_contract(self):
        shop = agent.build_agent(FunctionModel(product_model))
        with patch.object(agent, 'get_agent', return_value=shop):
            response = self.client.post('/api/chat', json={'message': 'Tell me about Yale hoodies.'})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'reply', 'products'})
        self.assertIn('$68', body['reply'])
        expected = next(item for item in self.client.get('/api/products').json() if item['id'] == PRODUCT_ID)
        self.assertEqual(body['products'][0], expected)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_chat_validation(self):
        for body in [{}, {'message': ''}, {'message': '   '}, {'message': 'x' * 2001}, {'message': 'Hi', 'unexpected': True}]:
            self.assertEqual(self.client.post('/api/chat', json=body).status_code, 422)

    def test_cors_allows_frontend(self):
        response = self.client.options('/api/chat', headers={'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'POST', 'Access-Control-Request-Headers': 'content-type'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['access-control-allow-origin'], 'http://localhost:5173')

    def test_image_path_resolution_and_missing_image_fallback(self):
        name = 'basic-hoodie-big-yale.jpg'
        for value in [name, 'products/' + name, 'data/products/' + name, 'products\\' + name]:
            self.assertEqual(main.image_url(value), '/images/' + name)
        for value in ['missing.jpg', '../campus_customs.db', 'products/../campus_customs.db', '/etc/passwd']:
            self.assertIsNone(main.image_url(value))

    def test_static_directory_does_not_expose_database(self):
        self.assertEqual(self.client.get('/images/campus_customs.db').status_code, 404)
        self.assertEqual(self.client.get('/images/%2E%2E/campus_customs.db').status_code, 404)
        self.assertEqual(self.client.get('/api/users').status_code, 404)

    def test_sqlite_connection_is_read_only(self):
        with closing(main.connect_db()) as connection:
            with self.assertRaises(sqlite3.OperationalError):
                connection.execute('CREATE TABLE should_never_exist (id INTEGER)')

    def test_missing_db_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / 'missing.db'
            with patch.object(main, 'DB_PATH', missing):
                response = self.client.get('/api/products')
            self.assertEqual(response.status_code, 503)
            self.assertFalse(missing.exists())


class ChatTests(unittest.TestCase):
    def setUp(self):
        agent.CONVERSATIONS.clear()
        self.client = TestClient(main.app)

    def tearDown(self):
        self.client.close()
        agent.CONVERSATIONS.clear()

    def test_followups_history_isolation_and_system_prompt_survives_trim(self):
        requests = []
        def record(messages, info):
            requests.append(messages[:])
            return greeting_model(messages, info)
        shop = agent.build_agent(FunctionModel(record))
        with patch.object(agent, 'get_agent', return_value=shop):
            for i in range(9):
                self.assertEqual(self.client.post('/api/chat', json={'message': f'Turn {i}'}).status_code, 200)
            with TestClient(main.app) as other:
                self.assertEqual(other.post('/api/chat', json={'message': 'Separate shopper'}).status_code, 200)
        def user_text(messages):
            return [part.content for item in messages if isinstance(item, ModelRequest)
                    for part in item.parts if isinstance(part, UserPromptPart)]
        self.assertEqual(user_text(requests[1]), ['Turn 0', 'Turn 1'])
        self.assertEqual(user_text(requests[8]), [f'Turn {i}' for i in range(2, 9)])
        self.assertEqual(user_text(requests[9]), ['Separate shopper'])
        for messages in requests:
            prompts = [part.content for item in messages if isinstance(item, ModelRequest)
                       for part in item.parts if isinstance(part, SystemPromptPart)]
            self.assertEqual(prompts, [agent.PROMPT_PATH.read_text()])

    def test_unretrieved_product_id_is_rejected_without_saving_history(self):
        def invent(messages, info):
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': 'Invented suggestion', 'products': [{**card(), 'id': 'imaginary-shirt'}],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(invent))):
            response = self.client.post('/api/chat', json={'message': 'Suggest a shirt'})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('imaginary-shirt', response.text)
        self.assertTrue(all(not c.turns for c in agent.CONVERSATIONS.values()))

    def test_tool_loop_stops_at_request_limit(self):
        calls = []
        def loop(messages, info):
            calls.append(1)
            return ModelResponse(parts=[ToolCallPart('get_product_price', {'product_id': PRODUCT_ID})])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(loop))):
            response = self.client.post('/api/chat', json={'message': 'Loop test'})
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(calls), agent.REQUEST_LIMIT)
        self.assertTrue(all(not c.turns for c in agent.CONVERSATIONS.values()))

    def test_timeout_and_missing_configuration_are_safe(self):
        async def slow(messages, info):
            await asyncio.sleep(1)
            return greeting_model(messages, info)
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(slow))), patch.object(agent, 'TURN_TIMEOUT_SECONDS', 0.01):
            self.assertEqual(self.client.post('/api/chat', json={'message': 'Hi'}).status_code, 504)
        with patch.object(agent, 'get_agent', side_effect=agent.MissingConfiguration('private config details')):
            response = self.client.post('/api/chat', json={'message': 'Hi'})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('private config details', response.text)

    def test_logout_and_account_changes_discard_context(self):
        shop = agent.build_agent(FunctionModel(greeting_model))
        with patch.object(agent, 'get_agent', return_value=shop):
            self.client.post('/api/chat', json={'message': 'Hello'})
        token = self.client.cookies.get(agent.CONVERSATION_COOKIE)
        self.assertIn(token, agent.CONVERSATIONS)
        self.client.post('/api/auth/logout')
        self.assertNotIn(token, agent.CONVERSATIONS)
        self.assertIsNone(self.client.cookies.get(agent.CONVERSATION_COOKIE))
        token, _ = agent.conversation_for(None, 'first-account')
        replacement, _ = agent.conversation_for(token, 'different-account')
        self.assertNotEqual(token, replacement)
        self.assertIn(token, agent.CONVERSATIONS)  # A foreign cookie cannot erase another user's context.

    def test_search_respects_budget_stock_and_result_limit(self):
        matches = tools.search_products('navy hoodies', max_price=70, size='M', limit=100)
        self.assertGreater(len(matches), 0)
        self.assertLessEqual(len(matches), 6)
        for item in matches:
            self.assertLessEqual(item.price, 70)
            self.assertGreater(next(stock.quantity for stock in item.sizes if stock.size == 'M'), 0)
        self.assertIsNone(tools.get_product("' OR 1=1 --"))
        self.assertEqual(tools.search_products(max_price=-1), [])

    def test_chat_rejects_cross_site_origin(self):
        response = self.client.post('/api/chat', json={'message': 'Hi'}, headers={'Origin': 'https://elsewhere.example'})
        self.assertEqual(response.status_code, 403)


class ProductLookupTests(unittest.TestCase):
    def test_exact_description_and_price_with_minimal_fields(self):
        with closing(tools.connect_db()) as db:
            name, description, price, colors = db.execute(
                'SELECT name, description, price, colors FROM catalogue WHERE product_id = ?', (PRODUCT_ID,),
            ).fetchone()
        result = tools.get_product_description(PRODUCT_ID)
        self.assertEqual(result.model_dump(), {'product_id': PRODUCT_ID, 'name': name, 'description': description, 'colors': json.loads(colors)})
        result = tools.get_product_price(PRODUCT_ID)
        self.assertEqual(result.model_dump(mode='json'), {'product_id': PRODUCT_ID, 'name': name, 'price': str(price)})

    def test_specific_size_zero_and_unrecorded_size_are_distinct(self):
        for size, quantity in [('S', 25), ('M', 20), ('XL', 0)]:
            result = tools.get_product_stock(PRODUCT_ID, ' ' + size.lower() + ' ')
            self.assertEqual(result.requested_size, size)
            self.assertEqual([item.model_dump() for item in result.sizes], [{'size': size, 'quantity': quantity}])
        missing = tools.get_product_stock(PRODUCT_ID, 'XXXL')
        self.assertIsNotNone(missing)
        self.assertEqual(missing.requested_size, 'XXXL')
        self.assertEqual(missing.sizes, [])
        all_sizes = tools.get_product_stock(PRODUCT_ID)
        self.assertIsNone(all_sizes.requested_size)
        self.assertEqual([item.size for item in all_sizes.sizes], ['XS', 'S', 'M', 'L', 'XL', 'XXL'])

    def test_unknown_ids_and_injection_like_inputs_return_no_match(self):
        for lookup in (tools.get_product_description, tools.get_product_price, tools.get_product_stock):
            for product_id in ('not-a-real-product', "' OR 1=1 --"):
                with self.subTest(tool=lookup.__name__, product_id=product_id):
                    self.assertIsNone(lookup(product_id))
        self.assertEqual(tools.get_product_stock(PRODUCT_ID, "M' OR 1=1 --").sizes, [])

    def test_no_rounding_or_fabricated_missing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / 'lookup.db'
            with closing(tools.connect_db()) as source, closing(sqlite3.connect(db_path)) as copied:
                source.backup(copied)
                copied.execute('UPDATE catalogue SET price = ?, description = ? WHERE product_id = ?', (68.375, '  ', PRODUCT_ID))
                copied.execute('DELETE FROM inventory WHERE product_id = ?', (PRODUCT_ID,))
                copied.commit()
            with patch.object(tools, 'DB_PATH', db_path):
                self.assertEqual(tools.get_product_price(PRODUCT_ID).model_dump(mode='json')['price'], '68.375')
                self.assertIsNone(tools.get_product_description(PRODUCT_ID).description)
                self.assertEqual(tools.get_product_stock(PRODUCT_ID).sizes, [])

    def test_description_tool_registered_and_runs_through_api(self):
        def describe(messages, info):
            if any(isinstance(part, UserPromptPart) for part in messages[-1].parts):
                self.assertEqual({tool.name for tool in info.function_tools}, {
                    'search_products', 'search_catalogue', 'get_product_description', 'get_product_price', 'get_product_stock', 'show_more_products', 'compare_products',
                })
                return ModelResponse(parts=[ToolCallPart('get_product_description', {'product_id': PRODUCT_ID})])
            result = messages[-1].parts[0].content
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': result.description, 'products': [card(result.product_id)],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(describe))), TestClient(main.app) as client:
            response = client.post('/api/chat', json={'message': 'Describe the Champion hoodie'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['reply'], tools.get_product_description(PRODUCT_ID).description)


class CatalogueCardTests(unittest.TestCase):
    def setUp(self):
        agent.CONVERSATIONS.clear()

    def tearDown(self):
        agent.CONVERSATIONS.clear()

    def test_category_keyword_and_constraints(self):
        hoodies = tools.search_catalogue(category='hoodies')
        self.assertEqual(len(hoodies), 6)
        self.assertTrue(all('hood' in item.garment_type.lower() for item in hoodies))
        bomber = tools.search_catalogue(category='bomber jackets')
        self.assertEqual([item.id for item in bomber], ['brooks-brothers-bomber-jacket-yale'])
        self.assertEqual(tools.search_catalogue(category='spacesuits'), [])
        self.assertEqual(tools.search_catalogue(category="' OR 1=1 --"), [])
        self.assertEqual(tools.search_catalogue(category='bomber jacket', keyword='navy'), bomber)
        self.assertEqual(tools.search_catalogue(category='bomber jacket', max_price=50), [])
        self.assertEqual(tools.search_catalogue(category='hoodies', size='XXXL'), [])
        self.assertEqual(len(tools.search_catalogue(keyword='champion reverse weave hoodie 1')), 1)

    def test_many_one_none_contract_and_each_card_opens_existing_detail(self):
        def browse(messages, info):
            prompts = [part.content for part in messages[-1].parts if isinstance(part, UserPromptPart)]
            if prompts:
                return ModelResponse(parts=[ToolCallPart('search_catalogue', {'category': prompts[-1]})])
            matches = messages[-1].parts[0].content
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': 'Here are some matches.' if matches else 'No matching products were found.',
                'products': [item.model_dump(mode='json') for item in matches],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(browse))), TestClient(main.app) as client:
            for category, count in [('hoodies', 6), ('bomber jackets', 1), ('spacesuits', 0)]:
                response = client.post('/api/chat', json={'message': category})
                self.assertEqual(response.status_code, 200, response.text)
                body = response.json()
                self.assertEqual(set(body), {'reply', 'products'})
                self.assertEqual(len(body['products']), count)
                for product in body['products']:
                    self.assertEqual(set(product), {'id', 'name', 'price', 'short_description', 'image_url', 'garment_type'})
                    detail = client.get('/api/products/' + quote(product['id'], safe=''))
                    self.assertEqual(detail.status_code, 200)
                    self.assertEqual({key: detail.json()[key] for key in product}, product)
                    self.assertEqual(len(detail.json()['sizes']), 6)
                    self.assertEqual(client.get(product['image_url']).status_code, 200)

    def test_card_fields_are_database_values_even_if_model_changes_them(self):
        expected = tools.search_catalogue(category='bomber jacket')[0].model_dump(mode='json')
        def tamper(messages, info):
            if any(isinstance(part, UserPromptPart) for part in messages[-1].parts):
                return ModelResponse(parts=[ToolCallPart('search_catalogue', {'category': 'bomber jacket'})])
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': 'Here is the match.',
                'products': [{**expected, 'name': 'Invented name', 'price': 0.01,
                              'image_url': 'https://invalid.example/fake.jpg', 'short_description': 'Invented description'}],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(tamper))), TestClient(main.app) as client:
            response = client.post('/api/chat', json={'message': 'What bomber jackets do you have?'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['products'], [expected])

    def test_prose_cannot_replace_matches_or_fill_empty_search(self):
        for category, invented_cards in [('bomber jacket', []), ('spacesuits', [card()])]:
            def invalid(messages, info):
                if any(isinstance(part, UserPromptPart) for part in messages[-1].parts):
                    return ModelResponse(parts=[ToolCallPart('search_catalogue', {'category': category})])
                return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                    'reply': 'A prose list instead of the actual results.', 'products': invented_cards,
                })])
            with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(invalid))), TestClient(main.app) as client:
                response = client.post('/api/chat', json={'message': 'Browse ' + category})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn('A prose list', response.text)


class UsabilityTests(unittest.TestCase):
    def setUp(self):
        agent.CONVERSATIONS.clear()
        self.addCleanup(agent.CONVERSATIONS.clear)

    def test_shop_filters_use_price_and_positive_size_stock(self):
        with TestClient(main.app) as client:
            response = client.get('/api/products', params={'category': 'hood', 'max_price': 70, 'size': 'M'})
            self.assertEqual(response.status_code, 200)
            products = response.json()
            self.assertGreater(len(products), 6)
            for product in products:
                self.assertIn('hood', product['garment_type'])
                self.assertLessEqual(product['price'], 70)
                stock = tools.get_product_stock(product['id'], 'M')
                self.assertGreater(stock.sizes[0].quantity, 0)
            self.assertEqual(client.get('/api/products', params={'category': "' OR 1=1 --"}).json(), [])
            for params in [{'max_price': -1}, {'max_price': 'nan'}, {'size': 'wrong'}]:
                self.assertEqual(client.get('/api/products', params=params).status_code, 422)

    def test_next_pages_preserve_constraints_no_repeats_and_exhaust(self):
        state = tools.CatalogueSearchState()
        token = tools.CATALOGUE_SEARCH.set(state)
        self.addCleanup(tools.CATALOGUE_SEARCH.reset, token)
        self.assertEqual(tools.show_more_products().status, 'no_search')
        first = tools.search_catalogue(category='hoodies', max_price=70, size='M')
        found = {item.id for item in first}
        for _ in range(20):
            page = tools.show_more_products()
            ids = {item.id for item in page.products}
            self.assertFalse(found & ids)
            self.assertLessEqual(len(ids), 6)
            found.update(ids)
            if page.status == 'end':
                break
        else:
            self.fail('Browse never ended')
        expected = {item.id for item in tools.list_catalogue(category='hood', max_price=70, size='M')}
        self.assertEqual(found, expected)
        self.assertEqual(tools.show_more_products().products, [])
        # A new browse resets the old filters and excluded IDs.
        jacket = tools.search_catalogue(category='bomber jacket')
        self.assertEqual(len(jacket), 1)
        self.assertEqual(tools.show_more_products().status, 'end')

    def test_comparison_exact_values_missing_records_and_safe_ids(self):
        other = 'brooks-brothers-double-knit-full-zip-hoodie-yale'
        result = tools.compare_products(PRODUCT_ID, other, ' xl ')
        self.assertEqual(str(result.price_difference), '20.0')
        self.assertEqual(result.requested_size, 'XL')
        self.assertEqual(result.products[0].sizes[0].quantity, 0)
        self.assertTrue(all([s.size for s in p.sizes] == ['XL'] for p in result.products))
        self.assertEqual(tools.compare_products(PRODUCT_ID, other, 'XXXL').products[0].sizes, [])
        missing = tools.compare_products(PRODUCT_ID, "' OR 1=1 --")
        self.assertEqual(len(missing.products), 1)
        self.assertEqual(missing.missing_ids, ["' OR 1=1 --"])
        self.assertIsNone(missing.price_difference)
        same = tools.compare_products(PRODUCT_ID, PRODUCT_ID)
        self.assertEqual(len(same.products), 1)
        self.assertIsNone(same.price_difference)

    def test_pagination_turns_are_isolated_and_failures_do_not_advance(self):
        def browse(messages, info):
            prompts = [p.content for p in messages[-1].parts if isinstance(p, UserPromptPart)]
            if prompts:
                call = ToolCallPart('search_catalogue', {'category': 'hoodies', 'max_price': 70, 'size': 'M'}) if prompts[0] == 'start' else ToolCallPart('show_more_products', {})
                return ModelResponse(parts=[call])
            result = messages[-1].parts[0].content
            matches = result if isinstance(result, list) else result.products
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': 'Here are the matches.', 'products': [p.model_dump(mode='json') for p in matches],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(browse))), TestClient(main.app) as client, TestClient(main.app) as guest:
            first = client.post('/api/chat', json={'message': 'start'}).json()['products']
            token = client.cookies.get(agent.CONVERSATION_COOKIE)
            before = set(agent.CONVERSATIONS[token].browse_cursor.shown)
            def fail_after_tool(messages, info):
                if any(isinstance(p, UserPromptPart) for p in messages[-1].parts):
                    return ModelResponse(parts=[ToolCallPart('show_more_products', {})])
                raise asyncio.TimeoutError()
            with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(fail_after_tool))):
                self.assertEqual(client.post('/api/chat', json={'message': 'more'}).status_code, 504)
            self.assertEqual(agent.CONVERSATIONS[token].browse_cursor.shown, before)
            second = client.post('/api/chat', json={'message': 'more'}).json()['products']
            self.assertEqual(len(first), 6)
            self.assertEqual(len(second), 6)
            self.assertFalse({p['id'] for p in first} & {p['id'] for p in second})
            self.assertEqual(guest.post('/api/chat', json={'message': 'more'}).json()['products'], [])

    def test_comparison_tool_returns_clickable_database_cards(self):
        ids = [PRODUCT_ID, 'brooks-brothers-double-knit-full-zip-hoodie-yale']
        def compare(messages, info):
            if any(isinstance(p, UserPromptPart) for p in messages[-1].parts):
                return ModelResponse(parts=[ToolCallPart('compare_products', {'first_product_id': ids[0], 'second_product_id': ids[1], 'size': 'XL'})])
            data = messages[-1].parts[0].content
            return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, {
                'reply': f'The price difference is ${data.price_difference}.',
                'products': [p.model_dump(mode='json') for p in data.products],
            })])
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(compare))), TestClient(main.app) as client:
            response = client.post('/api/chat', json={'message': 'Compare both in XL'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual([p['id'] for p in response.json()['products']], ids)
            for item in response.json()['products']:
                self.assertEqual(client.get('/api/products/' + item['id']).status_code, 200)


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'output' / 'audit_trail.json'
        self.audit_patch = patch.object(tools, 'AUDIT_PATH', self.path)
        self.audit_patch.start()
        self.addCleanup(self.audit_patch.stop)
        agent.CONVERSATIONS.clear()
        self.addCleanup(agent.CONVERSATIONS.clear)

    def entries(self):
        return json.loads(self.path.read_text())

    def test_real_message_history_first_run_and_followup_do_not_repeat_old_events(self):
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(product_model))), TestClient(main.app) as client:
            self.assertFalse(self.path.exists())
            self.assertEqual(client.post('/api/chat', json={'message': 'Price and stock?'}).status_code, 200)
            first = self.entries()
            self.assertEqual(client.post('/api/chat', json={'message': 'Check again.'}).status_code, 200)
        after = self.entries()
        self.assertEqual(after[:len(first)], first)
        runs = {entry['run_id'] for entry in after}
        self.assertEqual(len(runs), 2)
        for run in runs:
            entries = [e for e in after if e['run_id'] == run]
            self.assertEqual([e['step'] for e in entries], list(range(1, len(entries)+1)))
            self.assertEqual(entries[-1]['event'], 'run_end')
            self.assertTrue(all(e['stop_reason'] == 'final_output' for e in entries))
            self.assertEqual(sum(e['event'] == 'tool_call' and e['tool_name'] == 'get_product_price' for e in entries), 1)
            stock = next(e for e in entries if e['event'] == 'tool_return' and e['tool_name'] == 'get_product_stock')
            self.assertIn('"quantity": 0', stock['result_summary'])
            call = next(e for e in entries if e['event'] == 'tool_call' and e['tool_name'] == 'get_product_stock')
            self.assertEqual(call['tool_call_id'], stock['tool_call_id'])
            self.assertIn('"size": "XL"', call['args_summary'])

    def test_partial_failure_keeps_tool_history_without_private_error_text(self):
        from pydantic_ai.exceptions import UnexpectedModelBehavior
        def failing(messages, info):
            if any(isinstance(p, UserPromptPart) for p in messages[-1].parts):
                return ModelResponse(parts=[ToolCallPart('get_product_price', {'product_id': PRODUCT_ID})])
            raise UnexpectedModelBehavior('private provider body never goes in audit')
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(failing))), TestClient(main.app) as client:
            self.assertEqual(client.post('/api/chat', json={'message': 'Price?'}).status_code, 503)
        entries = self.entries()
        self.assertTrue(any(e['event'] == 'tool_return' and e['tool_name'] == 'get_product_price' for e in entries))
        self.assertEqual(entries[-1]['stop_reason'], 'error')
        self.assertNotIn('private provider body', self.path.read_text())

    def test_timeout_and_usage_limit_have_explicit_stop_records(self):
        async def slow(messages, info):
            await asyncio.sleep(1)
            return greeting_model(messages, info)
        def loop(messages, info):
            return ModelResponse(parts=[ToolCallPart('get_product_price', {'product_id': PRODUCT_ID})])
        with TestClient(main.app) as client:
            with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(slow))), patch.object(agent, 'TURN_TIMEOUT_SECONDS', 0.01):
                self.assertEqual(client.post('/api/chat', json={'message': 'Hello'}).status_code, 504)
            with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(loop))):
                self.assertEqual(client.post('/api/chat', json={'message': 'Loop'}).status_code, 429)
        self.assertEqual([e['stop_reason'] for e in self.entries() if e['event'] == 'run_end'], ['timeout', 'usage_limit'])

    def test_corrupt_existing_file_is_never_reset(self):
        self.path.parent.mkdir()
        self.path.write_text('damaged previous history')
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(greeting_model))), TestClient(main.app) as client:
            response = client.post('/api/chat', json={'message': 'Hello'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.path.read_text(), 'damaged previous history')
        self.assertNotIn(str(self.path), response.text)

    def test_concurrent_appends_and_atomic_write_failure_preserve_old_history(self):
        from concurrent.futures import ThreadPoolExecutor
        from datetime import datetime, timezone
        def write(number):
            tools.append_audit_entries(tools.audit_entries([], str(number), 'final_output', datetime.now(timezone.utc)))
        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, range(24)))
        self.assertEqual({e['run_id'] for e in self.entries()}, {str(i) for i in range(24)})
        before = self.path.read_bytes()
        with patch.object(tools.os, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(tools.AuditLogError): write(25)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob('.audit-*.tmp')), [])

    def test_new_python_process_appends_instead_of_resetting(self):
        import subprocess, sys
        from datetime import datetime, timezone
        tools.append_audit_entries(tools.audit_entries([], 'before-restart', 'final_output', datetime.now(timezone.utc)))
        before = self.entries()
        script = ('from pathlib import Path; from datetime import datetime,timezone; from backend import tools; '
                  'import sys; tools.AUDIT_PATH=Path(sys.argv[1]); '
                  'tools.append_audit_entries(tools.audit_entries([],"after-restart","final_output",datetime.now(timezone.utc)))')
        subprocess.run([sys.executable, '-c', script, str(self.path)], cwd=tools.PROJECT_ROOT, check=True, capture_output=True)
        self.assertEqual(self.entries()[:-1], before)
        self.assertEqual(self.entries()[-1]['run_id'], 'after-restart')

    def test_summaries_omit_private_messages_thinking_and_credentials(self):
        from datetime import datetime, timezone
        from pydantic_ai.messages import TextPart, ThinkingPart, ToolReturnPart
        messages = [
            ModelRequest(parts=[SystemPromptPart('private-system'), UserPromptPart('private-customer')]),
            ModelResponse(parts=[TextPart('private-answer'), ThinkingPart('private-thinking'),
                                ToolCallPart('search_products', {'query': 'a@example.com password=secret123 sk-abcdef', 'password': 'do-not-log'}),
                                ToolCallPart('final_result', {'reply': 'private-final', 'products': []})], finish_reason='stop'),
            ModelRequest(parts=[ToolReturnPart('search_products', [{'name': 'Hoodie '+('x'*200), 'price': 68, 'password': 'hidden'}]*6)]),
        ]
        entries = tools.audit_entries(messages, 'privacy-test', 'final_output', datetime.now(timezone.utc))
        tools.append_audit_entries(entries)
        text = self.path.read_text()
        for secret in ('private-system','private-customer','private-answer','private-thinking','private-final','a@example.com','secret123','sk-abcdef','do-not-log','hidden'):
            self.assertNotIn(secret, text)
        self.assertTrue(all(len(e.args_summary or '') <= 300 and len(e.result_summary or '') <= 300 for e in entries))
        self.assertEqual(next(e.model_finish_reason for e in entries if e.event == 'model_response'), 'stop')
        self.assertIn('68', text)

    def test_concurrent_runs_capture_their_own_tools(self):
        async def model(messages, info):
            if any(isinstance(p, UserPromptPart) for p in messages[-1].parts):
                request = next(p.content for p in messages[-1].parts if isinstance(p, UserPromptPart))
                await asyncio.sleep(0.01)
                return ModelResponse(parts=[ToolCallPart(request, {'product_id': PRODUCT_ID})])
            return greeting_model(messages, info)
        async def run():
            return await asyncio.gather(agent.answer('get_product_price', None, None),
                                        agent.answer('get_product_description', None, None))
        with patch.object(agent, 'get_agent', return_value=agent.build_agent(FunctionModel(model))):
            asyncio.run(run())
        by_run = {}
        for e in self.entries():
            if e['event'] == 'tool_call' and e['tool_name'] != 'final_result':
                by_run.setdefault(e['run_id'], []).append(e['tool_name'])
        self.assertEqual(sorted(by_run.values()), [['get_product_description'], ['get_product_price']])


if __name__ == '__main__':
    unittest.main()
