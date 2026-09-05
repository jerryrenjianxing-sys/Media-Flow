import unittest
from agent_contract import check_contract, ROUTES
from agent_runtime import PROVIDER_ID, MODEL_ID


def fixture():
    paths = {path: {method: {}} for path, method in ROUTES.items()}
    for path, names in [('/session', ['agent', 'model', 'title']),
                        ('/session/{sessionID}/prompt_async', ['parts', 'model', 'messageID']),
                        ('/provider/{providerID}/oauth/authorize', ['method']),
                        ('/question/{requestID}/reply', ['answers'])]:
        paths[path]['post']['requestBody'] = {'content': {'application/json': {'schema': {
            'properties': {name: {} for name in names}}}}}
    return {'paths': paths}, {'all': [{'id': PROVIDER_ID, 'models': {MODEL_ID: {}}}, {'id': 'original'}]}


class AgentContractTests(unittest.TestCase):
    def test_expected_routes_and_additive_provider(self):
        doc, catalog = fixture()
        self.assertEqual(check_contract(doc, catalog, baseline_catalog={'all': [{'id': 'original'}]})['routes_checked'], 17)

    def test_missing_auth_route_blocks_contract(self):
        doc, catalog = fixture()
        del doc['paths']['/provider/auth']
        with self.assertRaises(ValueError):
            check_contract(doc, catalog)

    def test_removed_native_provider_fails(self):
        doc, catalog = fixture()
        with self.assertRaises(ValueError):
            check_contract(doc, catalog, baseline_catalog={'all': [{'id': 'lost-provider'}]})
