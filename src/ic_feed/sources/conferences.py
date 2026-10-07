"""Edition-scoped conference discovery; publication year alone is insufficient."""
from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import re
from urllib.parse import urlencode

from ic_feed.enrich import _clean_text, _valid_abstract
from ic_feed.models import PaperRecord, ScholarlyPage
from ic_feed.normalize import normalize_doi
from ic_feed.sources import crossref
from ic_feed.venues import _normalize, match_venue


@dataclass(frozen=True, slots=True)
class ConferenceEdition:
    id: str
    venue: str
    year: int
    source: str
    container: str
    doi_roots: tuple[str, ...]
    exclude_children: tuple[int, ...]
    publication_dates: tuple[str, ...] = ()
    exclude_dois: tuple[str, ...] = ()
    search_title: str = ''


class ChangedTotalError(ValueError):
    """A valid catalog changed while an old pagination checkpoint was in flight."""


def restarted_cursor(query: str, cursor: str) -> str:
    progress = _progress(cursor)
    return json.dumps({'cursor':'*','examined':0,'total':None,
        'page_size':progress['page_size']},separators=(',',':'))


def load_editions(path: Path = Path('config/conference_editions.json')) -> tuple[ConferenceEdition, ...]:
    raw = json.loads(path.read_text(encoding='utf-8'))
    if type(raw) is not dict or set(raw) != {'version','editions'} or raw['version'] != 1 or type(raw['editions']) is not list:
        raise ValueError('invalid conference edition catalog')
    result = []
    ids = set()
    for item in raw['editions']:
        required = {'id','venue','year','source','container','doi_roots','exclude_children'}
        if type(item) is not dict or not required <= set(item) or set(item)-required-{'publication_dates','exclude_dois','search_title'}:
            raise ValueError('invalid conference edition')
        if (type(item['id']) is not str or not re.fullmatch(r'[a-z0-9-]+',item['id']) or item['id'] in ids
                or item['venue'] not in {'dac','iccad','fmcad','cav','isca','micro','hpca','asplos','isscc','vlsi'}
                or type(item['year']) is not int or item['year'] != 2026
                or item['source'] not in {'crossref','datacite','ieee'}
                or type(item['container']) is not str or not item['container'].strip()
                or type(item['doi_roots']) is not list or any(type(v) is not str or normalize_doi(v) != v for v in item['doi_roots'])
                or type(item['exclude_children']) is not list or any(type(v) is not int or v < 1 for v in item['exclude_children'])):
            raise ValueError('invalid conference edition values')
        ids.add(item['id'])
        dates = item.get('publication_dates',[])
        excluded = item.get('exclude_dois',[])
        search = item.get('search_title','')
        if type(dates) is not list or any(type(v) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',v) for v in dates) or len(dates) > 1:
            raise ValueError('invalid conference publication window')
        for value in dates:
            date.fromisoformat(value)
        if type(excluded) is not list or any(type(v) is not str or normalize_doi(v) != v for v in excluded) or type(search) is not str:
            raise ValueError('invalid conference discovery metadata')
        result.append(ConferenceEdition(**{**item, 'doi_roots':tuple(item['doi_roots']), 'exclude_children':tuple(item['exclude_children']),
            'publication_dates':tuple(dates),'exclude_dois':tuple(excluded),'search_title':search}))
    return tuple(result)


def get_edition(identifier: str) -> ConferenceEdition:
    return next(e for e in load_editions() if e.id == identifier)


def edition_accepts(edition: ConferenceEdition, record: PaperRecord) -> bool:
    venue = match_venue(record)
    if venue is None or venue.id != edition.venue:
        return False
    if is_auxiliary_title(record.title):
        return False
    doi = normalize_doi(record.doi)
    if doi in edition.exclude_dois:
        return False
    if edition.doi_roots:
        root = next((root for root in edition.doi_roots if doi and re.fullmatch(re.escape(root) + r'[._]\d+',doi)), None)
        if root is None:
            return False
        child = int(doi[len(root)+1:])
        if child in edition.exclude_children:
            return False
        # DOI roots prove the edition, but reject mismatched volume containers.
        return edition.venue in {'cav','fmcad'} or _normalize(record.journal) == _normalize(edition.container)
    if edition.source == 'ieee':
        return re.search(rf'\b{edition.year}\b',record.journal) is not None
    return _normalize(record.journal) == _normalize(edition.container)


def is_auxiliary_title(title: str) -> bool:
    if re.search(r'\b(?:invited(?:\s+talk)?|tutorial|front\s*matter|preface|student\s+forum|keynote)\b', title, re.I):
        return True
    return re.fullmatch(r'(?:front |back )?cover(?: page)?|copyright(?: notice| information)?|'
        r'(?:table of )?contents|(?:author|subject|session|keyword) index|'
        r'(?:program|programme|organizing|organising|conference|technical program) committee(?:s)?|'
        r'(?:conference )?(?:program|programme)|acknowledg(?:e)?ments|'
        r'(?:message|welcome)(?: from (?:the )?(?:general|program|programme) chairs?)?',
        _normalize(title)) is not None


def _progress(cursor: str, page_size: int = 1000) -> dict:
    if cursor.startswith('{'):
        value = json.loads(cursor)
        if (type(value) is not dict or set(value) != {'cursor','examined','total','page_size'}
                or type(value['cursor']) is not str or not value['cursor']
                or type(value['examined']) is not int or value['examined'] < 0
                or type(value['page_size']) is not int or not 1 <= value['page_size'] <= 1000
                or (value['total'] is not None and (type(value['total']) is not int
                    or value['total'] < value['examined']))):
            raise ValueError('invalid conference pagination checkpoint')
        return value
    return {'cursor':cursor,'examined':0,'total':None,'page_size':min(page_size,1000)}


def initial_cursor(query: str, cursor: str, rows: int) -> str:
    progress = _progress(cursor, rows)
    if get_edition(query).source == 'datacite' and not cursor.startswith('{') and cursor != '*':
        page = int(cursor)
        if page < 1:
            raise ValueError('invalid DataCite page number')
        progress['examined'] = (page-1)*progress['page_size']
    return json.dumps(progress,separators=(',',':'))


def page_size(cursor: str) -> int:
    return _progress(cursor)['page_size']


def _next_cursor(progress: dict, raw_cursor: str, examined: int, total: int) -> str:
    return json.dumps({**progress,'cursor':raw_cursor,'examined':examined,'total':total},separators=(',',':'))


def build_url(query: str, from_date: date, rows: int, cursor: str = '*') -> str:
    edition = get_edition(query)
    progress = _progress(cursor,rows)
    raw_cursor = progress['cursor']
    if edition.source == 'datacite':
        page = 1 if raw_cursor == '*' else int(raw_cursor)
        return 'https://api.datacite.org/dois?' + urlencode({
            'query':'doi:' + edition.doi_roots[0] + '*',
            'page[size]':progress['page_size'], 'page[number]':page, 'sort':'doi'})
    if edition.source != 'crossref':
        raise ValueError('IEEE conference requires authenticated discovery')
    params = {'filter': f'container-title:{edition.container},from-pub-date:{edition.year}-01-01,until-pub-date:{edition.year}-12-31',
              'rows': min(rows,1000), 'cursor':raw_cursor, 'select':crossref._SELECT}
    if edition.publication_dates:
        # Independently checked against whole-edition Crossref and DBLP TOCs.
        # This avoids comma-delimited filter ambiguity in the two ASPLOS titles.
        day = edition.publication_dates[0]
        params['filter'] = f'prefix:10.1145,type:proceedings-article,from-pub-date:{day},until-pub-date:{day}'
    return 'https://api.crossref.org/works?' + urlencode(params)


def parse_page(query: str, body: bytes, cursor: str = '*') -> ScholarlyPage:
    edition = get_edition(query)
    progress = _progress(cursor)
    payload = json.loads(body)
    if edition.source == 'crossref':
        page = crossref.parse_page(body)
        total = payload['message'].get('total-results')
        if type(total) is int and total >= 0 and progress['total'] is not None and progress['total'] != total:
            raise ChangedTotalError('Crossref conference catalog changed; restarting query')
        examined = progress['examined'] + page.item_count
        if (type(total) is not int or total < 0 or examined > total
                or (progress['total'] is not None and progress['total'] != total)
                or (not page.item_count and examined < total)):
            raise ValueError('invalid Crossref conference totals')
        next_cursor = None
        if examined < total:
            if page.next_cursor is None or page.next_cursor == progress['cursor']:
                raise ValueError('Crossref conference cursor did not advance')
            next_cursor = _next_cursor(progress,page.next_cursor,examined,total)
        return ScholarlyPage([r for r in page.records if edition_accepts(edition,r)],next_cursor,page.item_count)
    data = payload.get('data') if type(payload) is dict else None
    meta = payload.get('meta') if type(payload) is dict else None
    if type(data) is not list or type(meta) is not dict or type(meta.get('total')) is not int or meta['total'] < 0:
        raise ValueError('invalid DataCite conference response')
    if progress['total'] is not None and progress['total'] != meta['total']:
        raise ChangedTotalError('DataCite conference catalog changed; restarting query')
    if meta['total'] and not data:
        raise ValueError('invalid DataCite conference response')
    raw_cursor = progress['cursor']
    page_number = 1 if raw_cursor == '*' else int(raw_cursor)
    size = progress['page_size']
    examined = (page_number-1)*size
    total = meta['total']
    total_pages = meta.get('totalPages')
    if (page_number < 1 or type(total_pages) is not int or total_pages != (total+size-1)//size
            or len(data) != min(size,max(0,total-examined))
            or (progress['total'] is not None and (progress['total'] != total or progress['examined'] != examined))
            or (total and page_number > total_pages)):
        raise ValueError('invalid DataCite pagination')
    records = []
    for item in data:
        if type(item) is not dict or type(item.get('attributes')) is not dict:
            continue
        attrs = item['attributes']
        types = attrs.get('types')
        if type(types) is not dict or str(types.get('resourceType','')).casefold() != 'inproceedings':
            continue
        titles = attrs.get('titles')
        if not titles or type(titles) is not list or type(titles[0]) is not dict:
            continue
        title = _clean_text(titles[0].get('title',''))
        doi = normalize_doi(attrs.get('doi'))
        if not title or not doi:
            continue
        issued = next((v.get('date') for v in (attrs.get('dates') or []) if type(v) is dict and v.get('dateType') == 'Issued'),str(attrs.get('publicationYear','')))
        try:
            parts = str(issued).split('-')
            published = datetime(int(parts[0]),int(parts[1]) if len(parts)>1 else 1,int(parts[2]) if len(parts)>2 else 1,tzinfo=timezone.utc)
        except (ValueError,TypeError):
            continue
        abstract = next((v.get('description','') for v in (attrs.get('descriptions') or []) if type(v) is dict and v.get('descriptionType') == 'Abstract'),'')
        authors = [' '.join(str(c.get(p,'')).strip() for p in ('givenName','familyName')).strip() or str(c.get('name','')).strip() for c in (attrs.get('creators') or []) if type(c) is dict]
        record = PaperRecord(title,_valid_abstract(title,abstract) or '',[a for a in authors if a],edition.container,published,doi,
            str(attrs.get('url') or f'https://doi.org/{doi}'),['datacite'],[doi])
        if edition_accepts(edition,record):
            records.append(record)
    next_cursor = _next_cursor(progress,str(page_number+1),examined+len(data),total) if page_number < total_pages else None
    return ScholarlyPage(records,next_cursor,len(data))
