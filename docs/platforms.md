# Python and platform support

Packaging requires Python >=3.10. The maintained Linux CI matrix is 3.10, 3.11,
3.12 and 3.13; newer interpreters are not automatically claimed supported merely
because installation is permitted.

| Platform | Evidence / status |
|---|---|
| Linux | Ubuntu CI: Python 3.10–3.13 tests, plus 3.13 coverage/security/evals/build/install checks |
| Windows | Python 3.13 CI builds wheel/sdist and smoke-tests an installed wheel; local full gate and wheel/sdist installs use Python 3.14. POSIX-only tests skip locally |
| Termux / Android | Design target, manually validated only; installer syntax is checked in CI. No Android runtime validation was performed for this release candidate |
| macOS | Expected through portable Python paths, best effort; no dedicated CI or local validation |

PRoot additionally requires a Termux host, installed `proot`, and a caller-provided
rootfs. Native and PRoot execution are not OS security boundaries. See
[execution security](execution-security.md) for exact guarantees and limitations.

## Generic source installation

Clone the repository and create a virtual environment using an available supported
Python. On POSIX use `python3 -m venv .venv` and `. .venv/bin/activate`; on Windows
use `py -3.13 -m venv .venv` and `.venv\Scripts\python.exe` directly if preferred.
Then run `python -m pip install .`, `sable --version`, `sable --help` and
`sable doctor .`. The Termux `install.sh` is not a generic Linux/Windows installer.

To install a locally built distribution, use
`python -m pip install dist/sable_ai_agent-2.0.0-py3-none-any.whl` (substitute the
actual built version). No PyPI release is implied. Uninstall with
`python -m pip uninstall sable-ai-agent`. Pip does not remove your repositories
or `~/.sable` user state; review and back up that state before any manual cleanup.

## Manual Termux smoke checklist

Use a maintained Termux installation with its own `pkg` packages. From the cloned
repository run `bash install.sh`; repeat to check editable-install idempotency.
The installer rejects non-Termux invocations, resolves its own source path, keeps
Termux's `python-pip` managed by `pkg`, and fails if private state permissions
cannot be applied. It never reads or writes provider credentials. In particular,
it does not attempt `pip install --upgrade pip`, which
[Termux explicitly blocks](https://github.com/termux/termux-packages/blob/master/packages/python-pip/install_py_preventing_pip_from_installing.patch).
Record Android/Termux/Python versions and whether native or PRoot was selected.

1. Run `sable --version` and `sable --help`.
2. Run `sable doctor` against a fresh temporary project. Missing provider config
   should report NOT READY and exit 2; doctor must leave project files unchanged.
3. Use `sable <path>` and inspect `/status`, `/sandbox`, `/verify`, `/txn`, `/session`.
4. In a disposable project, perform one small edit/verification and `/undo`;
   confirm rollback and bounded failure handling. Provider use is explicitly manual.
5. If testing PRoot, configure an existing rootfs and verify the reported best-effort
   guarantees; do not interpret a working PRoot command as proven isolation.

Do not run destructive smoke tasks against real projects. Linux CI cannot replace
this Android checklist. Record actual results before upgrading a support claim.
