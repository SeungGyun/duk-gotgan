import { Link } from "react-router-dom";
import s from "./About.module.css";

/** 소개 — **로그인 없이 열립니다.**
 *
 *  처음 들어온 사람이 보는 화면입니다. "누구세요?" 앞에서 한 번, 들어온
 *  뒤에도 계정 메뉴에서 다시 볼 수 있습니다.
 *
 *  기능을 나열하지 않습니다. 새로 온 사람이 실제로 궁금한 것은 **여기서
 *  뭘 하는 곳이고, 내가 뭘 하면 되는가** 뿐입니다. 파이프라인이 몇 단계인지는
 *  관리자만 알면 됩니다. */
export function About() {
  return (
    <div className={s.wrap}>
      <article className={s.page}>
        <header className={s.top}>
          <span className={s.brand}>
            덕<span className={s.sep}>!</span>곳간
          </span>
          <h1 className={s.title}>
            유튜브를 뒤지는 대신,
            <br />
            읽을 것만 쌓아 둡니다
          </h1>
          <p className={s.lede}>
            관심 있는 주제를 걸어 두면 알아서 강의를 찾아 요약해 둡니다.
            들어와서 읽기만 하면 됩니다.
          </p>
        </header>

        <section className={s.how}>
          <ol className={s.steps}>
            <li>
              <b>주제를 겁니다</b>
              <span>
                &ldquo;쿠버네티스 네트워킹&rdquo; 처럼 검색어를 넣거나, 좋아하는
                채널을 구독합니다.
              </span>
            </li>
            <li>
              <b>알아서 찾습니다</b>
              <span>
                새 영상이 올라오면 가져와 말을 글로 옮기고, 광고나 짧은 것은
                걸러 냅니다.
              </span>
            </li>
            <li>
              <b>요약해 둡니다</b>
              <span>
                무슨 이야기인지, 어디쯤에 뭐가 나오는지 정리해 둡니다. 원본
                영상 링크도 붙습니다.
              </span>
            </li>
            <li>
              <b>읽습니다</b>
              <span>
                안 읽은 것이 먼저 뜹니다. 관심 없는 것은 빼면 그 뒤로 비슷한
                채널이 덜 옵니다.
              </span>
            </li>
          </ol>
        </section>

        <section className={s.block}>
          <h2>사람마다 다르게 보입니다</h2>
          <p>
            내가 건 주제가 데려온 것만 내 목록에 오릅니다. 읽었는지, 아껴
            뒀는지, 뺐는지도 각자 따로 남습니다 &mdash; <b>내가 읽은 표시가
            남에게 보이지 않습니다.</b>
          </p>
          <p className={s.aside}>
            같은 주제를 여럿이 봐도 기계는 한 번만 일합니다. 그래서 이미 있는
            주제를 구독하는 편이 새로 만드는 것보다 언제나 낫습니다.
          </p>
        </section>

        <section className={s.block}>
          <h2>집 안에서만 씁니다</h2>
          <p>
            이 곳간은 집에 있는 맥 한 대에서 돕니다. 같은 공유기에 붙어 있을
            때만 열리고, 밖에서는 들어올 수 없습니다.
          </p>
          <p>
            비밀번호는 숫자 네 자리입니다. 안 걸어도 되지만, 걸면 다른 식구가
            내 목록을 열지 못합니다.
          </p>
        </section>

        <div className={s.go}>
          <Link to="/who" className={s.goBtn}>
            시작하기
          </Link>
        </div>

        <footer className={s.foot}>
          만든 것 · 쓰는 법 ·{" "}
          <a href="https://github.com/SeungGyun/duk-gotgan" target="_blank" rel="noreferrer">
            소스와 설치 방법
          </a>
        </footer>
      </article>
    </div>
  );
}
