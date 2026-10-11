# Age — comandos de desenvolvimento no Windows
#
# O Makefile é a referência (ARQUITETURA.md §11.4), mas `make` não vem no
# Windows nem no Git Bash. Este script expõe os mesmos comandos.
#
#   .\age.ps1 up        sobe tudo e migra
#   .\age.ps1 smoke     valida a integridade
#   .\age.ps1 help      lista tudo

[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [string]$Command = "help",

    [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# --env-file: o .env está na raiz e o compose em infra/ — o Docker só
# carrega .env automaticamente do diretório do arquivo compose.
$Compose = @(
    "compose", "-f", "infra/docker-compose.yml", "--env-file", ".env"
)

function Invoke-Compose {
    # `$Args` é variável automática do PowerShell: usar esse nome como
    # parâmetro faz o splat chegar vazio no binário. Daí `ComposeArgs`.
    param([string[]]$ComposeArgs)
    # `Continue` durante a chamada: o compose (e o alembic por baixo
    # dele) escrevem progresso normal em stderr, e com o `Stop` do
    # script o PowerShell promoveria isso a exceção mesmo com exit 0.
    # Quem decide o sucesso é o exit code, conferido logo abaixo.
    $anterior = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # `2>&1 | Out-String` em vez de deixar o stderr solto: o
        # PowerShell o pinta de vermelho com um bloco de NativeCommand-
        # Error por linha, o que faz progresso normal do alembic e do
        # compose parecer falha. Aqui vira texto comum, e o veredito
        # continua sendo o exit code.
        & docker @Compose @ComposeArgs 2>&1 | ForEach-Object {
            # `$_.Exception.Message` e não `$_.ToString()`: num
            # ErrorRecord vindo de stderr nativo o ToString() às vezes
            # devolve o nome da classe em vez da linha original. Sem
            # operador ternário, que não existe no PowerShell 5.1.
            if ($_ -is [System.Management.Automation.ErrorRecord]) {
                Write-Host $_.Exception.Message
            }
            else { Write-Host $_ }
        }
    }
    finally { $ErrorActionPreference = $anterior }
    if ($LASTEXITCODE -ne 0) { throw "docker compose falhou (exit $LASTEXITCODE)" }
}

function Get-GitBash {
    # `bash` no PATH do Windows resolve para o stub do WSL, que não tem
    # bash instalado. O que queremos é o do Git for Windows.
    $candidates = @(
        "$env:ProgramFiles\Git\bin\bash.exe",
        "${env:ProgramFiles(x86)}\Git\bin\bash.exe",
        "$env:LOCALAPPDATA\Programs\Git\bin\bash.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
    return $null
}

function Assert-Env {
    if (-not (Test-Path ".env")) {
        Write-Host ".env não existe. Rode: .\age.ps1 env" -ForegroundColor Red
        exit 1
    }
}

function New-Secret {
    # 24 bytes em hex = 48 caracteres, acima do mínimo de 32 do config.
    $bytes = New-Object byte[] 24
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

function Wait-CoreApi {
    param([int]$TimeoutSeconds = 150)
    Write-Host "aguardando core-api ficar pronto…" -NoNewline
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $status = & docker @Compose ps core-api 2>$null
        if ($status -match "healthy") { Write-Host " ok"; return }
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 3
    }
    Write-Host ""
    throw "core-api não ficou pronto em ${TimeoutSeconds}s. Veja: .\age.ps1 logs"
}

function Wait-Web {
    # O `depends_on` espera o core-api ficar healthy, mas em dev o
    # Next ainda compila a rota sob demanda depois disso. Abrir a
    # página nessa janela dá 503 em tela cheia
    # ("upstream_unavailable"), que parece defeito e é só pressa.
    # Então o `up` só devolve o controle quando o web responde.
    param([int]$TimeoutSeconds = 180)
    Write-Host "aguardando web compilar…" -NoNewline
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $r = Invoke-WebRequest -Uri "http://localhost:3000/login" -UseBasicParsing -TimeoutSec 20 -ErrorAction Stop
            if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 400) {
                Write-Host " ok"; return
            }
        } catch {}
        Write-Host "." -NoNewline
        Start-Sleep -Seconds 3
    }
    Write-Host ""
    Write-Host "web não respondeu em ${TimeoutSeconds}s — veja os logs" -ForegroundColor Yellow
}

