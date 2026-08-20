import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import type { Keyword, Person } from "../api";
import { useAsync } from "../hooks/useAsync";
import { ErrorState, Loading } from "../components/ui";
import s from "./Who.module.css";

/** 비밀번호는 네 자리 숫자입니다. 집 안에서만 쓰고 폰으로도 자주 들어오는데,
    긴 비밀번호는 매번 입력하는 비용만 크고 얻는 것이 적습니다. 짧아도 되는
    이유는 서버가 다섯 번 틀리면 잠그기 때문입니다. */
const PIN_LEN = 4;

/** 새로 온 사람이 한 번에 고를 수 있는 키워드 수. 서버 상한과 같아야
    "골랐는데 저장이 안 되는" 일이 없습니다. */
const PICK_MAX = 10;

type Step =
  | { at: "list" }
  | { at: "pin"; person: Person }
  | { at: "remove"; person: Person }
  | { at: "name" }
  | { at: "keywords"; name: string; pin: string | null };

/** 로그인 전 화면. **이 화면만 쿠키 없이 열립니다.**
 *
 *  유튜브처럼 누구인지 먼저 고릅니다. 고른 결과가 쿠키에 남아서 다음부터는
 *  바로 들어가고, 쿠키가 없을 때만 여기로 옵니다.
 *
 *  예전 설계에서는 쿠키가 없으면 서버가 새 사람을 자동으로 만들었습니다.
 *  그러면 맥과 폰이 서로 다른 사람이 되어서, 둘을 묶을 복구 코드가 따로
 *  필요했습니다. 고르게 하면 폰에서도 그냥 자기 이름을 누르면 그만입니다. */
export function Who() {
  const people = useAsync(() => api.listPeople(), []);
  const [step, setStep] = useState<Step>({ at: "list" });
  // 지우는 손잡이는 접어 둡니다. 매번 보는 화면에 ⊗ 가 늘 떠 있으면
  // 누르려던 것 옆에 되돌릴 수 없는 것이 붙어 있는 셈입니다.
  const [editing, setEditing] = useState(false);
  // 지운 뒤 한 줄. 키워드와 강의까지 사라진 것을 나중에 빈 목록으로 알게
  // 되는 것보다, 누른 자리에서 말해 주는 편이 낫습니다.
  const [note, setNote] = useState<string | null>(null);

  const enter = () => {
    // **주소만 바꾸면 안 들어가집니다.** 위쪽 `getMe()` 는 쿠키가 없던
    // 시점에 401 로 끝난 채로 남아 있어서, 라우터로만 옮기면 그 값이
    // 그대로라 곧장 이 화면으로 되돌려 보냅니다 — 비밀번호를 맞혔는데
    // 아무 일도 안 일어난 것처럼 보입니다.
    //
    // 다시 부르라고 알려도 소용이 없었습니다. 다시 부르라는 신호는
    // **다음 렌더에서야** "부르는 중" 이 되는데, 되돌려 보내는 판단은
    // 그보다 먼저 일어납니다. 그 사이 한 번의 렌더가 틈입니다.
    //
    // 그래서 통째로 다시 읽습니다. 로그인은 자주 하는 일이 아니고,
    // 나가기(사용자 바꾸기)도 같은 방식이라 흐름이 한 가지로 모입니다.
    window.location.assign("/");
  };

  if (people.error) return <ErrorState message={people.error} onRetry={people.reload} />;
  if (!people.data) {
    return (
      <div className={s.wrap}>
        <Loading />
      </div>
    );
  }

  return (
    <div className={s.wrap}>
      <div className={s.card}>
        {step.at === "list" && (
          <Pick
            people={people.data}
            editing={editing}
            note={note}
            onPick={(p) => {
              setNote(null);
              if (p.hasPin) setStep({ at: "pin", person: p });
              else void api.pickPerson(p.id).then(enter);
            }}
            onRemove={(p) => {
              setNote(null);
              setStep({ at: "remove", person: p });
            }}
            onToggleEdit={() => {
              setNote(null);
              setEditing((v) => !v);
            }}
            onNew={() => setStep({ at: "name" })}
          />
        )}

        {step.at === "pin" && (
          <Pin
            person={step.person}
            onBack={() => setStep({ at: "list" })}
            onDone={enter}
          />
        )}

        {step.at === "remove" && (
          <RemoveStep
            person={step.person}
            onBack={() => setStep({ at: "list" })}
            onDone={(said) => {
              setNote(said);
              // 마지막 한 명을 지웠으면 접습니다 — ⊗ 가 없는 목록에서
              // "편집 끝내기" 만 남아 있으면 무엇을 편집하는 화면인지
              // 알 수 없습니다.
              setEditing(false);
              setStep({ at: "list" });
              people.reload();
            }}
          />
        )}

        {step.at === "name" && (
          <NameStep
            onBack={() => setStep({ at: "list" })}
            onNext={(name, pin) => setStep({ at: "keywords", name, pin })}
          />
        )}

        {step.at === "keywords" && (
          <KeywordStep
            name={step.name}
            pin={step.pin}
            onBack={() => setStep({ at: "name" })}
            onDone={enter}
          />
        )}
      </div>
    </div>
  );
}

