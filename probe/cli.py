"""Local interactive entrypoint for the approved compatibility experiment."""
import argparse
from datetime import datetime, timezone
import getpass
import json
import warnings
from pathlib import Path

from .coremail_transport import CoremailSession, CoremailError, _login_form
from .connectivity import verify_read, verify_roundtrip, _account
from urllib.request import Request


def _password_dialog(account, server, send_self):
    # User entry remains in this process. No secret is exposed in argv, stdout,
    # reports, files, browser automation, or tool input parameters.
    import tkinter as tk
    from tkinter import simpledialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        value = simpledialog.askstring(
            'Coremail 独立连接测试',
            f'目标：{server}\n账号：{account}\n\n'
            '请输入邮箱密码（仅在本机内存使用，不保存）。\n'
            + ('本次将向此账号自己发送一封固定测试邮件。' if send_self else '本次仅验证登录和邮件读取。'),
            show='*', parent=root,
        )
    finally:
        root.destroy()
    if value is None:
        raise KeyboardInterrupt()
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description='Temporary Coremail login/read/self-send compatibility test')
    parser.add_argument('--account', default='wangbiao@inspur.com')
    parser.add_argument('--server', default='https://newmail.inspur.com/coremail/')
    parser.add_argument('--send-self', action='store_true', help='Send one fixed message to the same account and check reception')
    parser.add_argument('--password-dialog', action='store_true', help='Use a local masked password dialog instead of getpass')
    parser.add_argument('--preflight', action='store_true', help='Only inspect the unauthenticated login form; no login or email')
    parser.add_argument('--folder-id', type=int, default=0)
    parser.add_argument('--report', default='analysis/connection-probe-result.json')
    args = parser.parse_args(argv)
    report = {
        'experiment': 'temporary_coremail_compatibility_probe',
        'started_at_utc': datetime.now(timezone.utc).isoformat(),
        'independent_session': True,
        'send_requested': args.send_self,
    }
    session = None
    exit_code = 2
    try:
        account = _account(args.account)
        session = CoremailSession(args.server)
        if args.preflight:
            print('STEP public_login_form', flush=True)
            raw, unused_url = session._fetch(Request(session.base_url))
            form = _login_form(raw.decode('utf-8', errors='replace'))
            report['preflight'] = {
                'status': 'passed' if form.has_password and not form.fake_password else 'unsupported',
                'hidden_field_count': len(form.fields),
                'normal_password_form': form.has_password and not form.fake_password,
            }
            exit_code = 0 if report['preflight']['status'] == 'passed' else 2
        else:
            print('Waiting for local password entry. Password is not saved or printed.', flush=True)
            if args.password_dialog:
                password = _password_dialog(account, args.server, args.send_self)
            else:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', getpass.GetPassWarning)
                    password = getpass.getpass('Coremail password: ')
            if not password:
                raise ValueError('Empty credential')
            print('STEP independent_login', flush=True)
            try:
                session.login(account, password)
            finally:
                password = None
            print('STEP folders_list_and_mime', flush=True)
            report.update(verify_read(session, folder_id=args.folder_id))
            report['login'] = {'status': 'passed'}
            if args.send_self:
                print('STEP send_fixed_self_test_and_verify_reception', flush=True)
                report['roundtrip'] = verify_roundtrip(session, account, folder_id=0)
                exit_code = 0 if report['roundtrip']['delivery']['status'] == 'passed' else 3
            else:
                exit_code = 0
    except CoremailError as error:
        report['error'] = error.code
        diagnostic = getattr(session, 'last_login_diagnostics', None)
        if isinstance(diagnostic, dict):
            report['login_diagnostics'] = diagnostic
    except getpass.GetPassWarning:
        report['error'] = 'PASSWORD_INPUT_UNAVAILABLE'
    except KeyboardInterrupt:
        report['error'] = 'CANCELLED_BY_USER'
        exit_code = 130
    except ValueError:
        report['error'] = 'PROTOCOL_VALIDATION_FAILED'
    except Exception:
        report['error'] = 'UNEXPECTED_LOCAL_ERROR'
    finally:
        if session is not None:
            session.close()
    report['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    report['status'] = 'passed' if exit_code == 0 else 'incomplete'
    target = Path(args.report)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    if report.get('error') == 'AUTH_INTERACTION_REQUIRED':
        print('The server did not establish a session. Stop here and inspect the normal login or MFA requirements.', flush=True)
    return exit_code
