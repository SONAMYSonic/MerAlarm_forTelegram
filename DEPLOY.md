# Oracle Cloud에 올리기 — 처음 하는 사람용

PC를 꺼도 감시가 계속되게 만든다. Oracle Cloud를 한 번도 안 써봤다는 전제로 쓴다.

- **걸리는 시간** — 가입 15분, 나머지 30분쯤
- **비용** — 0원. 단, 가입에 신용카드 등록이 필요하다 (본인 확인용)
- **준비물** — 해외 결제가 되는 신용/체크카드, 휴대폰, 이메일

가장 중요한 목적은 **"데이터센터 IP에서 메루카리가 차단되는지"** 를 알아내는 것이다.
차단되면 라즈베리파이 같은 집 회선 장비로 방향을 틀어야 한다. 그래서 5단계에서
확인부터 하고, 거기서 막히면 그만둔다.

---

## 알아둘 말 세 개

| 말 | 뜻 |
|---|---|
| **인스턴스(Instance)** | 클라우드에 빌리는 컴퓨터 한 대. 우리가 만들 것 |
| **셰이프(Shape)** | 그 컴퓨터의 사양(CPU·메모리) |
| **SSH 키** | 그 컴퓨터에 들어가는 열쇠 파일. 비밀번호 대신 쓴다 |

---

## 1단계 · 가입

### ⚠️ 먼저 읽을 것 — 홈 리전은 나중에 못 바꾼다

가입 화면에 **Home Region(홈 리전)** 을 고르는 칸이 있다. 여기서 반드시
**Japan Central (Osaka)** 또는 **Japan East (Tokyo)** 를 고른다.

이유가 둘이다.

1. 무료 리소스는 **홈 리전에서만** 만들 수 있는데, 홈 리전은 계정을 만들 때 정해지고
   **나중에 바꿀 수 없다.** 서울로 만들면 일본 리전에 무료 서버를 못 만든다.
2. 일본 서비스에 일본에서 접속하는 편이 자연스럽다. 차단 확률을 조금이라도 낮춘다.

### 절차

1. <https://www.oracle.com/kr/cloud/free/> 접속 → **무료로 시작하기**
2. 국가, 이름, 이메일 입력 → 이메일로 온 인증 링크 클릭
3. 비밀번호 설정, **Cloud Account Name** 입력 (아무 이름이나. 나중에 로그인할 때 쓰니 적어둘 것)
4. **Home Region 을 Japan Central (Osaka) 또는 Japan East (Tokyo) 로 선택**
5. 휴대폰 인증
6. 카드 등록 — 1달러 정도가 가승인되었다가 며칠 뒤 취소된다. 실제 청구가 아니다
7. 가입 완료까지 몇 분 기다린다

> **✅ 여기까지 됐으면** — Oracle Cloud 콘솔 화면이 보인다.

---

## 2단계 · 서버 만들기

### 먼저 네트워크부터 만든다

인스턴스 생성 화면에서 네트워크를 같이 만들려고 하면 Subnet 칸이 비활성 상태로
남아 공용 IP를 켤 수 없다. 콘솔도 화면 위에 "VCN과 Subnet을 먼저 만들고 오라"고
경고한다. **순서를 바꿔서 네트워크를 먼저 만든다.** 마법사가 있어 클릭 몇 번이면 된다.

1. 햄버거 메뉴(≡) → **Networking** → **Virtual cloud networks**
2. **Start VCN Wizard** 클릭

   > **⚠️ 버튼이 두 개인데 이름이 비슷하다. 잘못 누르기 쉽다.**
   >
   > ```
   > [ Create VCN ]        ← 수동. 주소 범위를 직접 입력해야 하고
   > [ Start VCN Wizard ]  ← 이것. 전부 자동으로 만들어준다
   > ```
   >
   > `Create VCN` 을 눌렀다면 **IPv4 CIDR Blocks** 칸에 "No matches found" 가
   > 뜬 화면이 보인다. 그 칸은 고르는 칸이 아니라 직접 타이핑하는 칸이다.
   > 취소하고 `Start VCN Wizard` 로 다시 들어간다.

3. **Create VCN with Internet Connectivity** 선택 → **Start VCN Wizard**
4. VCN Name 에 `meralarm-vcn` 입력. **CIDR 블록 등 나머지는 건드리지 않는다**
5. **Next** → **Create**

마법사는 CIDR 을 묻지 않는다. 물어본다면 마법사가 아니라 수동 생성 화면이다.

30초쯤 걸린다. VCN, 공개 서브넷, 인터넷 게이트웨이가 한꺼번에 만들어진다.

