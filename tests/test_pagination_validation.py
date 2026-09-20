import pytest
from urllib.parse import parse_qsl

from tests.seed_data import seed_tag


@pytest.mark.parametrize('endpoint', [
    '/documents', '/search?keyword=Doc',
    '/tags/Python/documents', '/categories/General/documents',
])
@pytest.mark.parametrize('params', [{'limit': -1}, {'offset': -1}, {'limit': 1, 'offset': -1}])
def test_negative_pagination_rejected(client, endpoint, params):
    path, _, query = endpoint.partition('?')
    response = client.get(path, params={**dict(parse_qsl(query)), **params})
    assert response.status_code == 422
    assert response.json()['detail'][0]['loc'][0] == 'query'


@pytest.mark.parametrize('endpoint', [
    '/documents', '/search?keyword=Doc', '/search?keyword=Python&search_type=tag',
    '/tags/Python/documents', '/categories/General/documents',
])
def test_pagination_boundaries(client, admin_headers, endpoint):
    headers, _ = admin_headers
    seed_tag('Python')
    assert client.post('/categories', json={'name': 'General'}, headers=headers).status_code == 200
    for title in ['Doc1', 'Doc2']:
        assert client.post('/documents', json={
            'title': title, 'content': 'body',
            'category': {'name': 'General'}, 'tags': [{'name': 'Python'}],
        }, headers=headers).status_code == 200

    def get_page(params):
        path, _, query = endpoint.partition('?')
        response = client.get(path, params={**dict(parse_qsl(query)), **params})
        assert response.status_code == 200
        return response.json()

    assert len(get_page({})) == 2
    assert get_page({'limit': 0}) == []
    assert len(get_page({'limit': 1, 'offset': 1})) == 1
    assert get_page({'limit': 1, 'offset': 2}) == []
    assert len(get_page({'offset': 2})) == 2
