import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

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
  const nav = useNavigate();
  const people = useAsync(() => api.listPeople(), []);
  const [step, setStep] = useState<Step>({ at: "list" });

  const enter = () => {
    // replace 로 갑니다 — 뒤로 가기를 눌렀을 때 이 화면이 다시 나오면
    // 이미 들어간 사람에게는 막다른 골목처럼 보입니다.
    nav("/", { replace: true });
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
            onPick={(p) => {
              if (p.hasPin) setStep({ at: "pin", person: p });
              else void api.pickPerson(p.id).then(enter);
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

/** 사람 고르기. */
function Pick({
  people,
  onPick,
  onNew,
}: {
  people: Person[];
  onPick: (p: Person) => void;
  onNew: () => void;
}) {
  return (
    <>
      <h1 className={s.ask}>누구세요?</h1>
      <div className={s.row}>
        {people.map((p) => (
          <button
            key={p.id}
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
        ))}

        <button type="button" className={`${s.who} ${s.add}`} onClick={onNew}>
          <span className={s.avatar} aria-hidden="true">
            +
          </span>
          <span className={s.nm}>새로 만들기</span>
          <span className={s.sub}>&nbsp;</span>
        </button>
      </div>
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

      {all.error && <p className={s.err}>{all.error}</p>}
      {!all.data ? (
        <Loading />
      ) : all.data.length === 0 ? (
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
