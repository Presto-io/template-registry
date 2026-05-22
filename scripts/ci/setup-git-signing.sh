#!/usr/bin/env bash
set -euo pipefail

repo="${1:-.}"
cd "${repo}"

configure_unsigned_bot_commit() {
  local name="${CI_GIT_USER_NAME:-github-actions[bot]}"
  local email="${CI_GIT_USER_EMAIL:-41898282+github-actions[bot]@users.noreply.github.com}"

  echo "::warning::CI GPG signing is not fully configured; using unsigned ${name} commits. Set CI_GIT_USER_NAME, CI_GIT_USER_EMAIL, and CI_GPG_PRIVATE_KEY to enable signed CI commits."
  git config user.name "${name}"
  git config user.email "${email}"
  git config --unset-all user.signingkey 2>/dev/null || true
  git config commit.gpgsign false
  git config tag.gpgSign false
  git config tag.forceSignAnnotated false
}

missing_config=false
for var_name in CI_GIT_USER_NAME CI_GIT_USER_EMAIL CI_GPG_PRIVATE_KEY; do
  if [ -z "${!var_name:-}" ]; then
    missing_config=true
  fi
done

if [ "${missing_config}" = true ]; then
  if [ "${CI_REQUIRE_GIT_SIGNING:-false}" = "true" ]; then
    echo "::error::CI_REQUIRE_GIT_SIGNING=true, but CI_GIT_USER_NAME, CI_GIT_USER_EMAIL, and CI_GPG_PRIVATE_KEY are not all configured."
    exit 1
  fi
  configure_unsigned_bot_commit
  exit 0
fi

git config user.name "${CI_GIT_USER_NAME}"
git config user.email "${CI_GIT_USER_EMAIL}"

base_dir="${RUNNER_TEMP:-/tmp}/presto-ci-gpg-${GITHUB_RUN_ID:-$$}-${RANDOM}"
gnupg_home="${base_dir}/gnupg"
mkdir -p "${gnupg_home}"
chmod 700 "${gnupg_home}"

key_file="${base_dir}/private.key"
if printf '%s' "${CI_GPG_PRIVATE_KEY}" | grep -q -- '-----BEGIN PGP PRIVATE KEY BLOCK-----'; then
  printf '%s\n' "${CI_GPG_PRIVATE_KEY}" > "${key_file}"
else
  printf '%s' "${CI_GPG_PRIVATE_KEY}" | base64 --decode > "${key_file}"
fi

cat > "${gnupg_home}/gpg-agent.conf" <<'EOF'
allow-loopback-pinentry
EOF

GNUPGHOME="${gnupg_home}" gpg --batch --import "${key_file}" >/dev/null
rm -f "${key_file}"

signing_key="${CI_GPG_KEY_ID:-}"
if [ -z "${signing_key}" ]; then
  signing_key="$(GNUPGHOME="${gnupg_home}" gpg --batch --with-colons --list-secret-keys | awk -F: '$1 == "fpr" { print $10; exit }')"
fi

if [ -z "${signing_key}" ]; then
  echo "::error::No GPG signing key was found after importing CI_GPG_PRIVATE_KEY."
  exit 1
fi

if ! GNUPGHOME="${gnupg_home}" gpg --batch --with-colons --list-secret-keys "${signing_key}" | grep -F "<${CI_GIT_USER_EMAIL}>" >/dev/null; then
  echo "::error::The imported CI GPG key does not contain the configured CI_GIT_USER_EMAIL (${CI_GIT_USER_EMAIL})."
  exit 1
fi

gpg_wrapper="${base_dir}/gpg-wrapper.sh"
cat > "${gpg_wrapper}" <<EOF
#!/usr/bin/env bash
export GNUPGHOME="${gnupg_home}"
if [ -n "\${CI_GPG_PASSPHRASE:-}" ]; then
  exec gpg --batch --pinentry-mode loopback --passphrase "\${CI_GPG_PASSPHRASE}" "\$@"
fi
exec gpg --batch --pinentry-mode loopback "\$@"
EOF
chmod 700 "${gpg_wrapper}"

git config gpg.program "${gpg_wrapper}"
git config user.signingkey "${signing_key}"
git config commit.gpgsign true
git config tag.gpgSign true
git config tag.forceSignAnnotated true

echo "Configured git signing for ${signing_key} in $(pwd)"
