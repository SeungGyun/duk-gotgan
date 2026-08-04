"""안티그래비티 실행기 — 두 번째 회사를 붙이며 못박아 둘 것들.

토큰이 모자라 요약 줄이 286건까지 밀렸고, 다른 회사를 붙여 나눠 가져가기로
했습니다. 받아오는 방법은 달라도 **받은 뒤는 같아야** 합니다.
"""

import inspect
import json
from pathlib import Path

PROMPTS = Path(__file__).resolve().parent.parent / "prompts"


def test_판정_로직은_한_벌뿐이다():
    """검증·점수 재계산·적재가 회사마다 따로 있으면, 한쪽에서 기준을
    고쳐도 다른 쪽은 옛 기준으로 계속 담습니다. 같은 강의가 어느 워커에
    걸리느냐에 따라 다른 점수를 받게 됩니다."""
    from app.llm import agy, tools

    assert "store.save(" in inspect.getsource(agy.review)
    assert "store.save(" in inspect.getsource(tools.build_server)
    # 껍데기에 판정이 남아 있으면 안 됩니다
    src = inspect.getsource(tools)
    for moved in ("weighted_score", "should_publish", "Lecture("):
        assert moved not in src, f"{moved} 는 store.py 에 있어야 합니다"


def test_루브릭은_두_회사가_같은_것을_읽는다():
    """전달 방식만 갈라져야 합니다. 본문이 갈라지면 판정이 갈라집니다."""
    from app.llm import agy, runner

    body = (PROMPTS / "lecture-review.md").read_text(encoding="utf-8")
    assert body in agy._prompt()
    assert body in runner._system_prompt()
    # 본문에는 전달 방식이 들어 있지 않아야 합니다
    assert "save_review" not in body
    assert "save_review" in (PROMPTS / "delivery-claude.md").read_text(encoding="utf-8")


def test_자막은_샌드박스_안에서만_읽힌다():
    """실측: 샌드박스 없이 돌렸더니 작업 폴더 밖의 `.env` 를 읽어 유튜브
    API 키까지 응답에 실어 냈습니다. agy 에는 도구를 좁히는 손잡이가 없어
    (클로드의 allowed_tools·경로 가드에 해당하는 것이 없습니다) 바깥에서
    막는 수밖에 없습니다."""
    from app.llm import agy, workspace

    ws = workspace.Workspace(path=Path("/tmp/dukgotgan-jobs/x"), video_id="x")
    argv = agy._argv(ws, Path("/tmp/x.schema.json"), Path("/tmp/x.sb"))
    assert argv[0] == "sandbox-exec", "샌드박스가 맨 앞에 와야 합니다"
    assert "-f" in argv and "/tmp/x.sb" in argv


def test_작업_폴더를_명시해서_넘긴다():
    """cwd 만으로는 부족합니다. agy 는 파일을 현재 디렉터리가 아니라 자기
    워크스페이스 기준으로 찾습니다 — 대화형 셸에서는 어쩌다 맞았는데
    launchd 로 띄우니 세 건 연속 `transcript.md 파일이 존재하지 않습니다`
    를 내고, 자막을 못 읽은 채 요약을 지어내려다 끊겼습니다."""
    from app.llm import agy, workspace

    ws = workspace.Workspace(path=Path("/tmp/dukgotgan-jobs/x"), video_id="x")
    argv = agy._argv(ws, Path("/tmp/x.schema.json"), None)
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == str(ws.path)


def test_샌드박스_규칙은_실제_경로로_쓴다():
    """seatbelt 의 subpath 는 심링크를 따라간 뒤의 경로와 맞춰 봅니다.
    맥에서 `/tmp` 는 `/private/tmp` 라, 적힌 그대로 넣으면 그 줄이 아무
    것도 걸러내지 못합니다."""
    from app.llm import agy, workspace

    src = inspect.getsource(agy._write_sandbox_profile)
    assert ".resolve()" in src

    ws = workspace.Workspace(path=Path("/tmp"), video_id="probe")
    profile = agy._SANDBOX_PROFILE.format(
        home=str(Path.home().resolve()), workspace=str(ws.path.resolve())
    )
    assert "/private/tmp" in profile or "/tmp" == str(Path("/tmp").resolve())
    assert f'(deny file-read* (subpath "{Path.home().resolve()}"))' in profile


def test_스키마는_작업_폴더_밖에_둔다():
    """안에 두면 모델이 자막인 줄 알고 읽어 토큰을 쓰고, '이 폴더에는
    파일 두 개가 있습니다'라는 프롬프트와도 어긋납니다."""
    from app.llm import agy

    src = inspect.getsource(agy._write_schema)
    assert "ws.path.parent" in src


def test_스키마가_실제로_직렬화된다():
    """`$ref` 가 남아 있으면 모델이 항목의 생김새를 모른 채 추측합니다."""
    from app.llm.schemas import LectureReview, flat_schema

    text = json.dumps(flat_schema(LectureReview))
    assert "$ref" not in text and "$defs" not in text
    assert "criterion" in text and "one_liner" in text


def test_agy_는_조기종료를_재지_않는다():
    """클로드는 고정 오버헤드가 18,700 으로 안정적이라 '총 입력 - 오버헤드'
    로 얼마나 읽었는지 잽니다. agy 는 같은 조건 실측이 42k~95k 로 흔들려서,
    그 값을 빼서 얻은 수를 '읽은 양'이라 적으면 화면의 절감 지표가
    거짓말을 합니다."""
    from app.llm import runner

    src = inspect.getsource(runner._via_agy)
    assert "run.early_exit = False" in src


def test_모델_이름이_회사를_따라간다():
    """`evaluations.model` 이 전부 클로드로 적히면, 나중에 어느 회사가
    어떤 판정을 냈는지 되짚을 수 없습니다."""
    from config.settings import Settings

    assert Settings(review_provider="antigravity").active_review_model.startswith("gemini")
    assert Settings(review_provider="claude").active_review_model.startswith("claude")


def test_실행_환경을_최소로_넘긴다():
    """자막이 환경변수를 통해 무언가를 흘리지 못하게 합니다."""
    from app.llm import agy

    src = inspect.getsource(agy.review)
    assert '"HOME"' in src and "os.environ.copy()" not in src
