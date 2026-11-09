# Инсталација на Bi-Dash

Изберете Windows, macOS или Linux. Bi-Dash се инсталира преку Docker и се користи во прелистувач. Преземањето е изворен код, не самостоен `.exe` или `.dmg` инсталатор.

## Предуслови

- Git и пристап до интернет.
- Docker со Compose v2 (`docker compose`). Docker мора да работи пред да започнете.
- Windows: Docker Desktop со Linux контејнери и PowerShell 7.
- macOS: Docker Desktop за вашата архитектура, Git и OpenSSL.
- Linux: Docker Engine, Buildx, Compose v2, Git, Bash и OpenSSL.

За локално AI планирајте околу 16 GB RAM и најмалку 30 GB слободен простор за локалната изградба и моделот. Ова се почетни процени, не гарантирани минимуми. Потребите зависат од моделот, зависностите и оптоварувањето. Серверската Linux постапка има посебни процени во своето упатство.

Windows со Linux контејнери е проверен. Свежа инсталација на физички Linux/macOS хост и ARM компатибилноста сè уште бараат валидација.

## Windows

Инсталирајте Docker Desktop, Git и PowerShell 7. Стартувајте Docker Desktop со Linux контејнери, потоа отворете PowerShell 7:

```powershell
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
pwsh -NoProfile -File ./scripts/start-local.ps1
```

Скриптата е за свежа инсталација: генерира тајни, ја гради апликацијата и ја иницијализира базата. За постоечка инсталација користете ги командите за повторно стартување подолу.

## macOS

Инсталирајте Docker Desktop за Intel или Apple Silicon, Git и OpenSSL. Стартувајте Docker Desktop. Во Terminal извршувајте ги командите по ред и продолжете само ако претходната успеала:

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash

if [ ! -e .env ]; then
  (
    set -eu
    umask 077
    cookie_secret=$(openssl rand -hex 32)
    data_secret=$(openssl rand -hex 32)
    printf '%s\n' \
      "REDASH_COOKIE_SECRET=$cookie_secret" \
      "REDASH_SECRET_KEY=$data_secret" \
      'OPENAI_API_KEY=' \
      'REDASH_AI_PROVIDER=ollama' \
      'REDASH_AI_MODEL=qwen2.5-coder:7b' \
      'REDASH_OLLAMA_URL=http://ollama:11434' > .env
  )
fi

docker compose -f compose.local.yaml config --quiet
docker compose -f compose.local.yaml build server
docker compose -f compose.local.yaml up -d postgres redis
docker compose -f compose.local.yaml run --rm server create_db
docker compose -f compose.local.yaml up -d
```

Овие команди се за свежа база. `create_db` не е команда за надградба. Не менувајте ги тајните во постоечка `.env` датотека: потребни се за дешифрирање на зачуваните пристапни податоци.

Apple Silicon и одделни конектори може да имаат ограничувања. Ако изградбата не успее, видете ја [деталната техничка референца на англиски](/en/downloads/INSTALLATION.html#4-macos) за опцијата со AMD64 емулација.

## Linux

За автоматизирана инсталација на Linux x86-64:

```bash
git clone https://github.com/SlavchoSpasenoski/Bi-Dash.git
cd Bi-Dash
bash scripts/linux.sh install
```

Оваа постапка користи `compose.linux.yaml` и `.env.linux`. Не мешајте ги со локалната постапка `compose.local.yaml` / `.env`. За сервер, SSH тунел и операции следете го [упатството за Linux](/downloads/LINUX_DEPLOYMENT.html).

## Прва најава и податоци

1. На компјутерот каде што работи апликацијата отворете `http://localhost:5001/setup`. За оддалечен Linux сервер прво отворете го SSH тунелот од серверското упатство.
2. Создајте организација и прв администратор. Нема вградена администраторска лозинка.
3. За следните посети користете `http://localhost:5001`.
4. Во **Settings → Data Sources** додајте ја вашата деловна база и изберете **Test Connection**.
5. Конфигурирајте корисници, групи и **Roles & Access** пред да споделувате извештаи.

Вклучениот PostgreSQL ги чува метаподатоците на Bi-Dash; тој не ги содржи автоматски вашите деловни податоци. `localhost` во контејнер се однесува на самиот контејнер. За база на Docker Desktop хостот, каде што е соодветно, користете `host.docker.internal`.

## AI е опционален

Прашањата и контролните табли работат и без AI. За локалниот стек, преземете го моделот еднаш:

```bash
docker compose -f compose.local.yaml exec ollama ollama pull qwen2.5-coder:7b
```

За серверската Linux постапка користете `bash scripts/linux.sh ai`. Или изберете надворешен провајдер во **Settings → AI Configuration** со сопствен модел и API клуч. Надворешниот провајдер може да наплатува и ги обработува испратените AI барања. Видете го [AI упатството](/downloads/AI_DASHBOARDS.html).

## Повторно стартување и проверка

За постоечкиот локален стек:

```bash
docker compose -f compose.local.yaml up -d
docker compose -f compose.local.yaml ps
docker compose -f compose.local.yaml logs --tail=100 server
```

За Linux сервер: `bash scripts/linux.sh start`, `status` и `logs`.

## Резервни копии и проблеми

Зачувајте резервна копија на PostgreSQL и соодветната `.env` или `.env.linux` датотека. Оригиналниот клуч за шифрирање е потребен за зачуваните пристапни податоци. Не користете `down -v` ако сакате да ги задржите податоците.

Ако Docker не одговара, проверете дали работи неговиот сервис и дали `docker version` ги прикажува клиентот и серверот. За неуспешна изградба пријавете систем, архитектура и порака за грешка, без лозинки и API клучеви.

Ова поставување слуша на `127.0.0.1:5001`. За компаниски пристап потребни се соодветни HTTPS, мрежни контроли, дозволи и резервни копии.

[Целосна техничка референца за инсталација (EN)](/en/downloads/INSTALLATION.html)
