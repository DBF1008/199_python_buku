#!/usr/bin/env python
# pylint: disable=wrong-import-order, ungrouped-imports
"""Server module."""
import collections
import random
import typing as T
from contextlib import contextmanager

import flask
from flask import current_app, redirect, request, url_for
from flask.views import MethodView
from werkzeug.exceptions import BadRequest
from flasgger import swag_from

import buku
from buku import BukuDb

try:
    from . import _
    from response import Response
    from forms import (TAG_RE, ApiBookmarkCreateForm, ApiBookmarkEditForm, ApiBookmarkRangeEditForm,
                       ApiBookmarkSearchForm, ApiTagForm, ApiFetchDataForm, ApiBookmarksReorderForm)
except ImportError:
    from bukuserver import _
    from bukuserver.response import Response
    from bukuserver.forms import (TAG_RE, ApiBookmarkCreateForm, ApiBookmarkEditForm, ApiBookmarkRangeEditForm,
                                  ApiBookmarkSearchForm, ApiTagForm, ApiFetchDataForm, ApiBookmarksReorderForm)


_parse_bool = lambda x: str(x).lower() == 'true'


def parse_random():
    """Parse/validate the optional ``?random=N`` query parameter.

    Validated before any DB read. Returns ``(count, error_response)`` where
    ``count`` is ``None`` when the parameter is absent (i.e. no sampling), and
    ``error_response`` is ``None`` unless the value is not a non-negative integer.
    """
    raw = request.args.get('random')
    if raw is None:
        return None, None
    try:
        count = int(raw)
        if count < 0:
            raise ValueError
    except (TypeError, ValueError):
        return None, Response.INPUT_NOT_VALID(
            data={'errors': {'random': [_('Must be a non-negative integer.')]}})
    return count, None


def pick_random(records, count):
    """Return up to ``count`` records chosen uniformly at random.

    The relative order of the input list is preserved (the selection is random,
    the ordering is not), so the subset stays consistent with any ``order`` that
    produced ``records``. ``count is None`` means no sampling (return all); a
    ``count`` larger than the result size returns every record.
    """
    if count is None:
        return records
    count = min(count, len(records))
    return [records[i] for i in sorted(random.sample(range(len(records)), count))]


def entity(bookmark, index=False):
    data = {
        'index': bookmark.id,
        'url': bookmark.url,
        'title': bookmark.title,
        'tags': bookmark.taglist,
        'description': bookmark.desc,
    }
    if not index:
        data.pop('index')
    return data


@contextmanager
def get_bukudb():
    """get bukudb instance"""
    bukudb = getattr(flask.g, 'bukudb', None)
    if bukudb:
        yield bukudb
    else:
        db_file = current_app.config.get('BUKUSERVER_DB_FILE', None)
        bukudb = BukuDb(dbfile=db_file)
        yield bukudb
        bukudb.close()


