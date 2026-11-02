import pytest

from app.errors import InvalidUrlError
from app.url_validation import parse_media_url

ID = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url,expected_short",
    [
        (f"https://www.youtube.com/watch?v={ID}", False),
        (f"https://youtube.com/watch?v={ID}&t=42s&list=PL123", False),
        (f"http://m.youtube.com/watch?feature=share&v={ID}", False),
        (f"https://music.youtube.com/watch?v={ID}", False),
        (f"https://youtu.be/{ID}", False),
        (f"https://youtu.be/{ID}?si=abcdef&t=10", False),
        (f"youtu.be/{ID}", False),
        (f"www.youtube.com/watch?v={ID}", False),
        (f"https://www.youtube.com/shorts/{ID}", True),
        (f"https://youtube.com/shorts/{ID}?feature=share", True),
        (f"https://www.youtube.com/embed/{ID}", False),
        (f"https://www.youtube-nocookie.com/embed/{ID}", False),
        (f"https://www.youtube.com/live/{ID}", False),
        (f"  https://WWW.YOUTUBE.COM/watch?v={ID}  ", False),
        (f"https://www.youtube.com.:443/watch?v={ID}", False),
    ],
)
def test_valid_urls_are_normalized(url, expected_short):
    ref = parse_media_url(url)
    assert ref.video_id == ID
    assert ref.is_short is expected_short
    assert ref.canonical_url == f"https://www.youtube.com/watch?v={ID}"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "   ",
        "not a url",
        f"https://evil.com/watch?v={ID}",
        f"https://youtube.com.evil.com/watch?v={ID}",
        f"https://evilyoutube.com/watch?v={ID}",
        f"https://www.youtube.com@evil.com/watch?v={ID}",
        f"https://user:pass@www.youtube.com/watch?v={ID}",
        f"https://www.youtube.com:8080/watch?v={ID}",
        f"ftp://www.youtube.com/watch?v={ID}",
        f"javascript:alert(1)//youtube.com/watch?v={ID}",
        f"file:///etc/passwd?v={ID}",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQQ",  # 12 chars
        "https://www.youtube.com/watch?v=dQw4w9WgX$Q",
        "https://www.youtube.com/watch",
        "https://www.youtube.com/playlist?list=PL1234567890",
        "https://www.youtube.com/@somechannel",
        "https://www.youtube.com/channel/UC123",
        "https://youtu.be/",
        f"https://youtu.be/{ID}/../../etc",
        f"https://www.youtube.com/watch?v={ID}\nhttps://evil.com",
        "https://www.youtube.com/watch?v=" + "a" * 3000,
        f"https://127.0.0.1/watch?v={ID}",
        f"https://[::1]/watch?v={ID}",
    ],
)
def test_invalid_urls_are_rejected(url):
    with pytest.raises(InvalidUrlError):
        parse_media_url(url)


def test_non_string_rejected():
    with pytest.raises(InvalidUrlError):
        parse_media_url(None)  # type: ignore[arg-type]


IG = "C9xYz_AbC-1"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.instagram.com/p/{IG}/",
        f"https://www.instagram.com/reel/{IG}/?igsh=abc123",
        f"https://instagram.com/reels/{IG}",
        f"https://www.instagram.com/tv/{IG}/",
        f"https://www.instagram.com/some.user/reel/{IG}/",
        f"https://m.instagram.com/p/{IG}",
        f"instagram.com/reel/{IG}",
        f"https://instagr.am/p/{IG}/",
    ],
)
def test_instagram_urls_are_normalized(url):
    ref = parse_media_url(url)
    assert (ref.platform, ref.video_id) == ("instagram", IG)
    assert ref.canonical_url == f"https://www.instagram.com/p/{IG}/"
    assert ref.cache_key == f"instagram:{IG}"


def test_youtube_refs_keep_their_platform():
    ref = parse_media_url(f"https://youtu.be/{ID}")
    assert ref.platform == "youtube" and ref.cache_key == f"youtube:{ID}"


@pytest.mark.parametrize(
    "url",
    [
        "https://www.instagram.com/",
        "https://www.instagram.com/some.user/",
        f"https://www.instagram.com/{IG}/",  # a single segment is a profile, not a post
        "https://www.instagram.com/stories/some.user/3141592653589793238/",
        "https://www.instagram.com/share/reel/BAbc123/",
        "https://www.instagram.com/explore/tags/cats/",
        "https://www.instagram.com/p/abc/",  # too short
        "https://www.instagram.com/p/bad$code/",
        f"https://www.instagram.com/p/{IG}/extra/more",
        f"https://instagram.com.evil.com/p/{IG}/",
        f"https://evil.com/instagram.com/p/{IG}/",
        f"https://user:pw@www.instagram.com/p/{IG}/",
        f"https://www.instagram.com:8443/p/{IG}/",
    ],
)
def test_invalid_instagram_urls_are_rejected(url):
    with pytest.raises(InvalidUrlError):
        parse_media_url(url)
