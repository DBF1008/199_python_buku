import os
from typing import Any, Dict
from http import HTTPStatus
import pytest
import flask
from click.testing import CliRunner
from buku import FetchResult
from bukuserver import server
from bukuserver.response import Response
from bukuserver.server import get_bool_from_env_var
from tests.util import mock_fetch


def assert_response(response, exp_res: Response, data: Dict[str, Any] = None):
    assert response.status_code == exp_res.status_code
    assert response.get_json() == exp_res.json(data=data)


@pytest.mark.parametrize(
    'data, exp_json', [
        [None, {'status': 0, 'message': 'Success.'}],
        [{}, {'status': 0, 'message': 'Success.'}],
        [{'key': 'value'}, {'status': 0, 'message': 'Success.', 'key': 'value'}],
    ]
)
def test_response_json(data, exp_json):
    assert Response.SUCCESS.json(data=data) == exp_json


@pytest.mark.parametrize(
    'args,word',
    [
        ('--help', 'bukuserver'),
        ('--version', 'buku')
    ]
)
def test_cli(args, word):
    runner = CliRunner()
    result = runner.invoke(server.cli, [args])
    assert result.exit_code == 0
    assert word in result.output


@pytest.fixture
def client(tmp_path):
    test_db = tmp_path / 'test.db'
    app = server.create_app(test_db.as_posix())
    client = app.test_client()
    yield client
    flask.g.bukudb.close()
    os.remove(test_db)


def test_home(client):
    rd = client.get('/')
    assert rd.status_code == 200
    assert not flask.g.bukudb.get_rec_all()


@pytest.mark.parametrize('method, url, exp_res, data', [
    ('get', '/api/tags', Response.SUCCESS, {'tags': []}),
    ('get', '/api/bookmarks', Response.SUCCESS, {'bookmarks': []}),
    ('get', '/api/bookmarks/search?keywords=x', Response.SUCCESS, {'bookmarks': []}),
    ('post', '/api/bookmarks/refresh', Response.FAILURE, None),
])
def test_api_empty_db(client, method, url, exp_res, data):
    rd = getattr(client, method)(url)
    assert_response(rd, exp_res, data)


@pytest.mark.parametrize('url, methods', [
    ('api/tags', ['post', 'put', 'delete']),
    ('/api/tags/tag1', ['post']),
    ('api/bookmarks', ['put']),
    ('/api/bookmarks/1', ['post']),
    ('/api/bookmarks/refresh', ['get', 'put', 'delete']),
    ('api/bookmarks/1/refresh', ['get', 'put', 'delete']),
    ('/api/bookmarks/1/2', ['post']),
])
def test_api_not_allowed(client, url, methods):
    for method in methods:
        rd = getattr(client, method)(url)
        assert rd.status_code == HTTPStatus.METHOD_NOT_ALLOWED.value


@pytest.mark.parametrize('method, url, json, exp_res', [
    ('get', '/api/tags/tag1', None, Response.TAG_NOT_FOUND),
    ('put', '/api/tags/tag1', {'tags': ['tag2']}, Response.TAG_NOT_FOUND),
    ('delete', '/api/tags/tag1', None, Response.TAG_NOT_FOUND),
    ('get', '/api/tags/tag1,tag2', None, Response.TAG_NOT_VALID),
    ('put', '/api/tags/tag1,tag2', {'tags': ['tag2']}, Response.TAG_NOT_VALID),
    ('delete', '/api/tags/tag1,tag2', None, Response.TAG_NOT_VALID),
    ('get', '/api/bookmarks/1', None, Response.BOOKMARK_NOT_FOUND),
    ('put', '/api/bookmarks/1', {'title': 'none'}, Response.BOOKMARK_NOT_FOUND),
    ('delete', '/api/bookmarks/1', None, Response.BOOKMARK_NOT_FOUND),
    ('post', '/api/bookmarks/1/refresh', None, Response.BOOKMARK_NOT_FOUND),
    ('get', '/api/bookmarks/1/2', None, Response.RANGE_NOT_VALID),
    ('put', '/api/bookmarks/1/2', {1: {'title': 'one'}, 2: {'title': 'two'}}, Response.RANGE_NOT_VALID),
    ('delete', '/api/bookmarks/1/2', None, Response.RANGE_NOT_VALID),
])
def test_api_invalid_id(client, method, url, json, exp_res):
    rd = getattr(client, method)(url, json=json)
    assert_response(rd, exp_res)