/** 사람 고르기.
 *
 *  **만드는 자리가 여기라 지우는 자리도 여기입니다.** 다만 ⊗ 는 "편집"
 *  을 누른 동안에만 나옵니다 — 매번 누르는 이름 옆에 되돌릴 수 없는
 *  버튼이 늘 붙어 있을 이유가 없습니다. */
function Pick({
  people,
  editing,
  note,
  onPick,
  onRemove,
  onToggleEdit,
  onNew,
}: {
  people: Person[];
  editing: boolean;
  note: string | null;
  onPick: (p: Person) => void;
  onRemove: (p: Person) => void;
  onToggleEdit: () => void;
  onNew: () => void;
}) {
  // 관리자는 못 지웁니다. 지울 사람이 하나도 없으면 "편집" 도 내지
  // 않습니다 — 눌러도 아무 일이 없는 버튼이 됩니다.
  const removable = people.some((p) => !p.isOwner);

  return (
    <>
      <h1 className={s.ask}>누구세요?</h1>

      {note && <p className={s.note}>{note}</p>}

      <div className={s.row}>
        {people.map((p) => (
          <div key={p.id} className={s.slot}>
            <button
              type="button"
              className={`${s.who} ${p.isOwner ? s.owner : ""}`}
              onClick={() => onPick(p)}
            >
              <span className={s.avatar}>{p.name.slice(0, 1)}</span>
              <span className={s.nm}>{p.name}</span>
              <span className={s.sub}>
                {/* 누르기 전에 뭐가 있을지 보이는 편이 낫습니다. */}
                {p.lectureCount}편{p.hasPin && " · 잠김"}
              </span>
            </button>

            {editing && !p.isOwner && (
              <button
                type="button"
                className={s.del}
                onClick={() => onRemove(p)}
                aria-label={`${p.name} 지우기`}
                title={`${p.name} 지우기`}
              >
                ⊗
              </button>
            )}
          </div>
        ))}

        <div className={s.slot}>
          <button type="button" className={`${s.who} ${s.add}`} onClick={onNew}>
            <span className={s.avatar} aria-hidden="true">
              +
            </span>
            <span className={s.nm}>새로 만들기</span>
            <span className={s.sub}>&nbsp;</span>
          </button>
        </div>
      </div>

      {removable && (
        <button type="button" className={s.editToggle} onClick={onToggleEdit} aria-pressed={editing}>
          {editing ? "편집 끝내기" : "편집"}
        </button>
      )}
      {editing && <p className={s.hint}>지울 사람의 ⊗ 를 누르세요. 관리자는 지울 수 없습니다.</p>}

      {/* 처음 온 사람은 여기가 뭐 하는 곳인지 모릅니다. 이름만 늘어놓고
          고르라고 하면 아무것도 알려 주지 않은 셈입니다. */}
      <Link to="/about" className={s.aboutLink}>
        덕!곳간이 뭔가요?
      </Link>
    </>
  );
}

/** 네 자리 입력. 다 채우면 자동으로 넘어갑니다 — 확인 버튼을 한 번 더
    누르게 할 이유가 없습니다. */
