"""Local certificate probe. Passwords are never accepted on the command line."""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from coremail_desktop.crypto.service import CryptoService, CryptoError
from coremail_desktop.crypto.recipients import RecipientCertificateStore


def prompt_password(explicit_path=None):
    """Read the certificate password without putting it on the command line.

    An explicit local file is used only when the operator asks for it, so the
    probe can run unattended; otherwise the local dialog is used. The password
    is never echoed, logged or persisted by this program.
    """
    if explicit_path:
        return Path(explicit_path).read_text(encoding='utf-8').splitlines()[0]
    try:
        import tkinter as tk
        from tkinter import simpledialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
    except Exception:
        import getpass
        return getpass.getpass('本机证书解锁密码（不回显）：')
    try:
        return simpledialog.askstring('本机证书解锁',
            '请输入个人 PFX 证书密码。\n密码仅用于本机解锁，不保存、不上传。\n取消可稍后在客户端中设置。',
            show='*', parent=root)
    finally:
        root.destroy()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=('inspect', 'decrypt', 'encrypt'))
    parser.add_argument('--pfx', required=True)
    parser.add_argument('--report', required=True)
    parser.add_argument('--input')
    parser.add_argument('--output', help='Encrypted output only; decrypt reports digest, never exports plaintext')
    parser.add_argument('--password-file',
        help='Optional local file whose first line holds the PFX password, for unattended runs. '
             'Never pass a password as a command-line argument.')
    args = parser.parse_args(argv)
    report = {'operation': args.operation, 'status': 'not_run', 'real_interoperability': 'not_verified'}
    service = CryptoService()
    password = None
    try:
        password = prompt_password(args.password_file)
        if password is None:
            report['status'] = 'cancelled'
            return 2
        info = service.unlock(args.pfx, password)
        password = None
        report.update(status='passed', fingerprint_sha256=info.fingerprint,
                      identity_count=len(info.emails), can_encrypt=info.can_encrypt,
                      valid_from=info.not_before.isoformat(), valid_until=info.not_after.isoformat(),
                      algorithm='RSA', trust='own_key; chain/revocation not verified')
        if args.operation in {'decrypt', 'encrypt'}:
            if not args.input:
                raise CryptoError('INPUT_REQUIRED')
            source = Path(args.input).read_bytes()
            if args.operation == 'decrypt':
                result = service.decrypt(source)
                report.update(plaintext_size=len(result.mime_bytes),
                              plaintext_sha256=hashlib.sha256(result.mime_bytes).hexdigest(),
                              format=result.format, signature_status=result.signature_status)
            else:
                if not args.output:
                    raise CryptoError('ENCRYPTED_OUTPUT_REQUIRED')
                encrypted = service.encrypt(source, (info,))
                Path(args.output).write_bytes(encrypted)
                report.update(encrypted_size=len(encrypted),
                              encrypted_sha256=hashlib.sha256(encrypted).hexdigest())
        return 0
    except CryptoError as error:
        report.update(status='failed', error=error.code)
        return 1
    except OSError:
        report.update(status='failed', error='FILE_ACCESS_FAILED')
        return 1
    except Exception as error:
        import traceback
        traceback.print_exc()
        report.update(status='failed', error='LOCAL_PROBE_FAILED', exception_type=type(error).__name__)
        if not report.get('fingerprint_sha256'):
            report.pop('exception_type', None)
        return 1
    finally:
        password = None
        service.lock()
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    raise SystemExit(main())
