from dataclasses import replace
from datetime import date, datetime, timezone
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from ic_feed import collect
from ic_feed.http import HttpResult
from ic_feed.sources import conferences
from ic_feed.state import FeedState, SourceContinuation, load_state, save_state
from ic_feed.venues import match_venue
from test_domain import record


def test_2026_edition_includes_asplos_published_in_2025():
    item = replace(record('RTL microarchitecture'), doi='10.1145/3760250.3762220',
        journal='Proceedings of the 31st ACM International Conference on Architectural Support for Programming Languages and Operating Systems, Volume 1',
        published_at=datetime(2025, 12, 11, tzinfo=timezone.utc))
    assert conferences.edition_accepts(conferences.get_edition('asplos-v1-2026'), item)
    assert not conferences.edition_accepts(conferences.get_edition('asplos-v2-2026'), item)
    assert not conferences.edition_accepts(conferences.get_edition('asplos-v1-2026'), replace(item, journal=item.journal + ' Companion'))


def test_verified_ieee_jsap_vlsi_wrapper():
    item = replace(record('Digital accelerator circuit'),
        journal='2026 IEEE/JSAP Symposium on VLSI Technology and Circuits (VLSI Technology and Circuits)',
        doi='10.1109/vlsitechnologyandcir65830.2026.11577475')
    assert match_venue(item).id == 'vlsi'
    assert match_venue(replace(item, journal='Regional JSAP ' + item.journal)) is None


def datacite_item(index, **overrides):
    attrs = {'doi': f'10.34727/2026/isbn.978-3-85448-093-8_{index}',
        'titles': [{'title': 'RTL hardware verification with SAT'}],
        'types': {'resourceType': 'Inproceedings'}, 'publicationYear': 2026,
        'dates': [{'date': '2026-09', 'dateType': 'Issued'}],
        'creators': [{'givenName': 'Alice', 'familyName': 'Example'}],
        'descriptions': [{'descriptionType': 'Abstract', 'description': 'Formal verification of RTL hardware.'}],
        'url': f'https://repositum.tuwien.at/handle/20.500.12708/{index}'}
    attrs.update(overrides)
    return {'type': 'dois', 'attributes': attrs}


def test_fmcad_datacite_parse_requires_exact_child_and_main_paper():
    payload = {'data': [datacite_item(i) for i in range(1, 7)] + [
        datacite_item(9, doi='10.34727/2025/isbn.978-3-85448-093-8_9'),
        datacite_item(10, titles=[{'title': 'Invited Talk: hardware checking'}])],
        'meta': {'total': 8, 'totalPages': 1}, 'links': {}}
    page = conferences.parse_page('fmcad-2026', json.dumps(payload).encode())
    assert page.item_count == 8
    assert [r.doi for r in page.records] == ['10.34727/2026/isbn.978-3-85448-093-8_6']
    assert page.records[0].authors == ['Alice Example']
    assert page.records[0].published_at.date() == date(2026, 9, 1)
    assert page.records[0].abstract == 'Formal verification of RTL hardware.'
    assert page.next_cursor is None


def test_fmcad_positive_total_missing_data_is_failure():
    with pytest.raises(ValueError):
        conferences.parse_page('fmcad-2026', b'{"meta":{"total":76},"data":[]}')


def test_conference_catalog_has_only_approved_2026_editions():
    editions = conferences.load_editions()
    assert {e.venue for e in editions} == {'dac','iccad','fmcad','cav','isca','micro','hpca','asplos','isscc','vlsi'}
    assert {e.year for e in editions} == {2026}


def test_new_source_continuations_roundtrip(tmp_path):
    progress = SourceContinuation(date(2025,1,1),json.dumps({'start_record':301,'until_date':'2026-10-07','total_records':450}))
    state = FeedState(source_continuations={'ieee:tc':progress,
        'conference:asplos-v2-2026':SourceContinuation(date(2026,1,1),'opaque-crossref-cursor')})
    path = tmp_path/'state.json'
    save_state(path,state)
    assert load_state(path).source_continuations == state.source_continuations