def test_api_tag(client):
    url = 'http://google.com'
    with mock_fetch(title='Google'):
        rd = client.post('/api/bookmarks', json={'url': url, 'tags': ['tag1', 'TAG2'], 'fetch': True})
    assert_response(rd, Response.SUCCESS, {'index': 1})
    rd = client.get('/api/tags')
    assert_response(rd, Response.SUCCESS, {'tags': ['tag1', 'tag2']})
    rd = client.get('/api/tags/tag1')
    assert_response(rd, Response.SUCCESS, {'name': 'tag1', 'usage_count': 1})
    rd = client.put('/api/tags/tag1', json={'tags': 'string'})
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': {'tags': ['Invalid input.']}})
    for json in [{}, {'tags': None}, {'tags': ''}, {'tags':[]}]:
        rd = client.put('/api/tags/tag1', json={'tags': []})
        assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': {'tags': ['This field is required.']}})
    rd = client.put('/api/tags/tag1', json={'tags': ['ok', '', None]})
    errors = {'tags': [[], ['Invalid input.'], ['The value must be a string.']]}
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': errors})
    rd = client.put('/api/tags/tag1', json={'tags': ['one,two', 3,]})
    errors = {'tags': [['Invalid input.'], ['The value must be a string.']]}
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': errors})
    rd = client.put('/api/tags/tag1', json={'tags': ['tag3', 'TAG 4']})
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/tags')
    assert_response(rd, Response.SUCCESS, {'tags': ['tag 4', 'tag2', 'tag3']})
    rd = client.put('/api/tags/tag 4', json={'tags': ['tag5']})
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/tags')
    assert_response(rd, Response.SUCCESS, {'tags': ['tag2', 'tag3', 'tag5']})
    rd = client.delete('/api/tags/tag3')
    assert_response(rd, Response.SUCCESS)
    rd = client.delete('/api/tags/tag3')
    assert_response(rd, Response.TAG_NOT_FOUND)
    rd = client.delete('/api/tags/tag,2')
    assert_response(rd, Response.TAG_NOT_VALID)
    rd = client.get('/api/bookmarks/1')
    assert_response(rd, Response.SUCCESS, {'description': '', 'tags': ['tag2', 'tag5'], 'title': 'Google', 'url': url})


def test_api_tag_search(client):
    """Test GET /api/tags with query parameters: q, limit, with_usage_count."""
    # Set up bookmarks with various tags for search testing
    for i, (url, tags) in enumerate([
        ('http://a.com', ['python', 'flask', 'web']),
        ('http://b.com', ['python', 'django', 'web']),
        ('http://c.com', ['python', 'fastapi', 'api']),
        ('http://d.com', ['javascript', 'react', 'web']),
        ('http://e.com', ['rust', 'systems']),
        ('http://f.com', ['go', 'systems']),
    ], start=1):
        with mock_fetch(title=f'Page {i}'):
            rd = client.post('/api/bookmarks', json={'url': url, 'tags': tags, 'fetch': True})
        assert_response(rd, Response.SUCCESS, {'index': i})

    # Default behavior: no params, returns up to 5 tags alphabetically (backward compatible)
    rd = client.get('/api/tags')
    assert_response(rd, Response.SUCCESS, {'tags': ['api', 'django', 'fastapi', 'flask', 'go']})

    # Filter by keyword 'py'
    rd = client.get('/api/tags', query_string={'q': 'py'})
    assert_response(rd, Response.SUCCESS, {'tags': ['python']})

    # Filter by keyword 'web'
    rd = client.get('/api/tags', query_string={'q': 'web'})
    assert_response(rd, Response.SUCCESS, {'tags': ['web']})

    # Filter with limit
    rd = client.get('/api/tags', query_string={'q': '', 'limit': 3})
    assert_response(rd, Response.SUCCESS, {'tags': ['api', 'django', 'fastapi']})

    # Increase limit beyond total tag count
    rd = client.get('/api/tags', query_string={'limit': 100})
    assert rd.status_code == 200
    tags = rd.get_json()['tags']
    assert len(tags) == 11  # all unique tags

    # Invalid limit falls back to 5
    rd = client.get('/api/tags', query_string={'limit': 'abc'})
    assert_response(rd, Response.SUCCESS, {'tags': ['api', 'django', 'fastapi', 'flask', 'go']})

    # With usage_count
    rd = client.get('/api/tags', query_string={'with_usage_count': 'true', 'limit': 5})
    assert rd.status_code == 200
    data = rd.get_json()
    tags_data = data['tags']
    assert len(tags_data) == 5
    # python is used in 3 bookmarks, web in 3 bookmarks - they should be at the top
    assert tags_data[0]['name'] == 'python'
    assert tags_data[0]['usage_count'] == 3
    assert tags_data[1]['name'] == 'web'
    assert tags_data[1]['usage_count'] == 3
    # Each item should have name and usage_count keys
    for item in tags_data:
        assert 'name' in item
        assert 'usage_count' in item
        assert isinstance(item['usage_count'], int)

    # With usage_count and keyword filter
    rd = client.get('/api/tags', query_string={'q': 's', 'with_usage_count': 'true', 'limit': 10})
    assert rd.status_code == 200
    data = rd.get_json()
    tag_names = [t['name'] for t in data['tags']]
    # 'systems' (used 2x), 'flask' (used 1x), 'fastapi' (used 1x), 'javascript' (used 1x), 'react' (used 1x)
    # 'systems' should be first due to highest usage
    assert tag_names[0] == 'systems'
    assert data['tags'][0]['usage_count'] == 2

    # Empty keyword should return all tags (no filter)
    rd = client.get('/api/tags', query_string={'q': '', 'limit': 2})
    assert_response(rd, Response.SUCCESS, {'tags': ['api', 'django']})

    # Non-matching keyword returns empty list
    rd = client.get('/api/tags', query_string={'q': 'zzzzz'})
    assert_response(rd, Response.SUCCESS, {'tags': []})


