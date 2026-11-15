# Contributor licence agreements

Contributions to the repositories in the `open-unbork` organisation are
accepted under a contributor licence agreement (CLA). Pull requests to
`open-unbork/unbork` and `open-unbork/unbork-playbooks` check for it. You keep the
copyright in your contribution; the agreement is a licence, not an
assignment.

| Document | Who signs it |
|---|---|
| [`v1/ICLA.md`](v1/ICLA.md) | You, contributing for yourself. |
| [`v1/CCLA.md`](v1/CCLA.md) | A company, for the employees it lists. |

## How to sign

**Individuals.** On your first pull request a bot comments and the
`CLAAssistant` check fails. Read the ICLA, then post this comment on the
pull request, exactly as written:

    I have read the CLA Document and I hereby sign the CLA

The check turns green. You sign once: the same signature covers later
pull requests in both repositories. If the check does not update, or
signing reports an error (two signatures written at the same moment can
collide), post `recheck`. If it still shows red, push a new commit to the
pull request: the check runs again on the new head.

Commits are matched to GitHub accounts by their email address. Make sure
the email your commits carry is
[added to your GitHub account](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-email-preferences/adding-an-email-address-to-your-github-account),
or the check cannot tell that the commits are yours.

**Companies.** Fill in and sign the CCLA, list the GitHub logins of the
employees it covers in Schedule A, and send it to the maintainer. Those
logins are then added to the check's allowlist in both repositories, so
they are not asked to sign the ICLA. Their commit emails must be linked
to those GitHub accounts, as above.

## What is stored, where, and why

When you sign, the check records your GitHub login, your GitHub account
id, the time, the pull request number and the id of your comment. For a
CCLA the signed document is kept as well. The record is kept in a
private repository in the `open-unbork` organisation that only the
maintainer can read, for as long as your contributions are part of the
projects, because it is the proof that the agreement was made. Ask the
maintainer if you want to see your record.

## Versions

A text is never edited once people have signed it. A corrected text
becomes `v2/` with a new signatures file, and contributors are asked to
sign it on their next pull request.
