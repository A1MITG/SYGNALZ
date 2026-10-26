"""Share-image recovery for feed items that carried no picture (offline)."""
from app.scraper import article_images as ai


def setup_function(_):
    ai._seen.clear()


def test_reads_og_image_in_either_attribute_order():
    html = ('<meta content="https://x.com/a.jpg" property="og:image" />'
            '<meta name="twitter:image" content="https://x.com/b.jpg">')
    assert ai.share_image(html) == 'https://x.com/a.jpg'


def test_falls_back_to_twitter_image():
    assert ai.share_image('<meta name="twitter:image" content="https://x.com/b.jpg">') \
        == 'https://x.com/b.jpg'


def test_publisher_logo_cards_are_generic():
    # The two real defaults that left the Insurance tile imageless.
    assert ai.is_generic('https://www.insurancejournal.com/img/social/opengraph/ij-social-default-1200x630.png')
    assert ai.is_generic('https://www.businessinsurance.com/wp-content/uploads/2023/03/cropped-BI-square-blue.png')
    assert not ai.is_generic('https://www.businessinsurance.com/wp-content/uploads/2026/09/Chubb-2.jpg')
    # The Loadstar's resized photos end in "-c-default"; they are story photos.
    assert not ai.is_generic('https://theloadstar.com/wp-content/uploads/32d57403416e8cfc09cfe872bbc9efbe-680x0-c-default.png')


def test_fills_only_missing_and_drops_images_shared_across_stories():
    articles = [
        {'url': 'https://p/1', 'image': ''},
        {'url': 'https://p/2', 'image': None},
        {'url': 'https://p/3'},
        {'url': 'https://p/4', 'image': 'https://feed/own.jpg'},
    ]
    served = {'https://p/1': 'https://p/story1.jpg',
              'https://p/2': 'https://p/masthead.png',
              'https://p/3': 'https://p/masthead.png'}
    asked = []

    def fetch(urls):
        asked.extend(urls)
        return [(u, served[u]) for u in urls]

    assert ai.fill_missing_images(articles, fetch=fetch) == 1
    assert sorted(asked) == ['https://p/1', 'https://p/2', 'https://p/3']
    assert articles[0]['image'] == 'https://p/story1.jpg'
    assert not articles[1]['image'] and 'image' not in articles[2]
    assert articles[3]['image'] == 'https://feed/own.jpg'


def test_a_feed_thumbnail_is_replaced_by_the_page_picture():
    """ET GCC's feed sends 100x100 crops; the page's share image is 1200x627."""
    thumb = 'https://etimg.etb2bimg.com/thumb/img-size-939740/134435247.cms'
    full = ('https://etimg.etb2bimg.com/thumb/msid-134435247,imgsize-939740,width-1200,'
            'height=627,overlay-etgcc/news/syneos.jpg')
    assert ai.is_thumbnail(thumb) and not ai.is_thumbnail(full)
    articles = [{'url': 'https://gcc.example/syneos', 'image': thumb},
                {'url': 'https://gcc.example/other', 'image': thumb.replace('134435247', '134435248')}]
    served = {'https://gcc.example/syneos': full, 'https://gcc.example/other': None}
    assert ai.fill_missing_images(articles, fetch=lambda urls: [(u, served[u]) for u in urls]) == 1
    assert articles[0]['image'] == full
    assert articles[1]['image'].endswith('134435248.cms')   # nothing better: the thumbnail stays


def test_disabled_by_env_never_touches_the_network(monkeypatch):
    monkeypatch.setenv('FETCH_ARTICLE_IMAGES', '0')
    assert ai.fill_missing_images([{'url': 'https://p/1'}]) == 0
