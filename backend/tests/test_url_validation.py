import pytest

from app.errors import InvalidUrlError
from app.url_validation import parse_youtube_url

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
    ref = parse_youtube_url(url)
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
        parse_youtube_url(url)


def test_non_string_rejected():
    with pytest.raises(InvalidUrlError):
        parse_youtube_url(None)  # type: ignore[arg-type]
