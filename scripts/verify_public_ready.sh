#!/usr/bin/env bash
# Public GitHub push öncesi: .env, logs, .venv ve gizli anahtar sızıntısı kontrolü.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

errors=0

fail() {
  echo -e "${RED}HATA:${NC} $*" >&2
  errors=$((errors + 1))
}

warn() {
  echo -e "${YELLOW}UYARI:${NC} $*" >&2
}

ok() {
  echo -e "${GREEN}OK:${NC} $*"
}

# ── .gitignore varlığı ve kritik kurallar ─────────────────────────────────────
if [[ ! -f .gitignore ]]; then
  fail ".gitignore bulunamadı"
else
  for pattern in "^\.env$" "^logs/" "^\.venv/"; do
    if ! grep -qE "$pattern" .gitignore 2>/dev/null; then
      fail ".gitignore içinde beklenen kural yok: $pattern"
    fi
  done
  ok ".gitignore kritik kurallar mevcut"
fi

# ── Yerel .env repoda tracked olmamalı (git varsa) ───────────────────────────
if [[ -f .env ]]; then
  if git rev-parse --git-dir >/dev/null 2>&1; then
    if git ls-files --error-unmatch .env >/dev/null 2>&1; then
      fail ".env dosyası git tarafından izleniyor — git rm --cached .env"
    else
      ok ".env yerelde var, git'te izlenmiyor"
    fi
  else
    warn "Git deposu yok — .env yalnızca yerelde kalmalı (git init sonrası tekrar çalıştırın)"
  fi
fi

# ── Git yoksa buradan sonrası atlanır (staged tarama) ───────────────────────
if ! git rev-parse --git-dir >/dev/null 2>&1; then
  if [[ $errors -eq 0 ]]; then
    echo ""
    ok "Temel dosya kontrolleri geçti (git init sonrası script'i yeniden çalıştırın)"
    exit 0
  fi
  exit 1
fi

# ── Yasak path'ler staged / tracked ─────────────────────────────────────────
FORBIDDEN_PATHS=(
  ".env"
  ".venv"
  "logs"
  ".streamlit/secrets.toml"
)

while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  for forbidden in "${FORBIDDEN_PATHS[@]}"; do
    case "$path" in
      "$forbidden"|"$forbidden"/*|*/"$forbidden"|*/"$forbidden"/*)
        fail "Yasak path git'te: $path"
        ;;
    esac
  done
  case "$path" in
    duzeltilmis_*|*/duzeltilmis_*)
      fail "Düzeltilmiş upload çıktısı git'te olmamalı: $path"
      ;;
  esac
done < <(git ls-files)

# Staged (index) dosyalar
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  for forbidden in "${FORBIDDEN_PATHS[@]}"; do
    case "$path" in
      "$forbidden"|"$forbidden"/*)
        fail "Yasak path staged: $path"
        ;;
    esac
  done
done < <(git diff --cached --name-only 2>/dev/null || true)

if [[ $errors -eq 0 ]]; then
  ok "Yasak path'ler (logs, .env, .venv) git'te yok"
fi

# ── Staged içerikte gizli anahtar taraması ───────────────────────────────────
PLACEHOLDER_PATTERN='your_.*_here|your-api-key|xxx|changeme|placeholder'

scan_file_for_secrets() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  # Binary atla
  if file -b --mime-type "$file" 2>/dev/null | grep -q 'charset=binary'; then
    return 0
  fi

  if grep -qE 'AIzaSy[0-9A-Za-z_-]{20,}' "$file" 2>/dev/null; then
    fail "Google API key benzeri desen: $file"
  fi
  if grep -qE 'sk-[a-zA-Z0-9]{20,}' "$file" 2>/dev/null; then
    fail "OpenAI/sk- benzeri anahtar: $file"
  fi
  # Gerçek key: placeholder olmayan GOOGLE_API_KEY=...
  if grep -E '^[[:space:]]*(export[[:space:]]+)?(GOOGLE_API_KEY|GEMINI_API_KEY)=' "$file" 2>/dev/null \
    | grep -vE "$PLACEHOLDER_PATTERN" \
    | grep -qE '=.{8,}'; then
    if ! grep -E '^[[:space:]]*(export[[:space:]]+)?(GOOGLE_API_KEY|GEMINI_API_KEY)=' "$file" \
      | grep -vE "$PLACEHOLDER_PATTERN" \
      | grep -qE '=(your_|""|$)'; then
      fail "Dolu API key satırı staged dosyada: $file"
    fi
  fi
}

# Yalnızca staged text dosyaları
while IFS= read -r path; do
  [[ -z "$path" ]] && continue
  case "$path" in
    .env)
      fail ".env staged — commit etmeyin: $path"
      continue
      ;;
    .env.*)
      if [[ "$path" != ".env.example" ]]; then
        fail ".env staged — commit etmeyin: $path"
        continue
      fi
      ;;
  esac
  if git show ":$path" >/dev/null 2>&1; then
    tmp="$(mktemp)"
    git show ":$path" >"$tmp" 2>/dev/null || true
    scan_file_for_secrets "$tmp"
    rm -f "$tmp"
  fi
done < <(git diff --cached --name-only --diff-filter=ACMR 2>/dev/null || true)

# Kaynak ağacında .env.example dışında .env.* tracked?
while IFS= read -r path; do
  case "$path" in
    .env.example) ;;
    .env)
      fail "Ortam dosyası commit'te: $path"
      ;;
    .env.*)
      fail "Ortam dosyası commit'te: $path"
      ;;
  esac
done < <(git ls-files '.env*' 2>/dev/null || true)

# ── Sonuç ───────────────────────────────────────────────────────────────────
echo ""
if [[ $errors -gt 0 ]]; then
  echo -e "${RED}$errors kontrol başarısız — public push yapmayın.${NC}" >&2
  exit 1
fi

echo -e "${GREEN}Public push için temel kontroller geçti.${NC}"
exit 0
