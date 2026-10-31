"""Small, bounded discovery of recipe links from labelled Good Food sections."""
from collections import deque
from datetime import UTC, datetime
import re
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .model import source_url

HEADINGS = {
    'trending': ('trending recipes', 'popular recipes', 'most popular recipes'),
    'new': ('latest added recipes', 'newly added recipes', 'latest recipes', 'new recipes'),
    'seasonal': ('seasonal recipes', 'seasonal inspiration'),
    'collections': ('most popular recipe collections this week', 'popular recipe collections'),
}
MONTHS = ('january', 'february', 'march', 'april', 'may', 'june', 'july',
          'august', 'september', 'october', 'november', 'december')


def public_card(node, collections=False):
    card = node.find_parent('article') or node
    kind = card.get('data-item-type')
    kinds = ('collection', 'editorialList') if collections else ('recipe',)
    if kind and kind not in kinds:
        return False
    value = card.get_text(' ', strip=True).lower()
    return not any(x in value for x in ('app-only', 'app only', 'premium', 'subscribed users'))


def section_nodes(soup, category):
    allowed = HEADINGS.get(category, (f'{category} recipes',))
    heading = next((h for h in soup.find_all(['h1', 'h2', 'h3', 'h4'])
                    if h.get_text(' ', strip=True).casefold().rstrip('.:… ') in allowed), None)
    if heading is None:
        raise ValueError(f"No labelled {category} recipe section")
    level = int(heading.name[1])
    parent = heading.parent
    wrapper = parent if parent.get("data-testid") in ("box", "card-carousel") else None
    for node in heading.next_elements:
        if wrapper is not None and wrapper not in node.parents:
            break
        if getattr(node, 'name', None) in ('h1', 'h2', 'h3', 'h4') and int(node.name[1]) <= level:
            break
        yield node


def section_links(markup, base, category, collections=False):
    result = []
    for node in section_nodes(BeautifulSoup(markup, 'html.parser'), category):
        if getattr(node, 'name', None) != 'a' or not public_card(node, collections):
            continue
        try:
            url = source_url(urljoin(base, node.get('href', '')), recipe=not collections)
            if collections and not re.fullmatch(r'/recipes/collection/[a-z0-9-]+', urlsplit(url).path):
                continue
            if url not in result:
                result.append(url)
        except ValueError:
            continue
    if not result:
        raise ValueError(f'No public {category} recipe links in labelled section')
    return result


def collection_links(markup, base):
    """Only typed recipe cards in collection results; ignore its hero and navigation."""
    if not re.fullmatch(r'/recipes/collection/[a-z0-9-]+', urlsplit(source_url(base, recipe=False)).path):
        raise ValueError('Expected a Good Food recipe collection')
    soup = BeautifulSoup(markup, 'html.parser')
    scope = soup.find('main') or soup
    result = []
    for card in scope.select('article[data-item-type="recipe"]'):
        for node in card.find_all('a', href=True):
            if not public_card(node):
                continue
            try:
                url = source_url(urljoin(base, node['href']))
                if url not in result:
                    result.append(url)
            except ValueError:
                continue
    if not result:
        raise ValueError('No public typed recipe cards in collection; structure may have changed')
    return result


def month_collection(cfg, now=None):
    now = (now or datetime.now(UTC)).astimezone(ZoneInfo(cfg.timezone))
    return f'https://www.bbcgoodfood.com/recipes/collection/{MONTHS[now.month - 1]}-recipes'


def mix(groups, limit):
    """Two popular candidates, then one new and one seasonal; deduplicate globally."""
    queues = {key: deque(values) for key, values in groups.items()}
    seen, result = set(), []
    while any(queues.values()) and len(result) < limit:
        for key in ('trending', 'trending', 'new', 'seasonal'):
            q = queues.get(key, deque())
            while q:
                url = q.popleft()
                if url not in seen:
                    seen.add(url)
                    result.append((url, key))
                    break
            if len(result) >= limit:
                break
    return result
