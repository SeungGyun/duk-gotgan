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
    를 내고, 자막을 못 읽은 채 요약을 지어내려다 끊겼습니다.

    **실제 경로로 엽니다.** 맥에서 `/tmp` 는 `/private/tmp` 입니다 —
    샌드박스와 프롬프트가 쓰는 경로와 어긋나면 안 됩니다."""
    from app.llm import agy, workspace

    ws = workspace.Workspace(path=Path("/tmp/dukgotgan-jobs/x"), video_id="x")
    argv = agy._argv(ws, Path("/tmp/x.schema.json"), None)
    assert "--add-dir" in argv
    assert argv[argv.index("--add-dir") + 1] == str(ws.path.resolve())


def test_자막_경로를_절대_경로로_알려준다():
    """이름만 알려주면 agy 가 자기 brain 폴더 기준으로 풉니다. 실측에서
    `/metadata.json` · `/tmp/metadata.json` ·
    `~/.gemini/antigravity-cli/brain/<uuid>/workspace/metadata.json` 세 군데를
    찍어 보다 죽었습니다 — 사흘에 9편."""
    from app.llm import agy, workspace

    ws = workspace.Workspace(path=Path("/tmp/dukgotgan-jobs/x"), video_id="x")
    argv = agy._argv(ws, Path("/tmp/x.schema.json"), None)
    prompt = argv[argv.index("-p") + 1]
    root = ws.path.resolve()
    assert str(root / "transcript.md") in prompt
    assert str(root / "metadata.json") in prompt


def test_캐시_프리픽스는_영상마다_같다():
    """영상마다 달라지는 것이 앞에 끼면 프롬프트 캐시가 매번 깨집니다.
    절대 경로는 **뒤에** 붙어야 합니다."""
    from app.llm import agy, workspace

    a = agy._argv(workspace.Workspace(Path("/tmp/dukgotgan-jobs/a"), "a"), Path("/tmp/s"), None)
    b = agy._argv(workspace.Workspace(Path("/tmp/dukgotgan-jobs/b"), "b"), Path("/tmp/s"), None)
    pa, pb = a[a.index("-p") + 1], b[b.index("-p") + 1]
    assert pa != pb, "영상마다 다른 지시가 들어가야 합니다"
    common = agy._prompt()
    assert pa.startswith(common) and pb.startswith(common)


def test_agy_에는_관대한_스키마를_넘긴다():
    """저쪽 검증기가 거부하면 우리 `Coercing` 이 고칠 기회 자체가 없습니다.
    실측(2026-08-21~23) 요약 실패 18건 중 12건이 `invalid arguments:` 였고,
    전부 받아서 고칠 수 있는 것이었습니다."""
    import json as _json

    from app.llm.schemas import LectureReview, flat_schema, lenient_schema

    loose = _json.dumps(lenient_schema(LectureReview), ensure_ascii=False)
    # 상한은 뺍니다 — 넘치면 우리가 잘라 담습니다
    assert "maxItems" not in loose and "maxLength" not in loose
    # 하한은 남깁니다 — 없는 내용을 지어낼 수는 없습니다
    assert "minItems" in loose
    # 클로드 쪽은 그대로 엄합니다 (되물을 수 있으니 엄한 편이 낫습니다)
    assert "maxItems" in _json.dumps(flat_schema(LectureReview))

    props = lenient_schema(LectureReview)["properties"]
    kinds = {b.get("type") for b in props["speaker_credentials"]["anyOf"]}
    assert kinds == {"string", "object", "null"}, "객체로 와도 받아야 합니다"
    # 필드 이름표까지 지우면 안 됩니다 — `title` 은 섹션 제목이기도 합니다
    section = props["summary"]["anyOf"][0]["properties"]["sections"]["items"]
    assert set(section["properties"]) == {"title", "start_sec", "bullets"}


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


def test_모델_이름이_회사를_따라간다(monkeypatch):
    """`evaluations.model` 이 전부 클로드로 적히면, 나중에 어느 회사가
    어떤 판정을 냈는지 되짚을 수 없습니다."""
    from app.llm import agy
    from config.settings import Settings

    assert Settings(review_provider="claude").active_review_model.startswith("claude")
    assert Settings(review_provider="antigravity", agy_model="x-high").active_review_model == "x-high"
    # 안 박아 두면 agy 설정의 기본 모델이 적힙니다
    monkeypatch.setattr(agy, "default_model", lambda: "Gemini 3.8 Flash (Medium)")
    assert Settings(review_provider="antigravity").active_review_model == "Gemini 3.8 Flash (Medium)"


def test_모델을_박아_두지_않으면_넘기지_않는다(monkeypatch):
    """`gemini-3.1-pro-high` 가 목록에서 사라지자 모든 요약이 `invalid model
    selection` 으로 죽었습니다. 비워 두면 `--model` 자체를 붙이지 않아
    agy 가 자기 기본 모델로 돕니다."""
    from app.llm import agy, workspace
    from config.settings import settings

    ws = workspace.Workspace(Path("/tmp/dukgotgan-jobs/v"), "v")
    monkeypatch.setattr(settings, "agy_model", "")
    assert "--model" not in agy._argv(ws, Path("/tmp/s"), None)
    monkeypatch.setattr(settings, "agy_model", "x-high")
    argv = agy._argv(ws, Path("/tmp/s"), None)
    assert argv[argv.index("--model") + 1] == "x-high"


def test_agy_기본_모델은_설정_파일에서_읽는다(tmp_path, monkeypatch):
    """결과 JSON 에 모델이 안 실려서, 설정 파일을 봐야 장부에 적을 이름이
    나옵니다. 못 읽으면 지어내지 않고 회사 이름으로 적습니다."""
    from app.llm import agy

    f = tmp_path / "settings.json"
    monkeypatch.setattr(agy, "_AGY_SETTINGS", f)
    assert agy.default_model() == "antigravity-default"
    f.write_text('{"model": "Gemini 3.8 Flash (Medium)"}', encoding="utf-8")
    assert agy.default_model() == "Gemini 3.8 Flash (Medium)"


def test_실행_환경을_최소로_넘긴다():
    """자막이 환경변수를 통해 무언가를 흘리지 못하게 합니다."""
    from app.llm import agy

    src = inspect.getsource(agy.review)
    assert '"HOME"' in src and "os.environ.copy()" not in src


def test_타임아웃에_communicate_를_다시_부르지_않는다():
    """취소된 첫 호출이 파이프를 물고 있어서 두 번째 호출이 영영 안
    끝납니다. 그렇게 워커가 72분을 멎어 있었습니다 — 자식은 이미 죽었는데
    죽은 자식의 파이프 두 개를 붙든 채로요. 그동안 요약은 0건이었습니다."""
    from app.llm import agy

    src = inspect.getsource(agy.review)
    after = src.split("except asyncio.TimeoutError:")[1]
    assert "proc.communicate()" not in after, "타임아웃 뒤에 다시 부르면 안 됩니다"
    assert "_kill_group(proc)" in after


def test_프로세스_그룹째_죽인다():
    """agy 는 언어 서버·agentapi 같은 도우미를 따로 띄우고, 그것들이 우리
    파이프를 물려받습니다. 맏이만 죽이면 도우미가 쓰기 끝을 쥔 채 남아
    EOF 가 오지 않습니다."""
    from app.llm import agy

    assert "start_new_session=True" in inspect.getsource(agy.review)
    kill = inspect.getsource(agy._kill_group)
    assert "killpg" in kill
    assert "SIGKILL" in kill
    # 정리하다가 다시 멎으면 처음 문제로 되돌아갑니다
    assert "wait_for" in kill


def test_고칠_수_있는_어긋남은_고쳐서_담는다():
    """agy 는 되물을 수 없습니다. 받은 뒤에 고칠 수 있는 것은 고쳐야
    한 편을 통째로 잃지 않습니다 — 실측에서 화자 이력이 객체로 온 것
    18편, 요약이 JSON 문자열로 온 것 3편이 그대로 영구 탈락했습니다."""
    from app.llm.schemas import LectureReview

    crit = [
        {"criterion": c, "score": 50, "evidence": "e"}
        for c in ("structure", "depth", "evidence", "authority", "density", "commercial")
    ]
    summary = {
        "one_liner": "가" * 95,
        "target_audience": "a",
        "prerequisites": ["p"] * 7,
        "sections": [None, True] + [
            {"title": f"s{i}", "start_sec": i * 60, "bullets": ["b"] * 11} for i in range(5)
        ],
        "closing": "c",
        "tags": [f"t{i}" for i in range(12)],
    }
    base = {
        "verdict": "expert", "expert_score": 70, "confidence": "high", "topic": "t",
        "keyword_relevance": 90, "criteria": crit,
    }

    r = LectureReview.model_validate(
        {**base, "speaker_credentials": {"name": "홍길동", "title": "수석연구원"}, "summary": summary}
    )
    assert r.speaker_credentials == "홍길동 · 수석연구원", "객체는 한 줄로 폅니다"
    assert len(r.summary.sections) == 5, "null·bool 이 섞여도 나머지는 살립니다"
    assert len(r.summary.sections[0].bullets) == 8  # 상한까지 자릅니다
    assert len(r.summary.tags) == 8 and len(r.summary.prerequisites) == 5
    assert len(r.summary.one_liner) == 80

    import json as _json

    r2 = LectureReview.model_validate(
        {**base, "summary": _json.dumps(summary, ensure_ascii=False)}
    )
    assert r2.summary is not None, "JSON 문자열로 온 요약은 풀어서 받습니다"


def test_지어낼_수_없는_것은_그대로_거부한다():
    """상한은 잘라 담을 수 있지만 하한은 지어낼 수 없습니다. 여기까지
    관대해지면 태그 한 개짜리 요약이 조용히 쌓입니다."""
    import pytest
    from pydantic import ValidationError

    from app.llm.schemas import LectureReview

    crit = [
        {"criterion": c, "score": 50, "evidence": "e"}
        for c in ("structure", "depth", "evidence", "authority", "density", "commercial")
    ]
    with pytest.raises(ValidationError):
        LectureReview.model_validate({
            "verdict": "expert", "expert_score": 70, "confidence": "high", "topic": "t",
            "keyword_relevance": 90, "criteria": crit,
            "summary": {
                "one_liner": "x", "target_audience": "a", "prerequisites": [],
                "sections": [{"title": "s", "start_sec": 0, "bullets": ["b"]}] * 4,
                "closing": "c", "tags": ["하나", "둘"],
            },
        })


def test_회사가_잠시_후_오라면_다시_간다():
    """2026-08-21 04:11 에 안티그래비티가 몇 분 붐볐고, 그 사이 13편이
    `please try again in a minute` 을 사유로 영구 탈락했습니다. 같은
    배치의 `Agent execution terminated` 9건은 멀쩡히 다시 시도됐습니다."""
    from app.llm.runner import _is_transient

    for message in (
        "Our servers are experiencing high traffic right now, please try again in a minute.",
        "The stream was interrupted. Please continue the task you were working on.",
        ("declaring permissions: cortex tool view_file: ... open /metadata.json: "
         "no such file or directory"),
    ):
        assert _is_transient(message), message

    # 영상 쪽 문제는 그대로 탈락입니다 — 다시 불러도 같습니다
    assert not _is_transient("자막이 없습니다.")
    assert not _is_transient("요약이 오지 않았습니다 · irrelevant")