def test_ieee_pagination_pins_window_and_resumes_at_unread_record(monkeypatch):
    from ic_feed.sources import ieee
    requests = []
    def request_page(query, start, end, offset, size, key, **kwargs):
        requests.append((start, end, offset, size))
        return ieee.IeeePage([], size, 450)
    monkeypatch.setattr(ieee, 'request_page', request_page)
    first = collect._collect_ieee('journal:0018-9340', date(2026,9,7), date(2026,10,7), 300, 'fixture', None)
    assert not first.complete and first.continuation is not None
    assert [r[2:] for r in requests] == [(1,200),(201,100)]
    second = collect._collect_ieee('journal:0018-9340', date(2026,10,8), date(2026,10,8), 300, 'fixture', first.continuation)
    assert requests[-1] == (date(2026,9,7), date(2026,10,7),301,150)
    assert second.complete
    assert second.completed_through == date(2026,10,7)


def test_ieee_failure_preserves_prior_page_and_safe_continuation(monkeypatch):
    from ic_feed.sources import ieee
    good = replace(record('RTL accelerator design'), journal='IEEE Transactions on Computers')
    def request_page(query, start, end, offset, size, key, **kwargs):
        if offset == 1:
            return ieee.IeeePage([good],200,450)
        raise ieee.IeeeFetchError('http_503','IEEE HTTP 503')
    monkeypatch.setattr(ieee, 'request_page', request_page)
    result = collect._collect_ieee('journal:0018-9340',date(2026,9,7),date(2026,10,7),300,'secret-fixture',None)
    assert result.records == [good] and not result.complete
    assert json.loads(result.continuation.cursor)['start_record'] == 201
    assert 'secret-fixture' not in repr(result)


def test_asplos_query_uses_verified_publication_day_not_2026_date_cutoff():
    params = parse_qs(urlparse(conferences.build_url('asplos-v1-2026',date(2026,9,7),300)).query)
    assert params['filter'] == ['prefix:10.1145,type:proceedings-article,from-pub-date:2025-12-11,until-pub-date:2025-12-11']
    assert params['cursor'] == ['*']
    assert 'query.container-title' not in params


def test_cav_excludes_only_verified_invited_chapter_not_other_volume_first_papers():
    edition = conferences.get_edition('cav-i-2026')
    item = replace(record('Formal hardware verification'),journal='Computer Aided Verification')
    assert not conferences.edition_accepts(edition,replace(item,doi='10.1007/978-3-032-32519-8_1'))
    assert conferences.edition_accepts(edition,replace(item,doi='10.1007/978-3-032-32526-6_1'))
    assert conferences.edition_accepts(edition,replace(item,doi='10.1007/978-3-032-32537-2_1'))


def test_ieee_admits_verified_title_variants_but_not_other_editions_or_workshops():
    edition = conferences.get_edition('isca-2026')
    item = replace(record('RISC-V architecture'),journal='2026 IEEE/ACM International Symposium on Computer Architecture (ISCA)')
    assert conferences.edition_accepts(edition,item)
    assert not conferences.edition_accepts(edition,replace(item,journal=item.journal.replace('2026','2025')))
    assert not conferences.edition_accepts(edition,replace(item,journal=item.journal+' Workshops'))


def test_conference_protocol_failure_is_retried_before_empty_success(monkeypatch):
    bodies = iter([b'{}',b'{"message":{"items":[],"total-results":0}}'])
    calls = []
    def fetch(url):
        calls.append(url)
        return HttpResult(next(bodies),200,url)
    harvest = collect._collect_scholarly('conference','cav-i-2026',date(2026,9,7),fetch,300)
    assert len(calls) == 2 and harvest.complete and harvest.failure is None