def test_api_bookmark(client):
    url = 'http://google.com'
    rd = client.post('/api/bookmarks', json={})
    errors = {'url': ['This field is required.']}
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': errors})
    with mock_fetch(title='Google'):
        rd = client.post('/api/bookmarks', json={'url': url, 'fetch': True})
        assert_response(rd, Response.SUCCESS, {'index': 1})
        rd = client.post('/api/bookmarks', json={'url': url, 'fetch': True})
        assert_response(rd, Response.FAILURE)
    rd = client.get('/api/bookmarks')
    assert_response(rd, Response.SUCCESS, {'bookmarks': [{'description': '', 'tags': [], 'title': 'Google', 'url': url}]})
    rd = client.get('/api/bookmarks/1')
    assert_response(rd, Response.SUCCESS, {'description': '', 'tags': [], 'title': 'Google', 'url': url})
    rd = client.put('/api/bookmarks/1', json={'tags': 'not a list'})
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': {'tags': ['Invalid input.']}})
    rd = client.put('/api/bookmarks/1', json={'tags': ['tag1', 'tag2']})
    assert_response(rd, Response.SUCCESS)
    with mock_fetch(title='Google'):
        rd = client.put('/api/bookmarks/1', json={'fetch': True})
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks/1')
    assert_response(rd, Response.SUCCESS, {'description': '', 'tags': ['tag1', 'tag2'], 'title': 'Google', 'url': url})
    rd = client.put('/api/bookmarks/1', json={'tags': [], 'description': 'Description'})
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks/1')
    assert_response(rd, Response.SUCCESS, {'description': 'Description', 'tags': [], 'title': 'Google', 'url': url})


@pytest.mark.parametrize('d_url', ['/api/bookmarks', '/api/bookmarks/1'])
def test_api_bookmark_delete(client, d_url):
    url = 'http://google.com'
    rd = client.post('/api/bookmarks', json={'url': url, 'fetch': False})
    assert_response(rd, Response.SUCCESS, {'index': 1})
    rd = client.delete(d_url)
    assert_response(rd, Response.SUCCESS)


@pytest.mark.parametrize('api_url', ['/api/bookmarks/refresh', '/api/bookmarks/1/refresh'])
def test_api_bookmark_refresh(client, api_url):
    url = 'http://google.com'
    with mock_fetch(title='Google'):
        rd = client.post('/api/bookmarks', json={'url': url})
        assert_response(rd, Response.SUCCESS, {'index': 1})
        rd = client.post(api_url)
        assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks/1')
    assert_response(rd, Response.SUCCESS, {'description': '', 'tags': [], 'title': 'Google', 'url': url})


@pytest.mark.parametrize('kwargs, kwmock, exp_res, data', [
    (
        {'data': {'url': 'http://google.com'}},
        {'title': 'Google', 'fetch_status': 200},
        Response.SUCCESS,
        {'bad url': 0, 'recognized mime': 0, 'tags': '', 'title': 'Google'}
    ),
    ({}, {}, Response.INPUT_NOT_VALID, {'errors': {'url': ['This field is required.']}}),
    (
        {'data': {'url': 'chrome://bookmarks/'}},
        {'bad': True},
        Response.SUCCESS,
        {'bad url': 1, 'recognized mime': 0, 'tags': '', 'title': ''}
    ),
])
@pytest.mark.parametrize('endpoint', ['/api/fetch_data', '/api/network_handle'])
def test_api_fetch_data(client, endpoint, kwargs, kwmock, exp_res, data):
    with mock_fetch(**kwmock):
        rd = client.post(endpoint, **kwargs)
    assert rd.status_code == exp_res.status_code
    rd_json = rd.get_json()
    rd_json.pop('description', None)
    if endpoint == '/api/fetch_data' and exp_res is Response.SUCCESS:
        data = FetchResult(kwargs['data']['url'], **kwmock)._asdict()
    assert rd_json == exp_res.json(data=data)


