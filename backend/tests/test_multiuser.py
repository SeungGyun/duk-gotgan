"""여러 사람이 쓸 때 서로 섞이지 않는가 — 실제 HTTP 로 확인합니다.

라우트 시험이 그동안 하나도 없었습니다. 이번 변경은 거의 전부 라우트라,
"모델은 맞는데 화면에는 남의 것이 보인다" 가 그대로 지나갈 수 있습니다.
그래서 여기서는 함수를 부르지 않고 **쿠키를 들고 실제로 호출**합니다.

특히 조용히 어긋나는 것들을 봅니다:
  - 남의 키워드가 데려온 강의가 내 목록에 오르는가
  - 내가 읽음 표시한 것이 남에게도 읽음으로 보이는가
  - 구독을 끊었을 때 즐겨찾기 해 둔 것까지 사라지는가
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.main import app
from app.db.models import Base, Keyword, Lecture, User, Video, VideoKeyword
from app.db.session import get_db
from app.security import hash_pin
from config.settings import settings
from config.time import now_kst

API = "/api/v1"


@pytest.fixture
def session_factory():
    url = settings.database_url.replace("/dukgotgan?", "/dukgotgan_test?")
    engine = create_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def db(session_factory):
    s = session_factory()
    yield s
    s.close()


@pytest.fixture
def client(session_factory, db):
    """앱의 DB 의존성만 시험용으로 바꿉니다 — 라우트는 그대로 탑니다."""

    def _db():
        s = session_factory()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


# ── 데이터 만들기 ──────────────────────────────────────────────


def a_user(db, name, pin=None, owner=False):
    u = User(name=name, password_hash=hash_pin(pin) if pin else None, is_owner=owner)
    db.add(u)
    db.commit()
    return u


def a_keyword(db, term, by=None):
    """`by` 가 만든 사람입니다 — **삭제와 제외를 가르는 값**이라, 삭제까지
    보는 시험은 반드시 넣어야 합니다(라우트로 만들면 자동으로 찍힙니다)."""
    k = Keyword(term=term, status="active", created_by=by.id if by else None)
    db.add(k)
    db.commit()
    return k


def a_lecture(db, video_id, keyword, title="강의", channel_id="ch1"):
    db.add(
        Video(
            id=video_id, title=title, channel_id=channel_id, channel_title="채널",
            state="PUBLISHED", duration_sec=1800, published_at=now_kst(),
        )
    )
    db.flush()
    db.add(VideoKeyword(video_id=video_id, keyword_id=keyword.id))
    db.add(
        Lecture(
            video_id=video_id, version=1, expert_score=80, verdict="expert",
            one_liner=title, sections=[], tags=[], duration_sec=1800,
        )
    )
    db.commit()


def login(client, user, pin=None):
    r = client.post(f"{API}/session", json={"userId": user.id, "pin": pin})
    assert r.status_code == 200, r.text
    return r


def fresh(db):
    """열려 있던 트랜잭션을 끊고 다시 읽습니다.

    라우트는 자기 세션에서 커밋하는데, 테스트 세션은 두 겹으로 옛 값을
    붙들고 있습니다 — MySQL 기본 격리 수준(REPEATABLE READ)이 트랜잭션
    시작 시점의 스냅샷을 보여 주고, 그 위에 SQLAlchemy 가 이미 읽은
    객체를 캐시해 둡니다.
    """
    # `rollback()` 만으로는 부족합니다. 세션이 이미 읽어 둔 객체를 그대로
    # 돌려주기 때문에, **DB 에는 바뀌어 있는데 테스트만 옛 값을 봅니다** —
    # 예전에 이걸로 "삭제가 안 먹었다" 고 한참 헤맸습니다. `close()` 는
    # 트랜잭션을 끝내고 읽어 둔 것까지 비웁니다.
    db.close()
    return db


def titles(client):
    """목록은 쪽으로 옵니다. 시험은 몇 편 안 되니 한 번에 받습니다."""
    r = client.get(f"{API}/lectures", params={"limit": 200})
    assert r.status_code == 200, r.text
    return {x["title"] for x in r.json()["items"]}


# ── 시험 ───────────────────────────────────────────────────────


def test_쿠키가_없으면_목록을_주지_않는다(client, db):
    a_user(db, "주인", owner=True)
    assert client.get(f"{API}/lectures").status_code == 401


def test_선택_화면은_로그인_없이_열린다(client, db):
    """이게 로그인 전 화면이라 여기까지 막으면 들어갈 방법이 없습니다."""
    a_user(db, "주인", pin="0000", owner=True)
    a_user(db, "아내")
    r = client.get(f"{API}/users")
    assert r.status_code == 200
    names = [u["name"] for u in r.json()]
    assert names == ["주인", "아내"], "주인이 앞에 와야 누르는 자리를 외울 수 있습니다"
    assert r.json()[0]["hasPin"] is True
    assert r.json()[1]["hasPin"] is False


def test_비번을_건_사람은_비번이_있어야_들어간다(client, db):
    u = a_user(db, "주인", pin="0000", owner=True)
    assert client.post(f"{API}/session", json={"userId": u.id}).status_code == 401
    assert client.post(f"{API}/session", json={"userId": u.id, "pin": "1111"}).status_code == 401
    assert client.post(f"{API}/session", json={"userId": u.id, "pin": "0000"}).status_code == 200


def test_비번을_안_건_사람은_누르면_바로_들어간다(client, db):
    u = a_user(db, "아내")
    assert client.post(f"{API}/session", json={"userId": u.id}).status_code == 200
    assert client.get(f"{API}/me").json()["name"] == "아내"


def test_비번을_여러_번_틀리면_잠긴다(client, db):
    """네 자리는 만 가지뿐입니다. **짧은 비밀번호를 쓸 수 있게 만드는 것이
    이 잠금입니다** — 없으면 그냥 다 찍어 볼 수 있습니다."""
    from app.api import auth

    auth._tries.clear()
    u = a_user(db, "주인", pin="0000", owner=True)
    for _ in range(auth.MAX_TRIES - 1):
        assert client.post(f"{API}/session", json={"userId": u.id, "pin": "9999"}).status_code == 401
    r = client.post(f"{API}/session", json={"userId": u.id, "pin": "9999"})
    assert r.status_code == 429
    # 잠긴 동안에는 **맞는 비밀번호도** 안 받습니다 — 안 그러면 잠금이
    # 시도를 늦추기만 할 뿐 막지는 못합니다.
    assert client.post(f"{API}/session", json={"userId": u.id, "pin": "0000"}).status_code == 429
    auth._tries.clear()


def test_내_키워드가_데려온_것만_보인다(client, db):
    """가장 기본입니다. 이게 새면 나머지는 볼 것도 없습니다."""
    from app.db.models import UserKeyword

    쿠버 = a_keyword(db, "쿠버네티스")
    요리 = a_keyword(db, "요리")
    a_lecture(db, "vid_k_000001", 쿠버, "CNI 플러그인")
    a_lecture(db, "vid_c_000001", 요리, "된장찌개")

    주인 = a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    db.add(UserKeyword(user_id=주인.id, keyword_id=쿠버.id))
    db.add(UserKeyword(user_id=아내.id, keyword_id=요리.id))
    db.commit()

    login(client, 주인)
    assert titles(client) == {"CNI 플러그인"}
    login(client, 아내)
    assert titles(client) == {"된장찌개"}


def test_읽음은_누른_사람만_바뀐다(client, db):
    from app.db.models import UserKeyword

    kw = a_keyword(db, "쿠버네티스")
    a_lecture(db, "vid_k_000001", kw, "CNI 플러그인")
    주인 = a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    for u in (주인, 아내):
        db.add(UserKeyword(user_id=u.id, keyword_id=kw.id))
    db.commit()

    login(client, 주인)
    assert client.patch(f"{API}/lectures/vid_k_000001", json={"isRead": True}).status_code == 204
    assert client.get(f"{API}/lectures").json()["items"][0]["isRead"] is True

    login(client, 아내)
    assert client.get(f"{API}/lectures").json()["items"][0]["isRead"] is False, (
        "남이 읽은 것이 내게 읽음으로 보이면 안 됩니다"
    )


def test_제외도_누른_사람만_바뀐다(client, db):
    from app.db.models import UserKeyword

    kw = a_keyword(db, "쿠버네티스")
    a_lecture(db, "vid_k_000001", kw, "CNI 플러그인")
    주인 = a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    for u in (주인, 아내):
        db.add(UserKeyword(user_id=u.id, keyword_id=kw.id))
    db.commit()

    login(client, 주인)
    client.patch(f"{API}/lectures/vid_k_000001", json={"isExcluded": True})
    assert titles(client) == set()
    assert client.get(f"{API}/lectures?excluded=true").json()["items"][0]["title"] == "CNI 플러그인"

    login(client, 아내)
    assert titles(client) == {"CNI 플러그인"}, "남이 뺀 것이 내게서 사라지면 안 됩니다"


def test_구독을_끊어도_즐겨찾기_한_것은_남는다(client, db):
    """**지운 적이 없는데 없어지는 것**이 가장 나쁩니다. 키워드 하나를
    끊었다고 아껴 둔 강의가 통째로 사라지면 안 됩니다."""
    from app.db.models import UserKeyword

    주인 = a_user(db, "주인", owner=True)
    kw = a_keyword(db, "쿠버네티스", by=주인)
    a_lecture(db, "vid_k_000001", kw, "아껴 둔 것")
    a_lecture(db, "vid_k_000002", kw, "그냥 것")
    db.add(UserKeyword(user_id=주인.id, keyword_id=kw.id))
    db.commit()

    login(client, 주인)
    client.patch(f"{API}/lectures/vid_k_000001", json={"isFavorite": True})
    assert client.delete(f"{API}/keywords/{kw.id}").status_code == 204

    assert titles(client) == {"아껴 둔 것"}


def test_마지막_구독자가_빠지면_수집이_멈춘다(client, db):
    """보는 사람이 0명인데 매일 수집하면 유튜브 유닛도 요약 토큰도
    아무도 안 읽을 것에 씁니다.

    빠지는 문이 둘(삭제·제외)이라도 세는 곳은 하나여야 합니다 — 한쪽만
    세면 그쪽으로 나간 사람은 없는 셈이 됩니다."""
    from app.db.models import UserKeyword

    주인 = a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    kw = a_keyword(db, "쿠버네티스", by=주인)
    for u in (주인, 아내):
        db.add(UserKeyword(user_id=u.id, keyword_id=kw.id))
    db.commit()

    login(client, 주인)
    client.delete(f"{API}/keywords/{kw.id}")
    assert fresh(db).get(Keyword, kw.id).status == "active", "아내가 아직 보고 있습니다"

    # 아내는 만든 사람이 아니라 제외로 나갑니다. 그래도 마지막 한 명입니다.
    login(client, 아내)
    client.post(f"{API}/keywords/{kw.id}/exclude")
    assert fresh(db).get(Keyword, kw.id).status == "archived"


def test_끊은_키워드는_내_삭제_영역에_남는다(client, db):
    """행째로 지우면 되살릴 방법이 없어집니다."""
    from app.db.models import UserKeyword

    주인 = a_user(db, "주인", owner=True)
    kw = a_keyword(db, "쿠버네티스", by=주인)
    db.add(UserKeyword(user_id=주인.id, keyword_id=kw.id))
    db.commit()

    login(client, 주인)
    client.delete(f"{API}/keywords/{kw.id}")
    gone = client.get(f"{API}/keywords?archived=true").json()
    assert [k["term"] for k in gone] == ["쿠버네티스"]

    assert client.post(f"{API}/keywords/{kw.id}/restore").status_code == 200
    mine = client.get(f"{API}/keywords").json()
    assert [k["term"] for k in mine] == ["쿠버네티스"]


def test_남이_만든_키워드는_구독만_하면_된다(client, db):
    """**수집 비용이 전혀 늘지 않는 길입니다.** 같은 검색어로 새로 만들면
    연결이 끊기고 수집도 두 번 돕니다."""
    from app.db.models import UserKeyword

    kw = a_keyword(db, "쿠버네티스")
    a_lecture(db, "vid_k_000001", kw, "CNI 플러그인")
    주인 = a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    db.add(UserKeyword(user_id=주인.id, keyword_id=kw.id))
    db.commit()

    login(client, 아내)
    assert titles(client) == set()
    others = client.get(f"{API}/keywords?mine=false").json()
    assert [k["term"] for k in others] == ["쿠버네티스"]
    assert others[0]["isMine"] is False

    assert client.post(f"{API}/keywords/{kw.id}/subscribe").status_code == 201
    assert titles(client) == {"CNI 플러그인"}
    assert fresh(db).query(Keyword).count() == 1, "키워드 행이 늘면 수집이 두 배가 됩니다"


def test_남이_만든_키워드는_구독해도_못_고친다(client, db):
    """설정은 키워드에 붙어 있어 고치면 **구독자 모두에게** 퍼집니다.

    구독자면 누구나 고칠 수 있게 두면 남이 정해 둔 값을 모르고 바꾸게
    됩니다 — 알림도 되돌릴 방법도 없이. 그래서 만든 사람에게만 엽니다.
    """
    아내 = a_user(db, "아내")
    주인 = a_user(db, "주인", owner=True)

    # 아내가 만듭니다 — 라우트를 그대로 태워야 created_by 가 찍힙니다.
    login(client, 아내)
    made = client.post(f"{API}/keywords", json={"term": "쿠버네티스", "minDurationSec": 300})
    assert made.status_code == 201, made.text
    kid = made.json()["id"]
    assert made.json()["canEdit"] is True
    assert made.json()["createdByName"] == "아내"

    # 주인이 구독합니다. 구독은 누구나 됩니다.
    login(client, 주인)
    assert client.post(f"{API}/keywords/{kid}/subscribe").status_code == 201

    mine = client.get(f"{API}/keywords").json()
    assert [k["term"] for k in mine] == ["쿠버네티스"]
    assert mine[0]["isMine"] is True, "구독은 됐습니다"
    assert mine[0]["canEdit"] is False, "만든 사람이 아니면 못 고칩니다"
    assert mine[0]["createdByName"] == "아내", "왜 못 고치는지 화면이 말할 수 있어야 합니다"

    # 설정 수정도, 일시정지도 같은 통로로 막힙니다.
    r = client.patch(f"{API}/keywords/{kid}", json={"minExpertScore": 10})
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "NOT_KEYWORD_AUTHOR"
    assert client.patch(f"{API}/keywords/{kid}", json={"status": "paused"}).status_code == 403
    assert fresh(db).get(Keyword, kid).min_expert_score != 10, "값이 실제로 안 바뀌어야 합니다"

    # **주인도 예외가 아닙니다.** 규칙이 하나라야 화면에서 설명할 것이 없습니다.
    assert fresh(db).get(Keyword, kid).status != "paused"

    # 삭제도 같은 문에서 막힙니다. 내 구독만 끊는 일인데 "삭제" 라고 부르면
    # 눌린 뒤에 남는 자리가 삭제 영역이라, 지운 적 없는 남의 키워드가 내
    # 휴지통에 쌓입니다 — 그리고 그건 아직 돌고 있습니다.
    r = client.delete(f"{API}/keywords/{kid}")
    assert r.status_code == 403, r.text
    assert r.json()["error"]["code"] == "NOT_KEYWORD_AUTHOR"

    # 대신 제외 — 내 구독만 끊는 일이라 누구나 됩니다.
    assert client.post(f"{API}/keywords/{kid}/exclude").status_code == 200


def test_남이_만든_키워드는_제외해도_수집이_돈다(client, db):
    """담아 봤다가 무르는 일이 삭제여선 안 됩니다.

    삭제 영역은 **수집이 멎은 것**을 두는 자리입니다. 만든 사람이 아직 보고
    있으면 그 키워드는 그대로 도는데, 그것까지 거기 넣으면 한쪽은 "지웠다",
    한쪽은 "돌고 있다" 고 말하게 됩니다. 다시 담을 자리도 흐려집니다.
    """
    아내 = a_user(db, "아내")
    주인 = a_user(db, "주인", owner=True)

    login(client, 아내)
    kid = client.post(f"{API}/keywords", json={"term": "쿠버네티스"}).json()["id"]

    login(client, 주인)
    assert client.post(f"{API}/keywords/{kid}/subscribe").status_code == 201
    r = client.post(f"{API}/keywords/{kid}/exclude")
    assert r.status_code == 200, r.text
    assert r.json()["isMine"] is False

    assert fresh(db).get(Keyword, kid).status != "archived", "아내가 아직 보고 있습니다"
    assert client.get(f"{API}/keywords").json() == [], "내 목록에서는 빠집니다"
    assert client.get(f"{API}/keywords?archived=true").json() == [], "삭제 영역에는 안 갑니다"

    # 다시 담는 자리는 한 곳 — "다른 사람도 보는 키워드" 입니다.
    others = client.get(f"{API}/keywords?mine=false").json()
    assert [k["term"] for k in others] == ["쿠버네티스"]
    assert client.post(f"{API}/keywords/{kid}/subscribe").status_code == 201
    assert [k["term"] for k in client.get(f"{API}/keywords").json()] == ["쿠버네티스"]


def test_마지막_사람이_제외하면_수집이_멎고_삭제_영역에_남는다(client, db):
    """제외라도 보는 사람이 0명이 되면 멈춥니다.

    아무도 안 읽을 것을 매일 수집하면 유튜브 유닛도 자막도 요약 토큰도 그냥
    나갑니다. 대신 되살릴 자리를 남겨야 하므로, 그때는 삭제 영역에 둡니다 —
    "다른 사람도 보는 키워드" 에는 보관된 것이 안 나오기 때문입니다.
    """
    아내 = a_user(db, "아내")
    주인 = a_user(db, "주인", owner=True)

    login(client, 아내)
    kid = client.post(f"{API}/keywords", json={"term": "쿠버네티스"}).json()["id"]

    login(client, 주인)
    client.post(f"{API}/keywords/{kid}/subscribe")

    # 만든 사람이 먼저 빠집니다. 주인이 아직 보고 있으니 수집은 계속됩니다.
    login(client, 아내)
    assert client.delete(f"{API}/keywords/{kid}").status_code == 204
    assert fresh(db).get(Keyword, kid).status != "archived"

    login(client, 주인)
    r = client.post(f"{API}/keywords/{kid}/exclude")
    assert r.status_code == 200 and r.json()["status"] == "archived"
    assert fresh(db).get(Keyword, kid).status == "archived"

    gone = client.get(f"{API}/keywords?archived=true").json()
    assert [k["term"] for k in gone] == ["쿠버네티스"], "되살릴 자리가 있어야 합니다"
    assert client.post(f"{API}/keywords/{kid}/restore").status_code == 200


def test_만든_사람은_고칠_수_있다(client, db):
    아내 = a_user(db, "아내")
    login(client, 아내)
    kid = client.post(f"{API}/keywords", json={"term": "카프카"}).json()["id"]

    r = client.patch(f"{API}/keywords/{kid}", json={"minExpertScore": 10})
    assert r.status_code == 200, r.text
    assert fresh(db).get(Keyword, kid).min_expert_score == 10


def test_키워드는_열_개까지_주인은_예외(client, db):
    from app.db.models import UserKeyword

    아내 = a_user(db, "아내")
    주인 = a_user(db, "주인", owner=True)
    kws = [a_keyword(db, f"주제{i}") for i in range(12)]
    for k in kws[: settings.max_keywords_per_user]:
        db.add(UserKeyword(user_id=아내.id, keyword_id=k.id))
    for k in kws:
        db.add(UserKeyword(user_id=주인.id, keyword_id=k.id))
    db.commit()

    login(client, 아내)
    r = client.post(f"{API}/keywords/{kws[10].id}/subscribe")
    assert r.status_code == 409 and r.json()["error"]["code"] == "KEYWORD_LIMIT"

    # 주인은 이미 12개를 쓰고 있습니다 — 상한이 생겼다고 지우라고 할 수 없습니다.
    login(client, 주인)
    assert client.get(f"{API}/me").json()["keywordLimit"] == 0


def test_주인이_아니어도_키워드를_만들_수_있다(client, db):
    """막으면 두 번째 사람에게 곳간이 "남이 고른 것만 읽는 곳" 이 됩니다.
    1인 10개 상한이 이미 피해를 묶고 있습니다."""
    a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    login(client, 아내)
    r = client.post(f"{API}/keywords", json={"term": "된장찌개", "sourceType": "search"})
    assert r.status_code == 201 and r.json()["isMine"] is True


def test_실행_버튼은_주인만(client, db):
    """보는 것은 막지 않습니다 — 왜 아직 안 올라왔는지 스스로 확인할 수
    있어야 물어볼 일이 줍니다."""
    a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    login(client, 아내)
    assert client.get(f"{API}/runs").status_code == 200
    assert client.get(f"{API}/queue").status_code == 200
    assert client.post(f"{API}/runs").status_code == 403
    assert client.delete(f"{API}/lectures/vid_k_000001").status_code == 403


def test_주인은_비밀번호를_비울_수_없다(client, db):
    """선택 화면에 주인이 그냥 떠 있어서, 비면 누구나 주인이 됩니다 —
    그러면 "주인만" 이 잠금이 아니라 표시가 됩니다."""
    from app.api import auth

    auth._tries.clear()
    주인 = a_user(db, "주인", pin="0000", owner=True)
    login(client, 주인, "0000")
    r = client.put(f"{API}/me/pin", json={"current": "0000", "next": None})
    assert r.status_code == 400 and r.json()["error"]["code"] == "OWNER_NEEDS_PIN"
    assert client.put(f"{API}/me/pin", json={"current": "0000", "next": "1234"}).status_code == 204
    assert client.get(f"{API}/me").json()["pinIsDefault"] is False


def test_새로_온_사람은_있는_키워드에서_고른다(client, db):
    kw = a_keyword(db, "쿠버네티스")
    a_lecture(db, "vid_k_000001", kw, "CNI 플러그인")
    a_user(db, "주인", owner=True)

    r = client.post(
        f"{API}/users", json={"name": "현우", "pin": "1234", "keywordIds": [kw.id]}
    )
    assert r.status_code == 201
    # 만들자마자 그 사람으로 들어가 있고, 고른 키워드의 강의가 채워집니다 —
    # 빈 곳간으로 시작하면 고장인지 아닌지 구분이 안 됩니다.
    assert client.get(f"{API}/me").json()["name"] == "현우"
    assert titles(client) == {"CNI 플러그인"}


def test_사용자_바꾸기는_이_기기만_나간다(client, db):
    주인 = a_user(db, "주인", owner=True)
    login(client, 주인)
    assert client.delete(f"{API}/session").status_code == 204
    assert client.get(f"{API}/me").status_code == 401
    # 계정은 그대로 남아 선택 화면에 다시 뜹니다
    assert [u["name"] for u in client.get(f"{API}/users").json()] == ["주인"]


def test_상단바_숫자와_목록이_같은_것을_센다(client, db):
    """상단바에는 332편인데 목록에는 41편이면 어느 쪽이 고장인지
    알 수 없게 됩니다."""
    from app.db.models import UserKeyword

    쿠버 = a_keyword(db, "쿠버네티스")
    요리 = a_keyword(db, "요리")
    a_lecture(db, "vid_k_000001", 쿠버, "CNI 플러그인")
    a_lecture(db, "vid_c_000001", 요리, "된장찌개")
    아내 = a_user(db, "아내")
    a_user(db, "주인", owner=True)
    db.add(UserKeyword(user_id=아내.id, keyword_id=요리.id))
    db.commit()

    login(client, 아내)
    assert client.get(f"{API}/stats/overview").json()["totalLectures"] == len(titles(client))


# ── 회사별 토큰 상한 ───────────────────────────────────────────


def test_회사별_사용량이_따로_보인다(client, db):
    """합쳐 놓으면 어느 쪽이 상한에 닿아 멈췄는지 알 수 없습니다 — 실제로
    한쪽 쿼터가 떨어졌는데 화면에는 "많이 썼네"로만 보였습니다."""
    from app.db.models import UsageWindow
    from app.llm import usage as usage_guard

    주인 = a_user(db, "주인", owner=True)
    start = usage_guard.window_start()
    db.add(UsageWindow(start=start, provider="claude", input_tokens=100, output_tokens=10))
    db.add(UsageWindow(start=start, provider="antigravity", input_tokens=7, output_tokens=3))
    db.commit()

    login(client, 주인)
    body = client.get(f"{API}/stats/usage").json()

    by = {p["provider"]: p for p in body["providers"]}
    assert by["claude"]["inputTokens"] == 100
    assert by["antigravity"]["outputTokens"] == 3
    # 두 회사 다 나와야 합니다 — 아직 안 쓴 회사도 상한을 걸 수 있어야 합니다
    assert set(by) >= {"claude", "antigravity"}
    # 합계는 여전히 맞습니다 (상단 미터가 이걸 씁니다)
    assert body["inputTokens"] == 107


def test_회사별_상한은_따로_걸린다(client, db):
    """한 값으로 묶으면 한쪽이 많이 쓴 것 때문에 아직 여유가 있는 쪽까지
    멈춥니다 — 토큰이 모자라서 회사를 늘렸는데 정반대가 됩니다."""
    from app.llm import usage as usage_guard

    주인 = a_user(db, "주인", owner=True)
    login(client, 주인)

    r = client.put(
        f"{API}/stats/usage/limit", json={"limitTokens": 9_000_000, "provider": "antigravity"}
    )
    assert r.status_code == 204, r.text

    body = client.get(f"{API}/stats/usage").json()
    by = {p["provider"]: p for p in body["providers"]}
    assert by["antigravity"]["limitTokens"] == 9_000_000
    assert by["antigravity"]["hasOwnLimit"] is True
    # 건드리지 않은 쪽은 공용 값을 그대로 씁니다
    assert by["claude"]["hasOwnLimit"] is False
    assert by["claude"]["limitTokens"] != 9_000_000


def test_회사_상한을_공용으로_되돌린다(client, db):
    """올려 봤다가 무르는 길이 없으면, 한 번 건드린 회사는 영영 따로
    관리해야 합니다."""
    주인 = a_user(db, "주인", owner=True)
    login(client, 주인)

    client.put(f"{API}/stats/usage/limit", json={"limitTokens": 1_000_000, "provider": "claude"})
    r = client.put(f"{API}/stats/usage/limit", json={"provider": "claude", "inherit": True})
    assert r.status_code == 204, r.text

    by = {p["provider"]: p for p in client.get(f"{API}/stats/usage").json()["providers"]}
    assert by["claude"]["hasOwnLimit"] is False
    assert by["claude"]["limitTokens"] != 1_000_000


def test_모르는_회사는_거절한다(client, db):
    """오타가 조용히 저장되면, 화면에는 안 보이는 값이 DB 에 남아
    "분명 걸었는데 왜 안 먹지"가 됩니다."""
    주인 = a_user(db, "주인", owner=True)
    login(client, 주인)
    r = client.put(
        f"{API}/stats/usage/limit", json={"limitTokens": 1_000, "provider": "안티그래비티"}
    )
    assert r.status_code == 400


def test_상한은_주인만_바꾼다(client, db):
    """**보는 것은 막지 않습니다** — 식구도 얼마나 썼는지는 봅니다.
    바꾸는 것만 주인입니다."""
    a_user(db, "주인", owner=True)
    아내 = a_user(db, "아내")
    login(client, 아내)

    assert client.get(f"{API}/stats/usage").status_code == 200
    assert client.put(f"{API}/stats/usage/limit", json={"limitTokens": 0}).status_code == 403
    assert (
        client.put(
            f"{API}/stats/usage/limit", json={"limitTokens": 0, "provider": "claude"}
        ).status_code
        == 403
    )


# ── 검색 기간 ──────────────────────────────────────────────────


def test_기간은_키워드마다_따로_저장된다(client, db):
    """`경제` 는 1일, `면역력` 은 90일. 한 키워드를 고쳐도 다른 키워드는
    그대로여야 합니다 — 전역값 하나였을 때는 이게 불가능했습니다."""
    주인 = a_user(db, "관리자", owner=True)
    login(client, 주인)

    빠름 = client.post(f"{API}/keywords", json={"term": "주식", "searchWindowDays": 1})
    느림 = client.post(f"{API}/keywords", json={"term": "면역력", "searchWindowDays": 90})
    assert 빠름.json()["searchWindowDays"] == 1
    assert 느림.json()["searchWindowDays"] == 90

    r = client.patch(f"{API}/keywords/{빠름.json()['id']}", json={"searchWindowDays": 3})
    assert r.status_code == 200 and r.json()["searchWindowDays"] == 3

    쪽 = {k["term"]: k["searchWindowDays"] for k in client.get(f"{API}/keywords").json()}
    assert 쪽 == {"주식": 3, "면역력": 90}


def test_기간_기본은_석_달(client, db):
    """안 정하고 만들면 예전과 같은 폭입니다 — 새 손잡이가 생겼다고
    기존 등록 습관이 조용히 좁아지면 안 됩니다."""
    주인 = a_user(db, "관리자", owner=True)
    login(client, 주인)
    r = client.post(f"{API}/keywords", json={"term": "과학"})
    assert r.json()["searchWindowDays"] == 90


@pytest.mark.parametrize("days", [0, 91, 365])
def test_기간은_석_달을_넘길_수_없다(client, db, days):
    """상한을 화면에서만 막으면 API 로는 열려 있습니다. 넓히는 것은 새
    것을 모으는 일이 아니라 과거를 긁는 일이고, 요약 비용이 통째로
    그쪽으로 갑니다."""
    주인 = a_user(db, "관리자", owner=True)
    login(client, 주인)

    assert client.post(f"{API}/keywords", json={"term": "경제", "searchWindowDays": days}).status_code == 400
    kw = client.post(f"{API}/keywords", json={"term": "경제"}).json()
    assert client.patch(f"{API}/keywords/{kw['id']}", json={"searchWindowDays": days}).status_code == 400