def test_collection_advances_ieee_only_to_frozen_window_and_preserves_history(tmp_path,monkeypatch):
    from pathlib import Path
    root = Path(__file__).parents[1]
    sources = tmp_path/'sources.tsv'
    sources.write_text('name\turl\nIEEE\thttps://ieeexplore.ieee.org/rss/TOC12.XML\n',encoding='utf-8')
    queries = tmp_path/'scholarly.json'
    queries.write_text(json.dumps({'version':1,'queries':[{'id':'tc','source':'ieee','query':'journal:0018-9340','limit':300}]}),encoding='utf-8')
    state_path = tmp_path/'state.json'
    known = replace(record('RISC-V accelerator'),journal='IEEE Transactions on Computers',ai_relevant=True,summary_zh='Existing validated summary')
    from ic_feed.normalize import record_key
    key = record_key(known)
    save_state(state_path,FeedState(papers={key:known}))
    monkeypatch.setenv('IEEE_API_KEY','fixture-private-key')
    def harvest(*args,**kwargs):
        return collect.ScholarlyHarvest('ieee',[],None,None,True,1,date(2026,10,7))
    monkeypatch.setattr(collect,'_collect_ieee',harvest)
    monkeypatch.setattr(collect,'collect_rss',lambda *_: pytest.fail('blocked redundant RSS must not be requested'))
    result = collect.main(['--config',str(root/'paper_feed_config.json'),'--state',str(state_path),'--sources',str(sources),
        '--queries',str(root/'config/queries.json'),'--scholarly-queries',str(queries),
        '--feed',str(tmp_path/'feed.xml'),'--failures',str(tmp_path/'failures.tsv')],
        now=lambda: datetime(2026,10,8,12,tzinfo=timezone.utc))
    assert result == 0
    state = load_state(state_path)
    assert state.source_watermarks['ieee:tc'] == '2026-10-07T00:00:00+00:00'
    assert state.papers[key].summary_zh == 'Existing validated summary'
    assert not state.pending_ai


@pytest.mark.parametrize('total', [None, True, -1, '81', 1.5])
def test_crossref_conference_requires_nonnegative_integer_total(total):
    message = {'items': [], 'total-results': total}
    with pytest.raises(ValueError):
        conferences.parse_page('cav-i-2026', json.dumps({'message': message}).encode())


def crossref_body(count, total, cursor='next'):
    # Raw non-publication metadata still counts toward examined service entries.
    return json.dumps({'message': {'items': [{}] * count,
        'total-results': total, 'next-cursor': cursor}}).encode()


def test_crossref_positive_total_empty_first_page_retries(monkeypatch):
    bodies = iter([crossref_body(0, 81), crossref_body(0, 0)])
    calls = []
    def fetch(url):
        calls.append(url)
        return HttpResult(next(bodies), 200, url)
    monkeypatch.setattr(collect.time, 'sleep', lambda _: None)
    result = collect._collect_scholarly('conference', 'cav-i-2026', date(2026,9,7), fetch, 300)
    assert len(calls) == 2
    assert result.complete and result.failure is None


def test_crossref_premature_empty_resumed_page_keeps_evidence(monkeypatch):
    first = collect._collect_scholarly('conference', 'cav-i-2026', date(2026,9,7),
        lambda url: HttpResult(crossref_body(2, 5, 'opaque-next'), 200, url), 2)
    assert not first.complete and first.continuation is not None
    progress = json.loads(first.continuation.cursor)
    assert progress['examined'] == 2 and progress['total'] == 5
    calls = []
    def fetch(url):
        calls.append(url)
        assert parse_qs(urlparse(url).query)['cursor'] == ['opaque-next']
        return HttpResult(crossref_body(0, 5), 200, url)
    monkeypatch.setattr(collect.time, 'sleep', lambda _: None)
    second = collect._collect_scholarly('conference', 'cav-i-2026', date(2026,10,8), fetch, 2, first.continuation)
    assert len(calls) == 3 and second.failure is not None and not second.complete
    assert second.continuation == first.continuation


def test_changed_conference_total_resets_only_query_progress_without_claiming_completion(monkeypatch):
    first = collect._collect_scholarly('conference','cav-i-2026',date(2026,9,7),
        lambda url:HttpResult(crossref_body(2,5,'opaque-next'),200,url),2)
    second = collect._collect_scholarly('conference','cav-i-2026',date(2026,10,8),
        lambda url:HttpResult(crossref_body(2,6,'opaque-new'),200,url),2,first.continuation)
    assert second.failure is not None and not second.complete
    progress = json.loads(second.continuation.cursor)
    assert progress['cursor'] == '*' and progress['examined'] == 0 and progress['total'] is None
    assert second.continuation.from_date == date(2026,9,7)
    third = collect._collect_scholarly('conference','cav-i-2026',date(2026,10,9),
        lambda url:HttpResult(crossref_body(6,6,'end'),200,url),10,second.continuation)
    assert third.complete and third.failure is None


