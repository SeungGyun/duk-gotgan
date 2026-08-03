# 설치

새 맥에 처음부터 올리는 순서입니다. 다 끝나면 **로그인만 하면 알아서 도는
상태**가 됩니다 — 재부팅해도 아무것도 누를 필요가 없습니다.

30분쯤 걸립니다. 대부분은 받아 오는 시간입니다.

---

## 0. 준비물

| | 왜 |
|---|---|
| **애플 실리콘 맥** (M1 이상) | 받아쓰기가 맥 GPU 를 씁니다. 인텔 맥에서는 이 부분만 못 씁니다 |
| **메모리 16GB 이상** | 받아쓰기와 요약이 같이 돕니다. 8GB 에서는 스왑이 차서 요약이 죽습니다 |
| **여유 공간 20GB** | 받아쓰기 모델 1.5GB + 도커 + DB |
| **Docker Desktop** | MySQL 을 여기에 띄웁니다 |
| **Homebrew** | 아래 것들을 받는 데 씁니다 |

```bash
# 없으면 받습니다
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install --cask docker
brew install uv node
```

**도커를 한 번 실행해 두세요.** 처음 실행할 때 권한을 묻습니다.

### 유튜브 API 키

[Google Cloud Console](https://console.cloud.google.com/) 에서 받습니다. 무료입니다.

1. 프로젝트 만들기
2. **API 및 서비스 → 라이브러리** → `YouTube Data API v3` → 사용 설정
3. **사용자 인증 정보 → 사용자 인증 정보 만들기 → API 키**
4. 나온 키를 복사해 둡니다 (아래 3번에서 씁니다)

> 하루 10,000 유닛이 무료입니다. 검색 한 번에 100 유닛이라 **하루 100회**
> 검색할 수 있습니다. 키워드 하나가 하루 최대 2회를 쓰므로 **키워드 45개까지**
> 여유롭습니다.

### Claude Code 로그인

요약을 만드는 부분입니다. **API 키가 아니라 구독을 씁니다.**

```bash
npm install -g @anthropic-ai/claude-code
claude          # 브라우저가 열리면 로그인하고 창을 닫습니다
```

`claude` 를 한 번 더 실행해서 바로 프롬프트가 뜨면 된 것입니다.

> 이 로그인은 **맥 사용자 계정에 붙습니다.** 워커가 같은 사용자로 돌기
> 때문에 따로 설정할 것이 없습니다. `ANTHROPIC_API_KEY` 는 쓰지 않습니다 —
> 환경 변수에 그 값이 있으면 오히려 그쪽으로 과금되니 지워 두세요.

---

## 1. 받기

```bash
mkdir -p ~/git && cd ~/git
git clone https://github.com/SeungGyun/duk-gotgan.git
cd duk-gotgan
```

---

## 2. MySQL 띄우기

```bash
cd backend
docker compose up -d
```

**3307 번 포트를 씁니다.** 3306 이 아닌 이유는 다른 프로젝트의 MySQL 과
겹치지 않게 하기 위해서입니다. 컨테이너도 볼륨도 따로 씁니다 — 같은 서버에
스키마만 얹으면 저쪽을 내릴 때 이쪽 데이터까지 사라집니다.

올라왔는지 봅니다:

```bash
docker compose ps          # dukgotgan_mysql 이 healthy 면 됩니다
```

---

## 3. 백엔드

```bash
# backend/ 에서
uv sync --extra asr --extra dev
cp .env.example .env
```

`.env` 를 열어 **유튜브 키 한 줄만** 채웁니다:

```
YOUTUBE_API_KEY=여기에_붙여넣기
```

나머지는 기본값으로 둡니다. 나중에 바꾸고 싶으면
[`backend/config/settings.py`](../backend/config/settings.py) 에 각 값이
왜 그 숫자인지 적혀 있습니다.

> `--extra asr` 는 받아쓰기(`mlx-whisper`)입니다. **애플 실리콘 전용**이라
> 기본 의존성이 아닙니다. 빼면 자막 API 가 열려 있을 때만 돌고, 막히면
> 영상이 대기 상태로 남습니다.

잘 깔렸는지 봅니다:

```bash
.venv/bin/python -m pytest -q      # 113 passed 가 나와야 합니다
```

---

## 4. 화면 빌드

```bash
cd ../frontend
npm install
echo "VITE_API=http" > .env
npm run build
```

**빌드 결과를 백엔드가 그대로 냅니다.** 그래서 주소가 8000 번 하나입니다 —
프로세스가 둘이면 재시작마다 한쪽이 빠질 여지가 생기고 주소도 둘이 됩니다.

---

## 5. 상시 서비스 등록

```bash
cd ..
ops/install.sh
```

세 서비스가 `launchd` 에 등록되고 바로 뜹니다.

| 이름 | 하는 일 |
|---|---|
| `com.dukgotgan.mysql` | 도커를 깨우고 컨테이너를 지킵니다 (60초마다 확인) |
| `com.dukgotgan.api` | 화면과 API (`:8000`) |
| `com.dukgotgan.worker` | 검색·자막·요약·정리 루프 |

확인:

```bash
ops/status.sh
```

> **`launchd` 에는 의존 순서가 없습니다.** 셋이 동시에 시작하므로 api·worker
> 가 각자 "도커와 MySQL 이 응답할 때까지" 기다렸다 뜹니다. 부팅 직후
> 도커 데몬이 응답하기까지 수십 초 걸리는 것까지 감안해 최대 5분 기다립니다.

---

## 6. 첫 접속

```
이 맥에서     http://localhost:8000
폰·다른 기기  http://<맥의 집 안 IP>:8000
```

IP 는 이렇게 확인합니다:

```bash
ipconfig getifaddr en0
```

### 들어가기

**누구세요?** 화면이 뜹니다. **주인**을 누르고 비밀번호 **`0000`** 을 넣습니다.

들어가면 맨 위에 "비밀번호가 0000 입니다" 띠가 있습니다. **오른쪽 위
이름을 눌러 바꾸세요.** 안 바꾸면 같은 공유기에 붙은 누구나 주인으로 들어와
수집을 돌리고 요약을 지울 수 있습니다.

### 식구 추가

**누구세요?** 화면의 **새로 만들기**를 누릅니다. 이름을 넣고(비밀번호는
선택), 볼 키워드를 고르면 이미 모아 둔 것이 바로 채워집니다.

---

## 7. 첫 키워드

**키워드** 화면에서 검색어를 하나 넣습니다. 1분 안에 검색이 돌고, 자막이
붙는 대로 요약이 쌓입니다.

**첫 편이 보이기까지 10~30분쯤 걸립니다.** 자막을 직접 받아쓰는 경우가
많은데, 40분짜리 영상이면 받아쓰는 데만 7~8분입니다. **실행 로그** 화면에서
지금 어디까지 왔는지 볼 수 있습니다.

> 검색어 대신 **관심 채널**(`@핸들`)로도 걸 수 있습니다. 검색은 호출당
> 100유닛인데 채널 구독은 1유닛이라 **50배 쌉니다.**

---

## 문제가 생기면

### 화면이 안 열립니다

```bash
ops/status.sh                          # 셋 다 살아 있나
tail -50 ~/Library/Logs/dukgotgan/api.log     # api 가 왜 죽었나
```

도커가 안 떠 있으면 `mysql` 지킴이가 60초마다 깨우려 합니다. Docker Desktop
을 직접 한 번 실행해 보세요.

### 요약이 안 만들어집니다

```bash
tail -100 ~/Library/Logs/dukgotgan/worker.log | grep -i review
```

`claude` 로그인이 풀렸을 수 있습니다. 터미널에서 `claude` 를 실행해
프롬프트가 뜨는지 보세요.

**토큰 상한에 걸렸을 수도 있습니다.** 대시보드에서 5시간 창 사용량을
확인하고, 필요하면 거기서 상한을 올립니다.

### 자막이 계속 실패합니다

유튜브 자막 API 가 IP 단위로 막힐 때가 있습니다(429). 그러면 자동으로
쉬었다가 소리를 받아 직접 받아쓰기로 넘어갑니다 — **실행 로그**에 "유튜브
자막이 막혀 쉬는 중"으로 뜹니다. 그대로 두면 됩니다.

`mlx-whisper` 가 안 깔렸으면 이 폴백이 없어서 계속 대기합니다:

```bash
cd backend && uv sync --extra asr
```

### 메모리가 모자랍니다

받아쓰기(약 2.4GB)와 요약이 같이 돌 때 스왑이 찹니다. 워커가 스왑 여유를
보고 알아서 쉬지만, 그래도 빠듯하면 다른 프로젝트의 MySQL 설정을 줄이는
편이 효과가 큽니다:

```
--performance-schema=OFF    진단용 계측인데 그것만 200MB 넘게 씁니다
--max-connections=50
```

### 화면만 고쳤습니다

```bash
ops/rebuild-ui.sh
```

---

## 끄기

```bash
ops/install.sh remove       # 서비스 해제 (파일과 데이터는 남습니다)
cd backend && docker compose down
```

데이터까지 지우려면:

```bash
cd backend && docker compose down -v    # ⚠️ 되돌릴 수 없습니다
```

---

## 밖에서 접속하고 싶다면

**지금 설계로는 안 됩니다.** 비밀번호가 네 자리뿐이고 TLS 도 없습니다.
집 공유기 안에서만 쓰는 전제로 만들어졌습니다.

공용 와이파이에 물릴 때는 아예 밖으로 안 열리게 막으세요:

```bash
launchctl setenv DUKGOTGAN_HOST 127.0.0.1 && ops/install.sh restart
```