function Pin({
  person,
  onBack,
  onDone,
}: {
  person: Person;
  onBack: () => void;
  onDone: () => void;
}) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLInputElement>(null);

  useEffect(() => {
    box.current?.focus();
  }, []);

  const submit = async (value: string) => {
    setBusy(true);
    try {
      await api.pickPerson(person.id, value);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "들어가지 못했습니다.");
      setPin("");
      box.current?.focus();
    } finally {
      setBusy(false);
    }
  };

  const change = (raw: string) => {
    const next = raw.replace(/\D/g, "").slice(0, PIN_LEN);
    setPin(next);
    setError(null);
    if (next.length === PIN_LEN) void submit(next);
  };

  return (
    <>
      <h1 className={s.ask}>{person.name}</h1>
      <p className={s.hint}>비밀번호 네 자리</p>

      {/* 칸 네 개는 보여 주기만 하고, 실제 입력은 뒤에 깔린 칸 하나가
          받습니다. 칸마다 input 을 두면 지우기·붙여넣기·자동완성에서
          제각각 어긋납니다. */}
      <div className={s.pinWrap} onClick={() => box.current?.focus()}>
        <input
          ref={box}
          className={s.pinInput}
          value={pin}
          onChange={(e) => change(e.target.value)}
          inputMode="numeric"
          autoComplete="off"
          // 폰에서 숫자 자판이 바로 뜨게 합니다.
          type="tel"
          maxLength={PIN_LEN}
          disabled={busy}
          aria-label="비밀번호 네 자리"
        />
        <div className={s.dots} aria-hidden="true">
          {Array.from({ length: PIN_LEN }, (_, i) => (
            <span key={i} className={i < pin.length ? s.dotOn : s.dot} />
          ))}
        </div>
      </div>

      {error && <p className={s.err}>{error}</p>}

      <button type="button" className={s.back} onClick={onBack}>
        ← 다른 사람
      </button>
    </>
  );
}

/** 지우기 전에 한 번. **되돌릴 수 없는 유일한 버튼이라 화면을 따로 씁니다.**
 *
 *  카드 위의 작은 확인 상자로 두면 무엇이 같이 사라지는지 쓸 자리가 없고,
 *  잠긴 사람에게 비밀번호를 받을 자리도 없습니다. 잠금은 여기서도 그대로
 *  지킵니다 — 들어가는 문이 잠겨 있는데 지우는 문이 열려 있으면 잠근 것이
 *  아닙니다. */
