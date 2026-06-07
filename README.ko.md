# Talaria Agent

> Discord, Slack, Telegram, CLI, 그리고 선택적 MCP를 위한 가벼운 개인 어시스턴트.

Talaria는 [Hermes Agent](https://github.com/NousResearch/hermes-agent)에서 모든 기능을 담은 프레임워크 표면을 덜어내고, 잘 정의된 작은 어시스턴트만 남긴 슬림하고 의견 있는 포크다. 업스트림 코드베이스 대비 **약 71% 작다** (원본의 약 29% 크기).

## 3분 시작

```bash
# 1. 설치 (Linux/macOS/Termux)
curl -fsSL https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.sh | bash

# 2. 마법사가 프로바이더 + API 키 + 기본 모델 골라줌
talaria setup

# 3. 실행
talaria -q "2+2는?"             # 원샷
talaria gateway run             # Discord / Slack / Telegram 게이트웨이 실행
```

끝. 일상 사용은 [자주 쓰는 커맨드](#자주-쓰는-커맨드)로 점프. 나머지는 참고용.

```
                       ░████████ 
               ████████████████  
         █████████████████░      
     ███████████████████████████ 
  ░███████████████████████████   
 ███████████████████░            
 ███████████████████████████████ 
 █████████████████████████████   
  █████████████████████          
   ██████████████████████        
    ███████░                     
```

## 왜 경량화했나

Hermes는 모든 기능을 담은 풀스택 프레임워크라, 개인 용도에는 오히려
넓은 표면적이 방해가 된다. Talaria는 세 가지 목표로 깎아낸 슬림 포크다:

- **목적 적합성** — 실제로 쓰는 것만 남겼다 (Discord / Slack / Telegram
  + Claude / GPT / Codex / Xiaomi / OpenRouter / 로컬). 음성, 웹 대시보드,
  애그리게이터 중간 계층, 서드파티 메모리 플러그인은 전부 제거. 코드가
  적을수록 잘못 설정할 여지도, 업그레이드에서 깨질 여지도 줄어든다.
- **커스터마이징 용이** — 추상화 계층이 적고 호출 경로가 짧다.
  optional-skill 레지스트리나 플러그인 마켓플레이스 같은 중간 레이어가
  없어, 툴을 추가하거나 프롬프트를 교체하거나 에이전트 루프를 바꾸는
  작업이 약 649k LOC를 헤집는 일이 아니라 약 186k LOC에 대한 작은 diff로
  끝난다.
- **자원 효율성** — 설치 용량이 작고 콜드 스타트가 빠르며 메모리
  베이스라인이 낮고 백그라운드 서비스도 적다. 워크스테이션뿐 아니라
  Termux 폰이나 소형 VPS에서도 무난히 돌아간다. "혹시 몰라서" 끌어오는
  옵션 의존성은 두지 않았다.

풀 Hermes 기능 매트릭스(음성, 웹 UI, 전체 메신저, RL/eval 하니스,
플러그인 레지스트리)가 필요하면 업스트림 Hermes를 쓰면 된다. Talaria는
한 명의 데일리 드라이버를 위한, 의견 있는 부분집합이다.

## 무엇이 포함되나

| 표면 | Talaria |
|---|---|
| **프로바이더** | Anthropic (Claude), OpenAI (GPT), OpenAI Codex, Xiaomi MiMo, OpenRouter (200+ 모델), 로컬 (LM Studio / Ollama / vLLM), 커스텀 (OpenAI 호환 엔드포인트 전체) |
| **메시징** | Discord, Slack, Telegram |
| **터미널 백엔드** | local, Docker, SSH |
| **MCP** | 지원. 기본 서버는 0개 — `talaria mcp add`로 직접 추가 |
| **Skills** | 번들 `configuration` + `devops` + `software-development`; `talaria skills install <repo>`로 추가 |

## 제거된 것

집중도를 높이기 위해 통째로 들어낸 서브시스템들:

- **음성 & TTS** — speech-to-text, text-to-speech, push-to-talk, Discord 보이스 채널, ElevenLabs/Edge/MiniMax/NeuTTS 프로바이더
- **웹 대시보드 & ink/React TUI 프론트엔드** — 그래픽 표면 없음
- **인터랙티브 터미널 채팅** — `talaria chat` REPL 제거됨. 이제 헤드리스로 동작 — 메시징 게이트웨이(Discord/Slack/Telegram) 또는 일회성 `talaria -q "..."` 사용
- **ACP 에디터 어댑터** — Zed / VS Code / JetBrains 연동용 Agent Client Protocol 서버 제거됨. Talaria는 메시징 우선
- **애그리게이터 경로** — Vercel AI Gateway 및 Nous Portal 구독 시스템 (OpenRouter는 이제 자체 키를 갖는 1급 프로바이더)
- **인증 플로우** — Nous Portal 디바이스 코드 로그인, OpenClaw 마이그레이션, `talaria login` 서브커맨드
- **백엔드** — Modal, Daytona, Singularity 샌드박스 실행기 (local / Docker / SSH만 잔존)
- **메시징** — Discord / Slack / Telegram 외 모든 플랫폼 (DingTalk, BlueBubbles, WhatsApp, Matrix, Signal 등). `Platform` enum은 이제 6개 멤버 (LOCAL, TELEGRAM, DISCORD, SLACK, API_SERVER, WEBHOOK)이며 플러그인 플랫폼은 여전히 `Platform._missing_()`을 통해 동적 등록됨
- **기타** — RL/eval 하니스, 서드파티 메모리 플러그인 (Honcho / Mem0 / Hindsight), 공식 `optional-skills/` 레지스트리, 약 2200줄의 사장된 프로바이더 카탈로그 및 미사용 디텍터

순감: 업스트림 Hermes의 **1,340 파일 약 649k Python LOC**에서 **218 파일 약 186k LOC**로.

---

## 설치

### 원라이너 (Linux / macOS / Termux)

```bash
curl -fsSL https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.sh | bash
```

저장소 클론, venv 생성, 모든 extras 설치, `talaria` 바이너리를 `PATH`에 연결, 그리고 `talaria setup` 실행까지 — 마치고 나면 동작하는 봇이 준비된다.

위저드를 건너뛰려면 `bash -s -- --skip-setup`로 실행한 뒤 나중에 `talaria setup`을 다시 돌리면 된다.

### Windows (PowerShell)

```powershell
irm https://raw.githubusercontent.com/Jeonhui/Talaria-Agent/main/scripts/install.ps1 | iex
```

### 수동 설치

```bash
git clone https://github.com/Jeonhui/Talaria-Agent.git
cd Talaria-Agent
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[all]"
talaria setup
```

### Docker

GitHub Container Registry의 사전 빌드 이미지:

```bash
mkdir -p ~/.talaria
docker run --rm -it -v ~/.talaria:/opt/data \
    -e TALARIA_UID=$(id -u) -e TALARIA_GID=$(id -g) \
    ghcr.io/jeonhui/talaria-agent:latest setup
docker run -d --name talaria --restart unless-stopped \
    --network host -v ~/.talaria:/opt/data \
    -e TALARIA_UID=$(id -u) -e TALARIA_GID=$(id -g) \
    ghcr.io/jeonhui/talaria-agent:latest
docker logs -f talaria
```

또는 포함된 compose 파일로 로컬 빌드:

```bash
git clone https://github.com/Jeonhui/Talaria-Agent.git
cd Talaria-Agent
mkdir -p ~/.talaria
TALARIA_UID=$(id -u) TALARIA_GID=$(id -g) docker compose build
docker compose run --rm gateway setup            # 인터랙티브 위저드, ~/.talaria/.env 작성
TALARIA_UID=$(id -u) TALARIA_GID=$(id -g) docker compose up -d
docker compose logs -f
```

이미지는 기본으로 `gateway run`을 실행한다. 동작 중인 컨테이너에 일회성 명령을 보내려면 `docker exec -it talaria /opt/talaria/talaria <cmd>`.

---

## 빠른 시작

`talaria setup` 후:

```bash
talaria -q "what is 2+2"      # 일회성 질의 (stdout 으로 답 출력 후 종료)
talaria gateway run           # 메시징 게이트웨이 포그라운드 실행
talaria gateway start         # 백그라운드 서비스로 설치 + 시작
talaria status                # 프로바이더 / API 키 / 플랫폼 / 게이트웨이 상태
talaria sessions status       # 활성 세션 + MCP 연결 상태
talaria doctor                # 상세 진단
```

설정은 `~/.talaria/`에 위치:

```
~/.talaria/
├── .env                # 시크릿 (API 키, 봇 토큰)
├── config.yaml         # 프로바이더, 터미널 백엔드, 에이전트 설정
├── sessions/           # 대화 이력
├── skills/             # 설치된 스킬
└── plugins/            # 사용자 추가 플러그인
```

대부분은 `talaria setup` (인터랙티브) 또는 `talaria config set <key> <value>`로 설정한다. 파일을 직접 편집해도 된다.

---

## 셋업 위저드

`talaria setup`은 핵심 세 단계를 안내한다:

1. **모델 & 프로바이더** — 프로바이더 선택, API 키 입력, 기본 모델 선택
2. **터미널 백엔드** — local / Docker / SSH
3. **메시징 플랫폼** — Discord / Slack / Telegram 봇 토큰 + 허용 목록

고급 섹션은 옵트인:

```bash
talaria setup tools           # 플랫폼별 툴셋 체크리스트
talaria setup agent           # 최대 반복, 압축, 디스플레이
```

또는 단일 섹션 직접 실행: `talaria setup model | terminal | gateway | tools | agent`.

---

## 자주 쓰는 커맨드

```bash
talaria -q "what is 2+2"           # 일회성 질의 (또는 --oneshot / -z)
talaria gateway run                # Discord / Slack / Telegram 으로 대화
talaria sessions status            # 활성 세션 + MCP 연결 상태

talaria model                      # 프로바이더/모델 전환
talaria config show                # 현재 설정 표시
talaria config set model.default mimo-v2.5-pro
talaria config set model.provider xiaomi

talaria gateway run                # 포그라운드 게이트웨이
talaria gateway start | stop | restart | status
talaria gateway install            # systemd / launchd 서비스로 설치
talaria logs --follow              # 게이트웨이 로그 tail

talaria mcp add <name> <url-or-cmd>  # Model Context Protocol 서버 연결
talaria mcp list

talaria skills browse              # 스킬 둘러보기
talaria skills install <repo>      # GitHub에서 설치

talaria cron list                  # 예약 작업
talaria cron create "0 9 * * *" "Daily standup reminder"

talaria insights --days 7          # 세션 사용 리포트
talaria status                     # 헬스 요약
talaria doctor                     # 상세 진단
talaria uninstall [--full]         # 제거 (--full은 ~/.talaria도 삭제)
```

---

## 환경 변수 설정

`~/.talaria/.env`에 시크릿을 보관. 전체 템플릿은 [`.env.example`](.env.example)에 있다. 자주 쓰는 키:

```bash
# 프로바이더 — 하나 또는 여러 개 선택
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
XIAOMI_API_KEY=...
OPENROUTER_API_KEY=sk-or-...            # 200+ 모델을 단일 엔드포인트로
LM_BASE_URL=http://localhost:11434/v1   # 로컬 Ollama / LM Studio

# Discord
DISCORD_BOT_TOKEN=...
DISCORD_ALLOWED_USERS=123456789012345678
DISCORD_HOME_CHANNEL=...

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Telegram
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALLOWED_USERS=...

# 터미널 백엔드 (local / docker / ssh)
TERMINAL_ENV=local
```

프로바이더/모델은 `~/.talaria/config.yaml`에 들어가서 멀티 봇 셋업이 환경 변수를 두고 충돌하지 않는다.

내부 `TALARIA_*` 변수(타임아웃·경로·게이트웨이 튜닝 등)는 [`docs/ENVIRONMENT.md`](docs/ENVIRONMENT.md)에 정리. 지원되는 `config.yaml` 키 전체는 [`cli-config.yaml.full.example`](cli-config.yaml.full.example) — 기본값 덮어쓸 때만 손대면 된다.

---

## 프로젝트 레이아웃

```
talaria_cli/         CLI 진입점, 셋업 위저드, 모델 피커, 게이트웨이 커맨드
  ├─ main.py             argparse 빌드 + 작은 cmd_ 핸들러 + 디스패치
  ├─ provider_flows.py   select_provider_and_model + _model_flow_* / _aux_* / OAuth
  ├─ sessions.py         인터랙티브 세션 피커 + 세션 이름 argv 결합기
  ├─ commands.py         슬래시 명령 레지스트리 (CLI / 게이트웨이 / 어댑터 공유)
  └─ auth.py / config.py 셋업 상태 + 프로바이더 자격증명
agent/               에이전트 루프, 프롬프트 빌더, 트랜스포트 (anthropic / chat_completions / codex)
tools/               빌트인 툴: terminal, file, web, browser, memory, todo, vision, MCP, skills
gateway/             메시징 게이트웨이 (Discord / Slack / Telegram 어댑터 + 세션 스토어)
  ├─ run.py              GatewayRunner — 어댑터 라이프사이클, 메시지 디스패치
  ├─ auth.py             사용자 권한 정책 (allowlist + pairing)
  ├─ session.py          SessionSource / SessionStore + session_key 빌더
  └─ platforms/          플랫폼별 어댑터 (discord, slack, telegram)
plugins/             사용자 확장 가능 플러그인 호스트
cron/                크론 스케줄러
skills/              번들 스킬 (configuration, devops, software-development)
docker/              Docker 진입점
scripts/             설치 / 제거 / 빌드 헬퍼
docs/                ENVIRONMENT.md (환경 변수 레퍼런스) 및 REFACTOR-ROADMAP.md
```

세 개의 큰 진입 표면 (`run_agent.py` 에이전트 코어 / `talaria_cli/main.py` CLI / `gateway/run.py` 게이트웨이) 상단에 NAVIGATION docstring이 있어 grep 없이 원하는 영역으로 점프 가능. 모놀리스 분할 진행 상황은 [`docs/REFACTOR-ROADMAP.md`](docs/REFACTOR-ROADMAP.md)에서 추적.

---

## 상태

- **버전:** v0.1.0 (2026-05-05) — 슬림 리팩터 후 첫 클린 Talaria 릴리스.
- **안정성:** 도그푸드 등급. 개인적으로 사용 중; 마주친 이슈는 리포트 환영.
- **테스트:** `pytest` 현재 **403 테스트** 통과 (모든 1급 모듈에 대한 import-smoke + 에이전트 루프 결정 로직 / 메시지 sanitizer / budget 회계 / config 로더 유닛 커버리지). 게이트웨이 메시지 파이프라인 통합 커버리지는 아직 얇음 — 깊은 모놀리스 분할을 막는 테스트 갭은 [`docs/REFACTOR-ROADMAP.md`](docs/REFACTOR-ROADMAP.md) 참고. 기여 환영.

---

## 라이선스

[MIT](LICENSE). [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent)에서 포크 (역시 MIT).

---

## 기여

[CONTRIBUTING.md](CONTRIBUTING.md) 참조. 버그 리포트 + 기능 요청은 [GitHub Issues](https://github.com/Jeonhui/Talaria-Agent/issues).
