"""Short-text relay over the user's own QQ mailbox.

This is the lightweight stand-in for an always-on task gateway: QQ's SMTP and
IMAP servers carry a small PortableAI note, and nothing else. It is not file
sync, not a general mail client, and not an instruction channel. Received text
is returned to the caller; this module never executes it.
"""
from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
from dataclasses import dataclass
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import parseaddr

QQ_SMTP_HOST = 'smtp.qq.com'
QQ_SMTP_PORT = 465
QQ_IMAP_HOST = 'imap.qq.com'
QQ_IMAP_PORT = 993
TIMEOUT_SECONDS = 20

KINDS = ('note', 'task', 'result')
MAX_TITLE = 80
MAX_BODY = 24_000
MAX_FETCH = 15
MAX_RAW_BYTES = 64_000

_LOGIN_RE = re.compile(r'^[A-Za-z0-9._+-]{1,64}@(qq\.com|foxmail\.com|vip\.qq\.com)$', re.IGNORECASE)
_RECIPIENT_RE = re.compile(r'^[A-Za-z0-9._+-]{1,64}@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}$')
_CODE_RE = re.compile(r'^[A-Za-z0-9]{16}$')
_SUBJECT_RE = re.compile(r'^PortableAI (note|task|result): ([^\r\n]{1,80})$')


class QQMailError(ValueError):
    pass


@dataclass(frozen=True)
class QQLogin:
    address: str
    auth_code: str

    def __repr__(self) -> str:
        return f'QQLogin(address={self.address!r}, auth_code=<redacted>)'

    def __str__(self) -> str:
        return repr(self)

    @classmethod
    def check(cls, address: str, auth_code: str) -> 'QQLogin':
        address = (address or '').strip()
        auth_code = (auth_code or '').strip()
        if not _LOGIN_RE.fullmatch(address):
            raise QQMailError('发件邮箱必须是你自己的 QQ 邮箱（@qq.com、@foxmail.com 或 @vip.qq.com）。')
        local, domain = address.rsplit('@', 1)
        address = local + '@' + domain.lower()
        if not _CODE_RE.fullmatch(auth_code):
            raise QQMailError('请填写 QQ 邮箱设置里生成的 16 位授权码，不要填写 QQ 密码。')
        return cls(address, auth_code)


@dataclass(frozen=True)
class Outgoing:
    sender: str
    recipient: str
    kind: str
    title: str
    body: str
    subject: str


@dataclass(frozen=True)
class IncomingNote:
    uid: str
    sender: str
    subject: str
    kind: str
    title: str
    body: str


def _clean_recipient(value: str) -> str:
    value = (value or '').strip()
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in value) or not _RECIPIENT_RE.fullmatch(value):
        raise QQMailError('对方邮箱只能填写一个地址，不能包含名称、逗号或换行。')
    local, domain = value.rsplit('@', 1)
    return local + '@' + domain.lower()


def _clean_title(title: str) -> str:
    title = (title or '').strip()
    if not title or len(title) > MAX_TITLE:
        raise QQMailError(f'标题需要 1 到 {MAX_TITLE} 个字符。')
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in title):
        raise QQMailError('标题不能包含换行或控制字符。')
    return title


def _clean_body(body: str, login: QQLogin, title: str) -> str:
    if not isinstance(body, str):
        raise QQMailError('正文必须是文本。')
    body = body.strip()
    if not body:
        raise QQMailError('正文不能为空。')
    if len(body) > MAX_BODY:
        raise QQMailError(f'正文超过 {MAX_BODY} 个字符。这条通道只发送短文本，不同步文件。')
    if '\x00' in body or login.auth_code in body or login.auth_code in title:
        raise QQMailError('标题或正文包含授权码或空字符，已拒绝发送。')
    return body


def build_message(login: QQLogin, recipient: str, title: str, body: str, kind: str = 'note') -> tuple[bytes, Outgoing]:
    """Validate and encode one outbound note. Does not open a network connection."""
    if kind not in KINDS:
        raise QQMailError('只支持 note、task、result 三种短消息。')
    recipient = _clean_recipient(recipient)
    title = _clean_title(title)
    body = _clean_body(body, login, title)
    subject = f'PortableAI {kind}: {title}'
    message = EmailMessage()
    message['From'] = login.address
    message['To'] = recipient
    message['Subject'] = subject
    message.set_content(body, charset='utf-8', cte='base64')
    raw = message.as_bytes(policy=policy.SMTP)
    return raw, Outgoing(login.address, recipient, kind, title, body, subject)


