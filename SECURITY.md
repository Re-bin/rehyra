# Security policy

## Supported versions

Security fixes are applied to the latest `0.3.x` release and to `main`.
Pre-release experiment snapshots and older tags are not supported.

## Report a vulnerability

While the repository is private, collaborators should open a private draft
advisory from the repository's **Security → Advisories** page. Before public
release, maintainers must enable
[GitHub private vulnerability reporting](https://github.com/Re-bin/rehyra/security/advisories/new)
for external reporters. Do not include an exploit, credential, private
transcript, or unpublished benchmark data in a public issue. Include the
affected version, impact, reproduction steps, and any suggested mitigation. We
will acknowledge a complete report within seven days.

## Trust boundary

ReHyra is an experimental research harness, not a security sandbox.

- Agent providers and evaluator commands run with the permissions of the user
  who launched ReHyra.
- Fresh candidate workspaces protect the source checkout from accidental
  promotion of unrelated edits. They do not prevent a malicious process from
  reading other local files, using the network, or starting child processes.
- Never expose API keys or private data to an untrusted Agent, candidate, task,
  evaluator, or plugin command.
- Run untrusted code in a disposable container or virtual machine with an
  explicit filesystem allowlist, network policy, process limits, and
  short-lived credentials.
- Evidence can contain prompts, transcripts, command output, source paths, and
  provider metadata. Review and redact evidence before publishing it.

The default local backend records that filesystem and network isolation are
disabled. A result is auditable evidence of what ran; it is not an attestation
that the process was securely contained.