function RemoveStep({
  person,
  onBack,
  onDone,
}: {
  person: Person;
  onBack: () => void;
  onDone: (note: string) => void;
}) {
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const gone = await api.deletePerson(person.id, pin || undefined);
      const also =
        gone.removedKeywords > 0
          ? ` 아무도 안 보게 된 키워드 ${gone.removedKeywords}개와 강의 ${gone.removedLectures}편도 함께 지웠습니다.`
          : "";
      onDone(`${person.name} 님을 지웠습니다.${also}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "지우지 못했습니다.");
      setPin("");
      setBusy(false);
    }
  };

  return (
    <>
      <h1 className={s.ask}>{person.name} 님을 지울까요?</h1>
      <p className={s.hint}>
        읽음·즐겨찾기·제외 표시가 사라집니다. {person.name} 님만 보던 키워드와 그 키워드가
        데려온 강의도 함께 지워집니다 — 다른 사람도 보는 키워드는 그대로 돕니다.{" "}
        <b>되돌릴 수 없습니다.</b>
      </p>

      {person.hasPin && (
        <div className={s.form}>
          <label className={s.field}>
            <span className={s.fieldLabel}>{person.name} 님의 비밀번호 네 자리</span>
            <input
              className={s.text}
              value={pin}
              onChange={(e) => {
                setPin(e.target.value.replace(/\D/g, "").slice(0, PIN_LEN));
                setError(null);
              }}
              onKeyDown={(e) => e.key === "Enter" && void go()}
              placeholder="숫자 네 자리"
              inputMode="numeric"
              type="tel"
              autoFocus
            />
          </label>
        </div>
      )}

      {error && <p className={s.err}>{error}</p>}

      <div className={s.actions}>
        <button type="button" className={s.back} onClick={onBack} disabled={busy}>
          ← 돌아가기
        </button>
        <button type="button" className={s.danger} onClick={() => void go()} disabled={busy}>
          지우기
        </button>
      </div>
    </>
  );
}

/** 이름과 (선택) 비밀번호. */
function NameStep({
  onBack,
  onNext,
}: {
  onBack: () => void;
  onNext: (name: string, pin: string | null) => void;
}) {
  const [name, setName] = useState("");
  const [pin, setPin] = useState("");
  const [error, setError] = useState<string | null>(null);

  const go = () => {
    if (!name.trim()) return setError("이름을 입력해 주세요.");
    if (pin && pin.length !== PIN_LEN) return setError("비밀번호는 숫자 네 자리입니다.");
    onNext(name.trim(), pin || null);
  };

  return (
    <>
      <h1 className={s.ask}>이름을 알려 주세요</h1>
      <p className={s.hint}>선택 화면에서 이 이름으로 찾습니다.</p>

      <div className={s.form}>
        <input
          className={s.text}
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
          onKeyDown={(e) => e.key === "Enter" && go()}
          placeholder="이름"
          maxLength={40}
          autoFocus
        />
        <label className={s.field}>
          <span className={s.fieldLabel}>비밀번호 (안 걸어도 됩니다)</span>
          <input
            className={s.text}
            value={pin}
            onChange={(e) => {
              setPin(e.target.value.replace(/\D/g, "").slice(0, PIN_LEN));
              setError(null);
            }}
            onKeyDown={(e) => e.key === "Enter" && go()}
            placeholder="숫자 네 자리"
            inputMode="numeric"
            type="tel"
          />
        </label>
      </div>

      {error && <p className={s.err}>{error}</p>}

      <div className={s.actions}>
        <button type="button" className={s.back} onClick={onBack}>
          ← 돌아가기
        </button>
        <button type="button" className={s.go} onClick={go}>
          다음
        </button>
      </div>
    </>
  );
}

/** 볼 키워드 고르기.
 *
 *  **빈 곳간으로 시작하지 않게 하는 것이 이 단계의 전부입니다.** 아무것도
 *  없는 화면은 "고장인가" 로 읽히는데, 이미 모아 둔 것이 있으니 고르는
 *  즉시 채워집니다. 남의 키워드를 몰래 붙이지도 않습니다.
 *
 *  이미 있는 키워드를 고르는 것은 **수집 비용이 전혀 늘지 않습니다** —
 *  같은 검색어를 두 사람이 봐도 유튜브 호출과 요약은 한 번입니다. */
function KeywordStep({
  name,
  pin,
  onBack,
  onDone,
}: {
  name: string;
  pin: string | null;
  onBack: () => void;
  onDone: () => void;
}) {
  const all = useAsync(() => api.listAllKeywords(), []);
  const [picked, setPicked] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const toggle = (k: Keyword) => {
    setError(null);
    setPicked((prev) =>
      prev.includes(k.id)
        ? prev.filter((x) => x !== k.id)
        : prev.length >= PICK_MAX
          ? prev
          : [...prev, k.id],
    );
  };

  const finish = async () => {
    setBusy(true);
    try {
      await api.createPerson({ name, pin, keywordIds: picked });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "만들지 못했습니다.");
      setBusy(false);
    }
  };

  return (
    <>
      <h1 className={s.ask}>무엇을 보시겠어요?</h1>
      <p className={s.hint}>
        이미 모아 둔 것들입니다. 고르면 바로 채워집니다 — 나중에 바꿀 수 있어요.
      </p>

      {/* **오류일 때 "불러오는 중" 을 같이 띄우면 안 됩니다.** 목록은
          영영 오지 않는데 화면은 기다리는 것처럼 보여서, 빨간 줄을 읽고도
          더 기다리게 됩니다. 다시 받아 볼 길만 내놓고 넘어갑니다 —
          키워드는 들어가서도 고를 수 있으니 여기서 막을 이유가 없습니다. */}
      {all.error && (
        <>
          <p className={s.err}>{all.error}</p>
          <button type="button" className={s.back} onClick={all.reload}>
            다시 불러오기
          </button>
        </>
      )}
      {!all.data && !all.error ? (
        <Loading />
      ) : !all.data ? null : all.data.length === 0 ? (
        <p className={s.hint}>아직 등록된 키워드가 없습니다. 들어가서 만드시면 됩니다.</p>
      ) : (
        <div className={s.chips}>
          {all.data.map((k) => {
            const on = picked.includes(k.id);
            return (
              <button
                key={k.id}
                type="button"
                className={`${s.chip} ${on ? s.chipOn : ""}`}
                onClick={() => toggle(k)}
                aria-pressed={on}
              >
                <span className={s.chipTerm}>{k.channelTitle ?? k.term}</span>
                <span className={s.chipCount}>{k.lectureCount}</span>
              </button>
            );
          })}
        </div>
      )}

      <p className={s.picked}>
        {picked.length}/{PICK_MAX} 고름
      </p>
      {error && <p className={s.err}>{error}</p>}

      <div className={s.actions}>
        <button type="button" className={s.back} onClick={onBack} disabled={busy}>
          ← 돌아가기
        </button>
        <button type="button" className={s.go} onClick={finish} disabled={busy}>
          {/* 하나도 안 고르고 들어가도 됩니다 — 막으면 "일단 들어가 보고
              싶은" 사람을 붙잡아 두게 됩니다. */}
          {picked.length ? "시작하기" : "일단 둘러보기"}
        </button>
      </div>
    </>
  );
}