def parse_message(uid: str, raw: bytes) -> IncomingNote | None:
    """Return a note only when the subject is one of ours. Other mail is ignored."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) > MAX_RAW_BYTES:
        return None
    try:
        message = BytesParser(policy=policy.default).parsebytes(bytes(raw))
        subject = str(message.get('Subject') or '').strip()
        matched = _SUBJECT_RE.fullmatch(subject)
        if not matched:
            return None
        part = message.get_body(preferencelist=('plain',))
        text = part.get_content() if part is not None else ''
    except Exception:
        return None
    if not isinstance(text, str):
        return None
    text = text.strip()[:MAX_BODY] or '（这封邮件没有纯文本正文）'
    sender = parseaddr(str(message.get('From') or ''))[1]
    return IncomingNote(str(uid), sender, subject, matched.group(1), matched.group(2).strip(), text)


def format_inbox(notes: list[IncomingNote], skipped: int) -> str:
    lines = ['以下文字来自 QQ 邮箱，只在屏幕上显示。程序不会自动执行，也不会自行发给模型。']
    if not notes:
        lines.append('没有 PortableAI 邮件。')
    long_body = False
    for note in notes:
        lines.append(f'— {note.kind} · {note.sender or "未知发件人"} · {note.title}')
        lines.append(note.body[:2000])
        long_body = long_body or len(note.body) > 2000
    if long_body:
        lines.append('控制台只展示每封正文的前 2000 字；填入任务框时使用完整正文。')
    if skipped:
        lines.append(f'另有 {skipped} 封因过大或无法识别而跳过。')
    return '\n'.join(lines)


def _public_failure(exc: BaseException, login: QQLogin) -> str:
    scrubbed = str(exc).replace(login.auth_code, '***')[:160]
    auth_failure = isinstance(exc, smtplib.SMTPAuthenticationError)
    lowered = scrubbed.lower()
    if auth_failure or ('auth' in lowered and ('fail' in lowered or '535' in lowered or 'login' in lowered)):
        return 'QQ 邮箱登录失败。请确认已开启 SMTP 和 IMAP，并使用 16 位授权码而不是 QQ 密码。'
    if isinstance(exc, (TimeoutError, smtplib.SMTPException, imaplib.IMAP4.error, OSError)):
        return '连接 QQ 邮箱失败：' + scrubbed
    return 'QQ 邮箱操作失败：' + scrubbed


def _open_smtp():
    return smtplib.SMTP_SSL(
        QQ_SMTP_HOST, QQ_SMTP_PORT,
        context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS)


def _open_imap():
    return imaplib.IMAP4_SSL(
        QQ_IMAP_HOST, QQ_IMAP_PORT,
        ssl_context=ssl.create_default_context(), timeout=TIMEOUT_SECONDS)


def _raw_from_fetch(payload) -> bytes | None:
    if not payload:
        return None
    for item in payload:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


class MemoryMailbox:
    """Offline stand-in used by tests. Never contacts QQ."""

    def __init__(self):
        self.sent: list[tuple[str, bytes]] = []

    def send(self, login: QQLogin, recipient: str, raw: bytes) -> None:
        self.sent.append((recipient, raw))

    def fetch(self, login: QQLogin, limit: int) -> list[tuple[str, bytes]]:
        chosen = self.sent[-max(1, min(int(limit), MAX_FETCH)):]
        return [(str(index), raw) for index, (_, raw) in enumerate(reversed(chosen), start=1)]


class QQSmtpImap:
    """Stdlib SMTP/IMAP against QQ's own endpoints only.

    Factories exist so tests can substitute a socket. Production calls pin the
    host, port, and certificate check; there is no setting to point the
    authorization code at another server.
    """

    def __init__(self, smtp_factory=None, imap_factory=None):
        self._smtp_factory = smtp_factory or _open_smtp
        self._imap_factory = imap_factory or _open_imap

    def send(self, login: QQLogin, recipient: str, raw: bytes) -> None:
        try:
            with self._smtp_factory() as smtp:
                smtp.login(login.address, login.auth_code)
                refused = smtp.sendmail(login.address, [recipient], raw)
        except QQMailError:
            raise
        except Exception as exc:
            raise QQMailError(_public_failure(exc, login)) from None
        if refused:
            raise QQMailError('对方地址拒绝接收这封邮件。')

    def fetch(self, login: QQLogin, limit: int) -> list[tuple[str, bytes]]:
        limit = max(1, min(int(limit), MAX_FETCH))
        try:
            with self._imap_factory() as imap:
                imap.login(login.address, login.auth_code)
                status, _ = imap.select('INBOX', readonly=True)
                if status != 'OK':
                    raise QQMailError('无法打开 QQ 收件箱。')
                status, data = imap.search(None, 'SUBJECT', 'PortableAI')
                if status != 'OK' or not data or not data[0]:
                    return []
                numbers = data[0].split()[-limit:]
                found = []
                for number in reversed(numbers):
                    status, payload = imap.fetch(number, '(BODY.PEEK[])')
                    raw = _raw_from_fetch(payload)
                    if status != 'OK' or raw is None:
                        continue
                    found.append((number.decode('ascii', 'replace'), raw))
                return found
        except QQMailError:
            raise
        except Exception as exc:
            raise QQMailError(_public_failure(exc, login)) from None


class QQMailbox:
    def __init__(self, login: QQLogin, transport):
        self.login = login
        self.transport = transport

    def send_text(self, recipient: str, title: str, body: str, kind: str = 'note') -> dict:
        raw, note = build_message(self.login, recipient, title, body, kind)
        self.transport.send(self.login, note.recipient, raw)
        return {
            'recipient': note.recipient,
            'subject': note.subject,
            'kind': note.kind,
            'bytes': len(raw),
        }

    def receive_text(self, limit: int = 10) -> tuple[list[IncomingNote], int]:
        rows = self.transport.fetch(self.login, limit)
        notes: list[IncomingNote] = []
        skipped = 0
        for uid, raw in rows:
            note = parse_message(str(uid), raw)
            if note is None:
                skipped += 1
            else:
                notes.append(note)
        return notes, skipped