> **✅ 여기까지 됐으면** — Virtual cloud networks 목록에 `meralarm-vcn` 이 보인다.

### 그다음 인스턴스를 만든다

콘솔 왼쪽 위 **햄버거 메뉴(≡)** → **Compute** → **Instances** → **Create instance**.

아래만 건드리고 나머지는 기본값으로 둔다.

### 이름

`meralarm` 처럼 알아보기 쉬운 이름.

### 이미지와 셰이프 (Image and shape → Edit)

| 항목 | 고를 것 |
|---|---|
| Image | **Canonical Ubuntu 24.04** |
| Shape | **VM.Standard.E2.1.Micro** |

> **⚠️ "Always Free eligible" 라벨이 붙어 있는지 반드시 확인한다.**
> 이 표시가 없는 사양을 고르면 돈이 나간다.

`Ampere A1`(4코어 24GB)이 성능은 훨씬 좋지만 **"Out of capacity"** 오류가 자주 난다.
이 프로그램은 메모리 수십 MB면 충분하므로 **E2.1.Micro로 충분하다.** 기다릴 이유가 없다.

### 네트워킹 (Networking)

앞에서 만들어둔 것을 고른다.

| 항목 | 고를 것 |
|---|---|
| Primary network | **Select existing virtual cloud network** |
| VCN | `meralarm-vcn` |
| Subnet | **`Public Subnet-meralarm-vcn`** |

> **⚠️ 반드시 이름에 `Public` 이 들어간 서브넷을 고른다.**
> Private 서브넷을 고르면 공용 IP를 붙일 수 없고, 그러면 SSH로 들어갈 방법이 없다.

서브넷을 고르는 순간 아래 **`Automatically assign public IPv4 address`** 토글이
활성화된다. **켠다.** 꺼진 채로 만들면 나중에 못 바꾸므로 인스턴스를 다시 만들어야 한다.

외부에서 들어오는 연결을 받는 프로그램이 아니므로 방화벽 포트는 열 필요 없다.

> **VCN이 뭔가** — 서버가 들어갈 가상의 사설망이다. 데이터센터에서 서버를 랙에 꽂고
> 네트워크에 연결하는 일을 클릭으로 하는 셈이다. 계정이 새 것이라 아직 하나도
> 없어서 Subnet 칸에 "No matches found" 가 떴던 것이다.

### SSH 키 추가 (Add SSH keys)

**Generate a key pair for me** 를 고르고 **Save private key** 버튼을 반드시 누른다.
`ssh-key-2026-08-02.key` 같은 파일이 다운로드된다.

> **이 파일을 잃어버리면 서버에 다시 못 들어간다.** 다운로드 폴더에 그대로 두거나
> 안전한 곳에 옮겨두고, 어디에 뒀는지 기억해둔다.

마지막으로 **Create** 를 누른다.

> **✅ 여기까지 됐으면** — 1~2분 뒤 상태가 주황색 `PROVISIONING` 에서 초록색
> `RUNNING` 으로 바뀐다. 화면에 **Public IP address** 가 보인다. 이 숫자를 적어둔다.

---

## 3단계 · 서버에 들어가기

Windows 10/11에는 접속 도구가 기본으로 들어 있다. 따로 설치할 게 없다.

**PowerShell을 열고** 아래를 실행한다.

> **⚠️ `<...>` 는 "여기에 본인 값을 넣으라"는 표시다. 꺾쇠괄호까지 같이 입력하면 안 된다.**
>
> ```
> ssh ... ubuntu@<203.0.113.45>   ← 잘못. Could not resolve hostname 오류
> ssh ... ubuntu@203.0.113.45     ← 이렇게
> ```

### 먼저 열쇠 파일 권한을 고친다

이 과정을 건너뛰면 접속이 거부된다. 초보자가 가장 많이 막히는 지점이다.

```powershell
cd $HOME\Downloads
icacls .\ssh-key-2026-08-02.key /reset
icacls .\ssh-key-2026-08-02.key /inheritance:r
icacls .\ssh-key-2026-08-02.key /grant:r "$(whoami):(R)"
```

제대로 됐는지 확인한다.

```powershell
icacls .\ssh-key-2026-08-02.key
```

`AE86\AE86:(R)` 처럼 **백슬래시 뒤에 계정 이름이 있어야 한다.**

