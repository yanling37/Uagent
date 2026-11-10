import smtplib
import unittest
from email.message import EmailMessage
from unittest.mock import patch

from portable_ai.qq_mail import (
    QQLogin, QQMailbox, QQMailError, QQSmtpImap, MemoryMailbox,
    build_message, format_inbox, parse_message,
)


CODE = 'Abcd1234Efgh5678'


class RecordingSMTP:
    def __init__(self):
        self.login_args=None
        self.sent=None
        self.fail=None

    def login(self, user, password):
        self.login_args=(user, password)
        if self.fail:
            raise self.fail

    def sendmail(self, sender, recipients, raw):
        self.sent=(sender, recipients, raw)
        return {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class RecordingIMAP:
    def __init__(self, raws):
        self.raws=raws
        self.readonly=None
        self.criteria=None
        self.peek=None
        self.stored=False

    def login(self, user, password):
        self.login_args=(user, password)
        return 'OK', []

    def select(self, mailbox, readonly=False):
        self.mailbox=mailbox
        self.readonly=readonly
        return 'OK', [b'2']

    def search(self, charset, *criteria):
        self.criteria=(charset, criteria)
        ids=b' '.join(str(i+1).encode() for i in range(len(self.raws)))
        return 'OK', [ids]

    def fetch(self, number, query):
        self.peek=query
        index=int(number)-1
        raw=self.raws[index]
        return 'OK', [(b'%s (BODY[] {%d}'%(number, len(raw)), raw), b')']

    def store(self, *args):
        self.stored=True
        raise AssertionError('receive must not change mailbox flags')

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def login(address='10001@qq.com'):
    return QQLogin.check(address, CODE)


class QQMailTests(unittest.TestCase):
    def test_login_rules_and_redaction(self):
        account=login('User@Foxmail.com')
        self.assertEqual(account.address,'User@foxmail.com')
        self.assertNotIn(CODE, repr(account))
        self.assertNotIn(CODE, str(account))
        for address, code in [('a@gmail.com', CODE), ('10001@qq.com', 'short'), ('10001@qq.com', 'password-not-code')]:
            with self.subTest(address=address, code=code), self.assertRaises(QQMailError):
                QQLogin.check(address, code)

    def test_rejects_header_injection_and_secrets_before_transport(self):
        box=QQMailbox(login(), MemoryMailbox())
        with self.assertRaises(QQMailError):
            box.send_text('a@qq.com\nBcc: evil@example.com', '标题', '正文')
        with self.assertRaises(QQMailError):
            box.send_text('a@qq.com', '标题\r\nBcc: evil@example.com', '正文')
        with self.assertRaises(QQMailError):
            box.send_text('两个,地址@qq.com', '标题', '正文')
        with self.assertRaises(QQMailError):
            box.send_text('a@qq.com', '标题', '授权码是 '+CODE)
        self.assertEqual(box.transport.sent, [])

    def test_roundtrip_keeps_chinese_and_newest_first(self):
        transport=MemoryMailbox()
        box=QQMailbox(login(), transport)
        box.send_text('peer@example.com', '第一封', '较早的正文', 'task')
        box.send_text('peer@example.com', '同步完成', '本机已写好。\n第二行', 'result')
        notes, skipped=box.receive_text()
        self.assertEqual(skipped, 0)
        self.assertEqual([n.title for n in notes], ['同步完成', '第一封'])
        self.assertEqual(notes[0].kind, 'result')
        self.assertEqual(notes[0].body, '本机已写好。\n第二行')
        self.assertEqual(notes[0].sender, '10001@qq.com')
        self.assertNotIn(CODE, transport.sent[0][1].decode('utf-8', 'replace'))
        text=format_inbox(notes, 0)
        self.assertIn('不会自动执行', text)
        self.assertIn('同步完成', text)

    def test_receive_skips_foreign_and_oversized_mail(self):
        foreign=EmailMessage()
        foreign['From']='x@example.com'
        foreign['To']='10001@qq.com'
        foreign['Subject']='普通通知'
        foreign.set_content('ignore me')
        transport=MemoryMailbox()
        transport.sent.append(('10001@qq.com', foreign.as_bytes()))
        transport.sent.append(('10001@qq.com', b'x'*(64_001)))
        notes, skipped=QQMailbox(login(), transport).receive_text()
        self.assertEqual(notes, [])
        self.assertEqual(skipped, 2)

    def test_smtp_is_pinned_to_qq_and_scrubs_auth_code(self):
        client=RecordingSMTP()
        client.fail=smtplib.SMTPAuthenticationError(535, b'auth failed '+CODE.encode())
        with patch('portable_ai.qq_mail.ssl.create_default_context', return_value='CTX'), \
             patch('portable_ai.qq_mail.smtplib.SMTP_SSL', return_value=client) as smtp:
            with self.assertRaises(QQMailError) as caught:
                QQSmtpImap().send(login(), 'peer@qq.com', b'raw')
        smtp.assert_called_once_with('smtp.qq.com', 465, context='CTX', timeout=20)
        self.assertNotIn(CODE, str(caught.exception))
        self.assertIn('授权码', str(caught.exception))

    def test_smtp_sends_only_the_validated_envelope(self):
        client=RecordingSMTP()
        raw, note=build_message(login(), 'Peer@Example.com', '你好', '正文', 'note')
        with patch('portable_ai.qq_mail.smtplib.SMTP_SSL', return_value=client):
            QQSmtpImap().send(login(), note.recipient, raw)
        self.assertEqual(client.login_args, ('10001@qq.com', CODE))
        self.assertEqual(client.sent[0], '10001@qq.com')
        self.assertEqual(client.sent[1], ['Peer@example.com'])
        self.assertIn(b'Subject:', client.sent[2])
        self.assertNotIn(b'\nBcc:', client.sent[2])

    def test_imap_peeks_without_writing_flags(self):
        raw, _=build_message(login('9@vip.qq.com'), '10001@qq.com', '取回', '请审阅', 'task')
        client=RecordingIMAP([raw])
        with patch('portable_ai.qq_mail.ssl.create_default_context', return_value='CTX'), \
             patch('portable_ai.qq_mail.imaplib.IMAP4_SSL', return_value=client) as imap:
            notes, skipped=QQMailbox(login(), QQSmtpImap()).receive_text()
        imap.assert_called_once_with('imap.qq.com', 993, ssl_context='CTX', timeout=20)
        self.assertTrue(client.readonly)
        self.assertEqual(client.criteria, (None, ('SUBJECT', 'PortableAI')))
        self.assertEqual(client.peek, '(BODY.PEEK[])')
        self.assertFalse(client.stored)
        self.assertEqual(skipped, 0)
        self.assertEqual(notes[0].body, '请审阅')
        self.assertEqual(notes[0].kind, 'task')

    def test_empty_inbox(self):
        client=RecordingIMAP([])
        with patch('portable_ai.qq_mail.imaplib.IMAP4_SSL', return_value=client):
            notes, skipped=QQMailbox(login(), QQSmtpImap()).receive_text(limit=99)
        self.assertEqual((notes, skipped), ([], 0))

    def test_body_limit_blocks_file_sized_payload(self):
        with self.assertRaises(QQMailError):
            build_message(login(), 'a@qq.com', '太大', '字'*24_001)


if __name__=='__main__':
    unittest.main()