switch ($Command.ToLower()) {

    "help" {
        Write-Host ""
        Write-Host "  Age — comandos" -ForegroundColor Cyan
        Write-Host ""
        $rows = @(
            @("env", "gera .env com segredos aleatórios"),
            @("up", "sobe os serviços e aplica as migrations"),
            @("rebuild", "reconstrói as imagens do zero e sobe"),
            @("down", "para os serviços (mantém os dados)"),
            @("ps", "estado dos containers"),
            @("logs", "segue os logs dos três serviços"),
            @("smoke", "valida a integridade do ambiente"),
            @("migrate", "aplica migrations dos dois schemas"),
            @("migration", 'cria migration: .\age.ps1 migration "descrição"'),
            @("test", "suíte completa"),
            @("lint", "ruff nos dois serviços + tsc no web"),
            @("fmt", "formata o código"),
            @("gen-types", "OpenAPI -> tipos TypeScript"),
            @("shell-db", "abre psql"),
            @("shell-redis", "abre redis-cli"),
            @("reset", "APAGA os volumes e recomeça")
        )
        foreach ($r in $rows) {
            Write-Host ("    {0,-12} {1}" -f $r[0], $r[1])
        }
        Write-Host ""
    }

    "env" {
        if (Test-Path ".env") {
            Write-Host ".env já existe — apague antes se quiser regerar." -ForegroundColor Yellow
            exit 1
        }
        Copy-Item ".env.example" ".env"
        $keys = @(
            "POSTGRES_PASSWORD", "AGE_OWNER_PASSWORD", "CORE_API_DB_PASSWORD",
            "AI_WORKER_DB_PASSWORD", "REDIS_PASSWORD", "MASTER_KEY",
            "JWT_SIGNING_KEY", "SERVICE_JWT_SECRET"
        )
        $content = Get-Content ".env" -Raw
        foreach ($key in $keys) {
            $content = $content -replace "(?m)^$key=.*$", "$key=$(New-Secret)"
        }
        Set-Content ".env" $content -NoNewline -Encoding utf8
        Write-Host ".env gerado com segredos aleatórios." -ForegroundColor Green
        Write-Host "AVISO: perder MASTER_KEY = perder as credenciais salvas." -ForegroundColor Yellow
    }

    "up" {
        Assert-Env
        # O build resolve `python:3.12-slim` e `node:22-alpine` no
        # registry para conferir se há versão nova. Com a internet
        # oscilando isso estoura em 60s e derruba o `up` inteiro —
        # mesmo com as imagens já construídas aqui. Nesse caso sobe com
        # o que existe: é dev, e a alternativa é não trabalhar.
        try {
            Invoke-Compose -ComposeArgs @("up", "-d", "--build")
        }
        catch {
            $locais = & docker images --format "{{.Repository}}" |
                Where-Object { $_ -like "age-*" } |
                Select-Object -Unique
            if ($locais.Count -lt 4) {
                Write-Host ""
                Write-Host "O build falhou e nao ha imagens locais para usar." -ForegroundColor Red
                Write-Host "Parece rede: confira a conexao e rode de novo." -ForegroundColor Yellow
                throw
            }
            Write-Host ""
            Write-Host "Build falhou (provavelmente rede). Subindo com as imagens ja construidas." -ForegroundColor Yellow
            Write-Host "Para reconstruir depois: .\age.ps1 rebuild" -ForegroundColor Yellow
            Write-Host ""
            Invoke-Compose -ComposeArgs @("up", "-d", "--no-build")
        }
        Wait-CoreApi
        & $PSCommandPath migrate
        Wait-Web
        Write-Host ""
        Write-Host "  web       http://localhost:3000" -ForegroundColor Green
        Write-Host "  core-api  http://localhost:8000/docs" -ForegroundColor Green
        Write-Host ""
    }

    "down" { Assert-Env; Invoke-Compose -ComposeArgs @("down") }

    "rebuild" {
        # Reconstrói de fato, consultando o registry. Use depois de um
        # `up` que caiu no modo offline, ou ao mudar dependência.
        Assert-Env
        Invoke-Compose -ComposeArgs @("build", "--pull")
        Invoke-Compose -ComposeArgs @("up", "-d")
        Wait-CoreApi
        & $PSCommandPath migrate
        Wait-Web
        Write-Host ""
        Write-Host "  web       http://localhost:3000" -ForegroundColor Green
        Write-Host "  core-api  http://localhost:8000/docs" -ForegroundColor Green
        Write-Host ""
    }
    "ps" { Assert-Env; Invoke-Compose -ComposeArgs @("ps") }

    "logs" {
        Assert-Env
        Invoke-Compose -ComposeArgs @("logs", "-f", "core-api", "ai-worker", "ai-worker-api", "web")
    }

    "migrate" {
        Assert-Env
        Write-Host "→ migrando schema core"
        Invoke-Compose -ComposeArgs @("exec", "-T", "core-api", "alembic", "upgrade", "head")
        Write-Host "→ migrando schema ai"
        Invoke-Compose -ComposeArgs @("exec", "-T", "ai-worker", "alembic", "upgrade", "head")
    }

    "migration" {
        Assert-Env
        $msg = $Rest -join " "
        if (-not $msg) {
            Write-Host 'uso: .\age.ps1 migration "descrição"' -ForegroundColor Red
            exit 1
        }
        Invoke-Compose -ComposeArgs @(
            "exec", "core-api", "alembic", "revision", "--autogenerate", "-m", $msg
        )
    }

    "smoke" {
        Assert-Env
        $bash = Get-GitBash
        if (-not $bash) {
            Write-Host "Git Bash não encontrado (instale o Git for Windows)." -ForegroundColor Red
            exit 1
        }
        & $bash "infra/smoke.sh"
        exit $LASTEXITCODE
    }

    "test" {
        Assert-Env
        Invoke-Compose -ComposeArgs @("exec", "-T", "core-api", "pytest", "-q")
        Invoke-Compose -ComposeArgs @("exec", "-T", "ai-worker", "pytest", "-q")
    }

    "lint" {
        Assert-Env
        Invoke-Compose -ComposeArgs @("exec", "-T", "core-api", "ruff", "check", ".")
        Invoke-Compose -ComposeArgs @("exec", "-T", "ai-worker", "ruff", "check", ".")
        Invoke-Compose -ComposeArgs @("exec", "-T", "web", "npx", "tsc", "--noEmit")
    }

    "fmt" {
        Assert-Env
        Invoke-Compose -ComposeArgs @("exec", "-T", "core-api", "ruff", "format", ".")
        Invoke-Compose -ComposeArgs @("exec", "-T", "ai-worker", "ruff", "format", ".")
    }

    "gen-types" {
        Assert-Env
        New-Item -ItemType Directory -Force -Path "packages/age-types" | Out-Null
        $spec = Join-Path $env:TEMP "age-openapi.json"
        try {
            Invoke-WebRequest -Uri "http://localhost:8000/openapi.json" `
                -OutFile $spec -UseBasicParsing
        }
        catch {
            Write-Host "core-api não responde — rode .\age.ps1 up" -ForegroundColor Red
            exit 1
        }
        Push-Location "services/web"
        try {
            & npx --yes openapi-typescript $spec -o "../../packages/age-types/api.ts"
        }
        finally { Pop-Location }
        Write-Host "tipos gerados em packages/age-types/api.ts" -ForegroundColor Green
    }

    "shell-db" {
        Assert-Env
        Invoke-Compose -ComposeArgs @("exec", "postgres", "psql", "-U", "age_super", "-d", "age")
    }

    "shell-redis" {
        Assert-Env
        Invoke-Compose -ComposeArgs @(
            "exec", "redis", "sh", "-c", 'redis-cli -a "$REDIS_PASSWORD"'
        )
    }

    "reset" {
        Assert-Env
        Write-Host "Isto apaga o banco e o Redis." -ForegroundColor Yellow
        $ans = Read-Host "Confirma? [y/N]"
        if ($ans -ne "y") { Write-Host "cancelado."; exit 0 }
        Invoke-Compose -ComposeArgs @("down", "-v")
        Write-Host "volumes removidos. rode .\age.ps1 up" -ForegroundColor Green
    }

    default {
        Write-Host "comando desconhecido: $Command" -ForegroundColor Red
        Write-Host "rode: .\age.ps1 help"
        exit 1
    }
}