def test_datacite_shrunk_catalog_empty_resumed_page_resets_query():
    cursor = json.dumps({'cursor':'2','examined':300,'total':450,'page_size':300})
    continuation = SourceContinuation(date(2026,9,7),cursor)
    body = json.dumps({'data':[],'meta':{'total':250,'totalPages':1}}).encode()
    result = collect._collect_scholarly('conference','fmcad-2026',date(2026,10,8),
        lambda url:HttpResult(body,200,url),300,continuation)
    assert not result.complete and result.failure is not None
    assert json.loads(result.continuation.cursor) == {'cursor':'*','examined':0,'total':None,'page_size':300}


def test_crossref_legitimate_exhausted_cursor_empty_page_is_valid():
    cursor = json.dumps({'cursor': 'terminal', 'examined': 81, 'total': 81, 'page_size': 1000})
    page = conferences.parse_page('cav-i-2026', crossref_body(0, 81, 'terminal'), cursor)
    assert page.item_count == 0 and page.next_cursor is None


@pytest.mark.parametrize('count,total,pages', [(1,76,0), (0,76,1), (1,0,1), (2,1,1), (1,76,2)])
def test_datacite_rejects_contradictory_counts(count, total, pages):
    body = json.dumps({'data': [{}] * count, 'meta': {'total': total, 'totalPages': pages}}).encode()
    with pytest.raises(ValueError):
        conferences.parse_page('fmcad-2026', body)