def test_api_bookmark_range(client):
    bookmarks = [('http://google.com', 'Google'),
                 ('http://example.com', 'Example Domain')]
    for index, (url, title) in enumerate(bookmarks, start=1):
        with mock_fetch(title=title):
            rd = client.post('/api/bookmarks', json={'url': url, 'fetch': True})
        assert_response(rd, Response.SUCCESS, {'index': index})

    rd = client.put('/api/bookmarks/1/2', json={
        '1': {'tags': ['tag1 A', 'tag1 B', 'tag1 C']},
        '2': {'tags': ['tag2']}
    })
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks/1/2')
    assert_response(rd, Response.SUCCESS, {'bookmarks': {
        '1': {'description': '', 'tags': ['tag1 a', 'tag1 b', 'tag1 c'], 'title': 'Google', 'url': 'http://google.com'},
        '2': {'description': '', 'tags': ['tag2',], 'title': 'Example Domain', 'url': 'http://example.com'}}})
    rd = client.put('/api/bookmarks/1/2', json={
        '1': {'title': 'Bookmark 1', 'tags': ['tag1 C', 'tag1 A'], 'del_tags': True},
        '2': {'title': 'Bookmark 2', 'tags': ['-', 'tag2'], 'del_tags': False}
    })
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks/1/2')
    assert_response(rd, Response.SUCCESS, {'bookmarks': {
        '1': {'description': '', 'tags': ['tag1 b'], 'title': 'Bookmark 1', 'url': 'http://google.com'},
        '2': {'description': '', 'tags': ['-', 'tag2',], 'title': 'Bookmark 2', 'url': 'http://example.com'}}})

    rd = client.put('/api/bookmarks/2/1', json={})
    assert_response(rd, Response.RANGE_NOT_VALID)

    rd = client.put('/api/bookmarks/1/2', json={})
    assert_response(rd, Response.INPUT_NOT_VALID, data={
        'errors': {
            '1': 'Input required.',
            '2': 'Input required.'
        }
    })
    rd = client.put('/api/bookmarks/1/2', json={'1': {'tags': []}})
    assert_response(rd, Response.INPUT_NOT_VALID, data={'errors': {'2': 'Input required.'}})
    rd = client.put('/api/bookmarks/1/2', json={
        '1': {'tags': ['ok', 'with,delim']},
        '2': {'tags': 'string'},
    })
    assert_response(rd, Response.INPUT_NOT_VALID, data={
        'errors': {
            '1': {'tags': [[], ['Invalid input.']]},
            '2': {'tags': ['Invalid input.']}
        }
    })
    rd = client.get('/api/bookmarks/2/1')
    assert_response(rd, Response.RANGE_NOT_VALID)
    rd = client.delete('/api/bookmarks/1/2')
    assert_response(rd, Response.SUCCESS)
    rd = client.get('/api/bookmarks')
    assert_response(rd, Response.SUCCESS, {'bookmarks': []})


def test_api_bookmark_search(client):
    with mock_fetch(title='Google'):
        rd = client.post('/api/bookmarks', json={'url': 'http://google.com', 'fetch': True})
    assert_response(rd, Response.SUCCESS, {'index': 1})
    rd = client.get('/api/bookmarks/search', query_string={'keywords': ['google']})
    assert_response(rd, Response.SUCCESS, {'bookmarks': [
        {'description': '', 'index': 1, 'tags': [], 'title': 'Google', 'url': 'http://google.com'}]})
    rd = client.delete('/api/bookmarks/search', data={'keywords': ['google']})
    assert_response(rd, Response.SUCCESS, {'deleted': 1})
    rd = client.get('/api/bookmarks')
    assert_response(rd, Response.SUCCESS, {'bookmarks': []})


@pytest.mark.parametrize('env_val, exp_val', [
    ['true', True],
    ['false', False],
    ['0', False],
    ['1', True],
    [None, True],
    ['random', True]
])
def test_get_bool_from_env_var(monkeypatch, env_val, exp_val):
    key = 'BUKUSERVER_TEST'
    if env_val is not None:
        monkeypatch.setenv(key, env_val)
    assert get_bool_from_env_var(key, True) == exp_val
