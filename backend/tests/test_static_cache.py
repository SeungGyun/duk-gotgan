"""폰이 옛 화면을 계속 들고 있던 것 — 캐시 규칙을 적어 보냅니다.

`rebuild-ui.sh` 로 화면을 고쳤는데 폰에서는 고치기 전 화면이 그대로
나왔습니다. 고친 것이 되돌아간 것처럼 보이지만, 실제로는 폰이 옛
`index.html` 을 들고 그것이 가리키는 옛 asset 을 계속 쓰는 것입니다.

원인은 **아무 캐시 규칙도 안 보낸 것**입니다. 그러면 브라우저가 알아서
정하는데(RFC 9111 §4.2.2 어림 신선도), 대개 `(지금 − Last-Modified) × 10%`
이고 폰의 사파리가 특히 오래 잡습니다. 만든 지 하루 된 파일이면 두 시간을
안 물어봅니다.

여기서 지키려는 것은 둘입니다.

  1. `index.html` 은 **매번 물어본다.** 이것이 어느 asset 을 쓸지 정하는
     문서라, 이것만 최신이면 나머지는 따라옵니다.
  2. 해시가 박힌 asset 은 **영원히 캐시한다.** 내용이 바뀌면 이름이
     바뀌므로 안전하고, 그래야 매번 다시 받지 않습니다.
"""

import pytest
from fastapi.testclient import TestClient

from app.api.main import DIST, app


@pytest.fixture
def client():
    return TestClient(app)


pytestmark = pytest.mark.skipif(
    not DIST.is_dir(), reason="frontend/dist 가 없으면 화면 라우트 자체가 안 붙습니다"
)


def test_index_는_매번_물어본다(client):
    """**이 문서 하나가 나머지를 정합니다.** 이것이 캐시되면 그것이 가리키는
    옛 asset 이 통째로 따라와서, 고친 화면이 폰에만 안 나옵니다."""
    r = client.get("/")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


def test_깊은_주소도_매번_물어본다(client):
    """`/lectures` 를 새로고침해 들어오는 것이 폰에서는 오히려 흔합니다."""
    r = client.get("/lectures")
    assert r.status_code == 200
    assert "no-cache" in r.headers.get("cache-control", "")


def test_해시_박힌_asset_은_영원히_캐시한다(client):
    """이름에 내용 해시가 있어(index-BbuZIiXP.js) 내용이 바뀌면 이름이
    바뀝니다. 매번 물어보게 두면 폰에서 켤 때마다 300KB 를 다시 받습니다."""
    assets = list((DIST / "assets").glob("*.js"))
    assert assets, "빌드 결과에 js 가 없습니다"

    r = client.get(f"/assets/{assets[0].name}")
    assert r.status_code == 200
    cc = r.headers.get("cache-control", "")
    assert "immutable" in cc and "max-age=31536000" in cc


def test_이름이_고정인_것은_영원히_캐시하지_않는다(client):
    """favicon 은 해시가 안 붙습니다. immutable 로 두면 아이콘을 바꿔도
    영영 안 바뀝니다."""
    if not (DIST / "favicon.svg").is_file():
        pytest.skip("favicon.svg 가 없습니다")
    r = client.get("/favicon.svg")
    assert r.status_code == 200
    assert "immutable" not in r.headers.get("cache-control", "")


def test_api_는_화면_폴백으로_새지_않는다(client):
    """없는 API 가 HTML 을 돌려주면 프론트가 JSON 파싱에서 엉뚱하게
    죽습니다. 캐시 헤더를 붙이면서 이 갈래를 건드리기 쉬워 같이 잠급니다."""
    assert client.get("/api/v1/그런것없음").status_code == 404