def test_datacite_nonmultiple_budget_persists_constant_size_without_loss(tmp_path):
    total = 2507
    examined = []
    requests = []
    def fetch(url):
        params = parse_qs(urlparse(url).query)
        size, page = int(params['page[size]'][0]), int(params['page[number]'][0])
        requests.append((page, size))
        start = (page - 1) * size
        indices = list(range(start, min(start + size, total)))
        examined.extend(indices)
        # Only one parsed/admitted record per page: budgets count raw entries.
        data = [{} for _ in indices]
        if indices:
            data[0] = datacite_item(indices[0] + 6)
        return HttpResult(json.dumps({'data': data, 'meta': {'total': total,
            'totalPages': (total + size - 1) // size}}).encode(), 200, url)
    continuation = None
    for run in range(3):
        result = collect._collect_scholarly('conference', 'fmcad-2026', date(2026,9,7), fetch, 1500, continuation)
        assert result.failure is None
        if run < 2:
            assert not result.complete
            path = tmp_path / 'state.json'
            save_state(path, FeedState(source_continuations={'conference:fmcad-2026': result.continuation}))
            continuation = load_state(path).source_continuations['conference:fmcad-2026']
        else:
            assert result.complete and result.continuation is None
    assert requests == [(1,1000),(2,1000),(3,1000)]
    assert examined == list(range(total))


def ieee_collection_args(tmp_path):
    from pathlib import Path
    root = Path(__file__).parents[1]
    sources = tmp_path / 'sources.tsv'
    sources.write_text('name\turl\n', encoding='utf-8')
    queries = tmp_path / 'scholarly.json'
    queries.write_text(json.dumps({'version':1,'queries':[
        {'id':'tc','source':'ieee','query':'journal:0018-9340','limit':300}]}), encoding='utf-8')
    return ['--config',str(root/'paper_feed_config.json'),'--state',str(tmp_path/'state.json'),
        '--sources',str(sources),'--queries',str(root/'config/queries.json'),
        '--scholarly-queries',str(queries),'--feed',str(tmp_path/'feed.xml'),
        '--failures',str(tmp_path/'failures.tsv')]


def test_all_failed_ieee_first_page_saves_checkpoint_atomically_and_remains_failed(tmp_path, monkeypatch):
    from ic_feed.sources import ieee
    args = ieee_collection_args(tmp_path)
    state_path = tmp_path / 'state.json'
    old_watermark = '2026-09-08T00:00:00+00:00'
    save_state(state_path, FeedState(source_watermarks={'ieee:tc':old_watermark}))
    feed = tmp_path / 'feed.xml'
    feed.write_text('unchanged valid prior feed', encoding='utf-8')
    monkeypatch.setenv('IEEE_API_KEY', 'fixture-private-key')
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        raise ieee.IeeeFetchError('http_503','IEEE HTTP 503')
    monkeypatch.setattr(ieee, 'request_page', request)
    first = collect.main(args, now=lambda: datetime(2026,10,7,tzinfo=timezone.utc))
    assert first == 2
    state = load_state(state_path)
    checkpoint = state.source_continuations['ieee:tc']
    assert checkpoint.from_date == date(2026,9,8)
    assert json.loads(checkpoint.cursor) == {'start_record':1,'until_date':'2026-10-07','total_records':None}
    assert 'ieee:tc' not in state.source_watermarks
    assert feed.read_text(encoding='utf-8') == 'unchanged valid prior feed'
    assert 'fixture-private-key' not in state_path.read_text(encoding='utf-8')
    assert 'fixture-private-key' not in (tmp_path/'failures.tsv').read_text(encoding='utf-8')
    second = collect.main(args, now=lambda: datetime(2026,10,8,tzinfo=timezone.utc))
    assert second == 2 and load_state(state_path).source_continuations['ieee:tc'] == checkpoint
    assert calls[-1][1:4] == (date(2026,9,8),date(2026,10,7),1)


def test_new_ieee_failure_freezes_initial_rolling_30_day_window(tmp_path, monkeypatch):
    from ic_feed.sources import ieee
    args = ieee_collection_args(tmp_path)
    monkeypatch.setenv('IEEE_API_KEY','fixture-private-key')
    def request(*_, **kwargs):
        raise ieee.IeeeFetchError('http_503','IEEE HTTP 503')
    monkeypatch.setattr(ieee,'request_page',request)
    assert collect.main(args, now=lambda: datetime(2026,10,7,tzinfo=timezone.utc)) == 2
    assert load_state(tmp_path/'state.json').source_continuations['ieee:tc'].from_date == date(2026,9,7)


def test_all_failed_partial_ieee_checkpoint_never_skips_unpublished_records(tmp_path, monkeypatch):
    from ic_feed.sources import ieee
    args = ieee_collection_args(tmp_path)
    monkeypatch.setenv('IEEE_API_KEY','fixture-private-key')
    item = replace(record('RTL hardware design'),journal='IEEE Transactions on Computers')
    def request(query,start,end,offset,size,key,**kwargs):
        if offset == 1:
            return ieee.IeeePage([item],200,450)
        raise ieee.IeeeFetchError('http_503','IEEE HTTP 503')
    monkeypatch.setattr(ieee,'request_page',request)
    assert collect.main(args, now=lambda: datetime(2026,10,7,tzinfo=timezone.utc)) == 2
    state = load_state(tmp_path/'state.json')
    checkpoint = json.loads(state.source_continuations['ieee:tc'].cursor)
    # The failed publication path commits checkpoint state only, not new feed records.
    assert not state.papers and checkpoint['start_record'] == 1
    assert checkpoint['until_date'] == '2026-10-07'


def test_resumed_ieee_retains_frozen_lower_publication_boundary(tmp_path, monkeypatch):
    from ic_feed.sources import ieee
    from ic_feed.normalize import record_key
    args = ieee_collection_args(tmp_path)
    continuation = SourceContinuation(date(2026,9,7), json.dumps(
        {'start_record':301,'until_date':'2026-10-07','total_records':301}))
    save_state(tmp_path/'state.json',FeedState(source_continuations={'ieee:tc':continuation}))
    item = replace(record('RTL hardware design'),journal='IEEE Transactions on Computers',
        published_at=datetime(2026,9,7,tzinfo=timezone.utc))
    monkeypatch.setenv('IEEE_API_KEY','fixture-private-key')
    monkeypatch.setattr(ieee,'request_page',lambda *_, **kwargs: ieee.IeeePage([item],1,301))
    assert collect.main(args, now=lambda: datetime(2026,10,8,tzinfo=timezone.utc)) == 0
    state = load_state(tmp_path/'state.json')
    assert record_key(item) in state.papers
    assert state.source_watermarks['ieee:tc'] == '2026-10-07T00:00:00+00:00'


def test_resumed_ieee_incremental_insertion_window_preserves_30_day_publications(tmp_path,monkeypatch):
    from ic_feed.sources import ieee
    from ic_feed.normalize import record_key
    args = ieee_collection_args(tmp_path)
    continuation = SourceContinuation(date(2026,10,7),json.dumps(
        {'start_record':301,'until_date':'2026-10-08','total_records':301}))
    save_state(tmp_path/'state.json',FeedState(source_continuations={'ieee:tc':continuation}))
    item = replace(record('RTL hardware design'),journal='IEEE Transactions on Computers',
        published_at=datetime(2026,9,20,tzinfo=timezone.utc))
    monkeypatch.setenv('IEEE_API_KEY','fixture-private-key')
    monkeypatch.setattr(ieee,'request_page',lambda *_,**kwargs: ieee.IeeePage([item],1,301))
    assert collect.main(args,now=lambda: datetime(2026,10,9,tzinfo=timezone.utc)) == 0
    state = load_state(tmp_path/'state.json')
    assert record_key(item) in state.papers
    assert state.source_watermarks['ieee:tc'] == '2026-10-08T00:00:00+00:00'


def asplos_record(title='The Unexpected Journey', abstract=''):
    edition = conferences.get_edition('asplos-v1-2026')
    return replace(record(title, abstract),journal=edition.container,doi='10.1145/3760250.3762220')


def test_verified_main_conference_missing_abstract_without_keywords_is_pending():
    from ic_feed.filtering import load_rules
    from ic_feed.normalize import record_key
    state = FeedState()
    item = asplos_record()
    assert conferences.edition_accepts(conferences.get_edition('asplos-v1-2026'),item)
    stats = collect.merge_into_state(state,[item],load_rules(Path('config/queries.json')))
    assert stats.added == 1 and stats.filtered == 0
    assert state.pending_ai == [record_key(item)]
    assert state.papers[record_key(item)].ai_relevant is None


@pytest.mark.parametrize('item', [
    asplos_record(abstract='A pure software study of web application security.'),
    replace(asplos_record(),journal='IEEE Transactions on Computers'),
    replace(asplos_record(),journal='Unapproved Conference'),
    replace(asplos_record(),journal=asplos_record().journal+' Workshops'),
])
def test_missing_abstract_fallback_does_not_broaden_other_admission(item):
    from ic_feed.filtering import load_rules
    state = FeedState()
    stats = collect.merge_into_state(state,[item],load_rules(Path('config/queries.json')))
    assert stats.filtered == 1 and not state.papers and not state.pending_ai


@pytest.mark.parametrize('title', ['Cover', 'Copyright', 'Table of Contents', 'Author Index',
    'Session Index', 'Preface', 'Program Committee', 'Front Matter', 'Keynote Address',
    'Invited Talk', 'Tutorial', 'Student Forum', 'Organizing Committee'])
def test_proceedings_scaffolding_is_excluded_before_missing_abstract_fallback(title):
    from ic_feed.filtering import load_rules
    item = asplos_record(title)
    assert not conferences.edition_accepts(conferences.get_edition('asplos-v1-2026'),item)
    state = FeedState()
    assert collect.merge_into_state(state,[item],load_rules(Path('config/queries.json'))).filtered == 1
    assert not state.papers


@pytest.mark.parametrize('judgment', [True, False])
def test_same_title_only_conference_metadata_never_requeues_old_judgment(judgment):
    from ic_feed.filtering import load_rules
    from ic_feed.normalize import record_key
    item = asplos_record()
    old = replace(item,ai_relevant=judgment,summary_zh='Existing summary',ai_confidence=0.9)
    key = record_key(old)
    state = FeedState(papers={key:old})
    collect.merge_into_state(state,[item],load_rules(Path('config/queries.json')))
    assert not state.pending_ai
    assert state.papers[key].ai_relevant == judgment
    assert state.papers[key].summary_zh == 'Existing summary'