def search_tag(
    db: BukuDb, stag: T.Optional[str] = None, limit: T.Optional[int] = None
) -> T.Tuple[T.List[str], T.Dict[str, int]]:
    """search tag.

    db:
        buku db instance
    stag:
        search tag
    limit:
        positive integer limit

    Returns
    -------
    tuple
        list of unique tags sorted alphabetically and dictionary of tag and its usage count

    Raises
    ------
    ValueError
        if limit is not positive
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")
    tags: T.Set[str] = set()
    counter = collections.Counter()
    query_list = ["SELECT DISTINCT tags , COUNT(tags) FROM bookmarks"]
    if stag:
        query_list.append("where tags LIKE :search_tag")
    query_list.append("GROUP BY tags")
    row: T.Tuple[str, int]
    for row in db.cur.execute(" ".join(query_list), {"search_tag": f"%{stag}%"}):
        for tag in row[0].strip(buku.DELIM).split(buku.DELIM):
            if not tag:
                continue
            tags.add(tag)
            counter[tag] += row[1]
    return list(sorted(tags)), dict(counter.most_common(limit))


def _fetch_data(convert):
    form = ApiFetchDataForm(request.form)
    if not form.validate():
        return Response.INPUT_NOT_VALID(data={'errors': form.errors})
    try:
        return Response.SUCCESS(data=convert(buku.fetch_data(form.url.data)))
    except Exception as e:
        current_app.logger.debug(str(e))
        return Response.FAILURE()

@swag_from('./apidocs/network_handle/post.yml')
def handle_network():
    return _fetch_data(lambda x: {'title': x.title, 'description': x.desc, 'tags': x.keywords,
                                  'recognized mime': int(x.mime), 'bad url': int(x.bad)})

@swag_from('./apidocs/fetch_data/post.yml')
def fetch_data():
    return _fetch_data(lambda x: x._asdict())


@swag_from('./apidocs/bookmarks_refresh/post.yml', endpoint='bookmarks_refresh')
@swag_from('./apidocs/bookmark_refresh/post.yml', endpoint='bookmark_refresh')
def refresh_bookmark(index: T.Optional[int]):
    with get_bukudb() as bdb:
        if index and not bdb.get_rec_by_id(index):
            return Response.BOOKMARK_NOT_FOUND()
        result_flag = bdb.refreshdb(index or None, request.form.get('threads', 4))
        return Response.from_flag(result_flag)

@swag_from('./apidocs/bookmarks_reorder/post.yml')
def reorder_bookmarks():
    try:
        form = ApiBookmarksReorderForm(data=request.get_json())
    except BadRequest:
        return Response.INVALID_REQUEST()
    if not form.validate():
        return Response.invalid(form.errors)
    try:
        with get_bukudb() as bdb:
            bdb.reorder(form.order.data)
        return Response.SUCCESS()
    except Exception as e:
        current_app.logger.exception(str(e))
        return Response.FAILURE()


get_tiny_url = swag_from('./apidocs/tiny_url/get.yml')(lambda index: Response.REMOVED())


@swag_from('./apidocs/tags/get.yml')
def get_all_tags():
    with get_bukudb() as bdb:
        return Response.SUCCESS(data={"tags": search_tag(db=bdb, limit=5)[0]})

class ApiTagView(MethodView):
    def get(self, tag: str):
        with get_bukudb() as bukudb:
            if not TAG_RE.match(tag):
                return Response.TAG_NOT_VALID()
            tag = tag.lower().strip()
            tags = search_tag(db=bukudb, stag=tag)
            return (Response.TAG_NOT_FOUND() if tag not in tags[1] else
                    Response.SUCCESS(data={"name": tag, "usage_count": tags[1][tag]}))

    def put(self, tag: str):
        if not TAG_RE.match(tag):
            return Response.TAG_NOT_VALID()
        try:
            form = ApiTagForm(data=request.get_json())
        except BadRequest:
            return Response.INVALID_REQUEST()
        if not form.validate():
            return Response.invalid(form.errors)
        with get_bukudb() as bukudb:
            tag = tag.lower().strip()
            tags = search_tag(db=bukudb, stag=tag)
            if tag not in tags[1]:
                return Response.TAG_NOT_FOUND()
            try:
                bukudb.replace_tag(tag, form.tags.data)
                return Response.SUCCESS()
            except (ValueError, RuntimeError):
                return Response.FAILURE()

    def delete(self, tag: str):
        if not TAG_RE.match(tag):
            return Response.TAG_NOT_VALID()
        with get_bukudb() as bukudb:
            tag = tag.lower().strip()
            tags = search_tag(db=bukudb, stag=tag)
            return (Response.TAG_NOT_FOUND() if tag not in tags[1] else
                    Response.from_flag(bukudb.delete_tag_at_index(0, tag, chatty=False)))


class ApiBookmarksView(MethodView):
    def get(self):
        count, error = parse_random()
        if error:
            return error
        with get_bukudb() as bukudb:
            order = request.args.getlist('order')
            all_bookmarks = pick_random(bukudb.get_rec_all(order=order), count)
            return Response.SUCCESS(data={'bookmarks': [entity(bookmark, index=order)
                                                        for bookmark in all_bookmarks]})

    def post(self):
        try:
            form = ApiBookmarkCreateForm(data=request.get_json())
        except BadRequest:
            return Response.INVALID_REQUEST()
        if not form.validate():
            return Response.invalid(form.errors)
        with get_bukudb() as bukudb:
            index = bukudb.add_rec(
                form.url.data,
                form.title.data,
                form.tags_str,
                form.description.data,
                fetch=form.fetch.data)
            return Response.from_flag(index is not None, data=index and {'index': index})

    def delete(self):
        with get_bukudb() as bukudb:
            return Response.from_flag(bukudb.cleardb(confirm=False))


class ApiBookmarkView(MethodView):
    def get(self, index: int):
        with get_bukudb() as bukudb:
            bookmark = bukudb.get_rec_by_id(index)
            return (Response.BOOKMARK_NOT_FOUND() if bookmark is None else
                    Response.SUCCESS(data=entity(bookmark)))

    def put(self, index: int):
        try:
            form = ApiBookmarkEditForm(data=request.get_json())
        except BadRequest:
            return Response.INVALID_REQUEST()
        if not form.validate():
            return Response.invalid(form.errors)
        with get_bukudb() as bukudb:
            if not bukudb.get_rec_by_id(index):
                return Response.BOOKMARK_NOT_FOUND()
            if not form.has_data:
                return Response.SUCCESS()  # noop
            success = bukudb.update_rec(index, url=form.url.data, title_in=form.title.data,
                                        tags_in=form.tags_str, desc=form.description.data)
            return Response.from_flag(success)

    def delete(self, index: int):
        with get_bukudb() as bukudb:
            return (Response.BOOKMARK_NOT_FOUND() if not bukudb.get_rec_by_id(index) else
                    Response.from_flag(bukudb.delete_rec(index, retain_order=True)))


class ApiBookmarkRangeView(MethodView):
    def get(self, start_index: int, end_index: int):
        with get_bukudb() as bukudb:
            max_index = bukudb.get_max_id() or 0
            if start_index > end_index or end_index > max_index:
                return Response.RANGE_NOT_VALID()
            result = {'bookmarks': {index: entity(bukudb.get_rec_by_id(index))
                                    for index in range(start_index, end_index + 1)}}
            return Response.SUCCESS(data=result)

    def put(self, start_index: int, end_index: int):
        with get_bukudb() as bukudb:
            max_index = bukudb.get_max_id() or 0
            if start_index > end_index or end_index > max_index:
                return Response.RANGE_NOT_VALID()
            updates = []
            errors = {}
            for index in range(start_index, end_index + 1):
                try:
                    json = request.get_json().get(str(index))
                except BadRequest:
                    return Response.INVALID_REQUEST()
                if json is None:
                    errors[index] = _('Input required.')
                    continue
                form = ApiBookmarkRangeEditForm(data=json)
                if not form.validate():
                    errors[index] = form.errors
                elif form.has_data:
                    updates += [{'index': index,
                                 'url': form.url.data,
                                 'title_in': form.title.data,
                                 'tags_in': form.tags_in,
                                 'desc': form.description.data}]
            if errors:
                return Response.invalid(errors)
            for update in updates:
                if not bukudb.update_rec(**update):
                    return Response.FAILURE(data={'index': update['index']})
            return Response.SUCCESS()

    def delete(self, start_index: int, end_index: int):
        with get_bukudb() as bukudb:
            max_index = bukudb.get_max_id() or 0
            if start_index > end_index or end_index > max_index:
                return Response.RANGE_NOT_VALID()
            result_flag = bukudb.delete_rec(None, start_index, end_index, is_range=True, retain_order=True)
            return Response.from_flag(result_flag)


class ApiBookmarkSearchView(MethodView):
    def get(self):
        form = ApiBookmarkSearchForm(request.args)
        if not form.validate():
            return Response.INPUT_NOT_VALID(data={'errors': form.errors})
        count, error = parse_random()
        if error:
            return error
        with get_bukudb() as bukudb:
            records = pick_random(bukudb.searchdb(**form.data), count)
            result = [entity(bookmark, index=True) for bookmark in records]
            current_app.logger.debug('total bookmarks:{}'.format(len(result)))
            return Response.SUCCESS(data={'bookmarks': result})

    def delete(self):
        form = ApiBookmarkSearchForm(request.form)
        if not form.validate():
            return Response.INPUT_NOT_VALID(data={'errors': form.errors})
        with get_bukudb() as bukudb:
            deleted, failed, indices = 0, 0, {x.id for x in bukudb.searchdb(**form.data)}
            current_app.logger.debug('total bookmarks:{}'.format(len(indices)))
            for index in sorted(indices, reverse=True):
                if bukudb.delete_rec(index, retain_order=True):
                    deleted += 1
                else:
                    failed += 1
            return Response.from_flag(failed == 0, data={'deleted': deleted}, errors={'failed': failed})


def bookmarklet_redirect():
    url = request.args.get('url')
    title = request.args.get('title')
    description = request.args.get('description')
    tags = request.args.get('tags')
    fetch = request.args.get('fetch')

    with get_bukudb() as bukudb:
        rec_id = bukudb.get_rec_id(url)
        goto = (url_for('bookmark.edit_view', id=rec_id, popup=True) if rec_id else
                url_for('bookmark.create_view', link=url, title=title, description=description, tags=tags, fetch=fetch, popup=True))
        return redirect(goto)