> **⚠️ `$env:USERNAME` 을 쓰면 안 된다.** 컴퓨터 이름과 계정 이름이 같은 경우
> icacls 가 그것을 컴퓨터 이름으로 해석해서 `AE86\:(R)` 처럼 사용자 자리가 빈
> 권한이 만들어진다. 그러면 아무도 파일을 못 읽어서 접속할 때
> `Load key "...": Permission denied` 가 뜬다.
> `whoami` 는 `컴퓨터\계정` 형태를 정확히 돌려주므로 이 문제가 없다.
>
> 이미 이 상태가 됐다면 `/reset` 을 먼저 실행해 원상복구한 뒤 다시 잡는다.

### 접속

```powershell
ssh -i .\ssh-key-2026-08-02.key ubuntu@<공용IP>
```

처음 접속하면 `Are you sure you want to continue connecting?` 라고 묻는다.
**yes** 를 입력하고 Enter.

> **✅ 여기까지 됐으면** — 프롬프트가 `ubuntu@meralarm:~$` 처럼 바뀐다.
> 이제부터 입력하는 명령은 서버에서 실행된다.

---

## 4단계 · 파일 보내기

### 내 PC에서 (새 PowerShell 창을 하나 더 연다)

보낼 파일만 골라 하나로 묶는다.

```powershell
powershell -ExecutionPolicy Bypass -File D:\Python\MerAlarm\scripts\pack.ps1
```

`MerAlarm-deploy.tar.gz` 가 만들어진다. 이걸 서버로 보낸다.

```powershell
cd $HOME\Downloads
scp -i .\ssh-key-2026-08-02.key D:\Python\MerAlarm\MerAlarm-deploy.tar.gz ubuntu@<공용IP>:~/
```

### 서버 창에서

```bash
mkdir -p ~/MerAlarm && tar xzf ~/MerAlarm-deploy.tar.gz -C ~/MerAlarm
cd ~/MerAlarm
sudo apt update && sudo apt install -y python3-venv
bash scripts/setup.sh
```

`setup.sh` 가 파이썬 확인부터 설치까지 알아서 한다. 2분쯤 걸린다.

> **✅ 여기까지 됐으면** — "준비 완료. 다음 순서로 진행하세요." 가 출력된다.

---

## 5단계 · 차단됐는지 확인 — 여기가 핵심

**이 프로그램을 이 서버에서 쓸 수 있는지 판정하는 단계다.**

```bash
./.venv/bin/python scripts/check_access.py
```

IP와 통신사를 보여준 뒤 메루카리 검색을 5번 시도하고 판정을 내린다.

| 판정 | 뜻 | 다음에 할 일 |
|---|---|---|
| **정상** | 5번 다 성공 | 6단계로 진행 |
| **불안정** | 일부만 성공 | `config.yaml` 의 주기를 60초 이상으로 늘리고 며칠 관찰 |
| **차단됨** | 403 또는 429 | **여기서 멈춘다.** 아래 "차단됐다면" 참고 |

### 차단됐다면

Oracle Cloud에서는 쓸 수 없다는 뜻이다. 실망할 일은 아니고, **알아내려던 답을 얻은 것이다.**
서버는 지워도 된다(맨 아래 "그만두려면" 참고). 선택지는 둘이다.

- **라즈베리파이** — 집 회선을 쓰므로 차단 위험이 가장 낮다. 초기 10만원 정도
- **지금처럼 PC에서 계속** — 돈이 안 든다. 대신 PC를 켜둬야 한다

---

## 6단계 · 실제로 돌리기

정상 판정이 나왔을 때만 진행한다.

### 알림 설정 옮기기

내 PC 에서 쓰던 값을 그대로 옮기는 게 가장 빠르다.

```bash
cp .env.example .env
nano .env
```

편집기가 열리면 내 PC의 `D:\Python\MerAlarm\.env` 에 있는 값을 그대로 옮겨 적는다.

```
TELEGRAM_BOT_TOKEN=123456789:AAExampleTokenReplaceWithYourOwn
TELEGRAM_CHAT_ID=987654321
```

다 적었으면 **Ctrl+O** → **Enter** (저장) → **Ctrl+X** (나가기).

> 이 값들은 예시다. 실제 토큰은 **어디에도 적어 두지 말 것.** `.env` 는
> `.gitignore` 에 들어 있어 저장소에 올라가지 않는다.

처음부터 새로 잡고 싶으면 설정 마법사를 써도 된다. SSH 는 대화형 터미널이라
PC 에서와 똑같이 물어본다.

```bash
./.venv/bin/python -m meralarm --setup
```

### 디스코드도 쓰려면

`.env` 에 아래를 채운다. **웹훅과 봇 중 하나만** 쓰면 된다. 둘 다 적어도 알림이 두 번
가지는 않는다(봇이 있으면 웹훅은 무시한다).

