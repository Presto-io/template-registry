#!/usr/bin/env bash
set -euo pipefail

repo="${1:-.}"
cd "${repo}"

if [ -z "${CI_GIT_USER_NAME:-}" ] || [ -z "${CI_GIT_USER_EMAIL:-}" ]; then
  echo "::error::CI_GIT_USER_NAME and CI_GIT_USER_EMAIL are required. They must match a GitHub account with the CI GPG public key attached."
  exit 1
fi

git config user.name "${CI_GIT_USER_NAME}"
git config user.email "${CI_GIT_USER_EMAIL}"

if [ -z "${CI_GPG_PRIVATE_KEY:-}" ]; then
  echo "::error::CI_GPG_PRIVATE_KEY is required for CI commits. Configure the repository or organization secret before this workflow can write to git."
  exit 1
fi

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
