# Talaria Agent

Talaria는 **CLI를 잘 쓰는 개인 비서**를 목표로 한 경량 에이전트입니다.

## 핵심만 남김
- 플랫폼: Discord, Slack, Telegram
- 모델: Claude, GPT, Codex, MiMo, local, custom
- 도구: web, browser, terminal, files, vision, memory, planning, messaging
- MCP: 지원하지만 기본 번들 서버는 0개

## 의도적으로 뺀 것
- 과도한 플랫폼 확장
- 기본 번들 MCP 서버
- 넓은 provider 전시장 같은 안내
- 기본 체크리스트에 보이는 plugin/toolset 과다 노출

## 현재 방향
- 코딩 전용 에이전트가 아니라 개인 비서
- CLI는 제품의 중심이 아니라 실행 도구
- 기본값은 lean, 필요할 때만 확장

## MCP 정책
- MCP 기능 자체는 유지
- 기본 번들 MCP 서버는 0개
- 사용자가 `talaria mcp add`로 직접 URL, command, env, auth를 입력해서 연결
- MCP 없이도 코어 비서 기능은 동작
