"""Contract checks against the pinned engine's actual /doc, not a latest SDK."""
from agent_runtime import ENGINE_VERSION, PROVIDER_ID, MODEL_ID

ROUTES = {
    '/global/health': 'get', '/provider': 'get', '/provider/auth': 'get',
    '/provider/{providerID}/oauth/authorize': 'post',
    '/provider/{providerID}/oauth/callback': 'post', '/auth/{providerID}': 'put',
    '/session': 'post', '/session/status': 'get',
    '/session/{sessionID}/message': 'get', '/session/{sessionID}/prompt_async': 'post',
    '/session/{sessionID}/abort': 'post', '/question': 'get',
    '/question/{requestID}/reply': 'post', '/permission': 'get',
    '/permission/{requestID}/reply': 'post', '/mcp': 'get', '/event': 'get',
}


def check_contract(doc, catalog, *, baseline_catalog=None):
    failures = []
    for path, method in ROUTES.items():
        if method not in doc.get('paths', {}).get(path, {}):
            failures.append(f'{method.upper()} {path}')
    def properties(path, method):
        return doc.get('paths', {}).get(path, {}).get(method, {}).get('requestBody', {}).get(
            'content', {}).get('application/json', {}).get('schema', {}).get('properties', {})
    if not {'agent', 'model', 'title'}.issubset(properties('/session', 'post')):
        failures.append('session.create.body')
    if not {'parts', 'model', 'messageID'}.issubset(properties('/session/{sessionID}/prompt_async', 'post')):
        failures.append('session.prompt_async.body')
    if 'method' not in properties('/provider/{providerID}/oauth/authorize', 'post'):
        failures.append('provider.oauth.method')
    if 'answers' not in properties('/question/{requestID}/reply', 'post'):
        failures.append('question.reply.answers')
    ids = {p.get('id') for p in catalog.get('all', [])}
    qwen = next((p for p in catalog.get('all', []) if p.get('id') == PROVIDER_ID), {})
    if MODEL_ID not in qwen.get('models', {}):
        failures.append('qwen.preset')
    if baseline_catalog:
        missing = {p.get('id') for p in baseline_catalog.get('all', [])} - ids
        if missing:
            failures.append('provider.catalog.removed')
    if failures:
        raise ValueError('OpenCode contract changed: ' + ', '.join(failures))
    return {'engine_version': ENGINE_VERSION, 'routes_checked': len(ROUTES), 'provider_count': len(ids)}