```
# 알림만 받기
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# 알림 + 명령어 (/add 같은 것을 디스코드에서도)
DISCORD_BOT_TOKEN=...
DISCORD_CHANNEL_ID=1234567890
DISCORD_OWNER_ID=9876543210
```

봇 토큰과 번호를 손으로 찾기 번거로우면 `--setup` 을 쓰면 초대 주소를 만들어 주고
채널 목록에서 고르게 해 준다. **디스코드는 무료다.** 봇을 만들어도 돈이 들지 않는다.

> **PC 와 서버에서 같은 봇 토큰을 동시에 돌리지 말 것.** 텔레그램은 한쪽을 끊고
> 로그에 `conflict` 를 남겨 알려주지만, 디스코드는 조용히 둘 다 붙어서 **명령이
> 두 번 실행될 수 있다.**

### 잠깐 돌려보기

```bash
./.venv/bin/python -m meralarm
```

로그가 몇 줄 올라오면 **Ctrl+C** 로 멈춘다. 화면이 없는 서버라 트레이 아이콘은
자동으로 꺼지고 감시만 돈다.

### 계속 켜두기

```bash
bash scripts/install_service.sh
```

이제 서버가 재부팅돼도 자동으로 뜨고, 죽으면 30초 뒤 되살아난다.

> **✅ 여기까지 됐으면** — 아래로 상태를 확인한다.
>
> ```bash
> systemctl status meralarm
> tail -f ~/MerAlarm/logs/meralarm.log
> ```
>
> `tail` 은 **Ctrl+C** 로 빠져나온다. SSH 창을 닫아도 서버는 계속 돈다.

---

## 7단계 · 마무리

> ### ⚠️ PC 쪽 MerAlarm을 반드시 끈다
>
> 같은 봇 토큰으로 두 대가 동시에 돌면 **알림이 두 번 온다.**
>
> - 트레이 아이콘 우클릭 → **종료**
> - 작업 스케줄러에 등록했다면:
>   `Unregister-ScheduledTask -TaskName MerAlarm -Confirm:$false`

이제 키워드를 바꾸려면 SSH로 들어가 `config.yaml` 을 고치고 재시작해야 한다.

```bash
cd ~/MerAlarm
nano config.yaml
sudo systemctl restart meralarm
```

이게 번거로우면 **텔레그램 봇 명령어**(`/add`, `/list`, `/pause`)를 붙이는 것이
다음 순서다. 화면 없는 서버에서는 그게 사실상 유일한 조작 수단이 된다.

---

## 문제 해결

| 증상 | 해결 |
|---|---|
| `Permissions for '...key' are too open` | 3단계의 `icacls` 를 실행했는지 확인 |
| `Load key "...": Permission denied` | 권한을 너무 잠갔다. `icacls <키> /reset` 후 3단계를 다시 |
| `Could not resolve hostname <...>` | 꺾쇠괄호 `< >` 를 같이 입력했다. 빼고 실행 |
| Subnet 칸에 `No matches found` | `Create new virtual cloud network` 를 골랐는지 확인 (2단계) |
| 공용 IP가 안 보임 | `Assign a public IPv4 address` 를 `Yes` 로 하고 인스턴스를 새로 만든다 |
| `Connection timed out` | 공용 IP가 맞는지, 인스턴스가 `RUNNING` 인지 확인 |
| `Permission denied (publickey)` | 사용자 이름이 `ubuntu` 인지 확인 (Oracle Linux는 `opc`) |
| `python3 -m venv` 실패 | `sudo apt install -y python3-venv` |
| Out of capacity | A1 대신 E2.1.Micro 선택. 이미 골랐다면 잠시 뒤 재시도 |
| 서비스가 안 뜸 | `journalctl -u meralarm -n 50` 으로 원인 확인 |
| 알림이 안 옴 | `.env` 값 확인 → `tail -50 ~/MerAlarm/logs/meralarm.log` |

### 자주 쓰는 명령

```bash
systemctl status meralarm          # 상태 보기
sudo systemctl restart meralarm    # 재시작 (설정 바꾼 뒤)
sudo systemctl stop meralarm       # 멈추기
tail -f ~/MerAlarm/logs/meralarm.log   # 로그 실시간 보기
```

---

## 그만두려면

서버를 지우면 요금 걱정이 완전히 사라진다.

콘솔 → **Compute** → **Instances** → 인스턴스 이름 클릭 → 오른쪽 위 **More actions**
→ **Terminate**. "Permanently delete the attached boot volume" 도 체크한다.

지워도 계정은 남으니 나중에 다시 만들 수 있다.
