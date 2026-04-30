# Talaria Agent

Talaria Agent는 `hermes-agent`를 기반으로 덩치를 줄인 파생판입니다.

## 남긴 것
- 메시징 플랫폼: Discord, Slack, Telegram
- 모델 연결: Claude(Anthropic), MiMo(Xiaomi), Codex(OpenAI Codex), GPT(OpenAI), custom endpoint, local(LM Studio)

## 제거/축소한 것
- 그 외 메시징 플랫폼 관련 어댑터 노출과 메뉴
- 대형 문서/웹/UI/테스트/예제/연구성 디렉터리 다수
- 사용하지 않을 가능성이 큰 추가 provider surface와 관련 메뉴
- 일부 무거운 기본 의존성(exa, firecrawl, parallel-web, fal-client)

## 현재 상태
이 저장소는 완전 재작성판이 아니라, Hermes의 큰 표면적을 줄인 1차 경량화 버전입니다.

## 다음 경량화 후보
1. `hermes_cli/auth.py` 내부 OAuth/legacy provider 코드 더 절단
2. `hermes_cli/main.py`, `setup.py`, `doctor.py`, `status.py`의 provider 안내 문구 정리
3. Slack/Discord/Telegram 외 플랫폼 설정 커맨드와 help text 축소
4. 남아 있는 불필요 skill/plugin 디렉터리 추가 정리

## 원칙
- 커밋은 형님이 직접 말씀하실 때만 진행
- 우선은 작동을 덜 깨는 방향으로 표면적부터 줄임
